"""RBAC and audit export coverage."""
from ado2gh.assignments.audit_export import AuditExportJob
from ado2gh.assignments.rbac import ProfileRole, RBAC
from ado2gh.state.db import StateDB


def test_rbac_from_actor():
    approver = RBAC.from_actor("approver")
    assert approver.has_role(ProfileRole.APPROVER)
    op = RBAC.from_actor("operator")
    assert op.has_role(ProfileRole.OPERATOR)
    assert not op.has_role(ProfileRole.APPROVER)


def test_audit_export_local(tmp_path):
    db = StateDB(str(tmp_path / "exp.db"))
    from ado2gh.assignments.audit import AuditWriter
    AuditWriter(db).write("e", "p1", payload={"x": 1})
    job = AuditExportJob(db)
    result = job.export_profile("p1")
    assert result["status"] == "local_only"
