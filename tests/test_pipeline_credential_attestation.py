from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone

import pytest

from ado2gh.clients.gh_client import GHClient
from ado2gh.core.migration_engine import MigrationEngine
from ado2gh.models import (
    PipelineMetadata,
    PipelineStage,
    PipelineType,
    RepoConfig,
)
from ado2gh.pipelines.approvals import ManualApprovalManifest, ManualApprovalRecord
from ado2gh.pipelines.credential_attestation import (
    CredentialAttestationVerifier,
    canonical_claim_bytes,
)
from ado2gh.pipelines.pev_types import PipelineValidationError
from ado2gh.pipelines.planner import PipelineConversionPlanner
from ado2gh.pipelines.transformer import PipelineTransformer


NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)
KEY = b"enterprise-controlled-canary-key-32-bytes-minimum"
SECRET_VERSION = "2026-07-21T11:59:00Z"
TARGET_ID = "R_target_immutable"


def _secret_pipeline() -> PipelineMetadata:
    return PipelineMetadata(
        pipeline_id=701,
        pipeline_name="credential-bound",
        pipeline_type=PipelineType.YAML,
        project="Payments",
        repo_name="orders-api",
        service_connections=[{
            "name": "deployment-service",
            "type": "service-principal",
        }],
        stages=[PipelineStage(
            name="build",
            agent_pool="ubuntu-latest",
            jobs=[{"job": "build", "steps": [{"bash": "python -m pytest"}]}],
        )],
    )


def _checkout_pipeline() -> PipelineMetadata:
    return PipelineMetadata(
        pipeline_id=702,
        pipeline_name="external-checkout",
        pipeline_type=PipelineType.YAML,
        project="Payments",
        repo_name="orders-api",
        stages=[PipelineStage(
            name="build",
            agent_pool="ubuntu-latest",
            jobs=[{
                "job": "build",
                "steps": [{"checkout": "shared", "ref": "refs/heads/release"}],
            }],
        )],
    )


def _verifier(*, now=NOW) -> CredentialAttestationVerifier:
    return CredentialAttestationVerifier(
        {"enterprise-q3": KEY},
        trusted_workflow_sha_by_key_id={"enterprise-q3": "c" * 40},
        max_age_seconds=300,
        max_ttl_seconds=600,
        clock_skew_seconds=0,
        now=lambda: now,
    )


def _canary(
    plan,
    ambiguity,
    *,
    capability: str,
    resource: str,
    secret_name: str = "DEPLOY_TOKEN",
    target_id: str = TARGET_ID,
    secret_version: str = SECRET_VERSION,
    issued_at: str = "2026-07-21T11:58:00Z",
    expires_at: str = "2026-07-21T12:03:00Z",
    external_repository_id: str | None = None,
    external_ref: str | None = None,
    external_commit_sha: str | None = None,
) -> dict:
    claims = {
        "schema_version": 1,
        "nonce": "nonce-for-test-0001",
        "issuer": "enterprise-q3",
        "audience": "ado2gh-pipeline-credential-canary",
        "canary_run_id": "canary-run-test-run-00000001",
        "canary_workflow_sha": "c" * 40,
        "outcome": "passed",
        "issued_at": issued_at,
        "expires_at": expires_at,
        "plan_id": plan.plan_id,
        "source_fingerprint": plan.source_fingerprint,
        "ambiguity_id": ambiguity.ambiguity_id,
        "capability": capability,
        "resource": resource,
        "target_repository_id": target_id,
        "secret_name": secret_name,
        "secret_updated_at": secret_version,
    }
    if capability == "external_repository_checkout":
        claims.update({
            "external_repository_id": external_repository_id or "R_external",
            "external_ref": external_ref or "refs/heads/release",
            "external_commit_sha": external_commit_sha or "a" * 40,
        })
    return {
        "type": "credential-canary",
        "key_id": "enterprise-q3",
        "claims": claims,
        "signature": "hmac-sha256:" + hmac.new(
            KEY, canonical_claim_bytes(claims), hashlib.sha256
        ).hexdigest(),
    }


def _record(meta, plan, ambiguity, target_mapping, canary) -> ManualApprovalRecord:
    return ManualApprovalRecord.create(
        ambiguity_id=ambiguity.ambiguity_id,
        source_fingerprint=plan.source_fingerprint,
        project=meta.project,
        repository=meta.repo_name,
        pipeline_id=meta.pipeline_id,
        pipeline_type=meta.pipeline_type.value,
        approver="release-control@example.com",
        ticket="CHG-credential-canary",
        approved_at="2026-07-21T11:58:30Z",
        target_mapping=target_mapping,
        evidence=[canary],
    )


