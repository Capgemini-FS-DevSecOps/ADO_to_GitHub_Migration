"""IDE audit paths must not leak secrets."""

from ado2gh.agents.migration_agent.utils import IdeAuditBridge


def test_ide_audit_redacts_token_in_metadata(tmp_path, monkeypatch):
    path = tmp_path / "state.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(path))
    bridge = IdeAuditBridge()
    eid = bridge.record(
        "session.start",
        profile_id="lightweight",
        session_id="ses_test",
        metadata={"note": "token ghp_abcdefghijklmnop"},
    )
    assert eid.startswith("aud_")
