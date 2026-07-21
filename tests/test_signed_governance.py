from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Optional

import pytest
import yaml

from ado2gh.core.ado_cleanup import ADOCleanup, authorize_cleanup_capability
from ado2gh.core.config_loader import ConfigLoader
from ado2gh.governance import (
    ADO_CLEANUP_ACTION,
    APPROVAL_CLAIMS_SCHEMA,
    APPROVAL_ENVELOPE_SCHEMA,
    PIPELINE_MANUAL_MAPPING_ACTION,
    PLAN_CREATION_ACTION,
    PLAN_EXECUTION_ACTION,
    GovernanceError,
    SignedApprovalVerifier,
    canonical_json,
    authorize_plan_creation,
    cleanup_resource,
    governance_resource_digest,
    governance_policy_digest,
    pipeline_manifest_resource,
    plan_execution_resource,
)
from ado2gh.models import PipelineMetadata, PipelineStage, PipelineType
from ado2gh.pev.orchestrator import MigrationOrchestrator
from ado2gh.pipelines.approvals import ManualApprovalManifest, ManualApprovalRecord
from ado2gh.pipelines.pev import create_enterprise_pipeline_transformer_from_env
from ado2gh.pipelines.planner import PipelineConversionPlanner
from ado2gh.state.db import StateDB


NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


@pytest.fixture
def signing(monkeypatch):
    cryptography = pytest.importorskip("cryptography")
    del cryptography
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    identities = {
        "planner-key": (
            "ADO2GH_PLANNER_PUBLIC_KEY", "planner@example.com", "plan-author",
        ),
        "executor-key": (
            "ADO2GH_EXECUTOR_PUBLIC_KEY", "executor@example.com",
            "migration-approver",
        ),
        "cleanup-key": (
            "ADO2GH_CLEANUP_PUBLIC_KEY", "cleanup@example.com",
            "cleanup-approver",
        ),
        "pipeline-key": (
            "ADO2GH_PIPELINE_PUBLIC_KEY", "pipeline@example.com",
            "pipeline-approver",
        ),
    }
    private_keys = {}
    trusted_keys = {}
    for key_id, (env_name, subject, role) in identities.items():
        private_key = Ed25519PrivateKey.generate()
        private_keys[key_id] = private_key
        public_raw = private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        monkeypatch.setenv(env_name, _b64(public_raw))
        trusted_keys[key_id] = {
            "public_key_env": env_name,
            "public_key_sha256": "sha256:" + hashlib.sha256(public_raw).hexdigest(),
            "allowed_subjects": [subject],
            "allowed_roles": [role],
        }
    policy = {
        "mode": "strict",
        "audience": "ado2gh-production",
        "planner_subject": "planner@example.com",
        "planner_key_id": "planner-key",
        "trusted_keys": trusted_keys,
        "required_roles": {
            "ado_cleanup": "cleanup-approver",
            "pipeline_manual_mapping": "pipeline-approver",
            "plan_creation": "plan-author",
            "plan_execution": "migration-approver",
        },
        "require_planner_executor_separation": True,
        "require_signed_pipeline_approvals": True,
        "revoked_nonces": [],
        "max_age_seconds": 300,
        "max_ttl_seconds": 900,
        "clock_skew_seconds": 0,
    }
    monkeypatch.setenv(
        "ADO2GH_GOVERNANCE_POLICY_SHA256", governance_policy_digest(policy)
    )
    return private_keys, policy


def _envelope(
    private_keys,
    policy,
    *,
    action: str,
    resource,
    subject: str,
    roles: list[str],
    nonce: str = "nonce-0001",
    ticket: str = "CHG-4100",
    plan_id: str = "",
    run_id: str = "",
    issued_at: datetime = NOW,
    expires_at: Optional[datetime] = None,
):
    key_id = {
        PLAN_CREATION_ACTION: "planner-key",
        PLAN_EXECUTION_ACTION: "executor-key",
        ADO_CLEANUP_ACTION: "cleanup-key",
        PIPELINE_MANUAL_MAPPING_ACTION: "pipeline-key",
    }[action]
    expires_at = expires_at or issued_at + timedelta(minutes=5)
    claims = {
        "schema": APPROVAL_CLAIMS_SCHEMA,
        "audience": policy["audience"],
        "action": action,
        "resource_digest": governance_resource_digest(resource),
        "plan_id": plan_id,
        "run_id": run_id,
        "subject": subject,
        "roles": roles,
        "issued_at": issued_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "nonce": nonce,
        "ticket": ticket,
    }
    signed = {
        "schema": APPROVAL_ENVELOPE_SCHEMA,
        "key_id": key_id,
        "claims": claims,
    }
    return {**signed, "signature": _b64(private_keys[key_id].sign(
        canonical_json(signed).encode("utf-8")
    ))}


