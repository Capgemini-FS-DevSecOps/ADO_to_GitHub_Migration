"""Agent IDE session lifecycle and guardrails."""
from fastapi.testclient import TestClient

from services.agent.main import app


client = TestClient(app)


def test_session_dry_run_default():
    r = client.post("/v1/sessions", json={"profile_id": "lightweight", "dry_run": True})
    assert r.status_code == 200
    assert r.json()["dry_run"] is True


def test_session_get_messages():
    created = client.post("/v1/sessions", json={"profile_id": "lightweight", "prompt": "hi"})
    sid = created.json()["session_id"]
    r = client.get(f"/v1/sessions/{sid}")
    assert r.status_code == 200
    body = r.json()
    assert "messages" in body
    assert len(body["messages"]) >= 1


def test_request_live_sets_live_approval_pending():
    created = client.post("/v1/sessions", json={"profile_id": "lightweight", "dry_run": True})
    sid = created.json()["session_id"]
    r = client.post(f"/v1/sessions/{sid}/request-live")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "idle"
    assert body.get("live_approval_status") == "pending"


def test_approve_session():
    created = client.post("/v1/sessions", json={"profile_id": "lightweight", "dry_run": True})
    sid = created.json()["session_id"]
    client.post(f"/v1/sessions/{sid}/request-live")
    r = client.post(f"/v1/sessions/{sid}/approve", json={"approved": True, "reason": "test"})
    assert r.status_code == 200


def test_session_message():
    created = client.post("/v1/sessions", json={"profile_id": "lightweight"})
    sid = created.json()["session_id"]
    r = client.post(f"/v1/sessions/{sid}/message", json={"message": "add wave 2"})
    assert r.status_code == 200
    assert "reply" in r.json()


def test_session_isolation():
    a = client.post("/v1/sessions", json={"profile_id": "lightweight", "prompt": "session-a"})
    b = client.post("/v1/sessions", json={"profile_id": "lightweight", "prompt": "session-b"})
    sid_a, sid_b = a.json()["session_id"], b.json()["session_id"]
    assert sid_a != sid_b
    msgs_a = client.get(f"/v1/sessions/{sid_a}").json()["messages"]
    msgs_b = client.get(f"/v1/sessions/{sid_b}").json()["messages"]
    assert msgs_a != msgs_b
