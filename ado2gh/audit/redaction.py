"""The platform's single secret-masking choke point (CA-003, FR-025).

``redact_payload`` is the one place secret shapes are recognised.
``ado2gh.agents.migration_agent.utils.mask_secrets``,
``ado2gh.core.scopes.git_scope._redact``, the audit writer in
``ado2gh.audit.writer`` and the root log handler installed by
``ado2gh.logging_config`` all delegate here, so a shape recognised in one place
is recognised everywhere.
"""
from __future__ import annotations

import re
from typing import Final

_MASK = "***"
_MAX_DEPTH = 20

# Secret *value* shapes. One combined alternation, deliberately: this runs on
# every audit write, agent message, server-sent event (SSE) frame and log record, so it must stay a
# single scan rather than one pass per pattern.
_SECRET_VALUE_RE = re.compile(
    # GitHub token prefixes (ghp_/gho_/ghu_/ghs_/ghr_), fine-grained PATs, pat-
    r"(?P<pfx>gh[a-z]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|pat-[A-Za-z0-9]+)"
    # `Authorization: Bearer <token>` copied into free text
    r"|(?P<bearer>bearer\s+[A-Za-z0-9._~+/=-]+)"
    # bare Azure DevOps personal access token (PAT): 52 opaque alphanumerics, no prefix at all
    r"|(?P<opaque>(?<![A-Za-z0-9])[A-Za-z0-9]{52}(?![A-Za-z0-9]))"
    # "token": "…" inside an already-serialised JSON blob. The value itself is
    # escape-aware (`(?:\\.|[^"\\])*\\?`, the same group `_SECRET_KEY_VALUE_RE`
    # uses below) rather than plain `[^"]*`: a value that escapes its own
    # closing quote (`"password": "abc\"def"`) otherwise stops the match at
    # that escaped quote and leaves `def"` in the output (GAP-131). The
    # closing quote itself is optional (`"?`, same as `_SECRET_KEY_VALUE_RE`
    # below) so a log line truncated mid-value (`{"password": "abc`, no
    # closing quote) still gets the value it does have masked (GAP-140); the
    # value character class already excludes an unescaped quote, so on a
    # well-formed document the match still stops at the real closing quote
    # rather than running on into a second key.
    r'|(?P<jsonkv>"(?:token|password|secret|pat|api[_-]?key)"\s*:\s*)"(?:\\.|[^"\\])*\\?"?',
    re.IGNORECASE,
)

# key=value / key: value in free text (absorbed from the agent's mask_secrets).
# A second pass, run after `_SECRET_VALUE_RE`: the whole value is masked
# regardless of its length or character set (GAP-109), so it cannot share one
# alternation with shapes that keep a triage prefix. The key names are the
# same literal list the single-pass regex used before this split — only the
# value side changed — so a non-secret word such as "failed" in "failed:
# password=..." never opens a match that would swallow the real secret after it.
_SECRET_KEY_VALUE_KEYS: Final[str] = r"password|passwd|secret|token|api[_-]?key|apikey|pat"
"""Key names whose entire assigned value `redact_text` masks."""

_SECRET_VALUE_DELIMITERS: Final[str] = r"\s,;)}\]>"
"""Characters that end an unquoted value after one of `_SECRET_KEY_VALUE_KEYS`."""

