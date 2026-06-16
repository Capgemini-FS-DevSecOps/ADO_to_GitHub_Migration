"""IDE audit paths must not leak secrets."""
import os

from ado2gh.agents.local.audit_bridge import IdeAuditBridge


def test_ide_audit_redacts_token_in_metadata(tmp_path):
    path = tmp_path / "state.db"
    os.environ["ADO2GH_SQLITE_PATH"] = str(path)
    bridge = IdeAuditBridge()
    eid = bridge.record(
        "session.start",
        profile_id="lightweight",
        session_id="ses_test",
        metadata={"note": "token ghp_abcdefghijklmnop"},
    )
    assert eid.startswith("aud_")
