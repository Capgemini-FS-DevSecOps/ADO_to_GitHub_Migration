"""Additional coverage for 004 ado2gh modules (T059)."""
import pytest
from fastapi import HTTPException

from ado2gh.api.live_approval_store import LiveApprovalStore, migrate_scope_id
from ado2gh.api.platform_rbac import operator_requires_live_approval
from ado2gh.auth.models import PlatformRole, PlatformUser
from ado2gh.auth.service import AuthService, permissions_for


def test_migrate_scope_id_format():
    sid = migrate_scope_id("prof1", 2, "migration.yaml")
    assert "prof1" in sid
    assert "2" in sid


def test_operator_requires_live_approval_matrix(monkeypatch):
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    op = PlatformUser("1", "op", PlatformRole.OPERATOR, "Op")
    admin = PlatformUser("2", "a", PlatformRole.ADMIN, "A")
    assert operator_requires_live_approval(op, False) is True
    assert operator_requires_live_approval(op, True) is False
    assert operator_requires_live_approval(admin, False) is False


def test_permissions_all_roles():
    for role in PlatformRole:
        perms = permissions_for(role)
        assert "can_approve_live_execution" in perms
        assert "can_manage_settings" in perms


def test_live_store_deny_pipeline_run(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(tmp_path / "d.db"))
    store = LiveApprovalStore(str(tmp_path / "d.db"))
    admin = PlatformUser("a", "admin", PlatformRole.ADMIN, "Admin")
    op = PlatformUser("o", "op", PlatformRole.OPERATOR, "Op")
    row = store.create_or_get_pending(
        op, "pipeline_run", "run-1", reason_request="live pipeline",
    )
    denied = store.deny(row["id"], admin, "not yet")
    assert denied["status"] == "denied"


def test_live_store_get_forbidden_for_other_operator(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(tmp_path / "g.db"))
    store = LiveApprovalStore(str(tmp_path / "g.db"))
    op1 = PlatformUser("1", "op1", PlatformRole.OPERATOR, "Op1")
    op2 = PlatformUser("2", "op2", PlatformRole.OPERATOR, "Op2")
    row = store.create_or_get_pending(op1, "agent_session", "sess_x")
    with pytest.raises(HTTPException) as exc:
        store.get_approval(row["id"], requester=op2)
    assert exc.value.status_code == 403


def test_llm_store_upsert_update_and_delete(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    from ado2gh.api.llm_model_store import LLMModelStore

    store = LLMModelStore()
    created = store.upsert({
        "display_name": "GPT",
        "provider": "openai",
        "model_id": "gpt-4o-mini",
        "api_key": "sk-test-key",
        "default_for_agent": True,
        "enabled": True,
        "validation_status": "passed",
        "validation_at": "2026-06-16T00:00:00Z",
    })
    updated = store.upsert(
        {"display_name": "GPT-4", "default_for_agent": False},
        model_id=created.id,
    )
    assert updated.display_name == "GPT-4"
    assert store.get_default_model() is not None
    assert store.get_default_model().id == created.id
    store.delete(created.id)
    assert store.get(created.id) is None


def test_llm_store_requires_api_key_for_openai(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    from ado2gh.api.llm_model_store import LLMModelStore

    store = LLMModelStore()
    with pytest.raises(ValueError, match="api_key"):
        store.upsert({"display_name": "Missing key", "provider": "openai"})


def test_auth_register_and_login(tmp_path):
    from ado2gh.state.db import StateDB

    svc = AuthService(db=StateDB(str(tmp_path / "auth.db")))
    svc.bootstrap_admin("admin", "twelve-char-pass", "Admin")
    session = svc.login("admin", "twelve-char-pass")
    assert session.user.username == "admin"
    op_user = svc.register_operator("operator1", "twelve-char-pass", "Op")
    assert op_user["role"] == "operator"
    assert op_user["status"] == "pending_approval"
    svc.approve_user(op_user["id"])
    op_session = svc.login("operator1", "twelve-char-pass")
    assert op_session.user.role == PlatformRole.OPERATOR