def _transform_secret(tmp_path, *, mutate_canary=None):
    meta = _secret_pipeline()
    plan = PipelineConversionPlanner().plan(meta)
    ambiguity = next(
        a for a in plan.ambiguities if a.kind == "service_connection_mapping"
    )
    canary = _canary(
        plan,
        ambiguity,
        capability="service_connection_mapping",
        resource="deployment-service",
    )
    if mutate_canary:
        mutate_canary(canary)
    record = _record(
        meta,
        plan,
        ambiguity,
        {
            "type": "external_configuration",
            "resource_type": "repository_secret",
            "resource": "deployment-service",
            "secret_names": ["DEPLOY_TOKEN"],
        },
        canary,
    )
    transformer = PipelineTransformer(
        manual_approval_manifest=ManualApprovalManifest([record]),
        credential_attestation_verifier=_verifier(),
        require_production_ready=True,
    )
    return meta, transformer.transform(meta, tmp_path)


def test_converter_accepts_only_authenticated_fresh_credential_canary(tmp_path):
    _meta, result = _transform_secret(tmp_path)
    assert result["production_ready"] is True
    assert result["stats"]["manual_approvals"] == 1


@pytest.mark.parametrize(
    "attack", ["forged", "expired", "wrong-secret", "wrong-workflow"]
)
def test_converter_rejects_forged_expired_or_misbound_canary(tmp_path, attack):
    def mutate(canary):
        if attack == "forged":
            canary["signature"] = "hmac-sha256:" + "0" * 64
        elif attack == "expired":
            canary["claims"]["issued_at"] = "2026-07-21T11:40:00Z"
            canary["claims"]["expires_at"] = "2026-07-21T11:45:00Z"
            canary["signature"] = "hmac-sha256:" + hmac.new(
                KEY,
                canonical_claim_bytes(canary["claims"]),
                hashlib.sha256,
            ).hexdigest()
        elif attack == "wrong-secret":
            canary["claims"]["secret_name"] = "OTHER_TOKEN"
            canary["signature"] = "hmac-sha256:" + hmac.new(
                KEY,
                canonical_claim_bytes(canary["claims"]),
                hashlib.sha256,
            ).hexdigest()
        else:
            canary["claims"]["canary_workflow_sha"] = "d" * 40
            canary["signature"] = "hmac-sha256:" + hmac.new(
                KEY,
                canonical_claim_bytes(canary["claims"]),
                hashlib.sha256,
            ).hexdigest()

    with pytest.raises(PipelineValidationError) as raised:
        _transform_secret(tmp_path, mutate_canary=mutate)
    assert "approval_credential_canary_invalid" in {
        finding.code for finding in raised.value.report.findings
    }


class _SecretGH:
    def __init__(self, *, target_id=TARGET_ID, version=SECRET_VERSION):
        self.target_id = target_id
        self.version = version

    def get_repo(self, org, repo):
        assert (org, repo) == ("target-org", "orders-api")
        return {"node_id": self.target_id}

    def list_actions_secret_metadata(self, org, repo):
        assert (org, repo) == ("target-org", "orders-api")
        return [{
            "name": "DEPLOY_TOKEN",
            "created_at": "2026-07-01T00:00:00Z",
            "updated_at": self.version,
        }]


def _engine(gh) -> MigrationEngine:
    engine = object.__new__(MigrationEngine)
    engine.gh = gh
    engine.cfg = {
        "pipeline_conversion": {
            "credential_attestation": {
                "key_env_by_id": {"enterprise-q3": "ENTERPRISE_CANARY_KEY"},
                "trusted_workflow_sha_by_key_id": {"enterprise-q3": "c" * 40},
            }
        }
    }
    engine._credential_attestation_verifier = _verifier()
    return engine


def _repo() -> RepoConfig:
    return RepoConfig(
        ado_project="Payments",
        ado_repo="orders-api",
        gh_org="target-org",
        gh_repo="orders-api",
        scopes=["pipelines"],
    )


@pytest.mark.parametrize(
    ("gh", "match"),
    [
        (_SecretGH(target_id="R_wrong"), "different immutable repository"),
        (_SecretGH(version="2026-07-21T12:00:00Z"), "rotated after"),
    ],
)
def test_runtime_rejects_wrong_target_or_secret_rotation(tmp_path, gh, match):
    _meta, result = _transform_secret(tmp_path)
    with pytest.raises(RuntimeError, match=match):
        _engine(gh)._verify_external_pipeline_configuration(_repo(), result)


