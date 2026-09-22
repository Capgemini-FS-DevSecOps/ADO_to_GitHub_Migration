"""GAP-121 (GAP-TOKEN-08) — short, quoted and punctuated secret values leaked.

``redact_text``'s ``kv`` shape required at least eight characters from a
narrow charset (``[A-Za-z0-9_\\-./+=]``) after a secret-looking key. A short
value (``password=abc123``), a quoted value (``password="s3cret"``, the quotes
break the charset), or a value carrying other punctuation
(``token: P@ssw0rd!``) all passed ``redact_text`` unmasked — this is CA-003,
non-negotiable (FR-025).

Every value below is an obvious fake; no real credential appears (CA-003).
"""
from __future__ import annotations

from ado2gh.audit.redaction import redact_text

MASK = "***"
"""Replacement used for every fake secret value in this regression module."""

SHORT_UNQUOTED_SECRET = "password=abc123"
SHORT_UNQUOTED_EXPECTED = f"password={MASK}"
QUOTED_SECRET = 'password="s3cret"'
QUOTED_SECRET_EXPECTED = f'password="{MASK}"'
SINGLE_QUOTED_SECRET = "token='s3cret'"
SINGLE_QUOTED_EXPECTED = f"token='{MASK}'"
PUNCTUATED_SECRET = "token: P@ssw0rd!, branch=main"
PUNCTUATED_SECRET_EXPECTED = f"token: {MASK}, branch=main"
NON_SECRET_VALUES: tuple[str, ...] = (
    "branch=abc123",
    'description="s3cret"',
    "org failed: password unset",
)


def test_secret_key_value_masks_short_unquoted_value() -> None:
    """A short value after a secret-looking key is masked, not passed through."""
    assert redact_text(SHORT_UNQUOTED_SECRET) == SHORT_UNQUOTED_EXPECTED


def test_secret_key_value_masks_double_quoted_value() -> None:
    """The surrounding quotes survive; the value inside them does not."""
    assert redact_text(QUOTED_SECRET) == QUOTED_SECRET_EXPECTED


def test_secret_key_value_masks_single_quoted_value() -> None:
    """Single quotes are honoured the same way as double quotes."""
    assert redact_text(SINGLE_QUOTED_SECRET) == SINGLE_QUOTED_EXPECTED


def test_secret_key_value_masks_punctuated_value_until_delimiter() -> None:
    """Punctuation inside a value is masked; the following field is not consumed."""
    assert redact_text(PUNCTUATED_SECRET) == PUNCTUATED_SECRET_EXPECTED


def test_non_secret_keys_and_prefixes_keep_their_values() -> None:
    """An ordinary key, and a non-secret word before a real key, are left alone."""
    for value in NON_SECRET_VALUES:
        assert redact_text(value) == value
