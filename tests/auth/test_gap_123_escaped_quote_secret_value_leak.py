"""GAP-123 — a backslash-escaped quote inside a quoted secret value ended the mask early.

``_SECRET_KEY_VALUE_RE``'s ``dqval``/``sqval`` branches (GAP-121) matched
``[^"]*``/``[^']*`` up to the first literal quote character, with no regard
for a preceding backslash. A value that escapes its own quote character
rather than closing on it — ``password="abc\\"def"`` — stopped the match at
that escaped quote, leaving the remainder of the real secret (``def``)
unmasked in the output. This is distinct from GAP-121, which covered a value
quoted in one style legitimately containing the *other* quote character
(``password="abc's"``); this covers an escaped occurrence of the value's own
quote character.

Every value below is an obvious fake; no real credential appears (CA-003).
"""
from __future__ import annotations

from ado2gh.audit.redaction import redact_text

MASK = "***"

# password="abc\"def" — the backslash escapes the quote rather than closing
# the value; before the fix, `def` reached the output unmasked.
ESCAPED_DOUBLE_QUOTE_SECRET = 'password="abc\\"def"'
ESCAPED_DOUBLE_QUOTE_EXPECTED = f'password="{MASK}"'

# token='abc\'def' — same shape, single-quoted.
ESCAPED_SINGLE_QUOTE_SECRET = "token='abc\\'def'"
ESCAPED_SINGLE_QUOTE_EXPECTED = f"token='{MASK}'"

# GAP-121 regression: a value quoted in one style containing the *other*
# quote character must still mask in full — the escape handling must not
# narrow this existing case.
OPPOSITE_QUOTE_SECRET = "password=\"abc's\""
OPPOSITE_QUOTE_EXPECTED = f'password="{MASK}"'


def test_escaped_double_quote_inside_a_double_quoted_value_is_fully_masked() -> None:
    """The tail of the secret after an escaped double quote must not leak."""
    result = redact_text(ESCAPED_DOUBLE_QUOTE_SECRET)
    assert result == ESCAPED_DOUBLE_QUOTE_EXPECTED
    assert "def" not in result


def test_escaped_single_quote_inside_a_single_quoted_value_is_fully_masked() -> None:
    """The single-quoted equivalent of the same escape shape is also closed."""
    result = redact_text(ESCAPED_SINGLE_QUOTE_SECRET)
    assert result == ESCAPED_SINGLE_QUOTE_EXPECTED
    assert "def" not in result


def test_opposite_quote_character_inside_a_value_still_masks_in_full() -> None:
    """GAP-121's original case — an unescaped, opposite quote inside the value — is unaffected."""
    assert redact_text(OPPOSITE_QUOTE_SECRET) == OPPOSITE_QUOTE_EXPECTED
