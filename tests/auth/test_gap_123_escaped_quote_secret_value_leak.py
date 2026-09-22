"""Regression check for register entry GAP-123 — a backslash-escaped quote inside a quoted secret value ended the mask early.

``_SECRET_KEY_VALUE_RE``'s ``dqval``/``sqval`` branches (GAP-121) matched
``[^"]*``/``[^']*`` up to the first literal quote character, with no regard
for a preceding backslash. A value that escapes its own quote character
rather than closing on it — ``password="abc\\"def"`` — stopped the match at
that escaped quote, leaving the remainder of the real secret (``def``)
unmasked in the output. This is distinct from GAP-121, which covered a value
quoted in one style legitimately containing the *other* quote character
(``password="abc's"``); this covers an escaped occurrence of the value's own
quote character.

A second-review follow-up: the escape-aware group ``(?:\\.|[^"\\])*`` needs a
character after every backslash to pair with, so a value ending on a lone
trailing backslash had no following character for the last ``\\.`` to
consume, and that final backslash reached the output unmasked. A trailing
``\\?`` on each quoted branch closes that gap.

Every value below is an obvious fake; no real credential appears (CA-003).
"""
from __future__ import annotations

from ado2gh.audit.redaction import redact_text

MASK = "***"
BACKSLASH = "\\"

# password="abc\"def" — the backslash escapes the quote rather than closing
# the value; before the fix, `def` reached the output unmasked.
ESCAPED_DOUBLE_QUOTE_SECRET = 'password="abc\\"def"'
ESCAPED_DOUBLE_QUOTE_EXPECTED = f'password="{MASK}"'

# token='abc\'def' — same shape, single-quoted.
ESCAPED_SINGLE_QUOTE_SECRET = "token='abc\\'def'"
ESCAPED_SINGLE_QUOTE_EXPECTED = f"token='{MASK}'"

# Register entry GAP-121 regression: a value quoted in one style containing the *other*
# quote character must still mask in full — the escape handling must not
# narrow this existing case.
OPPOSITE_QUOTE_SECRET = "password=\"abc's\""
OPPOSITE_QUOTE_EXPECTED = f'password="{MASK}"'

# password="abc\" — the value ends on a lone backslash immediately before the
# closing quote; before the fix that final backslash had no character to
# pair with in `\\.` and reached the output unmasked.
TRAILING_BACKSLASH_DOUBLE_QUOTE_SECRET = 'password="abc' + BACKSLASH + '"'
TRAILING_BACKSLASH_DOUBLE_QUOTE_EXPECTED = f'password="{MASK}"'

# token='abc\' — same trailing-backslash shape, single-quoted.
TRAILING_BACKSLASH_SINGLE_QUOTE_SECRET = "token='abc" + BACKSLASH + "'"
TRAILING_BACKSLASH_SINGLE_QUOTE_EXPECTED = f"token='{MASK}'"

# password="abc\ with no closing quote at all — the line was cut off right on
# the trailing backslash. The value still has to mask in full up to the
# delimiter the code uses, not leak that dangling backslash. This, and its
# single-quoted equivalent below, are the shapes that actually demonstrate the
# fix: with nothing after the backslash, the escape-aware group has no
# character left to pair `\\.` with, so it stopped one character short and
# that trailing backslash reached the output unmasked. The two
# "before a closing quote" cases above pass with or without this fix, because
# `\\.` already consumes the backslash together with that closing quote as an
# escaped character when nothing follows it — they are pinned as regression
# coverage for GAP-123's original escaped-quote shape, not for this backslash
# gap.
UNTERMINATED_AFTER_BACKSLASH_SECRET = 'password="abc' + BACKSLASH
UNTERMINATED_AFTER_BACKSLASH_EXPECTED = f'password="{MASK}'

# token='abc\ — the single-quoted equivalent of the case above, covering the
# `sqval` branch the same way the double-quoted case covers `dqval`.
UNTERMINATED_AFTER_BACKSLASH_SINGLE_SECRET = "token='abc" + BACKSLASH
UNTERMINATED_AFTER_BACKSLASH_SINGLE_EXPECTED = f"token='{MASK}"

# password="abc with no closing quote and no trailing backslash — the
# pre-existing unterminated-quote shape, pinned here as a regression check
# alongside the backslash variant above.
UNTERMINATED_PLAIN_SECRET = 'password="abc'
UNTERMINATED_PLAIN_EXPECTED = f'password="{MASK}'


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
    """The original case for register entry GAP-121 — an unescaped, opposite quote inside the value — is unaffected."""
    assert redact_text(OPPOSITE_QUOTE_SECRET) == OPPOSITE_QUOTE_EXPECTED


def test_trailing_backslash_before_a_double_closing_quote_is_fully_masked() -> None:
    """A value ending in a lone backslash must not leave that backslash unmasked."""
    result = redact_text(TRAILING_BACKSLASH_DOUBLE_QUOTE_SECRET)
    assert result == TRAILING_BACKSLASH_DOUBLE_QUOTE_EXPECTED
    assert "abc" not in result


def test_trailing_backslash_before_a_single_closing_quote_is_fully_masked() -> None:
    """The single-quoted equivalent of the trailing-backslash shape is also closed."""
    result = redact_text(TRAILING_BACKSLASH_SINGLE_QUOTE_SECRET)
    assert result == TRAILING_BACKSLASH_SINGLE_QUOTE_EXPECTED
    assert "abc" not in result


def test_unterminated_double_quote_cut_off_on_a_trailing_backslash_masks_to_the_delimiter() -> None:
    """A value cut off right on its trailing backslash still masks in full, backslash included."""
    result = redact_text(UNTERMINATED_AFTER_BACKSLASH_SECRET)
    assert result == UNTERMINATED_AFTER_BACKSLASH_EXPECTED
    assert "abc" not in result
    assert BACKSLASH not in result


def test_unterminated_single_quote_cut_off_on_a_trailing_backslash_masks_to_the_delimiter() -> None:
    """The single-quoted equivalent, covering the `sqval` branch's own trailing-backslash fix."""
    result = redact_text(UNTERMINATED_AFTER_BACKSLASH_SINGLE_SECRET)
    assert result == UNTERMINATED_AFTER_BACKSLASH_SINGLE_EXPECTED
    assert "abc" not in result
    assert BACKSLASH not in result


def test_unterminated_quote_without_a_trailing_backslash_still_masks_to_the_delimiter() -> None:
    """The plain unterminated-quote shape, pinned so the trailing-backslash fix cannot narrow it."""
    result = redact_text(UNTERMINATED_PLAIN_SECRET)
    assert result == UNTERMINATED_PLAIN_EXPECTED
    assert "abc" not in result
