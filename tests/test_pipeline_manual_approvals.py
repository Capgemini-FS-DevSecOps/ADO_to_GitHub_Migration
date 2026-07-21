from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pytest
import yaml

from ado2gh.core.config_loader import ConfigLoader
from ado2gh.models import PipelineMetadata, PipelineStage, PipelineType, RepoConfig
from ado2gh.pev.planner import MigrationPlanner
from ado2gh.pipelines.approvals import (
    ApprovalManifestError,
    ManualApprovalManifest,
    ManualApprovalRecord,
)
from ado2gh.pipelines.pev import create_enterprise_pipeline_transformer_from_env
from ado2gh.pipelines.pev_types import PipelineValidationError
from ado2gh.pipelines.planner import PipelineConversionPlanner
from ado2gh.pipelines.transformer import PipelineTransformer


def _runner_pipeline() -> PipelineMetadata:
    return PipelineMetadata(
        pipeline_id=44,
        pipeline_name="Private pool build",
        pipeline_type=PipelineType.YAML,
        project="Payments",
        repo_name="orders-api",
        trigger_branches=["main"],
        stages=[PipelineStage(
            name="build",
            agent_pool="ADO-Private-Linux",
            jobs=[{
                "job": "build",
                "steps": [{"bash": "python -m pytest", "displayName": "Test"}],
            }],
        )],
    )


def _ambiguous_task_pipeline() -> PipelineMetadata:
    return PipelineMetadata(
        pipeline_id=45,
        pipeline_name="Proprietary compiler",
        pipeline_type=PipelineType.YAML,
        project="Payments",
        repo_name="orders-api",
        trigger_branches=["main"],
        stages=[PipelineStage(
            name="build",
            jobs=[{
                "job": "build",
                "steps": [{"task": "ContosoCompiler@1", "inputs": {"target": "release"}}],
            }],
        )],
    )


def _ambiguous_condition_pipeline() -> PipelineMetadata:
    return PipelineMetadata(
        pipeline_id=46,
        pipeline_name="Conditional build",
        pipeline_type=PipelineType.YAML,
        project="Payments",
        repo_name="orders-api",
        trigger_branches=["main"],
        stages=[PipelineStage(
            name="build",
            condition="customAdoPredicate()",
            jobs=[{
                "job": "build",
                "steps": [{"bash": "python -m pytest"}],
            }],
        )],
    )


def _approval(
    meta: PipelineMetadata,
    *,
    fingerprint: Optional[str] = None,
    ticket: str = "CHG-4821",
    runner: str = "arc-linux-production",
) -> ManualApprovalRecord:
    plan = PipelineConversionPlanner().plan(meta)
    ambiguity = next(item for item in plan.manual_ambiguities if item.kind == "runner_mapping")
    return ManualApprovalRecord.create(
        ambiguity_id=ambiguity.ambiguity_id,
        source_fingerprint=fingerprint or plan.source_fingerprint,
        project=meta.project,
        repository=meta.repo_name,
        pipeline_id=meta.pipeline_id,
        pipeline_type=meta.pipeline_type.value,
        approver="jane.operator@example.com",
        ticket=ticket,
        approved_at="2026-07-21T09:00:00Z",
        target_mapping={"type": "runner", "runner_label": runner},
        evidence=[{
            "type": "change_record",
            "reference": f"servicenow:{ticket}",
            "digest": "sha256:" + "a" * 64,
        }],
    )


def test_free_form_runner_label_cannot_clear_enterprise_trust_boundary(tmp_path):
    meta = _runner_pipeline()
    record = _approval(meta)
    manifest = ManualApprovalManifest([record])

    with pytest.raises(PipelineValidationError) as raised:
        PipelineTransformer(
            manual_approval_manifest=manifest,
            require_production_ready=True,
        ).transform(meta, tmp_path)

    evidence = json.loads(Path(raised.value.evidence_file).read_text(encoding="utf-8"))
    assert record.approval_id.startswith("approval-sha256:")
    assert evidence["manual_approvals"]["manifest_digest"] == manifest.manifest_digest
    assert evidence["manual_approvals"]["accepted"] == []
    assert evidence["manual_approvals"]["failures"][0]["code"] == (
        "approval_target_mapping_incompatible"
    )


