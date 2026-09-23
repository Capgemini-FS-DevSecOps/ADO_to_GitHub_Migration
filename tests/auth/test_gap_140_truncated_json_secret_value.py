"""Regression check for register entry GAP-140 — a truncated JSON secret value leaked past the mask.

``_SECRET_VALUE_RE``'s ``jsonkv`` branch required a literal closing quote on
the value. A log line cut off mid-value (``{"password": "abc``, no closing
quote — as happens when a log line is truncated) never matched, so the whole
value reached the output unmasked. The fix makes the closing quote optional
(``"?``), the same way ``_SECRET_KEY_VALUE_RE`` already does for its quoted
branches.

Every value below is an obvious fake; no real credential appears (CA-003).
"""
from __future__ import annotations

from ado2gh.audit.redaction import redact_text

# A serialised JSON fragment cut off mid-value — no closing quote at all.
TRUNCATED_JSON = '{"password": "abc'


def test_truncated_json_secret_value_is_masked() -> None:
    """The visible part of a truncated secret value must not leak (GAP-140)."""
    result = redact_text(TRUNCATED_JSON)
    assert "abc" not in result
    assert "***" in result


def test_pattern_does_not_run_on_past_a_well_formed_second_key() -> None:
    """A well-formed document with two keys must mask each independently.

    The optional closing quote must not make the match greedy across a
    correctly closed value into the next key's name or value.
    """
    two_keys = '{"password": "abc", "token": "xyz"}'
    result = redact_text(two_keys)
    assert result == '{"password": "***", "token": "***"}'
    assert "abc" not in result
    assert "xyz" not in result


def test_plain_json_secret_value_still_masks_as_before() -> None:
    """The ordinary, closed shape is unaffected by the optional-quote fix."""
    assert redact_text('{"secret": "plainvalue"}') == '{"secret": "***"}'
