"""Auto-provision GitHub OIDC federated credentials and repo secrets.

For Azure RM, Kubernetes Service, and Azure Container Registry service
connections, the migration tool can configure repo-level OIDC trust and create
the corresponding GitHub repository secrets without operator input. When the
required values are unavailable or the GitHub/Azure API rejects the request, a
:class:`ProvisioningResult` with ``success=False`` and a ``failure_reason`` is
returned so the caller can surface the connection in the Resolve Dependencies
flow (FR-030). Operators may pre-configure mappings to skip auto-provisioning
entirely (FR-020).
"""
from __future__ import annotations

from base64 import b64encode
from dataclasses import dataclass, field
from typing import Any, Optional

# Service connection types that support OIDC auto-provisioning.
AUTO_PROVISIONABLE_TYPES: frozenset[str] = frozenset({
    "azurerm",
    "kubernetes",
    "azurecr",
    "acr",
    "dockerregistry",  # ACR-backed registries
})

# Required GitHub secret names per connection type. Values are sourced from the
# service connection metadata captured during discovery (or operator input).
SECRET_TEMPLATES: dict[str, list[str]] = {
    "azurerm": ["AZURE_CLIENT_ID", "AZURE_TENANT_ID", "AZURE_SUBSCRIPTION_ID"],
    "kubernetes": ["KUBE_CONFIG"],
    "azurecr": ["ACR_LOGIN_SERVER", "ACR_CLIENT_ID", "ACR_TENANT_ID"],
    "acr": ["ACR_LOGIN_SERVER", "ACR_CLIENT_ID", "ACR_TENANT_ID"],
    "dockerregistry": ["ACR_LOGIN_SERVER", "ACR_CLIENT_ID", "ACR_TENANT_ID"],
}


@dataclass
class ProvisioningResult:
    """Outcome of attempting to auto-provision a service connection."""

    sc_name: str
    sc_type: str
    success: bool
    provisioned_secrets: list[str] = field(default_factory=list)
    failure_reason: Optional[str] = None
    operator_required: bool = False
    skipped_override: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "sc_name": self.sc_name,
            "sc_type": self.sc_type,
            "success": self.success,
            "provisioned_secrets": list(self.provisioned_secrets),
            "failure_reason": self.failure_reason,
            "operator_required": self.operator_required,
            "skipped_override": self.skipped_override,
        }


def is_auto_provisionable(sc_type: str) -> bool:
    """Return True if ``sc_type`` supports OIDC auto-provisioning."""
    return (sc_type or "").lower() in AUTO_PROVISIONABLE_TYPES