def test_runtime_reauthenticates_canary_and_binds_live_secret_version(tmp_path):
    _meta, result = _transform_secret(tmp_path)
    receipt = _engine(_SecretGH())._verify_external_pipeline_configuration(
        _repo(), result
    )
    assert receipt["verified"] is True
    assert receipt["credential_attestation_count"] == 1
    assert receipt["target_repository_id"] == TARGET_ID
    assert receipt["secret_metadata_versions"] == {
        "DEPLOY_TOKEN": SECRET_VERSION
    }


class _CheckoutGH:
    def __init__(self, *, sha="a" * 40):
        self.sha = sha

    def get_repo(self, org, repo):
        if (org, repo) == ("target-org", "orders-api"):
            return {"node_id": TARGET_ID}
        assert (org, repo) == ("shared-org", "shared-repo")
        return {"node_id": "R_external"}

    def list_actions_secret_metadata(self, _org, _repo):
        return [{
            "name": "CHECKOUT_TOKEN",
            "created_at": "2026-07-01T00:00:00Z",
            "updated_at": SECRET_VERSION,
        }]

    def get_commit_sha(self, org, repo, ref):
        assert (org, repo, ref) == (
            "shared-org", "shared-repo", "refs/heads/release"
        )
        return self.sha


def test_runtime_rejects_checkout_ref_resolving_to_unattested_commit(tmp_path):
    meta = _checkout_pipeline()
    plan = PipelineConversionPlanner().plan(meta)
    ambiguity = next(
        a for a in plan.ambiguities if a.kind == "external_repository_checkout"
    )
    canary = _canary(
        plan,
        ambiguity,
        capability="external_repository_checkout",
        resource="shared-org/shared-repo",
        secret_name="CHECKOUT_TOKEN",
        external_ref="refs/heads/release",
        external_commit_sha="a" * 40,
    )
    record = _record(
        meta,
        plan,
        ambiguity,
        {
            "type": "repository_checkout",
            "repository": "shared-org/shared-repo",
            "ref": "refs/heads/release",
            "token_secret": "CHECKOUT_TOKEN",
        },
        canary,
    )
    result = PipelineTransformer(
        manual_approval_manifest=ManualApprovalManifest([record]),
        credential_attestation_verifier=_verifier(),
        require_production_ready=True,
    ).transform(meta, tmp_path)
    with pytest.raises(RuntimeError, match="external_commit_sha drifted"):
        _engine(_CheckoutGH(sha="b" * 40))._verify_external_pipeline_configuration(
            _repo(), result
        )


def test_config_stores_only_key_environment_references(tmp_path, monkeypatch):
    from ado2gh.core.config_loader import ConfigLoader

    config = tmp_path / "migration.yaml"
    config.write_text(
        """global:
  gh_org: target-org
  pipeline_conversion:
    credential_attestation:
      key_env_by_id:
        enterprise-q3: ENTERPRISE_CANARY_KEY
      trusted_workflow_sha_by_key_id:
        enterprise-q3: cccccccccccccccccccccccccccccccccccccccc
      max_age_seconds: 300
      max_ttl_seconds: 600
      clock_skew_seconds: 0
waves: []
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("ENTERPRISE_CANARY_KEY", KEY.decode())
    global_cfg, _waves = ConfigLoader.load(str(config))
    attestation = global_cfg["pipeline_conversion"]["credential_attestation"]
    assert attestation["key_env_by_id"] == {
        "enterprise-q3": "ENTERPRISE_CANARY_KEY"
    }
    assert attestation["trusted_workflow_sha_by_key_id"] == {
        "enterprise-q3": "c" * 40
    }
    assert KEY.decode() not in repr(global_cfg)


def test_github_secret_metadata_is_complete_versioned_and_paginated():
    client = object.__new__(GHClient)
    rows = [
        {
            "name": f"SECRET_{index:03d}",
            "created_at": "2026-07-01T00:00:00Z",
            "updated_at": f"2026-07-21T11:{index % 60:02d}:00Z",
        }
        for index in range(101)
    ]

    def get(_path, *, params):
        page = params["page"]
        start = (page - 1) * 100
        return {"total_count": 101, "secrets": rows[start:start + 100]}

    client._get = get
    assert client.list_actions_secret_metadata("target-org", "orders-api") == rows
    assert client.list_actions_secret_names("target-org", "orders-api") == [
        item["name"] for item in rows
    ]
