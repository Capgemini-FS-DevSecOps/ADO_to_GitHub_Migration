"""Planner-Executor-Validator orchestration for pipeline conversion."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Union
from uuid import uuid4

from ado2gh.models import PipelineMetadata
from ado2gh.governance import (
    PIPELINE_MANUAL_MAPPING_ACTION,
    SignedApprovalVerifier,
    governance_resource_digest,
    normalize_governance_settings,
    pipeline_manifest_resource,
)
from ado2gh.pipelines.approvals import (
    ApprovalManifestError,
    ManualApprovalManifest,
    ManualApprovalRecord,
    validate_record_for_plan,
)
from ado2gh.pipelines.credential_attestation import (
    CredentialAttestationVerifier,
)
from ado2gh.pipelines.executor import PipelineConversionExecutor
from ado2gh.pipelines.llm import (
    PipelineLLMClient,
    create_pipeline_llm_client_from_env,
)
from ado2gh.pipelines.pev_types import (
    LLMResolution,
    PipelineValidationError,
    PlanAmbiguity,
)
from ado2gh.pipelines.planner import PipelineConversionPlanner
from ado2gh.pipelines.validator import PipelineWorkflowValidator


logger = logging.getLogger(__name__)


ExternalApprovalResult = Optional[Union[ManualApprovalRecord, Mapping[str, Any]]]
ExternalApproval = Callable[[PlanAmbiguity, PipelineMetadata], ExternalApprovalResult]


class PipelinePEVConverter:
    """Run a bounded Plan -> Resolve -> Execute -> Validate conversion.

    Deterministic rules are always preferred.  The optional LLM receives only
    planner-identified, redacted ambiguities.  LLM failures and manual/external
    requirements remain visible in validation and can never be reported as
    production-ready.
    """

    def __init__(
        self,
        transformer: Any,
        *,
        planner: Optional[PipelineConversionPlanner] = None,
        validator: Optional[PipelineWorkflowValidator] = None,
        llm_client: Optional[PipelineLLMClient] = None,
        min_llm_confidence: float = 0.75,
        max_llm_resolutions: int = 32,
        require_llm_for_ambiguity: bool = False,
        require_production_ready: bool = False,
        manual_approval_manifest: Optional[Any] = None,
        external_approval: Optional[ExternalApproval] = None,
        credential_attestation_verifier: Optional[
            CredentialAttestationVerifier
        ] = None,
    ) -> None:
        if not 0.0 <= min_llm_confidence <= 1.0:
            raise ValueError("min_llm_confidence must be between 0 and 1")
        if not 0 <= max_llm_resolutions <= 100:
            raise ValueError("max_llm_resolutions must be between 0 and 100")
        self.transformer = transformer
        self.planner = planner or PipelineConversionPlanner()
        self.validator = validator or PipelineWorkflowValidator()
        self.llm_client = llm_client
        self.min_llm_confidence = min_llm_confidence
        self.max_llm_resolutions = max_llm_resolutions
        self.require_llm_for_ambiguity = require_llm_for_ambiguity
        self.require_production_ready = require_production_ready
        self.manual_approval_manifest = (
            manual_approval_manifest
            if isinstance(manual_approval_manifest, ManualApprovalManifest)
            else ManualApprovalManifest.from_config(manual_approval_manifest)
            if manual_approval_manifest is not None
            else None
        )
        self.external_approval = external_approval
        self.credential_attestation_verifier = credential_attestation_verifier

    def convert(self, meta: PipelineMetadata, output_dir: Path) -> dict[str, Any]:
        started = datetime.now(timezone.utc)
        plan = self.planner.plan(meta)
        # Model output is never an execution authority. Proposals are retained
        # as content-addressed review evidence only; the executor receives no
        # LLM fragments. A subsequent run may apply the exact fragment through
        # a ManualApprovalRecord bound to this source fingerprint/ambiguity.
        proposals: dict[str, LLMResolution] = {}
        resolution_failures: list[dict[str, str]] = []
        approved_records: dict[str, ManualApprovalRecord] = {}
        approval_failures: list[dict[str, str]] = []
        llm_attempts = 0

        if self.manual_approval_manifest is not None:
            approved_records, approval_failures = self.manual_approval_manifest.evaluate(
                plan,
                meta,
                credential_verifier=self.credential_attestation_verifier,
            )

        # Backward-compatible integration point for approval services.  The
        # callback must now return a complete content-addressed record; a bare
        # True/False can never cross this authorization boundary.
        if self.external_approval is not None:
            for ambiguity in plan.manual_ambiguities:
                try:
                    raw_approval = self.external_approval(ambiguity, meta)
                    if raw_approval is None:
                        continue
                    if isinstance(raw_approval, bool):
                        raise ApprovalManifestError(
                            "approval callbacks must return a content-addressed record, not a boolean"
                        )
                    record = (
                        raw_approval
                        if isinstance(raw_approval, ManualApprovalRecord)
                        else ManualApprovalRecord.from_mapping(raw_approval)
                    )
                    failure = validate_record_for_plan(
                        record,
                        ambiguity,
                        plan,
                        meta,
                        credential_verifier=self.credential_attestation_verifier,
                    )
                    if failure is not None:
                        approval_failures.append(failure)
                        continue
                    if ambiguity.ambiguity_id in approved_records:
                        prior = approved_records.pop(ambiguity.ambiguity_id)
                        approval_failures.append({
                            "approval_id": record.approval_id,
                            "ambiguity_id": ambiguity.ambiguity_id,
                            "code": "duplicate_approval",
                            "prior_approval_id": prior.approval_id,
                        })
                        continue
                    approved_records[ambiguity.ambiguity_id] = record
                except Exception as exc:  # approval integration must fail closed
                    approval_failures.append({
                        "ambiguity_id": ambiguity.ambiguity_id,
                        "code": "external_approval_invalid",
                        "error_type": type(exc).__name__,
                    })

        # A canary execution receipt is single-purpose. Even independently
        # signed claims may not reuse its nonce or external run identity for a
        # second ambiguity/credential in the same conversion plan.
        seen_canary_nonces: dict[str, str] = {}
        seen_canary_runs: dict[str, str] = {}
        replayed: set[str] = set()
        for ambiguity_id, record in approved_records.items():
            for evidence in record.evidence:
                if evidence.get("type") != "credential-canary":
                    continue
                claims = evidence.get("claims", {})
                nonce = str(claims.get("nonce", ""))
                run_id = str(claims.get("canary_run_id", ""))
                for seen, identity in (
                    (seen_canary_nonces, nonce),
                    (seen_canary_runs, run_id),
                ):
                    prior = seen.get(identity)
                    if prior is not None:
                        replayed.update({prior, ambiguity_id})
                    else:
                        seen[identity] = ambiguity_id
        for ambiguity_id in sorted(replayed):
            record = approved_records.pop(ambiguity_id, None)
            if record is not None:
                approval_failures.append({
                    "approval_id": record.approval_id,
                    "ambiguity_id": ambiguity_id,
                    "code": "approval_credential_canary_replayed",
                })

        externally_resolved = set(approved_records)

        for ambiguity in plan.llm_ambiguities:
            # A content-addressed operator record can approve an exact
            # executable fragment. In that case the approved fragment is used
            # directly and the LLM is not allowed to propose a different one.
            if ambiguity.ambiguity_id in approved_records:
                continue
            if llm_attempts >= self.max_llm_resolutions and self.llm_client is not None:
                resolution_failures.append({
                    "ambiguity_id": ambiguity.ambiguity_id,
                    "code": "llm_resolution_limit",
                    "error_type": "LimitExceeded",
                })
                continue
            if self.llm_client is None:
                resolution_failures.append({
                    "ambiguity_id": ambiguity.ambiguity_id,
                    "code": "llm_provider_unavailable",
                    "error_type": "ProviderUnavailable",
                })
                continue
            llm_attempts += 1
            context = {
                "pipeline_id": meta.pipeline_id,
                "pipeline_name": meta.pipeline_name,
                "pipeline_type": meta.pipeline_type.value,
                "project": meta.project,
                "repository": meta.repo_name,
                "source_fingerprint": plan.source_fingerprint,
                "ruleset_version": plan.ruleset_version,
            }
            try:
                resolution = self.llm_client.resolve(ambiguity, context)
                if resolution is None:
                    resolution_failures.append({
                        "ambiguity_id": ambiguity.ambiguity_id,
                        "code": "llm_requested_manual_review",
                        "error_type": "ManualReview",
                    })
                    continue
                if resolution.ambiguity_id != ambiguity.ambiguity_id:
                    raise ValueError("LLM resolution id mismatch")
                if resolution.confidence < self.min_llm_confidence:
                    resolution_failures.append({
                        "ambiguity_id": ambiguity.ambiguity_id,
                        "code": "llm_low_confidence",
                        "error_type": "LowConfidence",
                    })
                    continue
                proposals[ambiguity.ambiguity_id] = resolution
                resolution_failures.append({
                    "ambiguity_id": ambiguity.ambiguity_id,
                    "code": "llm_proposal_requires_content_approval",
                    "error_type": "ProposalOnly",
                })
            except Exception as exc:
                # Never place exception messages in evidence: provider errors
                # can contain response fragments. The type + stable code is
                # sufficient for operations and avoids leaking source data.
                logger.warning(
                    "LLM pipeline resolution failed [%s/%s]: %s",
                    meta.pipeline_id, ambiguity.ambiguity_id, type(exc).__name__,
                )
                resolution_failures.append({
                    "ambiguity_id": ambiguity.ambiguity_id,
                    "code": "llm_resolution_error",
                    "error_type": type(exc).__name__,
                })

        executor = PipelineConversionExecutor(self.transformer)
        result = executor.execute(
            meta,
            plan,
            {},
            Path(output_dir),
            manual_approvals=approved_records,
        )
        report = self.validator.validate(
            result["workflow_file"], meta, plan, {}, externally_resolved,
        )
        # The validator reads the workflow from disk.  Snapshot it immediately
        # afterwards and bind the bytes to the validator's digest so a local
        # writer cannot replace a validated artifact before it is recorded or
        # published.  Downstream code consumes these approved bytes rather
        # than reopening the mutable path.
        workflow_file = Path(result["workflow_file"])
        approved_workflow_bytes = workflow_file.read_bytes()
        approved_workflow_sha256 = (
            "sha256:" + hashlib.sha256(approved_workflow_bytes).hexdigest()
        )
        if not report.workflow_digest or not hmac.compare_digest(
            approved_workflow_sha256, report.workflow_digest
        ):
            raise RuntimeError(
                "Generated workflow changed after deterministic validation"
            )

        if proposals:
            from ado2gh.pipelines.pev_types import FindingSeverity
            ambiguity_by_id = {
                item.ambiguity_id: item for item in plan.ambiguities
            }
            for ambiguity_id, proposal in proposals.items():
                ambiguity = ambiguity_by_id[ambiguity_id]
                if ambiguity.kind == "unsupported_condition":
                    code = "llm_condition_requires_content_approval"
                elif isinstance(proposal.replacement, Mapping) and proposal.replacement.get(
                    "run"
                ):
                    code = "llm_shell_requires_content_approval"
                else:
                    code = "llm_action_requires_content_approval"
                report.add(
                    code,
                    FindingSeverity.MANUAL_REVIEW,
                    "The LLM output is an untrusted proposal. Approve the exact "
                    "content-addressed fragment before it can enter workflow YAML.",
                    ambiguity_id,
                )

        if approval_failures:
            from ado2gh.pipelines.pev_types import FindingSeverity
            for failure in approval_failures:
                report.add(
                    failure["code"],
                    FindingSeverity.ERROR,
                    "A configured manual approval was rejected by the immutable approval boundary.",
                    failure.get("ambiguity_id", ""),
                )

        # Explicitly surface the configured LLM requirement. The unresolved
        # ambiguity already blocks production readiness; this finding makes the
        # operational cause clear in evidence without a silent fallback.
        if self.require_llm_for_ambiguity and plan.llm_ambiguities and self.llm_client is None:
            from ado2gh.pipelines.pev_types import FindingSeverity
            report.add(
                "required_llm_provider_missing",
                FindingSeverity.ERROR,
                "This conversion contains LLM-eligible ambiguities but no provider is configured.",
            )

        finished = datetime.now(timezone.utc)
        safe_name = result["workflow_file"].stem
        evidence_file = Path(output_dir) / f"{safe_name}_conversion_evidence.json"
        evidence = {
            "schema_version": 1,
            "attempt_id": str(uuid4()),
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "pipeline": {
                "id": meta.pipeline_id,
                "name": meta.pipeline_name,
                "project": meta.project,
                "repository": meta.repo_name,
                "type": meta.pipeline_type.value,
            },
            "plan": self._plan_evidence_summary(plan.to_dict()),
            "llm": {
                "used": llm_attempts > 0,
                "provider": getattr(self.llm_client, "provider_name", "") if self.llm_client else "",
                "model": getattr(self.llm_client, "model", "") if self.llm_client else "",
                "execution_settings_digest": getattr(
                    self.llm_client, "execution_settings_digest", ""
                ) if self.llm_client else "",
                "attempt_count": llm_attempts,
                "accepted_resolution_count": 0,
                "proposal_count": len(proposals),
                "proposals": [
                    {
                        "ambiguity_id": resolution.ambiguity_id,
                        "confidence": resolution.confidence,
                        "model": resolution.model,
                        "prompt_digest": resolution.prompt_digest,
                        "response_digest": resolution.response_digest,
                        "replacement": resolution.replacement,
                    }
                    for resolution in proposals.values()
                ],
                "failures": resolution_failures,
            },
            "manual_approvals": {
                "manifest_digest": (
                    self.manual_approval_manifest.manifest_digest
                    if self.manual_approval_manifest is not None else ""
                ),
                "accepted_count": len(approved_records),
                "accepted": [
                    approved_records[key].to_dict()
                    for key in sorted(approved_records)
                ],
                "failures": approval_failures,
            },
            "external_approval_count": len(externally_resolved),
            "workflow_file": str(result["workflow_file"]),
            "validation": report.to_dict(),
        }
        self._write_json_atomic(evidence_file, evidence)
        approved_evidence_bytes = evidence_file.read_bytes()
        approved_evidence_sha256 = (
            "sha256:" + hashlib.sha256(approved_evidence_bytes).hexdigest()
        )

        validation_dict = report.to_dict()
        result.update({
            "plan": plan.to_dict(),
            "validation": validation_dict,
            "production_ready": report.production_ready,
            "conversion_status": report.status,
            "evidence_file": evidence_file,
            "llm_used": llm_attempts > 0,
            "resolution_failures": resolution_failures,
            # Internal, in-memory immutable artifact handoff.  These fields
            # are intentionally omitted from durable JSON/SQLite payloads;
            # only their content digests are persisted there.
            "_approved_workflow_bytes": approved_workflow_bytes,
            "_approved_evidence_bytes": approved_evidence_bytes,
            "approved_workflow_sha256": approved_workflow_sha256,
            "approved_evidence_sha256": approved_evidence_sha256,
        })
        result.setdefault("stats", {})
        result["stats"].update({
            "plan_id": plan.plan_id,
            "source_fingerprint": plan.source_fingerprint,
            "ruleset_version": plan.ruleset_version,
            "mode": plan.mode.value,
            "llm_used": llm_attempts > 0,
            "llm_attempts": llm_attempts,
            "llm_resolutions": 0,
            "llm_proposals": len(proposals),
            "manual_approvals": len(approved_records),
            "approval_failures": len(approval_failures),
            "validation_status": report.status,
            "production_ready": report.production_ready,
            "evidence_file": str(evidence_file),
            "approved_workflow_sha256": approved_workflow_sha256,
            "approved_evidence_sha256": approved_evidence_sha256,
        })

        if not report.valid:
            raise PipelineValidationError(
                f"Pipeline '{meta.pipeline_name}' failed deterministic workflow validation; "
                f"evidence: {evidence_file}",
                report,
                evidence_file=str(evidence_file),
                result=result,
            )
        if self.require_production_ready and not report.production_ready:
            raise PipelineValidationError(
                f"Pipeline '{meta.pipeline_name}' requires review before production; "
                f"evidence: {evidence_file}",
                report,
                evidence_file=str(evidence_file),
                result=result,
            )
        return result

    @staticmethod
    def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + f".{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True),
                encoding="utf-8",
            )
            temporary.replace(path)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _plan_evidence_summary(plan: Mapping[str, Any]) -> dict[str, Any]:
        """Remove source snippets from the durable audit artifact.

        Operators retain locations, rationale, and stable digests without
        copying proprietary scripts or pipeline inputs into a second file.
        """
        summary = dict(plan)
        ambiguities = []
        for raw in plan.get("ambiguities", []) or []:
            item = dict(raw)
            source = item.pop("source", None)
            item["source_digest"] = hashlib.sha256(
                json.dumps(source, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
            ambiguities.append(item)
        summary["ambiguities"] = ambiguities
        return summary


def create_enterprise_pipeline_transformer_from_env(
    *,
    external_approval: Optional[ExternalApproval] = None,
    config: Optional[Mapping[str, Any]] = None,
    governance_config: Optional[Mapping[str, Any]] = None,
    governance_plan_id: str = "",
    governance_run_id: str = "",
    governance_db: Any = None,
) -> Any:
    """Factory for MigrationEngine's strict, environment-configured converter."""

    from ado2gh.pipelines.transformer import (
        ENTERPRISE_ACTION_PINS,
        PipelineTransformer,
    )
    from ado2gh.pipelines.validator import (
        PipelineValidationPolicy,
        PipelineWorkflowValidator,
    )

    options = dict(config or {})
    client = create_pipeline_llm_client_from_env(
        required=False,
        config=options,
    )
    approval_config = options.get("manual_approval_manifest")
    approval_manifest = (
        ManualApprovalManifest.from_config(approval_config)
        if approval_config is not None else None
    )
    governance = normalize_governance_settings(governance_config)
    governance_verifier = SignedApprovalVerifier(governance)
    approval_envelope_env = options.get(
        "manual_approval_governance_envelope_env"
    )
    if approval_envelope_env is not None and approval_manifest is None:
        raise ApprovalManifestError(
            "manual approval governance envelope reference requires a manual "
            "approval manifest"
        )
    if governance_verifier.strict:
        if external_approval is not None:
            raise ApprovalManifestError(
                "strict governance prohibits unsigned dynamic approval callbacks; "
                "use a signed manual approval manifest"
            )
        if approval_manifest is not None and approval_manifest.approvals:
            if not governance_plan_id or not governance_run_id or governance_db is None:
                raise ApprovalManifestError(
                    "strict manual approvals require an exact PEV plan/run and StateDB"
                )
            if not isinstance(approval_envelope_env, str):
                raise ApprovalManifestError(
                    "strict manual approvals require a plan-bound envelope env reference"
                )
            encoded_envelope = os.environ.get(approval_envelope_env, "")
            if not encoded_envelope or len(encoded_envelope.encode("utf-8")) > 256 * 1024:
                raise ApprovalManifestError(
                    "manual approval governance envelope environment value is "
                    "missing or exceeds 256 KiB"
                )
            try:
                approval_envelope = json.loads(encoded_envelope)
            except json.JSONDecodeError as exc:
                raise ApprovalManifestError(
                    "manual approval governance envelope is not valid JSON"
                ) from exc
            resource = pipeline_manifest_resource(approval_manifest)
            verified = governance_verifier.verify(
                approval_envelope,
                action=PIPELINE_MANUAL_MAPPING_ACTION,
                resource=resource,
                plan_id=governance_plan_id,
                run_id=governance_run_id,
            )
            assert verified is not None
            governance_db.ensure_pev_governance_approval(
                verified.to_evidence(),
                action=PIPELINE_MANUAL_MAPPING_ACTION,
                resource_digest=governance_resource_digest(resource),
                plan_id=governance_plan_id,
                run_id=governance_run_id,
            )
    attestation_config = options.get("credential_attestation")
    credential_attestation_verifier = (
        CredentialAttestationVerifier.from_config(attestation_config)
        if attestation_config is not None else None
    )
    action_pins = dict(ENTERPRISE_ACTION_PINS)
    action_pins.update(dict(options.get("action_pins", {})))
    allowed_actions = sorted({
        str(action).split("@", 1)[0]
        for action in action_pins
    })
    policy = PipelineValidationPolicy(
        require_permissions=bool(options.get("require_permissions", True)),
        require_pinned_action_sha=bool(
            options.get("require_pinned_action_sha", True)
        ),
        forbid_remote_script_execution=bool(
            options.get("forbid_remote_script_execution", True)
        ),
        max_workflow_bytes=int(options.get("max_workflow_bytes", 1_000_000)),
        max_jobs=int(options.get("max_jobs", 256)),
        max_steps_per_job=int(options.get("max_steps_per_job", 1_000)),
        allowed_actions=tuple(allowed_actions),
    )
    return PipelineTransformer(
        validator=PipelineWorkflowValidator(policy),
        llm_client=client,
        min_llm_confidence=float(options.get("min_llm_confidence", 0.75)),
        max_llm_resolutions=int(options.get("max_llm_resolutions", 32)),
        require_llm_for_ambiguity=True,
        require_production_ready=True,
        manual_approval_manifest=approval_manifest,
        external_approval=external_approval,
        credential_attestation_verifier=credential_attestation_verifier,
        include_pipeline_identity_in_filename=True,
        action_pins=action_pins,
        review_staging_branch=str(
            options.get(
                "review_staging_branch", "ado2gh/migrated-workflows"
            )
        ),
    )
