"""Immutable audit event writer with secret redaction (CA-003).

``redact_payload`` is the platform's single secret-masking choke point (FR-025).
``ado2gh.agents.migration_agent.utils.mask_secrets``,
``ado2gh.core.scopes.git_scope._redact`` and the root log handler installed by
``ado2gh.logging_config`` all delegate here, so a shape recognised in one place
is recognised everywhere.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

_MASK = "***"
_MAX_DEPTH = 20

# Secret *value* shapes. One combined alternation, deliberately: this runs on
# every audit write, agent message, SSE frame and log record, so it must stay a
# single scan rather than one pass per pattern.
_SECRET_VALUE_RE = re.compile(
    # GitHub token prefixes (ghp_/gho_/ghu_/ghs_/ghr_), fine-grained PATs, pat-
    r"(?P<pfx>gh[a-z]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|pat-[A-Za-z0-9]+)"
    # `Authorization: Bearer <token>` copied into free text
    r"|(?P<bearer>bearer\s+[A-Za-z0-9._~+/=-]+)"
    # bare Azure DevOps PAT: 52 opaque alphanumerics, no prefix at all
    r"|(?P<opaque>(?<![A-Za-z0-9])[A-Za-z0-9]{52}(?![A-Za-z0-9]))"
    # "token": "…" inside an already-serialised JSON blob
    r'|(?P<jsonkv>"(?:token|password|secret|pat|api[_-]?key)"\s*:\s*)"[^"]*"'
    # key=value / key: value in free text (absorbed from the agent's mask_secrets)
    r"|(?P<kv>(?:password|passwd|secret|token|api[_-]?key|apikey|pat)\s*[=:]\s*)"
    r"[A-Za-z0-9_\-./+=]{8,}",
    re.IGNORECASE,
)

# Secret *key* names, matched on the lower-cased key so camelCase collapses too
# (`clientSecret` -> `clientsecret`, `apiKey` -> `apikey`). Word-boundary-ish on
# the first branch so `db_path`, `max_tokens` and `monkeypatch` are not hits.
_SECRET_KEY_RE = re.compile(
    r"(?:^|_)(?:pat|passwd|password|secret|token|api[_-]?key|credentials?"
    r"|authorization)(?:$|_)"
    r"|(?:client|api|access|private|secret|auth)_?(?:key|secret|token)"
)


def _mask_match(m: "re.Match[str]") -> str:
    """Replacement for one `_SECRET_VALUE_RE` hit, keyed on which branch fired."""
    name = m.lastgroup
    if name == "jsonkv":
        return f'{m.group("jsonkv")}"{_MASK}"'
    if name == "kv":
        return m.group("kv") + _MASK
    if name == "bearer":
        return f'{m.group(0).split(None, 1)[0]} {_MASK}'
    return m.group(0)[:4] + _MASK  # pfx / opaque: keep a 4-char prefix for triage


def _is_secret_key(key: Any) -> bool:
    return isinstance(key, str) and _SECRET_KEY_RE.search(key.lower()) is not None


def redact_payload(payload: Any, _depth: int = 0) -> Any:
    """Recursively redact likely secrets from any payload, message, or log record.

    Matches secret **key names** (case- and affix-insensitive: `ado_pat`,
    `GH_TOKEN`, `clientSecret`, `apiKey`) *and* secret **value shapes** (GitHub
    token prefixes, `Bearer <token>`, a bare 52-character ADO PAT, `key=value`
    pairs) — key-name matching alone cannot reach a secret that arrives inside
    free text or as a bare list element.
    """
    if payload is None or isinstance(payload, (bool, int, float)):
        return payload
    if isinstance(payload, str):
        if len(payload) < 5:  # nothing we match is shorter
            return payload
        return _SECRET_VALUE_RE.sub(_mask_match, payload)
    if _depth >= _MAX_DEPTH:
        return _MASK  # fail safe: pathological nesting / cycle -> redact
    if isinstance(payload, dict):
        return {
            k: (
                (v[:4] + _MASK if len(v) > 4 else _MASK)
                if _is_secret_key(k) and isinstance(v, str)
                else redact_payload(v, _depth + 1)
            )
            for k, v in payload.items()
        }
    if isinstance(payload, (list, tuple)):
        out = [redact_payload(x, _depth + 1) for x in payload]
        return tuple(out) if isinstance(payload, tuple) else out
    return payload


class AuditWriter:
    """Append-only audit events to StateDB."""

    def __init__(self, db: Any):
        self.db = db

    def write(
        self,
        event_type: str,
        profile_id: str,
        actor: str = "",
        payload: Optional[dict] = None,
    ) -> str:
        """Record an audit event; returns event id."""
        event_id = f"aud_{uuid4().hex[:12]}"
        safe = redact_payload(payload or {})
        self.db.insert_audit_event(
            event_id=event_id,
            event_type=event_type,
            profile_id=profile_id,
            actor=actor,
            payload_json=json.dumps(safe),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        return event_id