class OIDCProvisioner:
    """Creates repo-level OIDC trust and GitHub secrets for a service connection."""

    def __init__(self, settings: Any | None = None) -> None:
        # ``settings`` is an optional SettingsStore for operator-override lookups.
        self.settings = settings

    def provision(
        self,
        sc_name: str,
        sc_type: str,
        repo_target: str,
        gh_client: Any,
        *,
        secret_values: dict[str, str] | None = None,
        profile_id: str | None = None,
    ) -> ProvisioningResult:
        """Provision OIDC + secrets for ``sc_name`` on ``repo_target`` (org/repo).

        Returns a :class:`ProvisioningResult`. On any failure the result has
        ``success=False`` with a ``failure_reason`` and (when applicable)
        ``operator_required=True`` so the caller can prompt the operator.
        """
        sc_type_norm = (sc_type or "").lower()

        # FR-020: operator override — if a mapping already exists in the profile,
        # use it instead of auto-provisioning.
        if self._has_operator_override(profile_id, sc_name):
            return ProvisioningResult(
                sc_name=sc_name,
                sc_type=sc_type_norm,
                success=True,
                skipped_override=True,
            )

        if not is_auto_provisionable(sc_type_norm):
            return ProvisioningResult(
                sc_name=sc_name,
                sc_type=sc_type_norm,
                success=False,
                operator_required=True,
                failure_reason=(
                    f"service connection type '{sc_type}' is not OIDC "
                    "auto-provisionable; operator must supply a secret value"
                ),
            )

        try:
            org, repo = self._split_repo(repo_target)
        except ValueError as exc:
            return ProvisioningResult(
                sc_name=sc_name, sc_type=sc_type_norm, success=False,
                failure_reason=str(exc),
            )

        required = SECRET_TEMPLATES.get(sc_type_norm, [])
        values = dict(secret_values or {})
        missing = [name for name in required if not values.get(name)]
        if missing:
            return ProvisioningResult(
                sc_name=sc_name,
                sc_type=sc_type_norm,
                success=False,
                operator_required=True,
                failure_reason=(
                    "missing values for required secrets: " + ", ".join(missing)
                ),
            )

        try:
            self._configure_oidc_subject(gh_client, org, repo, sc_name)
            provisioned = self._create_secrets(gh_client, org, repo, required, values)
        except Exception as exc:  # noqa: BLE001 - surface any API failure to operator
            return ProvisioningResult(
                sc_name=sc_name,
                sc_type=sc_type_norm,
                success=False,
                operator_required=True,
                failure_reason=f"{type(exc).__name__}: {exc}",
            )

        return ProvisioningResult(
            sc_name=sc_name,
            sc_type=sc_type_norm,
            success=True,
            provisioned_secrets=provisioned,
        )

    # ── internals ───────────────────────────────────────────────────────────

    def _has_operator_override(self, profile_id: str | None, sc_name: str) -> bool:
        if not (self.settings and profile_id):
            return False
        try:
            resolutions = self.settings.get_operator_resolutions(profile_id)
        except Exception:
            return False
        # A resolution keyed by the SC name (any field containing it) counts.
        return any(sc_name in key for key in (resolutions or {}))

    @staticmethod
    def _split_repo(repo_target: str) -> tuple[str, str]:
        parts = (repo_target or "").split("/")
        if len(parts) != 2 or not all(parts):
            raise ValueError(f"invalid repo target '{repo_target}' (expected org/repo)")
        return parts[0], parts[1]

    @staticmethod
    def _configure_oidc_subject(gh_client: Any, org: str, repo: str, sc_name: str) -> None:
        """Configure the repo's OIDC subject claim for federated login.

        Uses the GitHub OIDC customization endpoint so workflows authenticate to
        the cloud provider without a stored client secret.
        """
        configure = getattr(gh_client, "set_oidc_subject_claim", None)
        if callable(configure):
            configure(org, repo)
            return
        # Fall back to the raw REST call when a typed helper is unavailable.
        raw_put = getattr(gh_client, "_put", None)
        if callable(raw_put):
            raw_put(
                f"/repos/{org}/{repo}/actions/oidc/customization/sub",
                {"use_default": False, "include_claim_keys": ["repo", "ref"]},
            )

    def _create_secrets(
        self,
        gh_client: Any,
        org: str,
        repo: str,
        names: list[str],
        values: dict[str, str],
    ) -> list[str]:
        public_key = gh_client.get_repo_public_key(org, repo)
        key_b64 = public_key["key"]
        key_id = public_key["key_id"]
        provisioned: list[str] = []
        for name in names:
            encrypted = self._encrypt_secret(key_b64, values[name])
            gh_client.create_secret(org, repo, name, encrypted, key_id)
            provisioned.append(name)
        return provisioned

    @staticmethod
    def _encrypt_secret(public_key_b64: str, value: str) -> str:
        """Encrypt ``value`` with the repo public key (libsodium sealed box)."""
        from base64 import b64decode

        from nacl import encoding, public

        pk = public.PublicKey(public_key_b64.encode("utf-8"), encoding.Base64Encoder())
        sealed = public.SealedBox(pk)
        encrypted = sealed.encrypt(value.encode("utf-8"))
        return b64encode(encrypted).decode("utf-8")
