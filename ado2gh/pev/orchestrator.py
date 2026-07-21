"""Top-level Planner–Executor–Validator control loop."""
from __future__ import annotations

import re
from typing import Any, Optional

from ado2gh.governance import (
    PLAN_EXECUTION_ACTION,
    SignedApprovalVerifier,
    assert_plan_creation_approval,
    governance_resource_digest,
    governance_is_strict,
    normalize_governance_settings,
    plan_execution_resource,
)
from ado2gh.models import MigrationStatus
from ado2gh.pev.contracts import MigrationPlan, OrchestrationResult
from ado2gh.pev.executor import PEVExecutor, plan_wave_id
from ado2gh.pev.validator import PEVValidator


class MigrationOrchestrator:
    """Run an approved plan and feed deterministic validation failures back.

    Repairs are bounded and limited to scopes whose deterministic validator can
    identify a safe replay.  Ambiguity, policy, secret, and review failures are
    never auto-approved or auto-merged.
    """

    def __init__(self, global_cfg: dict[str, Any], ado: Any, gh: Any, db: Any):
        self.cfg = dict(global_cfg)
        self.ado = ado
        self.gh = gh
        self.db = db
        self.executor = PEVExecutor(self.cfg, ado, gh, db)
        self.validator = PEVValidator(ado, gh, db, self.cfg)

    def run(
        self,
        plan: MigrationPlan,
        approved_plan_id: str = "",
        dry_run: bool = False,
        run_id: Optional[str] = None,
        output_path: Optional[str] = None,
        max_repair_attempts: Optional[int] = None,
        approval_envelope: Optional[dict[str, Any]] = None,
    ) -> OrchestrationResult:
        verified_approval = None
        effective_run_id = run_id
        if not dry_run:
            configured_governance = normalize_governance_settings(
                self.cfg.get("governance")
            )
            planned_governance = normalize_governance_settings(
                plan.policy.get("governance")
            )
            if configured_governance != planned_governance:
                raise PermissionError(
                    "Runtime governance policy does not match the immutable plan"
                )
            if governance_is_strict(planned_governance):
                assert_plan_creation_approval(plan, planned_governance, self.db)
                if not effective_run_id:
                    claims = (
                        approval_envelope.get("claims")
                        if isinstance(approval_envelope, dict) else None
                    )
                    signed_run_id = (
                        str(claims.get("run_id", ""))
                        if isinstance(claims, dict) else ""
                    )
                    if not re.fullmatch(r"run_[A-Za-z0-9._-]{8,120}", signed_run_id):
                        raise PermissionError(
                            "strict plan execution approval must assign a new safe run_id"
                        )
                    if self.db.get_pev_run(signed_run_id):
                        raise PermissionError(
                            "signed run_id already exists; pass it explicitly as a resume"
                        )
                    effective_run_id = signed_run_id
            verifier = SignedApprovalVerifier(planned_governance)
            resource = plan_execution_resource(plan, effective_run_id or "")
            verified_approval = verifier.verify(
                approval_envelope,
                action=PLAN_EXECUTION_ACTION,
                resource=resource,
                plan_id=plan.plan_id,
                run_id=effective_run_id or "",
            )
            if verified_approval is not None:
                evidence = verified_approval.to_evidence()
                self.db.claim_pev_governance_approval(
                    evidence,
                    action=PLAN_EXECUTION_ACTION,
                    resource_digest=governance_resource_digest(resource),
                    plan_id=plan.plan_id,
                    run_id=effective_run_id or "",
                )
                if not self.db.get_pev_run(effective_run_id):
                    self.db.create_pev_run(
                        plan.plan_id,
                        config_digest=plan.config_digest,
                        status="planned",
                        run_id=effective_run_id,
                    )
        execution = self.executor.execute(
            plan,
            approved_plan_id=approved_plan_id,
            dry_run=dry_run,
            run_id=effective_run_id,
        )
        if verified_approval is not None:
            self.db.record_validation_evidence(
                execution.run_id,
                "signed_plan_execution_approval",
                "pass",
                evidence=verified_approval.to_evidence(),
            )
        if dry_run:
            return OrchestrationResult(
                plan_id=plan.plan_id,
                run_id=execution.run_id,
                status=execution.status,
                execution=execution,
            )

        validation = self.validator.validate(
            plan, execution.run_id, output_path=output_path
        )
        configured_attempts = int(
            self.cfg.get("validation", {}).get("max_repair_attempts", 1)
        )
        repair_limit = (
            configured_attempts if max_repair_attempts is None
            else max(0, max_repair_attempts)
        )
        repair_attempts = 0

        while validation.status == "failed" and repair_attempts < repair_limit:
            repairable = [
                item for item in validation.failures if item.get("retryable")
            ]
            if not repairable or any(
                not item.get("retryable") for item in validation.failures
            ):
                break
            self._invalidate_scopes(plan, repairable)
            repair_attempts += 1
            execution = self.executor.execute(
                plan,
                approved_plan_id=approved_plan_id,
                dry_run=False,
                run_id=execution.run_id,
                repair=True,
            )
            validation = self.validator.validate(
                plan, execution.run_id, output_path=output_path
            )

        status = {
            "passed": "completed",
            "needs_review": "needs_review",
            "failed": "failed",
        }[validation.status]
        self.db.upsert_pev_run(
            execution.run_id,
            plan.plan_id,
            status=status,
            config_digest=plan.config_digest,
            summary={
                "status": status,
                "repair_attempts": repair_attempts,
                "validation_failures": len(validation.failures),
                "validation_warnings": len(validation.warnings),
            },
        )
        return OrchestrationResult(
            plan_id=plan.plan_id,
            run_id=execution.run_id,
            status=status,
            execution=execution,
            validation=validation,
            repair_attempts=repair_attempts,
        )

    def _invalidate_scopes(
        self, plan: MigrationPlan, failures: list[dict[str, Any]]
    ) -> None:
        repos = {repo.source_key: repo for repo in plan.repositories}
        invalidated: set[tuple[str, str]] = set()
        wave_id = plan_wave_id(plan)
        for failure in failures:
            source_key = str(failure.get("source_key", ""))
            scope = str(failure.get("scope", ""))
            if not scope or source_key not in repos:
                continue
            key = (source_key, scope)
            if key in invalidated:
                continue
            self.db.upsert_migration(
                wave_id,
                repos[source_key].to_repo_config(),
                scope,
                MigrationStatus.FAILED,
                error=(
                    "Invalidated by PEV validator for bounded repair: "
                    + str(failure.get("detail", ""))
                ),
            )
            invalidated.add(key)