def _verify_plan(signing, **changes):
    private_key, policy = signing
    plan = SimpleNamespace(plan_id="plan_exact", config_digest="f" * 64)
    resource = plan_execution_resource(plan)
    values = {
        "action": PLAN_EXECUTION_ACTION,
        "resource": resource,
        "subject": "executor@example.com",
        "roles": ["migration-approver"],
        "plan_id": plan.plan_id,
    }
    values.update(changes)
    envelope = _envelope(private_key, policy, **values)
    return SignedApprovalVerifier(policy), envelope, resource, plan


def test_signed_approval_accepts_exact_identity_role_and_resource(signing):
    verifier, envelope, resource, plan = _verify_plan(signing)
    result = verifier.verify(
        envelope,
        action=PLAN_EXECUTION_ACTION,
        resource=resource,
        plan_id=plan.plan_id,
        now=NOW,
    )
    assert result.claims["subject"] == "executor@example.com"
    assert result.key_fingerprint.startswith("sha256:")


def test_signed_approval_rejects_forgery(signing):
    verifier, envelope, resource, plan = _verify_plan(signing)
    envelope["signature"] = _b64(b"x" * 64)
    with pytest.raises(GovernanceError, match="signature verification"):
        verifier.verify(
            envelope, action=PLAN_EXECUTION_ACTION, resource=resource,
            plan_id=plan.plan_id, now=NOW,
        )


def test_signed_approval_rejects_expired_envelope(signing):
    verifier, envelope, resource, plan = _verify_plan(
        signing,
        issued_at=NOW - timedelta(minutes=20),
        expires_at=NOW - timedelta(minutes=15),
    )
    with pytest.raises(GovernanceError, match="older|expired"):
        verifier.verify(
            envelope, action=PLAN_EXECUTION_ACTION, resource=resource,
            plan_id=plan.plan_id, now=NOW,
        )


@pytest.mark.parametrize("case", ["action", "resource", "role"])
def test_signed_approval_rejects_wrong_authority(signing, case):
    private_key, policy = signing
    plan = SimpleNamespace(plan_id="plan_exact", config_digest="f" * 64)
    resource = plan_execution_resource(plan)
    envelope = _envelope(
        private_key,
        policy,
        action=(ADO_CLEANUP_ACTION if case == "action" else PLAN_EXECUTION_ACTION),
        resource=({"different": True} if case == "resource" else resource),
        subject="executor@example.com",
        roles=(["cleanup-approver"] if case == "role" else ["migration-approver"]),
        plan_id=plan.plan_id,
    )
    with pytest.raises(GovernanceError):
        SignedApprovalVerifier(policy).verify(
            envelope,
            action=PLAN_EXECUTION_ACTION,
            resource=resource,
            plan_id=plan.plan_id,
            now=NOW,
        )


def test_signed_approval_rejects_revoked_nonce(signing, monkeypatch):
    verifier, envelope, resource, plan = _verify_plan(signing)
    policy = {**verifier.policy, "revoked_nonces": ["nonce-0001"]}
    monkeypatch.setenv(
        "ADO2GH_GOVERNANCE_POLICY_SHA256", governance_policy_digest(policy)
    )
    with pytest.raises(GovernanceError, match="revoked"):
        SignedApprovalVerifier(policy).verify(
            envelope, action=PLAN_EXECUTION_ACTION, resource=resource,
            plan_id=plan.plan_id, now=NOW,
        )


def test_signed_approval_enforces_planner_executor_separation(signing):
    verifier, envelope, resource, plan = _verify_plan(
        signing, subject="planner@example.com"
    )
    with pytest.raises(GovernanceError, match="subject is not authorized"):
        verifier.verify(
            envelope, action=PLAN_EXECUTION_ACTION, resource=resource,
            plan_id=plan.plan_id, now=NOW,
        )


def test_config_loader_normalizes_strict_governance_and_rejects_dormant_policy(
    signing, tmp_path,
):
    _, policy = signing
    path = tmp_path / "migration.yaml"
    path.write_text(
        yaml.safe_dump({"global": {"gh_org": "octo", "governance": policy}}),
        encoding="utf-8",
    )
    global_cfg, waves = ConfigLoader.load(str(path))
    assert waves == []
    assert global_cfg["governance"]["mode"] == "strict"
    assert (
        global_cfg["governance"]["trusted_keys"]
        ["planner-key"]["public_key_env"]
        == "ADO2GH_PLANNER_PUBLIC_KEY"
    )

    path.write_text(
        yaml.safe_dump({
            "global": {
                "governance": {"mode": "disabled", "audience": "shadow-policy"},
            }
        }),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="dormant trust"):
        ConfigLoader.load(str(path))


