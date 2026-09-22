"""Data models for migration settings and profiles."""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from ado2gh.api.phase_definitions import default_phase_definitions
from ado2gh.models import DEFAULT_MIGRATION_STRATEGY


def _settings_path() -> Path:
    """Locate the JSON file that backs the UI settings store.

    Returns:
        Path: ``ui_settings.json`` inside the directory named by the
        ``ADO2GH_DATA_DIR`` environment variable, or inside the current
        working directory when that variable is unset.

    """
    base = os.environ.get("ADO2GH_DATA_DIR", ".")
    return Path(base) / "ui_settings.json"


@dataclass
class GitHubToken:
    """One named GitHub credential belonging to a migration profile.

    The secret itself is held in ``token`` and is never included in the
    public representation returned by :meth:`to_public`.
    """

    id: str
    name: str
    token: str = ""
    note: str = ""
    created_at: str = ""
    updated_at: str = ""
    last_validated_at: str = ""
    last_validation: dict[str, Any] = field(default_factory=dict)

    def to_public(self) -> dict[str, Any]:
        """Render the token for API responses with the secret withheld.

        Returns:
            dict[str, Any]: The token identifier, display name, note and
            validation timestamps, with the secret replaced by a fixed
            mask marker when one is stored and an empty string when it is
            not. The raw credential is never present.

        """
        return {
            "id": self.id,
            "name": self.name,
            "token": "***" if self.token else "",
            "note": self.note,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_validated_at": self.last_validated_at,
            "last_validation": self.last_validation,
        }


@dataclass
class MigrationProfile:
    """One source ADO organisation → target GitHub organisation migration configuration."""

    id: str
    name: str
    ado_org_url: str = ""
    ado_pat: str = ""
    gh_org: str = ""
    github_tokens: list[GitHubToken] = field(default_factory=list)
    status: str = "active"
    is_default: bool = False
    submitted_by: str = ""
    approval: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    last_scan_at: str = ""
    scan_summary: dict[str, Any] = field(default_factory=dict)

    def to_public(self) -> dict[str, Any]:
        """Render the profile for API responses with secrets withheld.

        Returns:
            dict[str, Any]: The profile identity, source ADO organization
            URL, target GitHub organization, lifecycle status, approval
            record, timestamps and last scan summary, plus each GitHub
            token in its own masked public form. The stored ADO personal
            access token is replaced by a fixed mask marker when present
            and an empty string when absent.

        """
        return {
            "id": self.id,
            "name": self.name,
            "ado_org_url": self.ado_org_url,
            "ado_pat": "***" if self.ado_pat else "",
            "gh_org": self.gh_org,
            "github_tokens": [t.to_public() for t in self.github_tokens],
            "status": self.status,
            "is_default": self.is_default,
            "submitted_by": self.submitted_by,
            "approval": self.approval,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_scan_at": self.last_scan_at,
            "scan_summary": self.scan_summary,
        }


@dataclass
class ConcurrencySettings:
    """The single source of the default worker-pool sizes and API rate ceilings.

    ``ado2gh/core/concurrency.py`` reads these same defaults for its
    ``ConcurrencyConfig`` dataclass and its ``from_dict`` fallback values, so a
    limit is changed once, here, rather than in two places that can drift
    apart.
    """

    max_git_workers: int = 4
    """How many git clone/push operations may run at the same time."""

    max_repo_workers: int = 8
    """How many repositories may be migrated at the same time."""

    max_pipeline_workers: int = 16
    """How many pipeline conversions may run at the same time."""

    max_ado_rps: float = 10.0
    """The ceiling on Azure DevOps API requests per second."""

    max_gh_rps: float = 20.0
    """The ceiling on GitHub API requests per second."""


def _agent_constant(name: str) -> int:
    """Read one integer default off the migration agent's constants module.

    The import is deferred to when a dataclass default is actually built,
    not done at the top of this module: ``ado2gh.agents.migration_agent``
    transitively imports ``ado2gh.api`` (its node, tool and session modules
    reach back into this package), so importing its ``constants`` submodule
    here at load time would cycle back into this file before it finishes
    loading. Same lazy-import pattern commit cc5bf55 used for
    ``ConcurrencySettings``, just in the opposite direction.

    Args:
        name: The constant's name in ``ado2gh.agents.migration_agent.constants``.

    Returns:
        int: The constant's current value.

    """
    from ado2gh.agents.migration_agent import constants as agent_constants

    return int(getattr(agent_constants, name))


