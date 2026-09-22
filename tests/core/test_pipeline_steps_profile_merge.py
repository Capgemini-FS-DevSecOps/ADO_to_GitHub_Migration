"""GAP-058 — the single-repo dry-run path must never probe unmerged config.

``_migrate_scoped`` builds its own wave for a single-repo dry run
(``ado2gh/api/pipeline_steps.py``), and that branch does not require an active
migration profile — ``gh_org`` falls back to a literal on the line above. The
credential merge is what puts the Azure DevOps personal access token (PAT) and the GitHub token into
``global_cfg``; the connectivity probes that follow read exactly those keys.

T077 (`d1427fd`) wrapped the merge in ``if profile:`` to silence mypy. With no
active profile the step then probed credential-less config, both probes failed
or passed on ambient environment values, and the step could report COMPLETED on
credentials nothing had checked (CA-001). These tests pin the two honest
outcomes: with a profile the merged credentials reach the probes, without one
the step fails loudly.

The last two tests cover the same step's other T077 review findings: the
credential merge rejects a missing profile at the choke point, and an unknown
``run.phase`` fails the step with a readable message instead of reaching
``PhaseRunRequest`` and raising pydantic's ValidationError mid-migration.
"""
from __future__ import annotations

import pytest

from ado2gh.api.pipeline_models import StepStatus
from ado2gh.api.pipeline_runner import PipelineRunner
from ado2gh.api.pipeline_store import PipelineRunStore
from ado2gh.api.settings_models import AdvancedSettings, GitHubToken, MigrationProfile, UISettings

REPO_ID = "Contoso/payments"
STEP_DEFS = [{"id": "migrate_repos", "label": "Migrate repositories", "description": ""}]

# Obvious fakes — never real credentials (CA-003).
FAKE_PAT = "fake-ado-pat-for-tests"
FAKE_GH_TOKEN = "fake-gh-token-for-tests"


class _FakeSettings:
    """Settings store stand-in: fixed advanced settings and active profile."""

    def __init__(self, advanced, profile):
        self._settings = UISettings(advanced=advanced)
        self._profile = profile

    def load(self):
        return self._settings

    def get_active_profile(self):
        return self._profile

    def apply_to_process_env(self):
        return None


class _FakeADO:
    """ADO client stand-in; ``list_projects`` is the dry-run connectivity probe."""

    def list_projects(self):
        return [{"id": "p1", "name": "Contoso"}]


class _FakeGH:
    """GitHub client stand-in; the probe only calls ``check_rate_limits``."""

    class _TokenManager:
        def check_rate_limits(self):
            return {"remaining": 5000}

    def __init__(self):
        self.token_manager = self._TokenManager()


@pytest.fixture
def probe_recorder(monkeypatch):
    """Capture the config each client builder is handed, and build no real client."""
    seen: list[dict] = []

    def _ado(cfg):
        seen.append(dict(cfg))
        return _FakeADO()

    def _gh(cfg):
        seen.append(dict(cfg))
        return _FakeGH()

    monkeypatch.setattr("ado2gh.api.accelerator._build_ado_client", _ado)
    monkeypatch.setattr("ado2gh.api.accelerator._build_gh_client", _gh)
    return seen


@pytest.fixture
def runner_env(tmp_path):
    """An advanced-settings block pointing at a temp config and state DB."""
    config_path = tmp_path / "migration.yaml"
    config_path.write_text("global:\n  gh_org: fake-org\n", encoding="utf-8")
    return AdvancedSettings(
        config_path=str(config_path),
        db_path=str(tmp_path / "state.db"),
    )


def _profile():
    return MigrationProfile(
        id="prof-1",
        name="fake profile",
        ado_org_url="https://dev.azure.com/fake-org",
        ado_pat=FAKE_PAT,
        gh_org="fake-org",
        github_tokens=[GitHubToken(id="t1", name="primary", token=FAKE_GH_TOKEN)],
    )


def _runner(advanced, profile, monkeypatch):
    runner = PipelineRunner(settings=_FakeSettings(advanced, profile))
    # The per-step warning helpers are not under test here and would walk the
    # state DB and the fake client.
    monkeypatch.setattr(runner, "_repo_migration_dry_run_warnings", lambda *a, **k: [])
    return runner


def _single_repo_run():
    return PipelineRunStore.create(
        "single repo dry run",
        dry_run=True,
        phase="poc",
        wave_id=None,
        step_defs=STEP_DEFS,
        repository_id=REPO_ID,
    )


def test_single_repo_dry_run_without_profile_fails_the_step(runner_env, probe_recorder, monkeypatch):
    """No active profile: the step fails and no probe sees unmerged config."""
    runner = _runner(runner_env, None, monkeypatch)
    run = _single_repo_run()
    try:
        runner._execute(run.id, ["migrate_repos"])
    finally:
        PipelineRunStore.clear_cancel(run.id)

    step = run.steps[0]
    assert step.status is StepStatus.FAILED, (
        f"no-profile single-repo dry run reported {step.status}: {step.message}"
    )
    assert "profile" in step.message.lower()
    assert run.status == "failed"
    assert probe_recorder == [], "connectivity probes ran against unmerged config"


def test_single_repo_dry_run_with_profile_merges_credentials(runner_env, probe_recorder, monkeypatch):
    """Active profile: its credentials reach the probes and the step completes."""
    runner = _runner(runner_env, _profile(), monkeypatch)
    run = _single_repo_run()
    try:
        runner._execute(run.id, ["migrate_repos"])
    finally:
        PipelineRunStore.clear_cancel(run.id)

    step = run.steps[0]
    assert step.status is StepStatus.COMPLETED, f"{step.status}: {step.message}"
    assert probe_recorder, "no connectivity probe ran"
    for cfg in probe_recorder:
        assert cfg["ado_pat"] == FAKE_PAT
        assert cfg["gh_token"] == FAKE_GH_TOKEN
        assert cfg["ado_org_url"] == "https://dev.azure.com/fake-org"


def test_unknown_phase_fails_the_step_before_the_phase_request(runner_env, probe_recorder, monkeypatch):
    """An unknown ``run.phase`` fails the step, not pydantic mid-migration.

    ``PipelineRunStartRequest.phase`` is free text and ``PhaseRunRequest.phase``
    is a Literal of the five known phases; T077 bridged the two with a
    ``type: ignore``, so a typo surfaced as a ValidationError raised inside the
    migration step.
    """
    runner = _runner(runner_env, None, monkeypatch)
    run = PipelineRunStore.create(
        "bogus phase",
        dry_run=False,
        phase="wave9",
        wave_id=None,
        step_defs=STEP_DEFS,
    )
    try:
        runner._execute(run.id, ["migrate_repos"])
    finally:
        PipelineRunStore.clear_cancel(run.id)

    step = run.steps[0]
    assert step.status is StepStatus.FAILED
    assert "wave9" in step.message
    assert "poc" in step.message, "the message should name the phases that are accepted"


def test_merge_profile_credentials_rejects_a_missing_profile():
    """The merge helper is the choke point: None is an error, not a no-op."""
    from ado2gh.api.validation_run import _merge_profile_credentials

    with pytest.raises(RuntimeError, match="No active migration profile"):
        _merge_profile_credentials({"gh_org": "fake-org"}, None, AdvancedSettings())
