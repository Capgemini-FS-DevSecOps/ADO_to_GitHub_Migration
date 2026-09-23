"""AgentRuntimeSettings must default to the constants module's own values,
and agent_runtime_settings() must read a persisted override, tolerate a
broken settings store, and load a settings file saved before this field
existed.
"""
import json

from ado2gh.agents.migration_agent import constants as agent_constants
from ado2gh.agents.migration_agent.constants import agent_runtime_settings
from ado2gh.api.settings_models import AdvancedSettings, AgentRuntimeSettings


def test_defaults_equal_the_constants():
    settings = AgentRuntimeSettings()
    assert settings.llm_timeout_seconds == agent_constants.LLM_TIMEOUT_SECONDS
    assert settings.max_pev_retries == agent_constants.MAX_PEV_RETRIES
    assert settings.max_iterations == agent_constants.MAX_ITERATIONS
    assert settings.graph_recursion_limit == agent_constants.GRAPH_RECURSION_LIMIT
    assert settings.planner_max_research_rounds == agent_constants.PLANNER_MAX_RESEARCH_ROUNDS
    assert settings.planner_min_research_tool_calls == agent_constants.PLANNER_MIN_RESEARCH_TOOL_CALLS
    assert settings.validator_max_tool_rounds == agent_constants.VALIDATOR_MAX_TOOL_ROUNDS
    assert settings.validator_min_tool_calls_pipelines == agent_constants.VALIDATOR_MIN_TOOL_CALLS_PIPELINES
    assert settings.sse_heartbeat_interval_seconds == agent_constants.SSE_HEARTBEAT_INTERVAL_SECONDS
    assert settings.context_token_budget == agent_constants.CONTEXT_TOKEN_BUDGET


def test_advanced_settings_carries_an_agent_runtime_settings_instance():
    adv = AdvancedSettings()
    assert isinstance(adv.agent_runtime, AgentRuntimeSettings)
    assert adv.agent_runtime.max_iterations == agent_constants.MAX_ITERATIONS


def test_accessor_falls_back_to_defaults_with_no_settings_file(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    settings = agent_runtime_settings()
    assert settings.context_token_budget == agent_constants.CONTEXT_TOKEN_BUDGET
    assert settings.max_iterations == agent_constants.MAX_ITERATIONS


def test_accessor_reads_a_persisted_override(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    (tmp_path / "ui_settings.json").write_text(json.dumps({
        "active_profile_id": None,
        "migration_profiles": [],
        "advanced": {"agent_runtime": {"max_iterations": 999}},
    }))
    settings = agent_runtime_settings()
    assert settings.max_iterations == 999
    # A key the saved override did not mention still falls back to the constant.
    assert settings.context_token_budget == agent_constants.CONTEXT_TOKEN_BUDGET


def test_accessor_falls_back_to_defaults_on_a_broken_settings_store(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    (tmp_path / "ui_settings.json").write_text("{not valid json")
    settings = agent_runtime_settings()
    assert settings.max_iterations == agent_constants.MAX_ITERATIONS


def test_an_old_settings_dict_without_agent_runtime_still_loads(tmp_path, monkeypatch):
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    (tmp_path / "ui_settings.json").write_text(json.dumps({
        "active_profile_id": None,
        "migration_profiles": [],
        "advanced": {"db_path": "legacy.db"},
    }))
    settings = agent_runtime_settings()
    assert settings.max_iterations == agent_constants.MAX_ITERATIONS
    assert settings.context_token_budget == agent_constants.CONTEXT_TOKEN_BUDGET
