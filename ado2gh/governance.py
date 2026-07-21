"""Cryptographic governance boundary for enterprise migration approvals.

The migration runtime deliberately receives only Ed25519 *public* keys.  It
can verify an external authorization but cannot mint one.  Policies are safe
to embed in an immutable PEV plan: they contain environment-variable
references and public-key fingerprints, never private signing material.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional


APPROVAL_ENVELOPE_SCHEMA = "ado2gh.signed-approval-envelope/v1"
APPROVAL_CLAIMS_SCHEMA = "ado2gh.signed-approval-claims/v1"
PLAN_EXECUTION_ACTION = "plan_execution"
ADO_CLEANUP_ACTION = "ado_cleanup"
PIPELINE_MANUAL_MAPPING_ACTION = "pipeline_manual_mapping"
PLAN_CREATION_ACTION = "plan_creation"
GOVERNED_ACTIONS = {
    PLAN_CREATION_ACTION,
    PLAN_EXECUTION_ACTION,
    ADO_CLEANUP_ACTION,
    PIPELINE_MANUAL_MAPPING_ACTION,
}
DEPLOYMENT_POLICY_DIGEST_ENV = "ADO2GH_GOVERNANCE_POLICY_SHA256"

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$")
_SAFE_ROLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_ENV = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_B64URL = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")
_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_ED25519_SIGNATURE = re.compile(r"^ed25519:([0-9a-f]{128})$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class GovernanceError(PermissionError):
    """A configured signed-approval policy was not satisfied."""


def canonical_json(value: Any) -> str:
    """Canonical JSON serialization used for signatures and resource IDs."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def governance_resource_digest(value: Any) -> str:
    """Return the canonical resource digest placed in signed claims."""
    return "sha256:" + hashlib.sha256(
        canonical_json(value).encode("utf-8")
    ).hexdigest()


def plan_execution_resource(plan: Any, run_id: str = "") -> dict[str, str]:
    """Canonical authorization resource for one immutable plan execution."""
    return {
        "schema": "ado2gh.governance-resource/plan-execution-v1",
        "plan_id": str(plan.plan_id),
        "config_digest": str(plan.config_digest),
        # Empty means a new run. A resume approval binds the existing run ID.
        "run_id": str(run_id or ""),
    }


def plan_creation_resource(plan: Any) -> dict[str, str]:
    """Canonical resource signed by the independent planning authority."""
    return {
        "schema": "ado2gh.governance-resource/plan-creation-v1",
        "plan_id": str(plan.plan_id),
        "config_digest": str(plan.config_digest),
        "source_org_url": str(plan.source_org_url),
        "target_org": str(plan.target_org),
    }


def cleanup_resource(request: Mapping[str, Any]) -> dict[str, Any]:
    """Canonical authorization resource for an exact destructive request."""
    return {
        "schema": "ado2gh.governance-resource/ado-cleanup-v1",
        "request": dict(request),
    }


def pipeline_manifest_resource(manifest: Any) -> dict[str, Any]:
    """Canonical authorization resource for all exact manual mappings."""
    records = [record.to_dict() for record in manifest.approvals]
    return {
        "schema": "ado2gh.governance-resource/pipeline-manifest-v1",
        "manifest_digest": str(manifest.manifest_digest),
        "approval_ids": sorted(str(record["approval_id"]) for record in records),
    }


def _safe_text(value: Any, field: str, *, maximum: int = 256) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    result = value.strip()
    if not result or len(result) > maximum or _CONTROL.search(result):
        raise ValueError(f"{field} must be a non-empty safe string")
    return result


def _safe_identifier(value: Any, field: str) -> str:
    result = _safe_text(value, field)
    if not _SAFE_ID.fullmatch(result):
        raise ValueError(f"{field} must be a safe identifier")
    return result


def _safe_role(value: Any, field: str) -> str:
    result = _safe_text(value, field, maximum=128)
    if not _SAFE_ROLE.fullmatch(result):
        raise ValueError(f"{field} must be a safe role identifier")
    return result


def _string_list(
    value: Any,
    field: str,
    *,
    role: bool = False,
    maximum: int = 1024,
) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{field} must be a list with at most {maximum} items")
    parser = _safe_role if role else _safe_identifier
    result = [parser(item, f"{field}[{index}]") for index, item in enumerate(value)]
    if result != sorted(set(result)):
        raise ValueError(f"{field} must be sorted and contain no duplicates")
    return result