def test_content_addressed_approval_can_authorize_exact_llm_eligible_shell(tmp_path):
    meta = _ambiguous_task_pipeline()
    plan = PipelineConversionPlanner().plan(meta)
    ambiguity = next(item for item in plan.llm_ambiguities if item.kind == "unknown_task")
    record = ManualApprovalRecord.create(
        ambiguity_id=ambiguity.ambiguity_id,
        source_fingerprint=plan.source_fingerprint,
        project=meta.project,
        repository=meta.repo_name,
        pipeline_id=meta.pipeline_id,
        pipeline_type=meta.pipeline_type.value,
        approver="jane.operator@example.com",
        ticket="CHG-4900",
        approved_at="2026-07-21T09:00:00Z",
        target_mapping={
            "type": "workflow_step",
            "step": {"name": "Compile", "run": "contoso-compiler --target release"},
        },
        evidence=[{
            "type": "change_record",
            "reference": "servicenow:CHG-4900",
            "digest": "sha256:" + "c" * 64,
        }],
    )

    result = PipelineTransformer(
        manual_approval_manifest=ManualApprovalManifest([record]),
        require_production_ready=True,
    ).transform(meta, tmp_path)

    assert result["production_ready"] is True
    assert result["llm_used"] is False
    assert "contoso-compiler --target release" in result["workflow_file"].read_text(
        encoding="utf-8"
    )


def test_content_addressed_approval_can_authorize_exact_condition(tmp_path):
    meta = _ambiguous_condition_pipeline()
    plan = PipelineConversionPlanner().plan(meta)
    ambiguity = next(
        item for item in plan.llm_ambiguities
        if item.kind == "unsupported_condition"
    )
    record = ManualApprovalRecord.create(
        ambiguity_id=ambiguity.ambiguity_id,
        source_fingerprint=plan.source_fingerprint,
        project=meta.project,
        repository=meta.repo_name,
        pipeline_id=meta.pipeline_id,
        pipeline_type=meta.pipeline_type.value,
        approver="jane.operator@example.com",
        ticket="CHG-4903",
        approved_at="2026-07-21T09:00:00Z",
        target_mapping={
            "type": "workflow_condition",
            "condition": "github.ref == 'refs/heads/main'",
        },
        evidence=[{
            "type": "change_record",
            "reference": "servicenow:CHG-4903",
            "digest": "sha256:" + "f" * 64,
        }],
    )

    result = PipelineTransformer(
        manual_approval_manifest=ManualApprovalManifest([record]),
        require_production_ready=True,
    ).transform(meta, tmp_path)

    assert result["production_ready"] is True
    assert result["llm_used"] is False
    workflow = yaml.safe_load(result["workflow_file"].read_text(encoding="utf-8"))
    assert "github.ref == 'refs/heads/main'" in workflow["jobs"]["build"]["if"]


def test_approval_manifest_rejects_inline_credentials():
    meta = _ambiguous_task_pipeline()
    plan = PipelineConversionPlanner().plan(meta)
    ambiguity = next(item for item in plan.llm_ambiguities)
    with pytest.raises(ApprovalManifestError, match="inline credential"):
        ManualApprovalRecord.create(
            ambiguity_id=ambiguity.ambiguity_id,
            source_fingerprint=plan.source_fingerprint,
            project=meta.project,
            repository=meta.repo_name,
            pipeline_id=meta.pipeline_id,
            pipeline_type=meta.pipeline_type.value,
            approver="jane.operator@example.com",
            ticket="CHG-4901",
            approved_at="2026-07-21T09:00:00Z",
            target_mapping={
                "type": "workflow_step",
                "step": {"run": "curl -H 'Authorization: Bearer secret-token-value'"},
            },
            evidence=[{
                "type": "change_record",
                "reference": "servicenow:CHG-4901",
                "digest": "sha256:" + "d" * 64,
            }],
        )


def test_approval_manifest_preserves_secret_names_but_never_values(tmp_path):
    meta = _runner_pipeline()
    meta.variable_groups = [{"name": "deployment", "variables": ["DEPLOY_TOKEN"]}]
    plan = PipelineConversionPlanner().plan(meta)
    ambiguity = next(
        item for item in plan.manual_ambiguities
        if item.kind == "variable_group_mapping"
    )
    record = ManualApprovalRecord.create(
        ambiguity_id=ambiguity.ambiguity_id,
        source_fingerprint=plan.source_fingerprint,
        project=meta.project,
        repository=meta.repo_name,
        pipeline_id=meta.pipeline_id,
        pipeline_type=meta.pipeline_type.value,
        approver="jane.operator@example.com",
        ticket="CHG-4902",
        approved_at="2026-07-21T09:00:00Z",
        target_mapping={
            "type": "external_configuration",
            "resource_type": "variable_group",
            "resource": "deployment",
            "secret_names": ["DEPLOY_TOKEN"],
        },
        evidence=[{
            "type": "change_record",
            "reference": "servicenow:CHG-4902",
            "digest": "sha256:" + "e" * 64,
        }],
    )

    assert record.target_mapping["secret_names"] == ["DEPLOY_TOKEN"]
    with pytest.raises(PipelineValidationError) as raised:
        PipelineTransformer(
            manual_approval_manifest=ManualApprovalManifest([record]),
            require_production_ready=True,
        ).transform(meta, tmp_path)
    evidence = json.loads(
        Path(raised.value.evidence_file).read_text(encoding="utf-8")
    )
    assert any(
        failure["code"] == "approval_target_mapping_incompatible"
        for failure in evidence["manual_approvals"]["failures"]
    )


