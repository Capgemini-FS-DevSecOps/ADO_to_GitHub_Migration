from __future__ import annotations

from types import SimpleNamespace

import pytest

from ado2gh.clients.ado_client import ADOClient
from ado2gh.core.migration_engine import MigrationEngine
from ado2gh.models import MigrationScope, RepoConfig
from ado2gh.pev.contracts import PlanIntegrityError, compute_source_refs_digest
from ado2gh.pev.executor import PEVExecutor
from ado2gh.pev.planner import MigrationPlanner
from ado2gh.reporting.post_migration_validator import PASS, PostMigrationValidator
from ado2gh.state.db import StateDB


MAIN_SHA = "a" * 40
TAG_SHA = "b" * 40
DRIFT_SHA = "c" * 40


class _Response:
    def __init__(self, rows, token=""):
        self._rows = rows
        self.headers = {"x-ms-continuationtoken": token} if token else {}

    def raise_for_status(self):
        return None

    def json(self):
        return {"value": self._rows}


class _PagedSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=45):
        self.calls.append((url, dict(params or {}), timeout))
        if len(self.calls) == 1:
            return _Response(
                [{"name": "refs/heads/main", "objectId": MAIN_SHA}],
                token="next page/token",
            )
        return _Response(
            [{"name": "refs/heads/release", "objectId": DRIFT_SHA}]
        )


def test_ado_list_refs_reads_all_continuation_pages():
    client = ADOClient("https://dev.azure.com/example", "secret")
    session = _PagedSession()
    client.session = session

    rows = client.list_refs("Payments & Billing", "repo/id", "refs/heads/")

    assert [row["name"] for row in rows] == [
        "refs/heads/main", "refs/heads/release"
    ]
    assert session.calls[0][1]["filter"] == "heads/"
    assert session.calls[1][1]["continuationToken"] == "next page/token"
    assert "Payments%20%26%20Billing" in session.calls[0][0]
    assert "repo%2Fid" in session.calls[0][0]


class RefADO:
    org_url = "https://dev.azure.com/example"

    def __init__(self):
        self.repo_id = "repo-id"
        self.default_branch = "main"
        self.branch_sha = MAIN_SHA
        self.tag_sha = TAG_SHA
        self.unstable = False
        self.head_reads = 0

    def get_repo(self, _project, repo):
        return {
            "id": self.repo_id,
            "name": str(repo),
            "defaultBranch": (
                f"refs/heads/{self.default_branch}"
                if self.default_branch else None
            ),
            "remoteUrl": "https://dev.azure.com/example/Payments/_git/api",
        }

    def list_refs(self, _project, _repo_id, prefix):
        if prefix == "heads/":
            self.head_reads += 1
            sha = (
                DRIFT_SHA if self.unstable and self.head_reads > 1
                else self.branch_sha
            )
            return [{"name": "refs/heads/main", "objectId": sha}]
        if prefix == "tags/":
            return [{"name": "refs/tags/v1", "objectId": self.tag_sha}]
        raise AssertionError(prefix)


class TargetGH:
    def repo_exists(self, *_args):
        return False


class PreviewEngine:
    def __init__(self, _cfg, _ado, _gh, _db, **_kwargs):
        pass

    def migrate_repo(self, _wave_id, repo, **_kwargs):
        return {
            "status": "completed",
            "scopes": {
                scope: {"status": "completed", "detail": {}}
                for scope in repo.scopes
            },
        }


def _cfg():
    return {
        "ado_org_url": "https://dev.azure.com/example",
        "gh_org": "octo",
        "default_scopes": [MigrationScope.REPO.value],
        "parallel": 1,
        "mapping": {
            "strategy": "project-prefix",
            "existing_target_policy": "fail",
        },
    }


def _repo():
    return RepoConfig(
        ado_project="Payments",
        ado_repo="api",
        gh_org="octo",
        gh_repo="payments-api",
        scopes=[MigrationScope.REPO.value],
    )


def test_planner_rejects_missing_immutable_source_id():
    ado = RefADO()
    ado.repo_id = ""
    with pytest.raises(PlanIntegrityError, match="immutable repository ID"):
        MigrationPlanner(ado, _cfg(), TargetGH()).create_plan([_repo()])


def test_planner_rejects_refs_that_change_during_snapshot():
    ado = RefADO()
    ado.unstable = True
    with pytest.raises(PlanIntegrityError, match="refs changed while planning"):
        MigrationPlanner(ado, _cfg(), TargetGH()).create_plan([_repo()])


