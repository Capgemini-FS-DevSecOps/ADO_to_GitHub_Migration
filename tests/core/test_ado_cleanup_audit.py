"""Auditability of the `ado-cleanup` result record (COV-DRIFT-004, CA-003/CA-004).

`ADOCleanup` does not call `AuditWriter` itself — it returns one result record
per repository and the caller persists it. What this module asserts is that the
record it returns is *fit to be audited*: it names the source project, the source
repository, the GitHub target and the outcome of every action that ran, and it
survives `AuditWriter.write` with no credential value reaching the stored
payload.

That the cleanup module emits no audit event of its own is recorded as a
follow-up rather than asserted here; no production code is changed by this
module.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from ado2gh.audit import AuditWriter, redact_payload
from ado2gh.core.ado_cleanup import ADOCleanup
from ado2gh.models import ExecutionMode, RepoConfig

# Obviously fake, shaped like the real thing (CA-003 — never a real credential).
FAKE_ADO_PAT = "a7x2k9q4m1p8s3v6y0b5n2h7j4l1d8f3g6t9w2z5c0r7e4u1i8o5"  # 52 chars
FAKE_GH_TOKEN = "ghp_fakecleanuptokenvalue0000000000001"

REPO = RepoConfig(
    ado_project="Contoso Core",
    ado_repo="payments",
    gh_org="fake-gh-org",
    gh_repo="payments",
)


class _CapturingDb:
    """Minimal StateDB stand-in recording what would have been persisted."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def insert_audit_event(self, **kwargs) -> None:
        self.rows.append(kwargs)


def _ado() -> MagicMock:
    ado = MagicMock()
    ado.org_url = f"https://fake-user:{FAKE_ADO_PAT}@dev.azure.com/fake-org"
    ado.API = "api-version=7.1"
    ado._encode_project.side_effect = lambda project: project.replace(" ", "%20")
    ado.list_all_pipelines.return_value = [{"id": 11}]
    ado.get_build_definition_full.return_value = {
        "id": 11, "name": "build-11", "repository": {"name": "payments"},
    }
    ado.get_repo.return_value = {"id": "repo-guid", "defaultBranch": "refs/heads/main"}
    ado._get.return_value = {"value": [{"objectId": "abc123"}]}
    for verb in ("put", "post", "patch"):
        getattr(ado.session, verb).return_value = MagicMock(ok=True, status_code=200)
    return ado


def _persist(payload: object) -> str:
    db = _CapturingDb()
    AuditWriter(db).write("ado.cleanup.completed", profile_id="lightweight", payload=payload)
    return db.rows[0]["payload_json"]


@pytest.fixture
def live_result() -> dict:
    return ADOCleanup(_ado(), mode=ExecutionMode.LIVE).cleanup_repos(
        [REPO], disable_pipelines=True, add_redirect=True, archive_repo=True,
    )[0]


# --------------------------------------------------------------------------
# The record identifies the repository and every action that ran
# --------------------------------------------------------------------------


def test_the_result_record_identifies_the_source_and_the_target(live_result):
    assert live_result["ado_project"] == "Contoso Core"
    assert live_result["ado_repo"] == "payments"
    assert live_result["gh_target"] == "fake-gh-org/payments"
    assert live_result["status"] == "completed"


def test_every_sub_operation_has_its_own_outcome_entry(live_result):
    actions = live_result["actions"]
    assert set(actions) == {"disable_pipelines", "redirect", "archive"}
    assert actions["disable_pipelines"] == {"disabled": 1, "failed": 0, "total": 1}
    assert actions["redirect"] == {"status": "added"}
    assert actions["archive"] == {"status": "archived"}


def test_a_dry_run_record_says_so_on_every_action():
    result = ADOCleanup(_ado(), mode=ExecutionMode.DRY_RUN).cleanup_repos(
        [REPO], disable_pipelines=True, add_redirect=True, archive_repo=True,
    )[0]
    for name, outcome in result["actions"].items():
        assert outcome.get("dry_run") is True, f"{name} did not mark itself as a rehearsal"


def test_an_error_record_names_the_repo_it_belongs_to():
    ado = _ado()
    ado.list_all_pipelines.side_effect = RuntimeError("org unreachable")
    result = ADOCleanup(ado, mode=ExecutionMode.LIVE).cleanup_repos([REPO])[0]
    assert result["status"] == "error"
    assert result["ado_project"] == "Contoso Core"
    assert result["ado_repo"] == "payments"
    assert "org unreachable" in result["error"]


# --------------------------------------------------------------------------
# The record is safe to persist (CA-003)
# --------------------------------------------------------------------------


def test_the_result_record_carries_no_credential_of_its_own(live_result):
    blob = json.dumps(live_result)
    assert FAKE_ADO_PAT not in blob
    assert FAKE_GH_TOKEN not in blob


def test_persisting_the_record_keeps_the_identifiers_readable(live_result):
    stored = _persist(live_result)
    assert "Contoso Core" in stored
    assert "payments" in stored
    assert "fake-gh-org/payments" in stored


def test_a_pat_reaching_the_record_is_masked_before_it_is_persisted(live_result):
    """Defence in depth: the audit sink masks whatever the cleanup hands it."""
    contaminated = dict(live_result, clone_url=f"https://x:{FAKE_ADO_PAT}@dev.azure.com/o/r")
    stored = _persist(contaminated)
    assert FAKE_ADO_PAT not in stored
    assert "***" in stored


def test_a_token_shaped_error_string_is_masked_before_it_is_persisted():
    ado = _ado()
    ado.list_all_pipelines.side_effect = RuntimeError(
        f"auth failed for token {FAKE_GH_TOKEN}",
    )
    result = ADOCleanup(ado, mode=ExecutionMode.LIVE).cleanup_repos([REPO])[0]
    stored = _persist(result)
    assert FAKE_GH_TOKEN not in stored
    assert "ghp_***" in stored, "the triage prefix was dropped along with the secret"


def test_redaction_leaves_the_action_counters_intact(live_result):
    redacted = redact_payload(live_result)
    assert redacted["actions"]["disable_pipelines"] == {
        "disabled": 1, "failed": 0, "total": 1,
    }
    assert redacted["status"] == "completed"
