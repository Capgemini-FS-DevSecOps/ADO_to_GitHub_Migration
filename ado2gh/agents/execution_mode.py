"""Parse and apply dry-run vs live execution mode from operator chat."""
from __future__ import annotations

import re
from typing import Any

_LIVE_PATTERNS = (
    r"\bno\s+dry[\s-]?run\b",
    r"\bnot\s+a?\s*dry[\s-]?run\b",
    r"\bwithout\s+dry[\s-]?run\b",
    r"\bdry[\s-]?run\s+(should\s+be\s+)?false\b",
    r"\bdry_run\s*=\s*false\b",
    r"\blive\s+(migration|run|execute|mode)\b",
    r"\b(execute|run|migrate)\s+live\b",
    r"\b(migrate|migration|execute|run)\b.*\blive\b",
    r"\blive\b.*\b(migrate|migration|execute|run)\b",
    r"\bfor\s+real\b",
    r"\bnot\s+dry\b",
)

_DRY_PATTERNS = (
    r"\bdry[\s-]?run\b",
    r"\bdry_run\s*=\s*true\b",
    r"\bsimulation\s+only\b",
)


def parse_execution_mode_from_message(message: str) -> bool | None:
    """Return True for dry-run, False for live, None if message does not specify mode."""
    msg = (message or "").lower()
    if not msg.strip():
        return None
    for pat in _LIVE_PATTERNS:
        if re.search(pat, msg):
            return False
    for pat in _DRY_PATTERNS:
        if re.search(pat, msg):
            return True
    return None


def apply_execution_mode_from_message(session: dict[str, Any], message: str) -> bool:
    """Update session dry_run from operator text. Returns True when mode changed."""
    parsed = parse_execution_mode_from_message(message)
    if parsed is None:
        return False
    previous = bool(session.get("dry_run", True))
    if previous == parsed:
        return False
    session["dry_run"] = parsed
    session.pop("migration_plan", None)
    return True
