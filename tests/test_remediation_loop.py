"""Remediation loop retry limits."""
from ado2gh.state.db import StateDB


def test_remediation_upsert(tmp_path):
    db = StateDB(str(tmp_path / "rem.db"))
    db.upsert_remediation_loop("ses1", "P/r1", 1, 3, "active")
    db.upsert_remediation_loop("ses1", "P/r1", 2, 3, "active")
    row = db.get_remediation_loop("ses1", "P/r1")
    assert row["retry_count"] == 2
