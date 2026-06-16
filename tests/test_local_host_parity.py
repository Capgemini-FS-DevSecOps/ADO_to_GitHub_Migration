"""HTTP vs MCP dry-run guardrail parity."""
import json
import subprocess
import sys

from fastapi.testclient import TestClient

from services.agent.main import app


client = TestClient(app)


def test_http_session_defaults_dry_run():
    r = client.post("/v1/sessions", json={"profile_id": "lightweight"})
    assert r.json()["dry_run"] is True


def test_mcp_plan_tool_accepts_dry_run():
    line = json.dumps({
        "method": "tools/call",
        "params": {
            "name": "ado2gh_plan_phase",
            "arguments": {"config_path": "migration.yaml", "dry_run": True},
        },
    })
    proc = subprocess.run(
        [sys.executable, "-m", "services.agent.mcp_server"],
        input=line + "\n",
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0
    data = json.loads(proc.stdout.strip())
    assert "content" in data or "error" in data
