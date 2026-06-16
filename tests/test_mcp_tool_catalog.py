"""Tests for shared MCP/HTTP tool catalog."""
import json
import subprocess
import sys

from ado2gh.agents.local.tool_catalog import (
    get_tool,
    list_tools,
    executor_allowlist,
    invoke_tool_http,
    default_arguments,
)


def test_catalog_lists_core_tools():
    names = {t.name for t in list_tools()}
    assert "ado2gh_discover" in names
    assert "ado2gh_plan_phase" in names
    assert "ado2gh_enqueue_job" in names


def test_unknown_tool_returns_none():
    assert get_tool("ado2gh_unknown") is None


def test_executor_allowlist_includes_mutations():
    allow = executor_allowlist()
    assert "ado2gh_enqueue_job" in allow
    assert "ado2gh_rollback" in allow


def test_dry_run_default_on_enqueue():
    tool = get_tool("ado2gh_enqueue_job")
    args = default_arguments(tool, {})
    assert args.get("dry_run", True) is True


def test_invoke_unreachable_accelerator():
    out = invoke_tool_http(
        "ado2gh_plan_phase",
        {"config_path": "migration.yaml"},
        "http://127.0.0.1:1",
    )
    assert out.get("error") == "accelerator_unreachable"
    assert out.get("remediation_steps")


def test_mcp_stdio_tools_list():
    line = json.dumps({"method": "tools/list"})
    proc = subprocess.run(
        [sys.executable, "-m", "services.agent.mcp_server"],
        input=line + "\n",
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0
    data = json.loads(proc.stdout.strip())
    tools = data.get("tools", [])
    assert any(t["name"] == "ado2gh_discover" for t in tools)
