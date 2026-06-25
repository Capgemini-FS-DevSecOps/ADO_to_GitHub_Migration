"""Rollback and symmetric ADO pipeline re-enable (FR-026a)."""
from unittest.mock import MagicMock, patch

from ado2gh.core.ado_cleanup import ADOCleanup
from ado2gh.core.rollback import RollbackHandler
from ado2gh.models import MigrationScope, MigrationStatus, RepoConfig
from ado2gh.state.db import StateDB


def test_enable_pipelines_dry_run(tmp_path):
    ado = MagicMock()
    ado.list_all_pipelines.return_value = [{"id": 1}]
    ado.get_build_definition_full.return_value = {
        "id": 1,
        "name": "build",
        "repository": {"name": "repo1"},
    }
    db = StateDB(str(tmp_path / "r.db"))
    cleanup = ADOCleanup(ado, db, dry_run=True)
    repo = RepoConfig(ado_project="P", ado_repo="repo1", gh_org="o", gh_repo="repo1")
    stats = cleanup.enable_pipelines(repo)
    assert stats.get("dry_run") is True


def test_rollback_pipelines_with_ado(tmp_path):
    db = StateDB(str(tmp_path / "r2.db"))
    gh = MagicMock()
    ado = MagicMock()
    handler = RollbackHandler(gh, db, ado=ado)
    repo = RepoConfig(ado_project="P", ado_repo="r1", gh_org="o", gh_repo="r1")
    db.upsert_migration(1, repo, MigrationScope.PIPELINES.value, MigrationStatus.COMPLETED)
    record = {
        "ado_project": "P",
        "ado_repo": "r1",
        "gh_org": "o",
        "gh_repo": "r1",
        "scope": MigrationScope.PIPELINES.value,
    }
    stats = {}
    with patch.object(ADOCleanup, "enable_pipelines", return_value={"enabled": 2}):
        handler._rollback_pipelines(1, record, False, stats)
    assert stats.get("ado_pipelines_reenabled") == 2
