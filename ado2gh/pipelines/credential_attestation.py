"""Authenticated, short-lived credential canaries for pipeline approvals.

The approval manifest is operator-authored input, so a statement such as
``type: credential-canary`` is not evidence by itself.  This module defines a
small, exact JSON claim schema and authenticates it with an enterprise-owned
HMAC key.  Configuration contains only key identifiers and environment
variable *names*; key material is read into process memory and is never
serialized into plans, evidence, logs, or state.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional


ATTESTATION_SCHEMA_VERSION = 1
_SIGNATURE_RE = re.compile(r"^hmac-sha256:[0-9a-f]{64}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_PLAN_ID_RE = re.compile(r"^plan-[0-9a-f]{24}$")
_AMBIGUITY_ID_RE = re.compile(r"^amb-[0-9a-f]{20}$")
_COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

_BASE_CLAIMS = {
    "schema_version",
    "nonce",
    "issuer",
    "audience",
    "canary_run_id",
    "canary_workflow_sha",
    "outcome",
    "issued_at",
    "expires_at",
    "plan_id",
    "source_fingerprint",
    "ambiguity_id",
    "capability",
    "resource",
    "target_repository_id",
    "secret_name",
    "secret_updated_at",
}
_CHECKOUT_CLAIMS = {
    "external_repository_id",
    "external_ref",
    "external_commit_sha",
}
_CANARY_FIELDS = {"type", "key_id", "claims", "signature"}


class CredentialAttestationError(ValueError):
    """Raised when a credential canary is malformed, stale, or untrusted."""


def canonical_claim_bytes(claims: Mapping[str, Any]) -> bytes:
    """Return the one canonical representation covered by the HMAC."""
    try:
        return json.dumps(
            dict(claims),
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CredentialAttestationError(
            "credential canary claims must contain finite JSON values"
        ) from exc


def _safe_text(value: Any, field: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CredentialAttestationError(f"{field} must be a non-empty string")
    text = value.strip()
    if len(text) > maximum or _CONTROL_RE.search(text):
        raise CredentialAttestationError(
            f"{field} is too long or contains control characters"
        )
    return text


def _utc_timestamp(value: Any, field: str) -> tuple[str, datetime]:
    """Require a canonical UTC RFC3339 timestamp and return its instant."""
    text = _safe_text(value, field, maximum=64)
    if not text.endswith("Z"):
        raise CredentialAttestationError(f"{field} must be an RFC3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise CredentialAttestationError(
            f"{field} must be an RFC3339 UTC timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None \
            or parsed.utcoffset().total_seconds() != 0:
        raise CredentialAttestationError(f"{field} must be in UTC")
    # Reject alternate spellings which represent the same instant.  This
    # removes signature ambiguity between issuers and verifiers.
    canonical = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if text != canonical:
        raise CredentialAttestationError(f"{field} is not canonically encoded")
    return text, parsed.astimezone(timezone.utc)


def normalize_canary(value: Any) -> dict[str, Any]:
    """Validate the exact envelope/claim grammar without trusting it yet."""
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) for key in value
    ):
        raise CredentialAttestationError("credential canary must be an object")
    raw = dict(value)
    if set(raw) != _CANARY_FIELDS:
        raise CredentialAttestationError(
            "credential canary envelope has missing or unknown fields"
        )
    if raw.get("type") != "credential-canary":
        raise CredentialAttestationError(
            "credential canary type must be exactly 'credential-canary'"
        )
    key_id = _safe_text(raw.get("key_id"), "credential canary key_id", maximum=128)
    if not _KEY_ID_RE.fullmatch(key_id):
        raise CredentialAttestationError("credential canary key_id is invalid")
    signature = _safe_text(
        raw.get("signature"), "credential canary signature", maximum=76
    ).lower()
    if not _SIGNATURE_RE.fullmatch(signature):
        raise CredentialAttestationError(
            "credential canary signature must be hmac-sha256:<64 lowercase hex>"
        )
    claims_value = raw.get("claims")
    if not isinstance(claims_value, Mapping) or not all(
        isinstance(key, str) for key in claims_value
    ):
        raise CredentialAttestationError("credential canary claims must be an object")
    claims = dict(claims_value)
    capability = _safe_text(
        claims.get("capability"), "credential canary capability", maximum=128
    )
    if not _CAPABILITY_RE.fullmatch(capability):
        raise CredentialAttestationError("credential canary capability is invalid")
    expected_fields = set(_BASE_CLAIMS)
    if capability == "external_repository_checkout":
        expected_fields.update(_CHECKOUT_CLAIMS)
    if set(claims) != expected_fields:
        raise CredentialAttestationError(
            "credential canary claims have missing or capability-inapplicable fields"
        )
    if claims.get("schema_version") != ATTESTATION_SCHEMA_VERSION:
        raise CredentialAttestationError(
            f"credential canary schema_version must be {ATTESTATION_SCHEMA_VERSION}"
        )
    nonce = _safe_text(claims.get("nonce"), "credential canary nonce", maximum=128)
    if not _NONCE_RE.fullmatch(nonce):
        raise CredentialAttestationError("credential canary nonce is invalid")
    issuer = _safe_text(
        claims.get("issuer"), "credential canary issuer", maximum=128
    )
    if not _KEY_ID_RE.fullmatch(issuer):
        raise CredentialAttestationError("credential canary issuer is invalid")
    audience = _safe_text(
        claims.get("audience"), "credential canary audience", maximum=128
    )
    if audience != "ado2gh-pipeline-credential-canary":
        raise CredentialAttestationError("credential canary audience is invalid")
    canary_run_id = _safe_text(
        claims.get("canary_run_id"),
        "credential canary canary_run_id",
        maximum=128,
    )
    if not re.fullmatch(r"canary-run-[A-Za-z0-9_-]{16,117}", canary_run_id):
        raise CredentialAttestationError("credential canary canary_run_id is invalid")
    canary_workflow_sha = _safe_text(
        claims.get("canary_workflow_sha"),
        "credential canary canary_workflow_sha",
        maximum=40,
    ).lower()
    if not _COMMIT_SHA_RE.fullmatch(canary_workflow_sha):
        raise CredentialAttestationError(
            "credential canary canary_workflow_sha must be a 40-character SHA"
        )
    outcome = _safe_text(
        claims.get("outcome"), "credential canary outcome", maximum=16
    )
    if outcome != "passed":
        raise CredentialAttestationError(
            "credential canary outcome must be exactly 'passed'"
        )
    issued_at, _issued = _utc_timestamp(
        claims.get("issued_at"), "credential canary issued_at"
    )
    expires_at, _expires = _utc_timestamp(
        claims.get("expires_at"), "credential canary expires_at"
    )
    plan_id = _safe_text(claims.get("plan_id"), "credential canary plan_id", maximum=80)
    if not _PLAN_ID_RE.fullmatch(plan_id):
        raise CredentialAttestationError("credential canary plan_id is invalid")
    source_fingerprint = _safe_text(
        claims.get("source_fingerprint"),
        "credential canary source_fingerprint",
        maximum=71,
    ).lower()
    if not _SHA256_RE.fullmatch(source_fingerprint):
        raise CredentialAttestationError(
            "credential canary source_fingerprint is invalid"
        )
    ambiguity_id = _safe_text(
        claims.get("ambiguity_id"), "credential canary ambiguity_id", maximum=32
    )
    if not _AMBIGUITY_ID_RE.fullmatch(ambiguity_id):
        raise CredentialAttestationError("credential canary ambiguity_id is invalid")
    resource = _safe_text(
        claims.get("resource"), "credential canary resource", maximum=512
    )
    target_repository_id = _safe_text(
        claims.get("target_repository_id"),
        "credential canary target_repository_id",
        maximum=256,
    )
    secret_name = _safe_text(
        claims.get("secret_name"), "credential canary secret_name", maximum=256
    )
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,255}", secret_name):
        raise CredentialAttestationError("credential canary secret_name is invalid")
    secret_updated_at, _secret_updated = _utc_timestamp(
        claims.get("secret_updated_at"), "credential canary secret_updated_at"
    )
    normalized_claims: dict[str, Any] = {
        "schema_version": ATTESTATION_SCHEMA_VERSION,
        "nonce": nonce,
        "issuer": issuer,
        "audience": audience,
        "canary_run_id": canary_run_id,
        "canary_workflow_sha": canary_workflow_sha,
        "outcome": outcome,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "plan_id": plan_id,
        "source_fingerprint": source_fingerprint,
        "ambiguity_id": ambiguity_id,
        "capability": capability,
        "resource": resource,
        "target_repository_id": target_repository_id,
        "secret_name": secret_name,
        "secret_updated_at": secret_updated_at,
    }
    if capability == "external_repository_checkout":
        external_repository_id = _safe_text(
            claims.get("external_repository_id"),
            "credential canary external_repository_id",
            maximum=256,
        )
        external_ref = _safe_text(
            claims.get("external_ref"),
            "credential canary external_ref",
            maximum=255,
        )
        external_commit_sha = _safe_text(
            claims.get("external_commit_sha"),
            "credential canary external_commit_sha",
            maximum=40,
        ).lower()
        if not _COMMIT_SHA_RE.fullmatch(external_commit_sha):
            raise CredentialAttestationError(
                "credential canary external_commit_sha must be a 40-character SHA"
            )
        normalized_claims.update({
            "external_repository_id": external_repository_id,
            "external_ref": external_ref,
            "external_commit_sha": external_commit_sha,
        })
    # JSON round trip guarantees a detached, immutable-by-convention copy.
    normalized_claims = json.loads(canonical_claim_bytes(normalized_claims))
    return {
        "type": "credential-canary",
        "key_id": key_id,
        "claims": normalized_claims,
        "signature": signature,
    }


class CredentialAttestationVerifier:
    """Authenticate canaries using environment-injected enterprise keys."""

    def __init__(
        self,
        keys: Mapping[str, bytes],
        *,
        trusted_workflow_sha_by_key_id: Mapping[str, str],
        max_age_seconds: int = 300,
        max_ttl_seconds: int = 900,
        clock_skew_seconds: int = 30,
        now: Optional[Callable[[], datetime]] = None,
    ) -> None:
        if not keys:
            raise CredentialAttestationError(
                "at least one credential attestation key must be configured"
            )
        normalized: dict[str, bytes] = {}
        for key_id, key in keys.items():
            if not isinstance(key_id, str) or not _KEY_ID_RE.fullmatch(key_id):
                raise CredentialAttestationError(
                    "credential attestation key id is invalid"
                )
            if not isinstance(key, bytes) or len(key) < 32:
                raise CredentialAttestationError(
                    "credential attestation HMAC keys must contain at least 32 bytes"
                )
            normalized[key_id] = bytes(key)
        for value, field, minimum, maximum in (
            (max_age_seconds, "max_age_seconds", 1, 86_400),
            (max_ttl_seconds, "max_ttl_seconds", 1, 86_400),
            (clock_skew_seconds, "clock_skew_seconds", 0, 300),
        ):
            if isinstance(value, bool) or not isinstance(value, int) \
                    or not minimum <= value <= maximum:
                raise CredentialAttestationError(
                    f"credential attestation {field} is out of range"
                )
        if max_age_seconds > max_ttl_seconds:
            raise CredentialAttestationError(
                "credential attestation max_age_seconds cannot exceed max_ttl_seconds"
            )
        self._keys = normalized
        if not isinstance(trusted_workflow_sha_by_key_id, Mapping) or set(
            trusted_workflow_sha_by_key_id
        ) != set(normalized):
            raise CredentialAttestationError(
                "every credential attestation key must have one trusted workflow SHA"
            )
        trusted_workflows: dict[str, str] = {}
        for key_id, value in trusted_workflow_sha_by_key_id.items():
            if not isinstance(value, str) or not _COMMIT_SHA_RE.fullmatch(
                value.lower()
            ):
                raise CredentialAttestationError(
                    "credential attestation trusted workflow values must be commit SHAs"
                )
            trusted_workflows[str(key_id)] = value.lower()
        self._trusted_workflows = trusted_workflows
        self.max_age_seconds = max_age_seconds
        self.max_ttl_seconds = max_ttl_seconds
        self.clock_skew_seconds = clock_skew_seconds
        self._now = now or (lambda: datetime.now(timezone.utc))

    @classmethod
    def from_config(
        cls,
        value: Any,
        *,
        environ: Optional[Mapping[str, str]] = None,
        now: Optional[Callable[[], datetime]] = None,
    ) -> "CredentialAttestationVerifier":
        if not isinstance(value, Mapping):
            raise CredentialAttestationError(
                "credential_attestation configuration must be an object"
            )
        raw = dict(value)
        allowed = {
            "key_env_by_id", "trusted_workflow_sha_by_key_id",
            "max_age_seconds", "max_ttl_seconds", "clock_skew_seconds",
        }
        if set(raw) - allowed:
            raise CredentialAttestationError(
                "credential_attestation configuration has unknown fields"
            )
        key_refs = raw.get("key_env_by_id")
        if not isinstance(key_refs, Mapping) or not key_refs:
            raise CredentialAttestationError(
                "credential_attestation.key_env_by_id must be a non-empty mapping"
            )
        source = environ if environ is not None else os.environ
        keys: dict[str, bytes] = {}
        seen_envs: set[str] = set()
        for key_id, env_name in key_refs.items():
            if not isinstance(key_id, str) or not _KEY_ID_RE.fullmatch(key_id):
                raise CredentialAttestationError(
                    "credential attestation key id is invalid"
                )
            if not isinstance(env_name, str) or not _ENV_NAME_RE.fullmatch(env_name):
                raise CredentialAttestationError(
                    "credential attestation keys must reference environment variable names"
                )
            if env_name in seen_envs:
                raise CredentialAttestationError(
                    "credential attestation environment key references must be unique"
                )
            seen_envs.add(env_name)
            material = source.get(env_name)
            if not isinstance(material, str) or not material:
                raise CredentialAttestationError(
                    f"credential attestation key environment variable is unset: {env_name}"
                )
            keys[key_id] = material.encode("utf-8")
        trusted_workflows = raw.get("trusted_workflow_sha_by_key_id")
        if not isinstance(trusted_workflows, Mapping):
            raise CredentialAttestationError(
                "credential_attestation.trusted_workflow_sha_by_key_id must be a mapping"
            )
        return cls(
            keys,
            trusted_workflow_sha_by_key_id=trusted_workflows,
            max_age_seconds=raw.get("max_age_seconds", 300),
            max_ttl_seconds=raw.get("max_ttl_seconds", 900),
            clock_skew_seconds=raw.get("clock_skew_seconds", 30),
            now=now,
        )

    def verify(
        self,
        value: Any,
        *,
        expected: Optional[Mapping[str, str]] = None,
    ) -> dict[str, Any]:
        """Authenticate a canary, enforce freshness, then exact bindings."""
        canary = normalize_canary(value)
        key = self._keys.get(canary["key_id"])
        if key is None:
            raise CredentialAttestationError(
                "credential canary key_id is not trusted"
            )
        digest = hmac.new(
            key, canonical_claim_bytes(canary["claims"]), hashlib.sha256
        ).hexdigest()
        supplied = canary["signature"].removeprefix("hmac-sha256:")
        if not hmac.compare_digest(digest, supplied):
            raise CredentialAttestationError(
                "credential canary signature is invalid"
            )
        if not hmac.compare_digest(
            canary["claims"]["issuer"].encode(), canary["key_id"].encode()
        ):
            raise CredentialAttestationError(
                "credential canary issuer does not match its trusted key id"
            )
        expected_workflow = self._trusted_workflows[canary["key_id"]]
        if not hmac.compare_digest(
            canary["claims"]["canary_workflow_sha"].encode(),
            expected_workflow.encode(),
        ):
            raise CredentialAttestationError(
                "credential canary was not issued by an approved workflow revision"
            )

        _issued_text, issued = _utc_timestamp(
            canary["claims"]["issued_at"], "credential canary issued_at"
        )
        _expires_text, expires = _utc_timestamp(
            canary["claims"]["expires_at"], "credential canary expires_at"
        )
        now = self._now()
        if not isinstance(now, datetime) or now.tzinfo is None \
                or now.utcoffset() is None:
            raise CredentialAttestationError(
                "credential attestation verifier clock must be timezone-aware"
            )
        now = now.astimezone(timezone.utc)
        if expires <= issued:
            raise CredentialAttestationError(
                "credential canary expiration must follow issuance"
            )
        if (expires - issued).total_seconds() > self.max_ttl_seconds:
            raise CredentialAttestationError(
                "credential canary lifetime exceeds the configured maximum"
            )
        if issued.timestamp() > now.timestamp() + self.clock_skew_seconds:
            raise CredentialAttestationError(
                "credential canary was issued in the future"
            )
        if now.timestamp() - issued.timestamp() > self.max_age_seconds:
            raise CredentialAttestationError("credential canary is stale")
        if expires.timestamp() <= now.timestamp() - self.clock_skew_seconds:
            raise CredentialAttestationError("credential canary has expired")

        claims = canary["claims"]
        for field, expected_value in dict(expected or {}).items():
            if field not in claims or not isinstance(expected_value, str):
                raise CredentialAttestationError(
                    "credential canary verifier received an invalid expected binding"
                )
            observed = str(claims[field])
            if not hmac.compare_digest(observed.encode(), expected_value.encode()):
                raise CredentialAttestationError(
                    f"credential canary {field} binding does not match"
                )
        return canary
