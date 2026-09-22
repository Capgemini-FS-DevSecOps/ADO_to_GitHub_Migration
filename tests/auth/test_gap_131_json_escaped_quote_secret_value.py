"""Regression check for register entry GAP-131 — an escaped quote inside a serialised JSON secret value leaked past the mask.

``_SECRET_VALUE_RE``'s ``jsonkv`` branch matched ``"token": "…"`` shapes with a
plain ``[^"]*`` value, stopping at the first literal quote character. A value
that escapes its own closing quote (``"password": "abc\\"def"``) stopped the
match there and left the tail (``def"``) in the output. ``_SECRET_KEY_VALUE_RE``
already handles this with an escape-aware group
(``(?:\\.|[^"\\])*\\?``); the fix makes the ``jsonkv`` branch use the same one.

Every value below is an obvious fake; no real credential appears (CA-003).
"""
from __future__ import annotations

from ado2gh.audit.redaction import redact_text

MASK = "***"

# "password": "abc\"def" inside an already-serialised JSON blob — the
# backslash escapes the quote rather than closing the value.
ESCAPED_QUOTE_JSON = '{"password": "abc\\"def"}'
ESCAPED_QUOTE_EXPECTED = '{"password": "***"}'

# Same shape for a different recognised key name.
ESCAPED_QUOTE_TOKEN_JSON = '{"token": "ghp_abc\\"def"}'
ESCAPED_QUOTE_TOKEN_EXPECTED = '{"token": "***"}'

# A value quoted normally must still mask exactly as before (no regression).
PLAIN_JSON = '{"secret": "plainvalue"}'
PLAIN_EXPECTED = '{"secret": "***"}'


def test_escaped_quote_inside_a_serialised_json_secret_value_is_fully_masked() -> None:
    """The tail of the secret after an escaped quote must not leak (GAP-131)."""
    result = redact_text(ESCAPED_QUOTE_JSON)
    assert result == ESCAPED_QUOTE_EXPECTED
    assert "def" not in result


def test_escaped_quote_inside_a_json_token_value_is_fully_masked() -> None:
    """The same escape shape under a different recognised key name."""
    result = redact_text(ESCAPED_QUOTE_TOKEN_JSON)
    assert result == ESCAPED_QUOTE_TOKEN_EXPECTED
    assert "def" not in result


def test_plain_json_secret_value_still_masks_as_before() -> None:
    """The ordinary, unescaped shape is unaffected by the escape-aware fix."""
    assert redact_text(PLAIN_JSON) == PLAIN_EXPECTED
