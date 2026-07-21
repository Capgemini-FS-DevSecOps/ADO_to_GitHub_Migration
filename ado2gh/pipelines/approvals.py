"""Content-addressed operator approvals for manual pipeline ambiguities.

An approval is not a boolean escape hatch.  It is an immutable, schema-checked
record bound to one pipeline source fingerprint and one planner ambiguity.  The
record also captures who approved the mapping, the governing change ticket,
the exact target mapping, and independently digestible evidence.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

import yaml
from yaml.constructor import ConstructorError

from ado2gh.models import PipelineMetadata, PipelineType
from ado2gh.pipelines.credential_attestation import (
    CredentialAttestationError,
    CredentialAttestationVerifier,
    normalize_canary,
)
from ado2gh.pipelines.llm import contains_sensitive_literal
from ado2gh.pipelines.pev_types import ConversionPlan, PlanAmbiguity


APPROVAL_SCHEMA_VERSION = 3
_MAX_MANIFEST_BYTES = 10 * 1024 * 1024
_MAX_RECORD_BYTES = 256 * 1024
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_APPROVAL_ID_RE = re.compile(r"^approval-sha256:[0-9a-f]{64}$")
_AMBIGUITY_ID_RE = re.compile(r"^amb-[0-9a-f]{20}$")
_REPOSITORY_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/[A-Za-z0-9._-]+$"
)
_SAFE_SECRET_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,255}$")
_GHA_EXPRESSION_RE = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)
_GHA_SECRET_DOT_RE = re.compile(r"\bsecrets\.([A-Za-z_][A-Za-z0-9_]*)\b")
_GHA_SECRET_INDEX_RE = re.compile(
    r"\bsecrets\[\s*(['\"])([A-Za-z_][A-Za-z0-9_]*)\1\s*\]"
)


class ApprovalManifestError(ValueError):
    """Raised when an approval manifest cannot be trusted."""


def workflow_secret_names(workflow_text: str) -> list[str]:
    """Return statically enumerable GitHub secret names or fail closed."""
    if not isinstance(workflow_text, str):
        raise ApprovalManifestError("Workflow content must be text")
    names: set[str] = set()
    for expression in _GHA_EXPRESSION_RE.findall(workflow_text):
        names.update(_GHA_SECRET_DOT_RE.findall(expression))
        indexed = _GHA_SECRET_INDEX_RE.findall(expression)
        names.update(name for _quote, name in indexed)
        residual = _GHA_SECRET_DOT_RE.sub("", expression)
        residual = _GHA_SECRET_INDEX_RE.sub("", residual)
        if re.search(r"\bsecrets\b", residual):
            raise ApprovalManifestError(
                "Bare, serialized, or dynamic GitHub secrets context is prohibited"
            )
    names.discard("GITHUB_TOKEN")
    return sorted(names)


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader: Any, node: Any, deep: bool = False) -> dict:
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise ConstructorError(
                "while constructing a mapping", node.start_mark,
                "found an unhashable mapping key", key_node.start_mark,
            ) from exc
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping", node.start_mark,
                f"found duplicate key {key!r}", key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def github_environment_configuration_digest(value: Any) -> str:
    """Digest the policy-bearing portion of a GitHub environment response."""
    if not isinstance(value, Mapping):
        raise ApprovalManifestError(
            "GitHub environment configuration must be an object"
        )

    def canonical(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {
                str(key): canonical(child)
                for key, child in sorted(item.items(), key=lambda pair: str(pair[0]))
                if str(key) not in {
                    "url", "html_url", "created_at", "updated_at"
                }
            }
        if isinstance(item, list):
            converted = [canonical(child) for child in item]
            return sorted(converted, key=lambda child: _canonical_bytes(child))
        if item is None or isinstance(item, (str, int, float, bool)):
            return item
        raise ApprovalManifestError(
            "GitHub environment configuration contains an unsupported value"
        )

    projection = {
        "name": value.get("name"),
        "protection_rules": value.get("protection_rules", []),
        "deployment_branch_policy": value.get("deployment_branch_policy"),
    }
    return _sha256(canonical(projection))


def _required_text(value: Any, field: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ApprovalManifestError(f"{field} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > maximum or _CONTROL_RE.search(normalized):
        raise ApprovalManifestError(f"{field} is too long or contains control characters")
    return normalized


def _exact_mapping(value: Any, field: str, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ApprovalManifestError(f"{field} must be a mapping")
    normalized = dict(value)
    if not all(isinstance(key, str) for key in normalized):
        raise ApprovalManifestError(f"{field} keys must be strings")
    unknown = set(normalized) - allowed
    if unknown:
        raise ApprovalManifestError(
            f"{field} has unknown field(s): {', '.join(sorted(unknown))}"
        )
    return normalized


def _json_copy(value: Any, field: str, *, maximum: int = _MAX_RECORD_BYTES) -> Any:
    try:
        encoded = _canonical_bytes(value)
    except (TypeError, ValueError) as exc:
        raise ApprovalManifestError(f"{field} must contain only finite JSON values") from exc
    if len(encoded) > maximum:
        raise ApprovalManifestError(f"{field} exceeds {maximum} bytes")
    return json.loads(encoded.decode("utf-8"))


def _approved_at(value: Any) -> str:
    text = _required_text(value, "approval.approved_at", maximum=64)
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ApprovalManifestError(
            "approval.approved_at must be an RFC 3339 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ApprovalManifestError(
            "approval.approved_at must include an explicit timezone"
        )
    return text


def _normalize_evidence(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or not value or len(value) > 20:
        raise ApprovalManifestError("approval.evidence must contain 1 to 20 records")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        field = f"approval.evidence[{index}]"
        if isinstance(item, Mapping) and item.get("type") == "credential-canary":
            try:
                normalized.append(normalize_canary(item))
            except CredentialAttestationError as exc:
                raise ApprovalManifestError(f"{field} is invalid: {exc}") from exc
            continue
        raw = _exact_mapping(item, field, {"type", "reference", "digest"})
        evidence_type = _required_text(raw.get("type"), f"{field}.type", maximum=64)
        reference = _required_text(raw.get("reference"), f"{field}.reference", maximum=2048)
        if contains_sensitive_literal(reference):
            raise ApprovalManifestError(
                f"{field}.reference contains an inline credential; use a non-secret reference"
            )
        digest = _required_text(raw.get("digest"), f"{field}.digest", maximum=71).lower()
        if not _SHA256_RE.fullmatch(digest):
            raise ApprovalManifestError(f"{field}.digest must be sha256:<64 lowercase hex>")
        normalized.append({
            "type": evidence_type,
            "reference": reference,
            "digest": digest,
        })
    return tuple(normalized)


def _normalize_target_mapping(value: Any) -> dict[str, Any]:
    raw = _exact_mapping(
        value,
        "approval.target_mapping",
        {
            "type", "resource_type", "resource", "secret_names", "outcome",
            "runner_label", "repository", "ref", "token_secret", "step",
            "condition",
            "configuration_digest",
        },
    )
    mapping_type = _required_text(raw.get("type"), "approval.target_mapping.type", maximum=64)
    allowed_by_type = {
        "external_configuration": {
            "type", "resource_type", "resource", "secret_names",
            "configuration_digest",
        },
        "parity_attestation": {"type", "outcome"},
        "runner": {"type", "runner_label"},
        "repository_checkout": {"type", "repository", "ref", "token_secret"},
        "workflow_step": {"type", "step"},
        "workflow_condition": {"type", "condition"},
    }
    if mapping_type not in allowed_by_type:
        raise ApprovalManifestError(
            "approval.target_mapping.type must be external_configuration, "
            "parity_attestation, runner, repository_checkout, workflow_step, "
            "or workflow_condition"
        )
    unknown = set(raw) - allowed_by_type[mapping_type]
    if unknown:
        raise ApprovalManifestError(
            "approval.target_mapping has fields invalid for type "
            f"{mapping_type!r}: {', '.join(sorted(unknown))}"
        )

    normalized: dict[str, Any] = {"type": mapping_type}
    if mapping_type == "external_configuration":
        normalized["resource_type"] = _required_text(
            raw.get("resource_type"), "approval.target_mapping.resource_type", maximum=128,
        )
        normalized["resource"] = _required_text(
            raw.get("resource"), "approval.target_mapping.resource", maximum=512,
        )
        names = raw.get("secret_names", [])
        if not isinstance(names, list) or len(names) > 256:
            raise ApprovalManifestError("approval.target_mapping.secret_names must be a list")
        checked_names: list[str] = []
        for name in names:
            if not isinstance(name, str) or not _SAFE_SECRET_NAME_RE.fullmatch(name):
                raise ApprovalManifestError(
                    "approval target secret_names must contain names, never secret values"
                )
            checked_names.append(name)
        if len(set(checked_names)) != len(checked_names):
            raise ApprovalManifestError("approval target secret_names contains duplicates")
        if checked_names:
            normalized["secret_names"] = sorted(checked_names)
        if raw.get("configuration_digest") is not None:
            digest = _required_text(
                raw["configuration_digest"],
                "approval.target_mapping.configuration_digest",
                maximum=71,
            ).lower()
            if not _SHA256_RE.fullmatch(digest):
                raise ApprovalManifestError(
                    "approval target configuration_digest must be sha256:<64 hex>"
                )
            normalized["configuration_digest"] = digest
    elif mapping_type == "parity_attestation":
        outcome = _required_text(raw.get("outcome"), "approval.target_mapping.outcome")
        if outcome != "parity_confirmed":
            raise ApprovalManifestError(
                "parity_attestation outcome must be exactly 'parity_confirmed'"
            )
        normalized["outcome"] = outcome
    elif mapping_type == "runner":
        normalized["runner_label"] = _required_text(
            raw.get("runner_label"), "approval.target_mapping.runner_label", maximum=256,
        )
    elif mapping_type == "repository_checkout":
        repository = _required_text(
            raw.get("repository"), "approval.target_mapping.repository", maximum=140,
        )
        if not _REPOSITORY_RE.fullmatch(repository) or repository.endswith(".git"):
            raise ApprovalManifestError(
                "approval.target_mapping.repository must be an owner/repository slug"
            )
        normalized["repository"] = repository
        # An external checkout without an explicit ref silently follows mutable
        # defaults.  Enterprise conversion requires a resolvable, receipt-bound
        # ref so execution and cutover validation can detect drift.
        normalized["ref"] = _required_text(
            raw.get("ref"), "approval.target_mapping.ref", maximum=255,
        )
        if raw.get("token_secret") is not None:
            token_secret = _required_text(
                raw["token_secret"], "approval.target_mapping.token_secret", maximum=256,
            )
            if not _SAFE_SECRET_NAME_RE.fullmatch(token_secret):
                raise ApprovalManifestError(
                    "approval.target_mapping.token_secret must be a GitHub secret name"
                )
            normalized["token_secret"] = token_secret
    elif mapping_type == "workflow_step":
        step = _exact_mapping(
            raw.get("step"), "approval.target_mapping.step",
            {"name", "run", "uses", "with", "env", "if", "shell", "working-directory"},
        )
        if bool(step.get("run")) == bool(step.get("uses")):
            raise ApprovalManifestError(
                "approval target workflow_step must contain exactly one of run or uses"
            )
        if contains_sensitive_literal(step):
            raise ApprovalManifestError(
                "approval target workflow_step contains an inline credential literal"
            )
        normalized["step"] = _json_copy(step, "approval.target_mapping.step")
    elif mapping_type == "workflow_condition":
        condition = _required_text(
            raw.get("condition"),
            "approval.target_mapping.condition",
            maximum=2_000,
        )
        normalized["condition"] = condition
    if contains_sensitive_literal(normalized):
        raise ApprovalManifestError(
            "approval.target_mapping contains an inline credential literal"
        )
    return _json_copy(normalized, "approval.target_mapping")


@dataclass(frozen=True)
class ManualApprovalRecord:
    approval_id: str
    ambiguity_id: str
    source_fingerprint: str
    project: str
    repository: str
    pipeline_id: int
    pipeline_type: str
    decision: str
    approver: str
    ticket: str
    approved_at: str
    target_mapping: Mapping[str, Any]
    evidence: tuple[Mapping[str, Any], ...]

    @classmethod
    def create(
        cls,
        *,
        ambiguity_id: str,
        source_fingerprint: str,
        project: str,
        repository: str,
        pipeline_id: int,
        pipeline_type: str,
        approver: str,
        ticket: str,
        approved_at: str,
        target_mapping: Mapping[str, Any],
        evidence: Iterable[Mapping[str, Any]],
    ) -> "ManualApprovalRecord":
        payload = {
            "ambiguity_id": ambiguity_id,
            "source_fingerprint": source_fingerprint,
            "project": project,
            "repository": repository,
            "pipeline_id": pipeline_id,
            "pipeline_type": pipeline_type,
            "decision": "approved",
            "approver": approver,
            "ticket": ticket,
            "approved_at": approved_at,
            "target_mapping": target_mapping,
            "evidence": list(evidence),
        }
        normalized = cls._normalize_payload(payload)
        normalized["approval_id"] = "approval-" + _sha256(normalized)
        return cls._from_normalized(normalized)

    @classmethod
    def from_mapping(cls, value: Any) -> "ManualApprovalRecord":
        allowed = {
            "approval_id", "ambiguity_id", "source_fingerprint", "project",
            "repository", "pipeline_id", "pipeline_type", "decision", "approver",
            "ticket", "approved_at", "target_mapping", "evidence",
        }
        raw = _exact_mapping(value, "approval", allowed)
        if set(raw) != allowed:
            missing = allowed - set(raw)
            raise ApprovalManifestError(
                "approval is missing required field(s): " + ", ".join(sorted(missing))
            )
        approval_id = _required_text(raw.get("approval_id"), "approval.approval_id", maximum=80)
        if not _APPROVAL_ID_RE.fullmatch(approval_id):
            raise ApprovalManifestError(
                "approval.approval_id must be approval-sha256:<64 lowercase hex>"
            )
        payload = dict(raw)
        payload.pop("approval_id")
        normalized = cls._normalize_payload(payload)
        expected = "approval-" + _sha256(normalized)
        if approval_id != expected:
            raise ApprovalManifestError(
                f"approval {approval_id!r} content digest does not match its fields"
            )
        normalized["approval_id"] = approval_id
        return cls._from_normalized(normalized)

    @classmethod
    def _normalize_payload(cls, raw: Mapping[str, Any]) -> dict[str, Any]:
        ambiguity_id = _required_text(raw.get("ambiguity_id"), "approval.ambiguity_id", maximum=32)
        if not _AMBIGUITY_ID_RE.fullmatch(ambiguity_id):
            raise ApprovalManifestError("approval.ambiguity_id is not a planner ambiguity id")
        source_fingerprint = _required_text(
            raw.get("source_fingerprint"), "approval.source_fingerprint", maximum=71,
        ).lower()
        if not _SHA256_RE.fullmatch(source_fingerprint):
            raise ApprovalManifestError(
                "approval.source_fingerprint must be sha256:<64 lowercase hex>"
            )
        project = _required_text(raw.get("project"), "approval.project", maximum=256)
        repository_value = raw.get("repository")
        if not isinstance(repository_value, str) or len(repository_value) > 256 or _CONTROL_RE.search(repository_value):
            raise ApprovalManifestError("approval.repository must be a safe string")
        repository = repository_value.strip()
        pipeline_id = raw.get("pipeline_id")
        if isinstance(pipeline_id, bool) or not isinstance(pipeline_id, int) or pipeline_id <= 0:
            raise ApprovalManifestError("approval.pipeline_id must be a positive integer")
        pipeline_type_value = raw.get("pipeline_type")
        if isinstance(pipeline_type_value, PipelineType):
            pipeline_type_value = pipeline_type_value.value
        pipeline_type = _required_text(
            pipeline_type_value, "approval.pipeline_type", maximum=32,
        ).casefold()
        if pipeline_type not in {member.value for member in PipelineType}:
            raise ApprovalManifestError("approval.pipeline_type is not recognized")
        decision = _required_text(raw.get("decision"), "approval.decision", maximum=32)
        if decision != "approved":
            raise ApprovalManifestError("approval.decision must be exactly 'approved'")
        return {
            "ambiguity_id": ambiguity_id,
            "source_fingerprint": source_fingerprint,
            "project": project,
            "repository": repository,
            "pipeline_id": pipeline_id,
            "pipeline_type": pipeline_type,
            "decision": decision,
            "approver": _required_text(raw.get("approver"), "approval.approver", maximum=320),
            "ticket": _required_text(raw.get("ticket"), "approval.ticket", maximum=256),
            "approved_at": _approved_at(raw.get("approved_at")),
            "target_mapping": _normalize_target_mapping(raw.get("target_mapping")),
            "evidence": [dict(item) for item in _normalize_evidence(raw.get("evidence"))],
        }

    @classmethod
    def _from_normalized(cls, value: Mapping[str, Any]) -> "ManualApprovalRecord":
        return cls(
            approval_id=str(value["approval_id"]),
            ambiguity_id=str(value["ambiguity_id"]),
            source_fingerprint=str(value["source_fingerprint"]),
            project=str(value["project"]),
            repository=str(value["repository"]),
            pipeline_id=int(value["pipeline_id"]),
            pipeline_type=str(value["pipeline_type"]),
            decision=str(value["decision"]),
            approver=str(value["approver"]),
            ticket=str(value["ticket"]),
            approved_at=str(value["approved_at"]),
            target_mapping=_json_copy(value["target_mapping"], "approval.target_mapping"),
            evidence=tuple(
                _json_copy(item, "approval.evidence") for item in value["evidence"]
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "ambiguity_id": self.ambiguity_id,
            "source_fingerprint": self.source_fingerprint,
            "project": self.project,
            "repository": self.repository,
            "pipeline_id": self.pipeline_id,
            "pipeline_type": self.pipeline_type,
            "decision": self.decision,
            "approver": self.approver,
            "ticket": self.ticket,
            "approved_at": self.approved_at,
            "target_mapping": _json_copy(self.target_mapping, "approval.target_mapping"),
            "evidence": [dict(item) for item in self.evidence],
        }

    @property
    def duplicate_key(self) -> tuple[str, int, str, str, str]:
        return (
            self.project.casefold(), self.pipeline_id, self.pipeline_type,
            self.repository.casefold(), self.ambiguity_id,
        )

    def same_pipeline_number(self, meta: PipelineMetadata) -> bool:
        return (
            self.project.casefold() == str(meta.project).casefold()
            and self.pipeline_id == int(meta.pipeline_id)
            and self.pipeline_type == meta.pipeline_type.value
        )


_EXTERNAL_CONFIGURATION_KINDS = {
    # Variable groups are intentionally excluded. Inventory currently knows
    # names but not an exact secret/non-secret classification or the least-
    # privilege step/job scope needed to inject them. An approval cannot make
    # an unapplied mapping executable; the planner keeps it as a blocking
    # manual ambiguity until scoped wiring is implemented.
    # Direct secret variables are also excluded: a repository-secret canary
    # proves existence/readiness but does not prove least-privilege injection
    # into the source step that consumed the ADO value. The consuming operation
    # must instead be represented by an exact approved workflow_step whose
    # secret references are authenticated and validated.
    "service_connection_mapping",
    "environment_protection",
    "package_authentication",
    "docker_publish_configuration",
}
_PARITY_KINDS = {"source_semantics_review", "classic_pipeline_semantics"}


def credential_requirements_for_mapping(
    target_mapping: Mapping[str, Any],
    ambiguity: Mapping[str, Any] | PlanAmbiguity,
) -> tuple[dict[str, str], ...]:
    """Return the exact signed-canary requirements introduced by a mapping."""
    if isinstance(ambiguity, PlanAmbiguity):
        ambiguity_id = ambiguity.ambiguity_id
        kind = ambiguity.kind
    elif isinstance(ambiguity, Mapping):
        ambiguity_id = str(ambiguity.get("ambiguity_id", ""))
        kind = str(ambiguity.get("kind", ""))
    else:
        raise ApprovalManifestError("approval ambiguity is malformed")
    mapping_type = str(target_mapping.get("type", ""))
    names: list[str] = []
    resource = ""
    capability = kind
    external_ref = ""
    if mapping_type == "external_configuration":
        names = sorted(str(item) for item in target_mapping.get("secret_names", []))
        resource = str(target_mapping.get("resource", ""))
    elif mapping_type == "repository_checkout":
        token_secret = str(target_mapping.get("token_secret", ""))
        names = [token_secret] if token_secret else []
        resource = str(target_mapping.get("repository", ""))
        capability = "external_repository_checkout"
        external_ref = str(target_mapping.get("ref", ""))
    elif mapping_type in {"workflow_step", "workflow_condition"}:
        fragment = (
            target_mapping.get("step", {})
            if mapping_type == "workflow_step"
            else target_mapping.get("condition", "")
        )
        names = workflow_secret_names(
            json.dumps(fragment, sort_keys=True, ensure_ascii=True)
        )
        resource = f"{mapping_type}:{ambiguity_id}"
    requirements = []
    for name in names:
        item = {
            "ambiguity_id": ambiguity_id,
            "capability": capability,
            "resource": resource,
            "secret_name": name,
        }
        if external_ref:
            item["external_ref"] = external_ref
        requirements.append(item)
    return tuple(requirements)


def verify_record_credential_attestations(
    record: ManualApprovalRecord,
    ambiguity: Mapping[str, Any] | PlanAmbiguity,
    *,
    plan_id: str,
    source_fingerprint: str,
    verifier: Optional[CredentialAttestationVerifier],
) -> tuple[dict[str, Any], ...]:
    """Authenticate and exactly bind every credential used by an approval."""
    requirements = credential_requirements_for_mapping(
        record.target_mapping, ambiguity
    )
    canaries = tuple(
        item for item in record.evidence
        if item.get("type") == "credential-canary"
    )
    if not requirements:
        if canaries:
            raise CredentialAttestationError(
                "approval contains an inapplicable credential canary"
            )
        return ()
    if verifier is None:
        raise CredentialAttestationError(
            "credential attestation verifier is not configured"
        )
    if len(canaries) != len(requirements):
        raise CredentialAttestationError(
            "approval must contain exactly one credential canary per credential"
        )

    by_key: dict[tuple[str, str, str, str], Mapping[str, Any]] = {}
    for canary in canaries:
        claims = canary.get("claims", {})
        if not isinstance(claims, Mapping):
            raise CredentialAttestationError("credential canary claims are malformed")
        key = (
            str(claims.get("ambiguity_id", "")),
            str(claims.get("capability", "")),
            str(claims.get("resource", "")),
            str(claims.get("secret_name", "")),
        )
        if key in by_key:
            raise CredentialAttestationError(
                "approval contains duplicate credential canaries"
            )
        by_key[key] = canary

    verified: list[dict[str, Any]] = []
    target_repository_ids: set[str] = set()
    nonces: set[str] = set()
    canary_run_ids: set[str] = set()
    for requirement in requirements:
        key = (
            requirement["ambiguity_id"],
            requirement["capability"],
            requirement["resource"],
            requirement["secret_name"],
        )
        canary = by_key.get(key)
        if canary is None:
            raise CredentialAttestationError(
                "approval credential canary does not match its capability/resource"
            )
        expected = {
            "plan_id": plan_id,
            "source_fingerprint": source_fingerprint,
            "ambiguity_id": requirement["ambiguity_id"],
            "capability": requirement["capability"],
            "resource": requirement["resource"],
            "secret_name": requirement["secret_name"],
        }
        if requirement.get("external_ref"):
            expected["external_ref"] = requirement["external_ref"]
        authenticated = verifier.verify(canary, expected=expected)
        target_repository_ids.add(
            str(authenticated["claims"]["target_repository_id"])
        )
        nonce = str(authenticated["claims"]["nonce"])
        canary_run_id = str(authenticated["claims"]["canary_run_id"])
        if nonce in nonces or canary_run_id in canary_run_ids:
            raise CredentialAttestationError(
                "credential canary nonce/run identity was replayed within an approval"
            )
        nonces.add(nonce)
        canary_run_ids.add(canary_run_id)
        verified.append(authenticated)
    if len(target_repository_ids) != 1:
        raise CredentialAttestationError(
            "one approval cannot target multiple immutable repositories"
        )
    return tuple(verified)


def validate_record_for_plan(
    record: ManualApprovalRecord,
    ambiguity: PlanAmbiguity,
    plan: ConversionPlan,
    meta: PipelineMetadata,
    *,
    credential_verifier: Optional[CredentialAttestationVerifier] = None,
) -> Optional[dict[str, str]]:
    """Return a stable failure finding, or ``None`` when the record applies."""
    base = {"approval_id": record.approval_id, "ambiguity_id": record.ambiguity_id}
    if not record.same_pipeline_number(meta):
        return {**base, "code": "approval_pipeline_mismatch"}
    if record.repository.casefold() != str(meta.repo_name).casefold():
        return {**base, "code": "approval_repository_mismatch"}
    if record.source_fingerprint != plan.source_fingerprint:
        return {**base, "code": "approval_source_fingerprint_mismatch"}
    if record.ambiguity_id != ambiguity.ambiguity_id:
        return {**base, "code": "approval_ambiguity_mismatch"}
    mapping_type = str(record.target_mapping.get("type", ""))
    compatible = (
        (ambiguity.kind in _EXTERNAL_CONFIGURATION_KINDS and mapping_type == "external_configuration")
        or (ambiguity.kind in _PARITY_KINDS and mapping_type == "parity_attestation")
        # A free-form label is not a trust boundary: every eligible runner
        # carrying it can receive the job and its secrets. Runner mappings stay
        # non-approvable until evidence binds an exact runner group, repository
        # access, capabilities, and eligible runner identities.
        or (
            ambiguity.kind == "external_repository_checkout"
            and mapping_type == "repository_checkout"
        )
        or (
            ambiguity.kind in {"unknown_task", "unknown_step"}
            and mapping_type == "workflow_step"
        )
        or (
            ambiguity.kind == "unsupported_condition"
            and mapping_type == "workflow_condition"
        )
    )
    if not compatible:
        return {**base, "code": "approval_target_mapping_incompatible"}
    if mapping_type == "workflow_step":
        # Reuse the executable-fragment allow-list.  Approval establishes
        # authorization and provenance, not permission to bypass safety gates.
        from ado2gh.pipelines.llm import validate_llm_replacement
        try:
            validate_llm_replacement(ambiguity, record.target_mapping["step"])
        except (TypeError, ValueError):
            return {**base, "code": "approval_target_mapping_invalid"}
    elif mapping_type == "workflow_condition":
        from ado2gh.pipelines.llm import validate_llm_replacement
        try:
            validate_llm_replacement(ambiguity, record.target_mapping["condition"])
        except (TypeError, ValueError):
            return {**base, "code": "approval_target_mapping_invalid"}
    elif mapping_type == "external_configuration":
        source = ambiguity.source if isinstance(ambiguity.source, Mapping) else {}
        resource_type = str(record.target_mapping.get("resource_type", ""))
        resource = str(record.target_mapping.get("resource", ""))
        secret_names = sorted(record.target_mapping.get("secret_names", []))
        if ambiguity.kind == "variable_group_mapping":
            expected_names = sorted(
                str(item) for item in source.get("variables", [])
            )
            if (
                resource_type != "variable_group"
                or resource != str(source.get("name", ""))
                or secret_names != expected_names
            ):
                return {**base, "code": "approval_external_resource_mismatch"}
        elif ambiguity.kind == "direct_secret_mapping":
            expected_name = str(source.get("name", ""))
            if (
                resource_type != "repository_secret"
                or resource != expected_name
                or secret_names != [expected_name]
            ):
                return {**base, "code": "approval_external_resource_mismatch"}
        elif ambiguity.kind == "environment_protection":
            if (
                resource_type != "environment"
                or resource != str(source.get("name", ""))
                or secret_names
                or not record.target_mapping.get("configuration_digest")
            ):
                return {**base, "code": "approval_external_resource_mismatch"}
        elif ambiguity.kind == "service_connection_mapping":
            if resource_type != "repository_secret" or not secret_names:
                return {**base, "code": "approval_external_resource_mismatch"}
        elif ambiguity.kind in {
            "package_authentication", "docker_publish_configuration"
        }:
            if resource_type != "repository_secret" or not secret_names:
                return {**base, "code": "approval_external_resource_mismatch"}
    elif mapping_type == "repository_checkout":
        if not record.target_mapping.get("token_secret"):
            return {**base, "code": "approval_checkout_token_missing"}
    try:
        verify_record_credential_attestations(
            record,
            ambiguity,
            plan_id=plan.plan_id,
            source_fingerprint=plan.source_fingerprint,
            verifier=credential_verifier,
        )
    except CredentialAttestationError:
        return {**base, "code": "approval_credential_canary_invalid"}
    return None


class ManualApprovalManifest:
    """Validated set of approval records with stable content identity."""

    def __init__(self, approvals: Iterable[ManualApprovalRecord]) -> None:
        records = tuple(sorted(approvals, key=lambda item: item.approval_id))
        seen: dict[tuple[str, int, str, str, str], str] = {}
        for record in records:
            prior = seen.get(record.duplicate_key)
            if prior is not None:
                raise ApprovalManifestError(
                    "duplicate approvals for one pipeline ambiguity are prohibited: "
                    f"{prior}, {record.approval_id}"
                )
            seen[record.duplicate_key] = record.approval_id
        self.approvals = records
        self.manifest_digest = _sha256({
            "schema_version": APPROVAL_SCHEMA_VERSION,
            "approvals": [record.to_dict() for record in records],
        })

    @classmethod
    def from_mapping(cls, value: Any) -> "ManualApprovalManifest":
        raw = _exact_mapping(
            value,
            "manual approval manifest",
            {"schema_version", "manifest_digest", "approvals"},
        )
        if raw.get("schema_version") != APPROVAL_SCHEMA_VERSION:
            raise ApprovalManifestError(
                f"manual approval manifest schema_version must be {APPROVAL_SCHEMA_VERSION}"
            )
        approvals = raw.get("approvals")
        if not isinstance(approvals, list) or len(approvals) > 100_000:
            raise ApprovalManifestError("manual approval manifest approvals must be a list")
        manifest = cls(ManualApprovalRecord.from_mapping(item) for item in approvals)
        supplied_digest = raw.get("manifest_digest")
        if supplied_digest is not None:
            supplied_digest = _required_text(
                supplied_digest, "manual approval manifest manifest_digest", maximum=71,
            ).lower()
            if supplied_digest != manifest.manifest_digest:
                raise ApprovalManifestError(
                    "manual approval manifest digest does not match its approval records"
                )
        return manifest

    @classmethod
    def from_config(
        cls,
        value: Any,
        *,
        base_dir: Optional[Path] = None,
    ) -> "ManualApprovalManifest":
        if isinstance(value, (str, Path)):
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = Path(base_dir or Path.cwd()) / candidate
            try:
                path = candidate.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise ApprovalManifestError(
                    f"manual approval manifest does not exist: {candidate}"
                ) from exc
            if not path.is_file():
                raise ApprovalManifestError(f"manual approval manifest is not a file: {path}")
            if path.stat().st_size > _MAX_MANIFEST_BYTES:
                raise ApprovalManifestError(
                    f"manual approval manifest exceeds {_MAX_MANIFEST_BYTES} bytes"
                )
            try:
                raw = yaml.load(path.read_text(encoding="utf-8-sig"), Loader=_UniqueKeyLoader)
            except (OSError, UnicodeError, yaml.YAMLError) as exc:
                raise ApprovalManifestError(
                    f"manual approval manifest could not be read safely: {path}"
                ) from exc
            return cls.from_mapping(raw)
        return cls.from_mapping(value)

    def to_dict(self) -> dict[str, Any]:
        checked = self._checked_records()
        return {
            "schema_version": APPROVAL_SCHEMA_VERSION,
            "manifest_digest": self.manifest_digest,
            "approvals": [record.to_dict() for record in checked],
        }

    def _checked_records(self) -> tuple[ManualApprovalRecord, ...]:
        """Rebuild records before use so nested-dict mutation cannot bypass IDs."""
        checked = tuple(
            ManualApprovalRecord.from_mapping(record.to_dict())
            for record in self.approvals
        )
        current = _sha256({
            "schema_version": APPROVAL_SCHEMA_VERSION,
            "approvals": [record.to_dict() for record in checked],
        })
        if current != self.manifest_digest:
            raise ApprovalManifestError(
                "manual approval manifest changed after content addressing"
            )
        return checked

    def evaluate(
        self,
        plan: ConversionPlan,
        meta: PipelineMetadata,
        *,
        credential_verifier: Optional[CredentialAttestationVerifier] = None,
    ) -> tuple[dict[str, ManualApprovalRecord], list[dict[str, str]]]:
        ambiguities = {item.ambiguity_id: item for item in plan.ambiguities}
        accepted: dict[str, ManualApprovalRecord] = {}
        failures: list[dict[str, str]] = []
        for record in self._checked_records():
            if not record.same_pipeline_number(meta):
                continue
            ambiguity = ambiguities.get(record.ambiguity_id)
            if ambiguity is None:
                failures.append({
                    "approval_id": record.approval_id,
                    "ambiguity_id": record.ambiguity_id,
                    "code": "approval_ambiguity_not_in_plan",
                })
                continue
            failure = validate_record_for_plan(
                record,
                ambiguity,
                plan,
                meta,
                credential_verifier=credential_verifier,
            )
            if failure is not None:
                failures.append(failure)
                continue
            accepted[record.ambiguity_id] = record
        return accepted, failures
