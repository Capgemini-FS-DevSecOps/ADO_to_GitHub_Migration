"""LLM-driven operator message analysis with Pydantic structured output."""
from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from ado2gh.agents.migration_agent.hitl.schemas import (
    OperatorIntent,
    OperatorMessageAnalysis,
)

_ANALYSIS_SYSTEM_PROMPT = """You analyze operator messages for an Azure DevOps → GitHub migration assistant.

Respond with JSON matching the OperatorMessageAnalysis schema exactly.

## Intent
- general_chat: greetings, unrelated questions, or chit-chat
- migration_info: status, discovery, inventory, plan details, progress questions
- migration_action: requests to migrate, plan, execute, retry, or change execution mode

## repository_named_in_message (required judgment)
Set true only when user_message itself contains an explicit repository reference
(Project/RepoName, a discoverable repo name, or unambiguous short name).
Set false when the request is vague, generic, or would require guessing — including
messages that only say "migrate", "migrate something", "another one", "next repo",
or that refer to session context without naming a repo in user_message.
Never set true based solely on session_context.plan_repository_id or known_repositories.
When plan_repository_id is already set and user_message only supplies other intake fields
(e.g. "dry run", "live", a phase name), repository_named_in_message must be false — that is
expected; the repository was collected on a prior turn or form submission in this session.

Each agent session is isolated. session_context only describes the current session_id.
Do not assume repository or plan state from other sessions or from discovery alone.

## Intake fields (use null when not stated or unclear)
- repository_id: set only when repository_named_in_message is true; otherwise null
- dry_run: true = dry-run/simulation only, false = live migration
- phase: only when operator names poc, pilot, wave1, wave2, or wave3
- plan_confirmed / confirm_execute / plan_notes: only when operator is responding about an existing plan
- cancellation_action: "stop" or "rollback" only for explicit cancellation choices
- requests_new_migration: true when operator wants a different migration without naming any repo
- is_cancellation: true for explicit cancel/stop/abort migration (not casual "stop" in other contexts)

## reasoning
Write 1–3 sentences explaining how you interpreted the message. This is shown to the operator."""


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    if cleaned.startswith("{"):
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if match:
        parsed = json.loads(match.group())
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("no JSON object in LLM response")


def _build_analysis_context(session: dict[str, Any]) -> dict[str, Any]:
    discovery = session.get("discovery_snapshot") or {}
    repos = discovery.get("repos", []) if isinstance(discovery, dict) else []
    repo_names: list[str] = []
    for repo in repos[:20]:
        if isinstance(repo, dict):
            project = repo.get("project", "")
            name = repo.get("repo_name") or repo.get("name") or ""
            if project and name:
                repo_names.append(f"{project}/{name}")
            elif name:
                repo_names.append(str(name))
    return {
        "session_status": session.get("status", "idle"),
        "session_id": session.get("session_id"),
        "plan_repository_id": session.get("plan_repository_id"),
        "last_completed_repository_id": session.get("last_completed_repository_id"),
        "dry_run": session.get("dry_run"),
        "execution_mode_confirmed": session.get("execution_mode_confirmed"),
        "has_migration_plan": bool(session.get("migration_plan")),
        "plan_approved": bool(session.get("plan_approved")),
        "known_repositories": repo_names[:80],
        "repository_count": len(repo_names),
    }


async def analyze_operator_message(
    llm: Any,
    user_message: str,
    session: dict[str, Any],
) -> OperatorMessageAnalysis | None:
    """Classify intent and extract intake fields via LLM + Pydantic."""
    if not llm or not (user_message or "").strip():
        return None

    human_payload = json.dumps({
        "user_message": user_message.strip(),
        "session_context": _build_analysis_context(session),
    })
    messages = [
        SystemMessage(content=_ANALYSIS_SYSTEM_PROMPT),
        HumanMessage(content=human_payload),
    ]

    try:
        if hasattr(llm, "with_structured_output"):
            structured = llm.with_structured_output(OperatorMessageAnalysis)
            result = await structured.ainvoke(messages)
            if isinstance(result, OperatorMessageAnalysis):
                return OperatorMessageAnalysis.model_validate(result.model_dump())
    except Exception:
        pass

    try:
        response = await llm.ainvoke(messages)
        raw = response.content if hasattr(response, "content") else str(response)
        if isinstance(raw, list):
            raw = "".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in raw
            )
        parsed = _extract_json_object(str(raw))
        intent_raw = str(parsed.get("intent", "general_chat")).lower()
        for label in OperatorIntent:
            if label.value in intent_raw:
                parsed["intent"] = label.value
                break
        else:
            parsed["intent"] = OperatorIntent.GENERAL_CHAT.value
        return OperatorMessageAnalysis.model_validate(parsed)
    except Exception:
        return None