@dataclass
class AgentRuntimeSettings:
    """Operator-tunable overrides for the migration agent's runtime limits.

    Every field defaults to the same value as the matching module constant in
    ``ado2gh/agents/migration_agent/constants.py``, so a settings file with no
    ``agent_runtime`` block behaves exactly as before. ``constants.py`` stays
    the single default source; this dataclass only lets a persisted value
    override it, read back through ``constants.agent_runtime_settings()``.
    """

    llm_timeout_seconds: int = field(default_factory=lambda: _agent_constant("LLM_TIMEOUT_SECONDS"))
    """Seconds the language model bridge waits for one call before it times out."""

    max_pev_retries: int = field(default_factory=lambda: _agent_constant("MAX_PEV_RETRIES"))
    """Plan-execute-validate loop retries allowed before escalating to the operator."""

    max_iterations: int = field(default_factory=lambda: _agent_constant("MAX_ITERATIONS"))
    """Total graph iterations allowed in one turn before the run stops itself."""

    graph_recursion_limit: int = field(default_factory=lambda: _agent_constant("GRAPH_RECURSION_LIMIT"))
    """LangGraph recursion ceiling passed to each graph run."""

    planner_max_research_rounds: int = field(default_factory=lambda: _agent_constant("PLANNER_MAX_RESEARCH_ROUNDS"))
    """Research rounds the planner may run before it must produce a plan."""

    planner_min_research_tool_calls: int = field(default_factory=lambda: _agent_constant("PLANNER_MIN_RESEARCH_TOOL_CALLS"))
    """Read-only tool calls the planner must make before its research counts as done."""

    validator_max_tool_rounds: int = field(default_factory=lambda: _agent_constant("VALIDATOR_MAX_TOOL_ROUNDS"))
    """Evidence-gathering rounds the validator may run before it must report a result."""

    validator_min_tool_calls_pipelines: int = field(
        default_factory=lambda: _agent_constant("VALIDATOR_MIN_TOOL_CALLS_PIPELINES"),
    )
    """Tool calls the validator must make before a pipeline validation counts as thorough."""

    sse_heartbeat_interval_seconds: int = field(default_factory=lambda: _agent_constant("SSE_HEARTBEAT_INTERVAL_SECONDS"))
    """Seconds between heartbeat events on a server-sent event stream."""

    context_token_budget: int = field(default_factory=lambda: _agent_constant("CONTEXT_TOKEN_BUDGET"))
    """Token budget the agent trims accumulated conversation history to before calling the language model."""

    planner_repository_sample_limit: int = 20
    """Repositories from discovery included as a sample in the planner's prompt."""

    planner_context_max_chars: int = 2000
    """Characters kept of the serialised prior plan included in the planner's revision prompt."""

    context_cycle_retention_count: int = 2
    """Most recent plan-execute-validate loop cycles kept in full instead of summarised."""

    old_cycle_summary_max_chars: int = 2000
    """Characters kept of the summarised older plan-execute-validate loop cycles."""

    recent_cycle_summary_max_chars: int = 3000
    """Characters kept of the summarised recent plan-execute-validate loop cycles."""


@dataclass
class AdvancedSettings:
    """Deployment-wide migration defaults shared by every profile.

    Covers file locations, the default execution mode, the migration
    strategy, concurrency limits, the configured rollout phases and the
    policy rules applied to generated workflows.
    """

    config_path: str = "migration.yaml"
    db_path: str = "migration_state.db"
    dry_run_default: bool = True
    migration_strategy: str = DEFAULT_MIGRATION_STRATEGY
    default_phase: str = "poc"
    repo_parallel: int = 4
    pipeline_parallel: int = 8
    output_dir: str = "output"
    phases: list[dict[str, Any]] = field(default_factory=list)
    workflow_layout_policy: str = "modular"
    policy_rules: dict[str, Any] = field(default_factory=dict)
    concurrency: ConcurrencySettings = field(default_factory=ConcurrencySettings)
    """The worker-pool and rate-limit defaults new ``ConcurrencyManager`` instances start from."""

    agent_runtime: AgentRuntimeSettings = field(default_factory=AgentRuntimeSettings)
    """The migration agent's iteration, retry, research-round and context-budget limits."""


@dataclass
class UISettings:
    """The complete persisted state of the accelerator settings store.

    Holds every migration profile, which one is currently active, the
    shared advanced settings and the per-profile operator resolutions.
    """

    active_profile_id: Optional[str] = None
    migration_profiles: list[MigrationProfile] = field(default_factory=list)
    advanced: AdvancedSettings = field(default_factory=AdvancedSettings)
    operator_resolutions: dict[str, dict[str, str]] = field(default_factory=dict)

    def to_public(self) -> dict[str, Any]:
        """Render the whole settings document for API responses.

        Returns:
            dict[str, Any]: The active profile identifier, every migration
            profile in its masked public form, and the advanced settings.
            When no rollout phases have been configured the advanced block
            is filled in with the built-in default phase definitions so
            callers always receive a usable phase list. Operator
            resolutions are deliberately omitted.

        """
        adv = asdict(self.advanced)
        if not adv.get("phases"):
            adv["phases"] = [p.to_dict() for p in default_phase_definitions()]
        return {
            "active_profile_id": self.active_profile_id,
            "migration_profiles": [p.to_public() for p in self.migration_profiles],
            "advanced": adv,
        }