def test_migration_yaml_cannot_appoint_an_unpinned_trust_root(signing):
    _, policy = signing
    attacker_policy = {**policy, "audience": "attacker-controlled"}
    with pytest.raises(GovernanceError, match="not trusted by this deployment"):
        SignedApprovalVerifier(attacker_policy).verify(
            {}, action=PLAN_EXECUTION_ACTION, resource={}, plan_id="plan_x"
        )


def test_required_sod_role_domains_must_be_distinct(signing):
    _, policy = signing
    bad_roles = dict(policy["required_roles"])
    bad_roles["plan_execution"] = bad_roles["plan_creation"]
    with pytest.raises(ValueError, match="required roles must be distinct"):
        SignedApprovalVerifier({**policy, "required_roles": bad_roles})


def test_orchestrator_api_requires_and_consumes_strict_approval(signing):
    private_key, policy = signing
    db = StateDB(":memory:")
    plan = SimpleNamespace(
        plan_id="plan_orchestrated",
        config_digest="d" * 64,
        source_org_url="https://dev.azure.com/example",
        target_org="octo",
        policy={"governance": policy},
    )
    planner_envelope = _envelope(
        private_key,
        policy,
        action=PLAN_CREATION_ACTION,
        resource={
            "schema": "ado2gh.governance-resource/plan-creation-v1",
            "plan_id": plan.plan_id,
            "config_digest": plan.config_digest,
            "source_org_url": plan.source_org_url,
            "target_org": plan.target_org,
        },
        subject="planner@example.com",
        roles=["plan-author"],
        plan_id=plan.plan_id,
        nonce="planner-nonce",
    )
    authorize_plan_creation(plan, policy, planner_envelope, db)
    [planner_record] = db.get_pev_governance_approvals(
        plan_id=plan.plan_id, action=PLAN_CREATION_ACTION, run_id=""
    )
    assert planner_record["key_id"] == "planner-key"
    assert planner_record["subject"] == "planner@example.com"
    resource = plan_execution_resource(plan, "run_governed01")
    envelope = _envelope(
        private_key,
        policy,
        action=PLAN_EXECUTION_ACTION,
        resource=resource,
        subject="executor@example.com",
        roles=["migration-approver"],
        plan_id=plan.plan_id,
        run_id="run_governed01",
        nonce="orchestrator-nonce",
    )
    orchestrator = MigrationOrchestrator({"governance": policy}, object(), object(), db)

    class Executor:
        def execute(self, plan, **kwargs):
            db.upsert_pev_run(
                kwargs["run_id"], plan.plan_id, status="executed",
                config_digest=plan.config_digest,
            )
            return SimpleNamespace(run_id=kwargs["run_id"], status="executed")

    class Validator:
        def validate(self, plan, run_id, **kwargs):
            return SimpleNamespace(
                status="passed", failures=[], warnings=[], passed=True,
            )

    orchestrator.executor = Executor()
    orchestrator.validator = Validator()
    result = orchestrator.run(
        plan,
        approved_plan_id=plan.plan_id,
        approval_envelope=envelope,
        max_repair_attempts=0,
    )
    assert result.status == "completed"
    evidence = db.list_validation_evidence("run_governed01")
    assert any(row["category"] == "signed_plan_execution_approval" for row in evidence)
    with pytest.raises(PermissionError, match="assign a new safe run_id"):
        orchestrator.run(plan, approved_plan_id=plan.plan_id, max_repair_attempts=0)
    with pytest.raises(PermissionError, match="already been consumed"):
        orchestrator.run(
            plan, approved_plan_id=plan.plan_id,
            approval_envelope=envelope, run_id="run_governed01",
            max_repair_attempts=0,
        )


def test_cleanup_authorization_api_persists_signed_nonreplayable_evidence(signing):
    private_key, policy = signing
    plan = SimpleNamespace(
        plan_id="plan_cleanup",
        policy={"governance": policy},
        validate=lambda: None,
        repositories=(),
        tasks=(),
    )
    run_id = "run-cleanup"
    request = {
        "schema": "ado2gh.destructive-operation/ado-cleanup-v1",
        "operation_kind": ADO_CLEANUP_ACTION,
        "plan_id": plan.plan_id,
        "run_id": run_id,
        "repositories": [{"source_key": "Payments/api"}],
    }
    envelope = _envelope(
        private_key,
        policy,
        action=ADO_CLEANUP_ACTION,
        resource=cleanup_resource(request),
        subject="cleanup@example.com",
        roles=["cleanup-approver"],
        nonce="cleanup-nonce",
        ticket="CHG-5000",
        plan_id=plan.plan_id,
        run_id=run_id,
    )
    db = StateDB(":memory:")
    db.upsert_pev_run(run_id, plan.plan_id, status="completed")
    db.register_pev_plan_capabilities(plan.plan_id, [{
        "source_key": "Payments/api", "gh_org": "octo", "gh_repo": "api",
        "scope": "repo", "input_digest": "a" * 64,
    }])
    capability, approval = authorize_cleanup_capability(
        db, plan, run_id, request,
        approval_envelope=envelope,
        expected_ticket="CHG-5000",
    )
    assert capability.startswith("destructive_")
    assert approval["signed_approval"]["claims"]["subject"] == "cleanup@example.com"
    service = ADOCleanup(
        object(), db, gh=object(), approved_plan=plan,
        approved_run_id=run_id, destructive_capability_id=capability,
        destructive_request=request,
    )
    service._authenticate_governance_capability()
    with pytest.raises(PermissionError, match="already been consumed"):
        authorize_cleanup_capability(
            db, plan, run_id, request,
            approval_envelope=envelope,
            expected_ticket="CHG-5000",
        )