def normalize_governance_settings(value: Any) -> dict[str, Any]:
    """Validate and normalize the plan-bound enterprise governance policy."""
    if value is None:
        return {"mode": "disabled"}
    if not isinstance(value, Mapping):
        raise ValueError("global.governance must be a mapping")
    raw = dict(value)
    allowed = {
        "mode", "audience", "planner_subject", "trusted_keys",
        "planner_key_id",
        "required_roles", "require_planner_executor_separation",
        "revoked_nonces", "max_age_seconds", "max_ttl_seconds",
        "clock_skew_seconds", "require_signed_pipeline_approvals",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(
            "global.governance has unknown key(s): " + ", ".join(sorted(unknown))
        )
    mode = str(raw.get("mode", "disabled")).strip().casefold()
    if mode not in {"disabled", "strict"}:
        raise ValueError("global.governance.mode must be 'disabled' or 'strict'")
    if mode == "disabled":
        if set(raw) - {"mode"}:
            raise ValueError(
                "disabled governance cannot contain dormant trust or approval policy"
            )
        return {"mode": "disabled"}

    audience = _safe_identifier(raw.get("audience"), "global.governance.audience")
    planner_subject = _safe_identifier(
        raw.get("planner_subject"), "global.governance.planner_subject"
    )
    planner_key_id = _safe_identifier(
        raw.get("planner_key_id"), "global.governance.planner_key_id"
    )
    separation = raw.get("require_planner_executor_separation", True)
    pipeline_required = raw.get("require_signed_pipeline_approvals", True)
    if not isinstance(separation, bool) or not isinstance(pipeline_required, bool):
        raise ValueError("governance separation and pipeline flags must be booleans")
    if not pipeline_required:
        raise ValueError(
            "strict governance requires signed pipeline manual approvals"
        )

    raw_keys = raw.get("trusted_keys")
    if not isinstance(raw_keys, Mapping) or not raw_keys or len(raw_keys) > 64:
        raise ValueError("global.governance.trusted_keys must contain 1 to 64 keys")
    trusted_keys: dict[str, dict[str, Any]] = {}
    seen_envs: set[str] = set()
    for raw_key_id, raw_key in raw_keys.items():
        key_id = _safe_identifier(raw_key_id, "governance trusted key id")
        if not isinstance(raw_key, Mapping):
            raise ValueError(f"governance trusted key {key_id!r} must be a mapping")
        entry = dict(raw_key)
        unknown_entry = set(entry) - {
            "public_key_env", "public_key_sha256", "allowed_subjects",
            "allowed_roles",
        }
        if unknown_entry:
            raise ValueError(
                f"governance trusted key {key_id!r} has unknown key(s): "
                + ", ".join(sorted(unknown_entry))
            )
        env_name = entry.get("public_key_env")
        if not isinstance(env_name, str) or not _SAFE_ENV.fullmatch(env_name):
            raise ValueError(
                f"governance trusted key {key_id!r} public_key_env must be an "
                "uppercase environment-variable reference"
            )
        if env_name in seen_envs:
            raise ValueError("governance public key environment references must be unique")
        seen_envs.add(env_name)
        fingerprint = str(entry.get("public_key_sha256", "")).strip()
        if not _SHA256.fullmatch(fingerprint):
            raise ValueError(
                f"governance trusted key {key_id!r} public_key_sha256 must be "
                "sha256:<64 lowercase hex>"
            )
        subjects = _string_list(
            entry.get("allowed_subjects"),
            f"global.governance.trusted_keys.{key_id}.allowed_subjects",
        )
        roles = _string_list(
            entry.get("allowed_roles"),
            f"global.governance.trusted_keys.{key_id}.allowed_roles",
            role=True,
        )
        if len(subjects) != 1 or not roles:
            raise ValueError(
                f"governance trusted key {key_id!r} must bind exactly one subject "
                "and at least one role"
            )
        trusted_keys[key_id] = {
            "public_key_env": env_name,
            "public_key_sha256": fingerprint,
            "allowed_subjects": subjects,
            "allowed_roles": roles,
        }

    raw_roles = raw.get("required_roles")
    if not isinstance(raw_roles, Mapping) or set(raw_roles) != GOVERNED_ACTIONS:
        raise ValueError(
            "global.governance.required_roles must contain exactly: "
            + ", ".join(sorted(GOVERNED_ACTIONS))
        )
    required_roles = {
        action: _safe_role(raw_roles[action], f"governance required role {action}")
        for action in sorted(GOVERNED_ACTIONS)
    }
    if len(set(required_roles.values())) != len(required_roles):
        raise ValueError(
            "governance required roles must be distinct for every governed action"
        )
    available_roles = {
        role for key in trusted_keys.values() for role in key["allowed_roles"]
    }
    missing_roles = set(required_roles.values()) - available_roles
    if missing_roles:
        raise ValueError(
            "governance required roles are not granted to any trusted key: "
            + ", ".join(sorted(missing_roles))
        )
    if planner_key_id not in trusted_keys:
        raise ValueError("governance planner_key_id is not a trusted key")
    planner_key = trusted_keys[planner_key_id]
    if planner_key["allowed_subjects"] != [planner_subject]:
        raise ValueError(
            "governance planner_subject must be the sole subject of planner_key_id"
        )
    if required_roles[PLAN_CREATION_ACTION] not in planner_key["allowed_roles"]:
        raise ValueError("governance planner_key_id lacks the plan_creation role")
    fingerprints = [item["public_key_sha256"] for item in trusted_keys.values()]
    if len(fingerprints) != len(set(fingerprints)):
        raise ValueError(
            "governance trusted key fingerprints must be unique across key IDs"
        )
    # A private key that can assert two required role domains defeats
    # separation of duties even if it signs different subject strings.
    action_roles = set(required_roles.values())
    for key_id, entry in trusted_keys.items():
        domains = action_roles.intersection(entry["allowed_roles"])
        if len(domains) > 1:
            raise ValueError(
                f"governance trusted key {key_id!r} spans multiple required "
                "separation-of-duties role domains"
            )

    revoked = _string_list(
        raw.get("revoked_nonces", []),
        "global.governance.revoked_nonces",
        maximum=100_000,
    )

    def bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
        item = raw.get(name, default)
        if isinstance(item, bool) or not isinstance(item, int) \
                or item < minimum or item > maximum:
            raise ValueError(
                f"global.governance.{name} must be between {minimum} and {maximum}"
            )
        return item

    max_age = bounded_int("max_age_seconds", 300, 1, 86_400)
    max_ttl = bounded_int("max_ttl_seconds", 900, 1, 86_400)
    skew = bounded_int("clock_skew_seconds", 30, 0, 300)
    if max_age > max_ttl:
        raise ValueError("governance max_age_seconds cannot exceed max_ttl_seconds")
    return {
        "mode": "strict",
        "audience": audience,
        "planner_subject": planner_subject,
        "planner_key_id": planner_key_id,
        "trusted_keys": dict(sorted(trusted_keys.items())),
        "required_roles": required_roles,
        "require_planner_executor_separation": separation,
        "revoked_nonces": revoked,
        "max_age_seconds": max_age,
        "max_ttl_seconds": max_ttl,
        "clock_skew_seconds": skew,
        "require_signed_pipeline_approvals": pipeline_required,
    }


def governance_policy_digest(policy: Any) -> str:
    """Digest of the complete normalized deployment trust policy."""
    normalized = normalize_governance_settings(policy)
    return governance_resource_digest({
        "schema": "ado2gh.governance-policy/v1",
        "policy": normalized,
    })


def assert_deployment_governance_policy(policy: Any) -> None:
    """Require an operator-owned, out-of-band pin for strict trust roots."""
    normalized = normalize_governance_settings(policy)
    if normalized["mode"] != "strict":
        return
    pinned = os.environ.get(DEPLOYMENT_POLICY_DIGEST_ENV, "").strip()
    if not _SHA256.fullmatch(pinned):
        raise GovernanceError(
            f"strict governance requires deployment-owned "
            f"{DEPLOYMENT_POLICY_DIGEST_ENV}=sha256:<digest>"
        )
    expected = governance_policy_digest(normalized)
    if pinned != expected:
        raise GovernanceError(
            "migration plan governance policy is not trusted by this deployment"
        )


def governance_is_strict(policy: Any) -> bool:
    return isinstance(policy, Mapping) and policy.get("mode") == "strict"


def _parse_utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not _UTC_TIMESTAMP.fullmatch(value):
        raise GovernanceError(f"signed approval {field} must be canonical UTC YYYY-MM-DDTHH:MM:SSZ")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise GovernanceError(f"signed approval {field} is not a valid timestamp") from exc


def _decode_base64(value: str, field: str) -> bytes:
    if not value or not _B64URL.fullmatch(value):
        raise GovernanceError(f"{field} must be base64url without whitespace")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:
        raise GovernanceError(f"{field} is not valid base64url") from exc


def _decode_signature(value: Any) -> bytes:
    text = str(value or "")
    hexadecimal = _ED25519_SIGNATURE.fullmatch(text)
    if hexadecimal:
        return bytes.fromhex(hexadecimal.group(1))
    return _decode_base64(text, "signed approval signature")


def _load_ed25519_public_key(key_policy: Mapping[str, Any]) -> tuple[Any, str]:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise GovernanceError(
            "strict signed approvals require the optional 'cryptography' package"
        ) from exc

    env_name = str(key_policy["public_key_env"])
    encoded = os.environ.get(env_name, "").strip()
    if not encoded:
        raise GovernanceError(
            f"trusted Ed25519 public key environment variable {env_name!r} is unset"
        )
    try:
        if encoded.startswith("-----BEGIN PUBLIC KEY-----"):
            key = serialization.load_pem_public_key(encoded.encode("ascii"))
        else:
            raw = _decode_base64(encoded, f"environment variable {env_name}")
            if len(raw) == 32:
                key = Ed25519PublicKey.from_public_bytes(raw)
            else:
                key = serialization.load_der_public_key(raw)
    except GovernanceError:
        raise
    except Exception as exc:
        raise GovernanceError(
            f"trusted public key {env_name!r} is not a valid Ed25519 key"
        ) from exc
    if not isinstance(key, Ed25519PublicKey):
        raise GovernanceError(f"trusted public key {env_name!r} is not Ed25519")
    raw_key = key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    fingerprint = "sha256:" + hashlib.sha256(raw_key).hexdigest()
    if fingerprint != key_policy["public_key_sha256"]:
        raise GovernanceError("trusted Ed25519 public key fingerprint does not match policy")
    return key, fingerprint


@dataclass(frozen=True)
class VerifiedApproval:
    key_id: str
    key_fingerprint: str
    claims: dict[str, Any]
    envelope: dict[str, Any]
    verified_at: str

    def to_evidence(self) -> dict[str, Any]:
        return {
            "schema": "ado2gh.verified-signed-approval/v1",
            "key_id": self.key_id,
            "key_fingerprint": self.key_fingerprint,
            "claims": dict(self.claims),
            "envelope": json.loads(canonical_json(self.envelope)),
            "verified_at": self.verified_at,
        }


class SignedApprovalVerifier:
    """Verify exact Ed25519 approval envelopes under a plan-bound policy."""

    def __init__(self, policy: Any):
        self.policy = normalize_governance_settings(policy)

    @property
    def strict(self) -> bool:
        return self.policy["mode"] == "strict"

    def verify(
        self,
        envelope: Any,
        *,
        action: str,
        resource: Any,
        plan_id: str = "",
        run_id: str = "",
        expected_ticket: str = "",
        now: Optional[datetime] = None,
    ) -> Optional[VerifiedApproval]:
        if action not in GOVERNED_ACTIONS:
            raise ValueError(f"unsupported governed action {action!r}")
        if not self.strict:
            return None
        assert_deployment_governance_policy(self.policy)
        if not isinstance(envelope, Mapping):
            raise GovernanceError(f"strict governance requires a signed {action} envelope")
        raw = dict(envelope)
        if set(raw) != {"schema", "key_id", "claims", "signature"}:
            raise GovernanceError("signed approval envelope fields are not exact")
        if raw.get("schema") != APPROVAL_ENVELOPE_SCHEMA:
            raise GovernanceError("signed approval envelope schema is unsupported")
        key_id = str(raw.get("key_id", ""))
        key_policy = self.policy["trusted_keys"].get(key_id)
        if key_policy is None:
            raise GovernanceError("signed approval key_id is not trusted")
        claims_raw = raw.get("claims")
        if not isinstance(claims_raw, Mapping):
            raise GovernanceError("signed approval claims must be an object")
        claims = dict(claims_raw)
        required_claims = {
            "schema", "audience", "action", "resource_digest", "plan_id",
            "run_id", "subject", "roles", "issued_at", "expires_at",
            "nonce", "ticket",
        }
        if set(claims) != required_claims:
            raise GovernanceError("signed approval claim fields are not exact")
        if claims.get("schema") != APPROVAL_CLAIMS_SCHEMA:
            raise GovernanceError("signed approval claims schema is unsupported")
        if claims.get("audience") != self.policy["audience"]:
            raise GovernanceError("signed approval audience does not match policy")
        if claims.get("action") != action:
            raise GovernanceError("signed approval action does not match request")
        expected_digest = governance_resource_digest(resource)
        if claims.get("resource_digest") != expected_digest:
            raise GovernanceError("signed approval resource digest does not match request")
        if claims.get("plan_id") != str(plan_id or ""):
            raise GovernanceError("signed approval plan_id does not match request")
        if claims.get("run_id") != str(run_id or ""):
            raise GovernanceError("signed approval run_id does not match request")
        subject = _safe_identifier(claims.get("subject"), "signed approval subject")
        if subject not in key_policy["allowed_subjects"]:
            raise GovernanceError("signed approval subject is not authorized for key")
        try:
            roles = _string_list(claims.get("roles"), "signed approval roles", role=True)
        except ValueError as exc:
            raise GovernanceError(str(exc)) from exc
        if not set(roles).issubset(key_policy["allowed_roles"]):
            raise GovernanceError("signed approval asserts a role not granted to its key")
        required_role = self.policy["required_roles"][action]
        if required_role not in roles:
            raise GovernanceError(f"signed approval lacks required role {required_role!r}")
        if action == PLAN_CREATION_ACTION and key_id != self.policy["planner_key_id"]:
            raise GovernanceError("plan creation approval did not use the pinned planner key")
        if (
            self.policy["require_planner_executor_separation"]
            and action == PLAN_EXECUTION_ACTION
            and subject == self.policy["planner_subject"]
        ):
            raise GovernanceError("planner and plan execution approver must be different subjects")
        nonce = _safe_identifier(claims.get("nonce"), "signed approval nonce")
        if nonce in self.policy["revoked_nonces"]:
            raise GovernanceError("signed approval nonce has been revoked")
        ticket = _safe_identifier(claims.get("ticket"), "signed approval ticket")
        if expected_ticket and ticket != expected_ticket:
            raise GovernanceError("signed approval ticket does not match requested ticket")

        issued = _parse_utc(claims.get("issued_at"), "issued_at")
        expires = _parse_utc(claims.get("expires_at"), "expires_at")
        observed = now or datetime.now(timezone.utc)
        if observed.tzinfo is None:
            raise ValueError("governance verification time must be timezone-aware")
        observed = observed.astimezone(timezone.utc)
        skew = self.policy["clock_skew_seconds"]
        age = (observed - issued).total_seconds()
        ttl = (expires - issued).total_seconds()
        if age < -skew:
            raise GovernanceError("signed approval is not yet valid")
        if age > self.policy["max_age_seconds"] + skew:
            raise GovernanceError("signed approval is older than policy permits")
        if ttl <= 0 or ttl > self.policy["max_ttl_seconds"]:
            raise GovernanceError("signed approval lifetime exceeds policy")
        if observed.timestamp() > expires.timestamp() + skew:
            raise GovernanceError("signed approval has expired")

        signature = _decode_signature(raw.get("signature"))
        if len(signature) != 64:
            raise GovernanceError("Ed25519 approval signature must be 64 bytes")
        public_key, fingerprint = _load_ed25519_public_key(key_policy)
        signed = canonical_json({
            "schema": raw["schema"],
            "key_id": key_id,
            "claims": claims,
        }).encode("utf-8")
        try:
            public_key.verify(signature, signed)
        except Exception as exc:
            raise GovernanceError("signed approval signature verification failed") from exc
        stored_envelope = json.loads(canonical_json(raw))
        # Persist a type-labelled hexadecimal signature. Random base64url can
        # coincidentally resemble an API-token prefix and be altered by the
        # generic audit redactor; hex is unambiguous and remains verifiable.
        stored_envelope["signature"] = "ed25519:" + signature.hex()
        return VerifiedApproval(
            key_id=key_id,
            key_fingerprint=fingerprint,
            claims=claims,
            envelope=stored_envelope,
            verified_at=observed.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        )


def load_approval_envelope(path: str) -> dict[str, Any]:
    """Safely load one small JSON signed-approval envelope from disk."""
    candidate = Path(path).expanduser()
    try:
        if candidate.is_symlink() or not candidate.is_file():
            raise ValueError("approval envelope path must be a regular file")
        if candidate.stat().st_size > 256 * 1024:
            raise ValueError("approval envelope exceeds 256 KiB")
        value = json.loads(candidate.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read approval envelope safely: {candidate}") from exc
    if not isinstance(value, dict):
        raise ValueError("approval envelope root must be an object")
    return value


def authorize_plan_creation(
    plan: Any,
    policy: Any,
    envelope: Any,
    db: Any,
) -> VerifiedApproval:
    """Verify and durably record the independent planner's signature."""
    verifier = SignedApprovalVerifier(policy)
    resource = plan_creation_resource(plan)
    if not isinstance(envelope, Mapping):
        raise GovernanceError(
            "strict plan creation requires a signed planner envelope for "
            f"plan_id={plan.plan_id} resource_digest="
            f"{governance_resource_digest(resource)}"
        )
    verified = verifier.verify(
        envelope,
        action=PLAN_CREATION_ACTION,
        resource=resource,
        plan_id=plan.plan_id,
        run_id="",
    )
    if verified is None:  # defensive: this API is only meaningful in strict mode
        raise GovernanceError("plan creation governance is not strict")
    if db is None or not callable(
        getattr(db, "claim_pev_governance_approval", None)
    ):
        raise GovernanceError(
            "strict plan creation requires durable StateDB approval evidence"
        )
    db.claim_pev_governance_approval(
        verified.to_evidence(),
        action=PLAN_CREATION_ACTION,
        resource_digest=governance_resource_digest(resource),
        plan_id=plan.plan_id,
        run_id="",
    )
    return verified


def assert_plan_creation_approval(plan: Any, policy: Any, db: Any) -> VerifiedApproval:
    """Re-authenticate the planner signature before live plan execution."""
    verifier = SignedApprovalVerifier(policy)
    if not verifier.strict:
        raise GovernanceError("plan creation approval assertion requires strict mode")
    rows = db.get_pev_governance_approvals(
        plan_id=plan.plan_id,
        action=PLAN_CREATION_ACTION,
        run_id="",
    )
    if not rows:
        raise GovernanceError(
            "strict plan has no cryptographically recorded planner approval"
        )
    resource = plan_creation_resource(plan)
    failures: list[Exception] = []
    for row in reversed(rows):
        evidence = row.get("evidence")
        if not isinstance(evidence, dict):
            continue
        try:
            verified_at = _parse_utc(evidence.get("verified_at"), "verified_at")
            verified = verifier.verify(
                evidence.get("envelope"),
                action=PLAN_CREATION_ACTION,
                resource=resource,
                plan_id=plan.plan_id,
                run_id="",
                # Historical approval remains valid after its signing window;
                # this proves it was accepted while the signed claims were live.
                now=verified_at,
            )
            if verified is None:
                raise GovernanceError("planner approval unexpectedly disabled")
            observed = verified.to_evidence()
            for field in (
                "schema", "key_id", "key_fingerprint", "claims", "envelope"
            ):
                if evidence.get(field) != observed.get(field):
                    raise GovernanceError("stored planner approval evidence was altered")
            db.assert_pev_governance_approval(
                evidence,
                action=PLAN_CREATION_ACTION,
                resource_digest=governance_resource_digest(resource),
                plan_id=plan.plan_id,
                run_id="",
            )
            return verified
        except Exception as exc:  # try an independently renewed approval if present
            failures.append(exc)
    raise GovernanceError(
        "strict plan has no valid cryptographically recorded planner approval"
    ) from (failures[-1] if failures else None)
