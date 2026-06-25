"""Unit tests for tool bindings — planner tools, role-based access, bind_tools."""
import pytest
from unittest.mock import MagicMock

from ado2gh.agents.migration_agent.tools.planner_tools import get_planner_tools
from ado2gh.agents.migration_agent.tools.executor_tools import get_executor_tools
from ado2gh.agents.migration_agent.tools.validator_tools import get_validator_tools
from ado2gh.agents.migration_agent.tools.orchestrator_tools import get_orchestrator_tools


def test_planner_tools_list_not_empty():
    tools = get_planner_tools()
    assert len(tools) >= 4


def test_executor_tool_names():
    tools = get_executor_tools()
    names = {t.name for t in tools}
    assert "github_api" in names
    assert "github_api_query" not in names
    assert "ado_api_query" in names


def test_planner_tool_names():
    tools = get_planner_tools()
    names = {t.name for t in tools}
    assert "get_current_profile" in names
    assert "ado_api_query" in names
    assert "github_api_query" in names


def test_all_agent_tools_include_get_current_profile():
    for getter in (get_planner_tools, get_executor_tools, get_validator_tools, get_orchestrator_tools):
        names = {t.name for t in getter()}
        assert "get_current_profile" in names


def test_planner_tools_are_structured():
    from langchain_core.tools import StructuredTool
    tools = get_planner_tools()
    for tool in tools:
        assert isinstance(tool, StructuredTool)


def test_planner_tools_have_descriptions():
    tools = get_planner_tools()
    for tool in tools:
        assert tool.description
        assert len(tool.description) > 10


def test_planner_tools_have_args_schema():
    tools = get_planner_tools()
    for tool in tools:
        assert tool.args_schema is not None


@pytest.mark.asyncio
async def test_get_current_profile_no_accel():
    tools = get_planner_tools(accel_get=None)
    profile_tool = next(t for t in tools if t.name == "get_current_profile")
    result = await profile_tool.ainvoke({})
    assert result["error"] == "accelerator_unavailable"


@pytest.mark.asyncio
async def test_get_current_profile_with_accel():
    async def mock_get(url, session_token=None):
        if url == "/v1/settings":
            return {
                "active_profile_id": "prof-1",
                "migration_profiles": [
                    {
                        "id": "prof-1",
                        "name": "Dev",
                        "ado_org_url": "https://dev.azure.com/acme",
                        "gh_org": "acme-github",
                    }
                ],
            }
        return {}

    session = {"profile_id": "lightweight", "dry_run": True}
    tools = get_planner_tools(
        accel_get=mock_get,
        session_token="tok",
        session_getter=lambda: session,
    )
    profile_tool = next(t for t in tools if t.name == "get_current_profile")
    result = await profile_tool.ainvoke({})
    assert result["migration_profile_id"] == "prof-1"
    assert result["api_access"]["ado_org_url"] == "https://dev.azure.com/acme"
    assert result["api_access"]["gh_org"] == "acme-github"
    assert result["dry_run"] is True


def test_bind_tools_integration():
    """Test that bind_tools can be called on a mock LLM."""
    class FakeLLM:
        def bind_tools(self, tools):
            self._bound_tools = tools
            return self

    llm = FakeLLM()
    tools = get_planner_tools()
    llm_with_tools = llm.bind_tools(tools)
    assert hasattr(llm_with_tools, "_bound_tools")
    assert len(llm_with_tools._bound_tools) >= 4
