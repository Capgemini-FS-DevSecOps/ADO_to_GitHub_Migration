"""messaging.py module."""
from __future__ import annotations

from typing import Any

# ─── Inter-agent messaging (T052) ─────────────────────────────────────

def _make_inter_agent_message(
    from_role: str,
    to_role: str,
    message_type: str,
    payload: dict[str, Any],
    correlation_ids: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create an inter-agent message for AgentState.inter_agent_messages."""
    from datetime import datetime, timezone
    return {
        "message_id": f"{from_role}_{to_role}_{message_type}_{datetime.now(timezone.utc).isoformat()}",
        "from_role": from_role,
        "to_role": to_role,
        "message_type": message_type,
        "payload": payload,
        "correlation_ids": correlation_ids or {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ─── PEV Cycle Summary (T054) ─────────────────────────────────────────

def _make_cycle_summary(
    cycle_number: int,
    executor_result: dict[str, Any],
    validation_result: dict[str, Any],
    next_action: str,
) -> dict[str, Any]:
    """Create a PevCycleSummary after each PEV cycle."""
    per_repo = executor_result.get("per_repo_results", [])
    failures = validation_result.get("failures", [])
    return {
        "cycle_number": cycle_number,
        "repos_processed": len(per_repo),
        "repos_succeeded": len(per_repo) - len(failures),
        "repos_failed": len(failures),
        "failures": failures[:10],  # Cap to prevent overflow
        "next_action": next_action,
    }