_SECRET_KEY_VALUE_RE = re.compile(
    rf"(?P<key>{_SECRET_KEY_VALUE_KEYS})(?P<sep>\s*[=:]\s*)"
    # Two separate quote branches, not one class excluding both quote
    # characters: a value quoted in one style can legitimately contain the
    # other (`password="abc's"`), and a shared `[^"\']` class stops at that
    # embedded character, leaving the remainder of the secret unmasked.
    # Each branch also honors a backslash escape of its own quote character
    # (`(?:\\.|[^"\\])*` rather than plain `[^"]*`): otherwise a value such as
    # `password="abc\"def"`, which escapes the quote it opened with rather
    # than closing early, stops the match at that escaped quote and leaves
    # `def` unmasked in the output. A trailing `\\?` closes each branch: a
    # value that ends on a lone backslash has no following character for
    # `\\.` to pair with, so without this the group stops one character
    # early and that final backslash reaches the output unmasked.
    r'(?:"(?P<dqval>(?:\\.|[^"\\])*\\?)"?'
    r"|'(?P<sqval>(?:\\.|[^'\\])*\\?)'?"
    rf"|(?P<value>[^{_SECRET_VALUE_DELIMITERS}]*))",
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
    if name == "bearer":
        return f'{m.group(0).split(None, 1)[0]} {_MASK}'
    return m.group(0)[:4] + _MASK  # pfx / opaque: keep a 4-char prefix for triage


def _mask_key_value_match(m: "re.Match[str]") -> str:
    """Replacement for one `_SECRET_KEY_VALUE_RE` hit.

    Masks the whole value that follows a secret-looking key, whatever its
    length or character set, and keeps a matching quote around the mask when
    the value was quoted (GAP-109).

    Args:
        m: The regex match, carrying the key, separator, and either a quoted
            or unquoted value group.

    Returns:
        The key and separator unchanged, followed by the mask — wrapped in the
        same quote character the value was wrapped in, when there was one.
    """
    prefix = f'{m.group("key")}{m.group("sep")}'
    if m.group("dqval") is not None:
        quote = '"'
    elif m.group("sqval") is not None:
        quote = "'"
    else:
        return prefix + _MASK
    closing_quote = quote if m.group(0).endswith(quote) else ""
    return f"{prefix}{quote}{_MASK}{closing_quote}"


def _is_secret_key(key: object) -> bool:
    """Report whether a mapping key name looks like it holds a secret.

    Args:
        key: Candidate mapping key; anything that is not a string never matches.

    Returns:
        ``True`` when the lower-cased key matches the secret-name pattern.
    """
    return isinstance(key, str) and _SECRET_KEY_RE.search(key.lower()) is not None


def redact_text(text: str) -> str:
    """Mask every recognised secret *value* shape in a block of free text.

    The string half of :func:`redact_payload`, exposed on its own for callers
    that already hold rendered text — a formatted log message, a traceback — and
    need a ``str`` back rather than ``object``.

    Args:
        text: Free text that may carry a token, PAT, ``Bearer`` header or
            ``key=value`` pair.

    Returns:
        The same text with every recognised secret masked.
    """
    if len(text) < 5:  # nothing we match is shorter
        return text
    masked = _SECRET_VALUE_RE.sub(_mask_match, text)
    return _SECRET_KEY_VALUE_RE.sub(_mask_key_value_match, masked)


def redact_payload(payload: object, _depth: int = 0) -> object:
    """Recursively redact likely secrets from any payload, message, or log record.

    Matches secret **key names** (case- and affix-insensitive: `ado_pat`,
    `GH_TOKEN`, `clientSecret`, `apiKey`) *and* secret **value shapes** (GitHub
    token prefixes, `Bearer <token>`, a bare 52-character ADO PAT, `key=value`
    pairs) — key-name matching alone cannot reach a secret that arrives inside
    free text or as a bare list element.

    Args:
        payload: A string, a dict, list or tuple of values, a log record's
            argument tuple, or a scalar. Types the walker does not recognise
            pass through untouched.
        _depth: Current recursion depth; internal, used to stop on pathological
            nesting or cycles.

    Returns:
        A value of the same shape as ``payload`` with every recognised secret
        masked.
    """
    if payload is None or isinstance(payload, (bool, int, float)):
        return payload
    if isinstance(payload, str):
        return redact_text(payload)
    if _depth >= _MAX_DEPTH:
        return _MASK  # fail safe: pathological nesting / cycle -> redact
    if isinstance(payload, dict):
        return {
            # A key can itself be a secret VALUE shape (a raw token used as a
            # dict key rather than the more usual named field) even when its
            # name gives no hint, so string keys go through the same
            # `redact_text` value-shape scan as any other string (GAP-141).
            (redact_text(k) if isinstance(k, str) else k): (
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