def _manual_manifest() -> ManualApprovalManifest:
    meta = PipelineMetadata(
        pipeline_id=45,
        pipeline_name="Proprietary compiler",
        pipeline_type=PipelineType.YAML,
        project="Payments",
        repo_name="orders-api",
        trigger_branches=["main"],
        stages=[PipelineStage(
            name="build",
            jobs=[{"job": "build", "steps": [{
                "task": "ContosoCompiler@1", "inputs": {"target": "release"},
            }]}],
        )],
    )
    plan = PipelineConversionPlanner().plan(meta)
    ambiguity = next(item for item in plan.llm_ambiguities if item.kind == "unknown_task")
    record = ManualApprovalRecord.create(
        ambiguity_id=ambiguity.ambiguity_id,
        source_fingerprint=plan.source_fingerprint,
        project=meta.project,
        repository=meta.repo_name,
        pipeline_id=meta.pipeline_id,
        pipeline_type=meta.pipeline_type.value,
        approver="pipeline@example.com",
        ticket="CHG-6000",
        approved_at="2026-07-21T14:55:00Z",
        target_mapping={
            "type": "workflow_step",
            "step": {"name": "Compile", "run": "contoso-compiler --target release"},
        },
        evidence=[{
            "type": "change_record", "reference": "servicenow:CHG-6000",
            "digest": "sha256:" + "c" * 64,
        }],
    )
    return ManualApprovalManifest([record])


def test_strict_pipeline_factory_rejects_unsigned_manual_mapping(signing):
    _, policy = signing
    manifest = _manual_manifest()
    db = StateDB(":memory:")
    with pytest.raises(Exception, match="environment value is missing"):
        create_enterprise_pipeline_transformer_from_env(
            config={
                "manual_approval_manifest": manifest.to_dict(),
                "manual_approval_governance_envelope_env": "PIPELINE_APPROVAL",
            },
            governance_config=policy,
            governance_plan_id="plan_pipeline",
            governance_run_id="run_pipeline01",
            governance_db=db,
        )


def test_strict_pipeline_factory_accepts_exact_signed_manifest(signing, monkeypatch):
    private_key, policy = signing
    manifest = _manual_manifest()
    envelope = _envelope(
        private_key,
        policy,
        action=PIPELINE_MANUAL_MAPPING_ACTION,
        resource=pipeline_manifest_resource(manifest),
        subject="pipeline@example.com",
        roles=["pipeline-approver"],
        nonce="pipeline-manifest-nonce",
        ticket="CHG-6000",
        plan_id="plan_pipeline",
        run_id="run_pipeline01",
    )
    monkeypatch.setenv("PIPELINE_APPROVAL", json.dumps(envelope))
    db = StateDB(":memory:")
    transformer = create_enterprise_pipeline_transformer_from_env(
        config={
            "manual_approval_manifest": manifest.to_dict(),
            "manual_approval_governance_envelope_env": "PIPELINE_APPROVAL",
        },
        governance_config=policy,
        governance_plan_id="plan_pipeline",
        governance_run_id="run_pipeline01",
        governance_db=db,
    )
    assert transformer is not None
    # Thread-local factories in the same execution may reuse the exact
    # declarative authority, but a different plan or run cannot.
    assert create_enterprise_pipeline_transformer_from_env(
        config={
            "manual_approval_manifest": manifest.to_dict(),
            "manual_approval_governance_envelope_env": "PIPELINE_APPROVAL",
        },
        governance_config=policy,
        governance_plan_id="plan_pipeline",
        governance_run_id="run_pipeline01",
        governance_db=db,
    ) is not None
    with pytest.raises(GovernanceError, match="plan_id does not match"):
        create_enterprise_pipeline_transformer_from_env(
            config={
                "manual_approval_manifest": manifest.to_dict(),
                "manual_approval_governance_envelope_env": "PIPELINE_APPROVAL",
            },
            governance_config=policy,
            governance_plan_id="plan_other",
            governance_run_id="run_pipeline01",
            governance_db=db,
        )
