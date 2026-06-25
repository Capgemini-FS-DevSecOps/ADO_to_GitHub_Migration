"""Audit history search, pagination, and export."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ado2gh.api.agentic_routes import router
from ado2gh.assignments.audit import AuditWriter
from ado2gh.state.audit_query import audit_events_to_csv
from ado2gh.state.db import StateDB


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "hist_api.db")
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", db_path)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_search_audit_events_filters(tmp_path):
    db = StateDB(str(tmp_path / "audit.db"))
    writer = AuditWriter(db)
    writer.write("user.login", "p1", actor="alice", payload={"ok": True})
    writer.write("profile.updated", "p1", actor="bob", payload={"field": "name"})
    writer.write("user.login", "p2", actor="alice", payload={"ok": True})

    by_actor = db.search_audit_events(actor="alice", limit=10, offset=0)
    assert len(by_actor) == 2

    by_type = db.search_audit_events(event_type="profile.updated", limit=10, offset=0)
    assert len(by_type) == 1
    assert by_type[0]["actor"] == "bob"

    by_profile = db.search_audit_events(profile_id="p1", limit=10, offset=0)
    assert len(by_profile) == 2
    assert db.count_audit_events(profile_id="p1") == 2

    by_search = db.search_audit_events(search="profile", limit=10, offset=0)
    assert len(by_search) == 1


def test_audit_pagination(tmp_path):
    db = StateDB(str(tmp_path / "page.db"))
    writer = AuditWriter(db)
    for i in range(5):
        writer.write("user.login", "p1", actor=f"user{i}", payload={"n": i})

    page0 = db.search_audit_events(profile_id="p1", limit=2, offset=0)
    page1 = db.search_audit_events(profile_id="p1", limit=2, offset=2)
    assert len(page0) == 2
    assert len(page1) == 2
    assert db.count_audit_events(profile_id="p1") == 5


def test_audit_events_to_csv():
    csv_text = audit_events_to_csv([
        {
            "id": "e1",
            "created_at": "2026-01-01T00:00:00Z",
            "event_type": "user.login",
            "actor": "alice",
            "profile_id": "_platform",
            "assignment_id": None,
            "payload_json": '{"role":"admin"}',
        },
    ])
    assert "user.login" in csv_text
    assert "alice" in csv_text


def test_history_api_pagination_and_export(client):
    from ado2gh.state.factory import create_state_db

    db = create_state_db()
    writer = AuditWriter(db)
    writer.write("user.login", "p1", actor="alice")
    writer.write("profile.scan", "p1", actor="bob")

    hist = client.get("/v1/history/sessions?limit=1&offset=0&profile_id=p1")
    assert hist.status_code == 200
    body = hist.json()
    assert body["total"] == 2
    assert len(body["sessions"]) == 1

    types = client.get("/v1/history/event-types?profile_id=p1")
    assert types.status_code == 200
    assert "user.login" in types.json()["event_types"]

    export = client.get("/v1/history/sessions/export?profile_id=p1&actor=alice")
    assert export.status_code == 200
    assert "text/csv" in export.headers["content-type"]
    assert "user.login" in export.text
    assert "profile.scan" not in export.text