@pytest.mark.parametrize(
    ("mutation", "detail"),
    [
        (lambda ado: setattr(ado, "repo_id", "replacement-id"), "identity drift"),
        (lambda ado: setattr(ado, "default_branch", "develop"), "default branch drift"),
        (lambda ado: setattr(ado, "branch_sha", DRIFT_SHA), "Source ref drift"),
        (lambda ado: setattr(ado, "tag_sha", DRIFT_SHA), "Source ref drift"),
    ],
)
def test_executor_blocks_any_source_identity_or_ref_drift(
    tmp_path, mutation, detail
):
    ado = RefADO()
    cfg = _cfg()
    gh = TargetGH()
    plan = MigrationPlanner(ado, cfg, gh).create_plan([_repo()])
    mutation(ado)
    db = StateDB(str(tmp_path / "state.db"))

    result = PEVExecutor(
        cfg, ado, gh, db, engine_factory=PreviewEngine
    ).execute(plan, approved_plan_id=plan.plan_id)

    assert result.status == "failed"
    assert detail in result.repository_results["Payments/api"]["errors"][0]


class _TokenManager:
    def get_token(self):
        return "github-token"


def _approved_config():
    branches = {"refs/heads/main": MAIN_SHA}
    tags = {"refs/tags/v1": TAG_SHA}
    return {
        **_cfg(),
        "pev_plan_id": "plan-approved",
        "pev_run_id": "run-approved",
        "pev_source_ref_snapshots": {
            "Payments/api": {
                "source_repo_id": "repo-id",
                "default_branch": "main",
                "source_head_sha": MAIN_SHA,
                "source_branch_refs": branches,
                "source_tag_refs": tags,
                "source_refs_digest": compute_source_refs_digest(
                    tuple(branches.items()), tuple(tags.items())
                ),
            }
        },
    }


def test_mirror_blocks_push_when_cloned_refs_differ(monkeypatch):
    ado = SimpleNamespace(pat="ado-token")
    gh = SimpleNamespace(
        BASE="https://api.github.com",
        token_manager=_TokenManager(),
    )
    engine = MigrationEngine(_approved_config(), ado, gh, object())
    pushed = False

    def fake_run(command, **_kwargs):
        nonlocal pushed
        if command[:3] == ["git", "clone", "--mirror"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[:4] == ["git", "remote", "get-url", "--all"]:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "https://dev.azure.com/example/Payments/_git/api\n"
                ),
                stderr="",
            )
        if command[:2] == ["git", "for-each-ref"]:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    f"refs/heads/main\t{DRIFT_SHA}\n"
                    f"refs/tags/v1\t{TAG_SHA}\n"
                ),
                stderr="",
            )
        if command[:3] == ["git", "symbolic-ref", "--short"]:
            return SimpleNamespace(returncode=0, stdout="main\n", stderr="")
        if command[:2] == ["git", "push"]:
            pushed = True
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("ado2gh.core.migration_engine.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="Cloned source drift"):
        engine._run_mirror_migration(
            _repo(),
            "https://dev.azure.com/example/Payments/_git/api",
        )
    assert pushed is False


def test_gei_rechecks_source_immediately_before_process(monkeypatch):
    ado = RefADO()
    ado.pat = "ado-token"
    ado.branch_sha = DRIFT_SHA
    gh = SimpleNamespace(token_manager=_TokenManager())
    engine = MigrationEngine(_approved_config(), ado, gh, object())
    invoked = False

    def fake_run(*_args, **_kwargs):
        nonlocal invoked
        invoked = True
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("ado2gh.core.migration_engine.subprocess.run", fake_run)
    with pytest.raises(RuntimeError, match="ADO source drift"):
        engine._run_gei_migration(_repo(), ado.get_repo("Payments", "api"))
    assert invoked is False


class NoLiveSourceReads:
    def __getattr__(self, name):
        raise AssertionError(f"PEV validation must not read mutable ADO source: {name}")


class ExactTarget:
    def repo_exists(self, *_args):
        return True

    def get_repo(self, *_args):
        return {"default_branch": "main"}

    def list_branches(self, *_args):
        return [{"name": "main", "commit": {"sha": MAIN_SHA}}]

    def list_tag_refs(self, *_args):
        return [{"ref": "refs/tags/v1", "object": {"sha": TAG_SHA}}]


def test_pev_ref_validation_uses_approved_plan_snapshot_not_live_ado(tmp_path):
    approved = _approved_config()["pev_source_ref_snapshots"]
    validator = PostMigrationValidator(
        NoLiveSourceReads(),
        ExactTarget(),
        StateDB(str(tmp_path / "state.db")),
        approved,
    )

    result = validator.validate([_repo()], max_workers=1)[0]

    assert result["overall"] == PASS
    assert result["checks"]["branches"]["source_digest"]
    assert result["checks"]["tags"]["source_digest"]
