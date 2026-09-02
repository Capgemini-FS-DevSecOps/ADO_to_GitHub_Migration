"""Guardrails for operator message analysis — enforce LLM-structured intake policy."""
from __future__ import annotations

from typing import Any

from ado2gh.agents.migration_agent.hitl.schemas import OperatorMessageAnalysis


def apply_analysis_guardrails(
    analysis: OperatorMessageAnalysis,
    user_message: str = "",
    *,
    from_form: bool = False,
) -> OperatorMessageAnalysis:
    """Re-validate analysis so repository intake matches repository_named_in_message."""
    del user_message  # policy is entirely LLM-structured; message text is not pattern-matched
    if from_form:
        return analysis
    return OperatorMessageAnalysis.model_validate(analysis.model_dump())


def repository_confirmed_for_turn(
    repository_id: str,
    analysis: OperatorMessageAnalysis | None,
    *,
    session: dict[str, Any] | None = None,
) -> bool:
    """True when repository_id is confirmed for this turn (named in message or already in session)."""
    if not repository_id:
        return False
    if session:
        from ado2gh.agents.migration_agent.hitl.intake import intake_from_session
        from ado2gh.agents.migration_agent.utils import find_discovery_repo, normalize_repo_key

        target = normalize_repo_key(repository_id)
        session_repo = str(session.get("plan_repository_id") or "").strip()
        if session_repo and normalize_repo_key(session_repo) == target:
            return True
        intake_repo = str(intake_from_session(session).resolved_repository_id() or "").strip()
        if intake_repo and normalize_repo_key(intake_repo) == target:
            return True
        discovery = session.get("discovery_snapshot")
        repos = discovery.get("repos", []) if isinstance(discovery, dict) else []
        if repos:
            for candidate in {repository_id, session_repo, intake_repo}:
                if not candidate:
                    continue
                match = find_discovery_repo(candidate, repos)
                if match:
                    from ado2gh.agents.migration_agent.utils import canonical_repo_id

                    if normalize_repo_key(canonical_repo_id(match)) == target:
                        return True
                    break
    if analysis is None:
        return False
    if not analysis.repository_named_in_message or not analysis.repository_id:
        return False
    from ado2gh.agents.migration_agent.utils import normalize_repo_key

    return normalize_repo_key(analysis.repository_id) == normalize_repo_key(repository_id)
