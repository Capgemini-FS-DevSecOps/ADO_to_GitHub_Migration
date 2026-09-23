"""Shared constants for the migration agent."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ado2gh.api.settings_models import AgentRuntimeSettings

NO_LLM_CONFIGURED_MESSAGE = (
    "No LLM models are configured. Go to **Settings → LLM models** to add, "
    "validate, and enable a model, then start a new chat."
)

LLM_TIMEOUT_SECONDS = 60
MAX_PEV_RETRIES = 3
MAX_ITERATIONS = 20
GRAPH_RECURSION_LIMIT = 40
PLANNER_MAX_RESEARCH_ROUNDS = 10
PLANNER_MIN_RESEARCH_TOOL_CALLS = 2
VALIDATOR_MAX_TOOL_ROUNDS = 12
VALIDATOR_MIN_TOOL_CALLS_PIPELINES = 4
SSE_HEARTBEAT_INTERVAL_SECONDS = 15

#: Token budget the orchestrator trims accumulated conversation history to before calling the language model.
CONTEXT_TOKEN_BUDGET = 32000

# ─── Agent-service HTTP boundary limits ───────────────────────────────
# Every value below bounds something a client controls: how much text one
# request may carry, how long a server-sent event stream (SSE) may run, and how much session
# state the process keeps resident (THR-10-001, THR-10-002).
MAX_CHAT_MESSAGE_CHARS = 20_000
MAX_FORM_SUBMISSION_CHARS = 100_000
SSE_MAX_EVENTS_PER_STREAM = 2_000
MAX_IN_MEMORY_SESSIONS = 200
SESSION_IDLE_TTL_SECONDS = 24 * 60 * 60
HEALTH_GRAPH_COMPILE_TIMEOUT_SECONDS = 5.0

# HTTP method assumed for a tool call that omits one. Matches the accelerator
# and GitHub tool schemas' own default (`CallAcceleratorArgs`, `GitHubApiArgs`
# in tools/orchestrator_tools.py; `executor_tools.call_accelerator`), so an
# omitted method is judged as the read those tools actually perform rather
# than as a write (GAP-086, THR-06-007).
DEFAULT_HTTP_METHOD = "GET"

# Guardrail tool identity for the deterministic executor's own accelerator
# writes (`nodes/executor/scope.py::execute_migration_scope`), evaluated only
# for a live (non-dry-run) write. Kept distinct from `call_accelerator` so the
# guardrail's model-tool dry-run short-circuit does not apply here: the
# deterministic path legitimately posts to the accelerator during dry-run
# because the accelerator itself previews safely, whereas the model-tool
# path must never post at all during dry-run.
DETERMINISTIC_SCOPE_WRITE_TOOL = "executor_scope_write"


def agent_runtime_settings() -> "AgentRuntimeSettings":
    """Load the effective agent runtime limits, honouring an operator override.

    The module constants above stay the single default source: an
    ``AgentRuntimeSettings`` instance built with no persisted settings carries
    the exact same values. This accessor lets a value saved through
    ``AdvancedSettings.agent_runtime`` take priority over the constant.

    Returns:
        AgentRuntimeSettings: The persisted override when the settings store
        loads cleanly, otherwise the constructor's defaults (which mirror the
        constants above). Never raises — a missing or broken settings file is
        not a reason to fail a running agent turn.

    """
    try:
        from dataclasses import asdict

        from ado2gh.api.settings_models import AgentRuntimeSettings
        from ado2gh.api.settings_store import SettingsStore

        raw = SettingsStore().load().advanced.agent_runtime
        if isinstance(raw, AgentRuntimeSettings):
            return raw
        # ``SettingsStore.load()`` now rehydrates every dataclass-typed field
        # of ``AdvancedSettings`` (this one included) back into its declared
        # type, so ``raw`` should already be an ``AgentRuntimeSettings``
        # instance in the branch above. This dict branch is a fallback for a
        # settings file saved by an older build before that rehydration
        # existed, or loaded through some other path that skips it — rebuild
        # it the same way the store does: defaults first, saved keys on top,
        # so a settings file saved before this field existed still loads.
        if isinstance(raw, dict):
            return AgentRuntimeSettings(**{**asdict(AgentRuntimeSettings()), **raw})
        return AgentRuntimeSettings()
    except Exception:
        from ado2gh.api.settings_models import AgentRuntimeSettings

        return AgentRuntimeSettings()
