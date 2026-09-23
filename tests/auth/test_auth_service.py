"""Platform auth service tests."""

from ado2gh.auth.password import hash_password, verify_password, validate_password_strength
from ado2gh.auth.service import AuthService


def test_password_hash_roundtrip():
    h = hash_password("long-password-123")
    assert verify_password("long-password-123", h)
    assert not verify_password("wrong", h)


def test_bootstrap_and_login(tmp_path, monkeypatch):
    db = tmp_path / "auth.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db))
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    svc = AuthService()
    assert svc.needs_bootstrap()
    session = svc.bootstrap_admin("admin", "twelve-char-pass", "Admin")
    assert session.user.username == "admin"
    assert not svc.needs_bootstrap()
    session2 = svc.login("admin", "twelve-char-pass")
    assert session2.user.id == session.user.id


def test_validate_password_strength():
    try:
        validate_password_strength("short")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_concurrent_bootstrap_rejected(tmp_path, monkeypatch):
    db = tmp_path / "auth2.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db))
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    svc = AuthService()
    svc.bootstrap_admin("admin", "twelve-char-pass", "Admin")
    try:
        svc.bootstrap_admin("other", "twelve-char-pass2", "Other")
        assert False, "expected PermissionError"
    except PermissionError:
        pass