def test_stale_source_fingerprint_fails_closed_with_evidence(tmp_path):
    meta = _runner_pipeline()
    record = _approval(meta, fingerprint="sha256:" + "b" * 64)

    with pytest.raises(PipelineValidationError) as raised:
        PipelineTransformer(
            manual_approval_manifest=ManualApprovalManifest([record]),
            require_production_ready=True,
        ).transform(meta, tmp_path)

    codes = {finding.code for finding in raised.value.report.findings}
    assert "approval_source_fingerprint_mismatch" in codes
    assert "external_requirement" in codes
    evidence = json.loads(Path(raised.value.evidence_file).read_text(encoding="utf-8"))
    assert evidence["manual_approvals"]["accepted_count"] == 0
    assert evidence["manual_approvals"]["failures"][0]["code"] == (
        "approval_source_fingerprint_mismatch"
    )


def test_duplicate_and_tampered_approvals_are_rejected_before_conversion():
    meta = _runner_pipeline()
    first = _approval(meta)
    second = _approval(meta, ticket="CHG-4822")

    with pytest.raises(ApprovalManifestError, match="duplicate approvals"):
        ManualApprovalManifest([first, second])

    tampered = first.to_dict()
    tampered["target_mapping"]["runner_label"] = "unreviewed-runner"
    with pytest.raises(ApprovalManifestError, match="content digest"):
        ManualApprovalRecord.from_mapping(tampered)

    manifest = ManualApprovalManifest([first])
    first.target_mapping["runner_label"] = "mutated-after-addressing"
    with pytest.raises(ApprovalManifestError, match="content digest"):
        manifest.evaluate(PipelineConversionPlanner().plan(meta), meta)


def test_boolean_approval_callback_is_never_authorization(tmp_path):
    with pytest.raises(PipelineValidationError) as raised:
        PipelineTransformer(
            external_approval=lambda _ambiguity, _meta: True,
            require_production_ready=True,
        ).transform(_runner_pipeline(), tmp_path)

    assert "external_approval_invalid" in {
        finding.code for finding in raised.value.report.findings
    }


def test_config_file_normalizes_manifest_and_enterprise_factory_consumes_it(
    tmp_path, monkeypatch,
):
    meta = _runner_pipeline()
    record = _approval(meta)
    manifest_path = tmp_path / "pipeline-approvals.json"
    manifest_path.write_text(
        json.dumps({"schema_version": 3, "approvals": [record.to_dict()]}),
        encoding="utf-8",
    )
    config_path = tmp_path / "migration.yaml"
    config_path.write_text(
        yaml.safe_dump({
            "global": {
                "gh_org": "target-org",
                "pipeline_conversion": {
                    "manual_approval_manifest": manifest_path.name,
                },
            },
            "waves": [],
        }),
        encoding="utf-8",
    )

    global_cfg, _ = ConfigLoader.load(str(config_path))
    normalized = global_cfg["pipeline_conversion"]["manual_approval_manifest"]
    assert normalized["manifest_digest"].startswith("sha256:")
    assert normalized["approvals"] == [record.to_dict()]

    class PlanADO:
        org_url = "https://dev.azure.com/example"

        def get_repo(self, _project, repo):
            return {"id": "repo-1", "name": str(repo), "defaultBranch": "refs/heads/main"}

        def list_refs(self, _project, _repo_id, filter_prefix):
            if filter_prefix == "heads/":
                return [{"name": "refs/heads/main", "objectId": "1" * 40}]
            return []

    class PlanGH:
        def repo_exists(self, _org, _repo):
            return False

    plan = MigrationPlanner(PlanADO(), global_cfg, PlanGH()).create_plan([
        RepoConfig(
            ado_project="Payments",
            ado_repo="orders-api",
            gh_org="target-org",
            gh_repo="orders-api",
            scopes=["repo"],
        )
    ])
    assert (
        plan.policy["pipeline_conversion"]["manual_approval_manifest"]
        ["manifest_digest"]
        == normalized["manifest_digest"]
    )
    assert plan.policy["pipeline_conversion"]["llm_provider"] == "disabled"
    assert (
        plan.policy["pipeline_conversion"]["llm_base_url"]
        == "https://api.openai.com/v1"
    )
    assert plan.policy["pipeline_conversion"]["llm_model"] == "gpt-5.6"

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    transformer = create_enterprise_pipeline_transformer_from_env(
        config=global_cfg["pipeline_conversion"],
    )
    result = transformer.transform(meta, tmp_path / "generated")
    assert result["production_ready"] is True
    assert result["stats"]["manual_approvals"] == 1
