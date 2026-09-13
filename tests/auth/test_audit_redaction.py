"""Audit payload redaction tests — the platform's single masking choke point.

`ado2gh/audit/redaction.py` is where `AuditWriter`, the agent's `mask_secrets`,
the git-scope redactor and the root log handler all converge, so a shape it
misses is a shape nothing catches (CA-003, FR-025).

A vurnix mutation run over the module scored 17/41 with the original three
assertions here. The survivors clustered on two classes, and every test below is
aimed at one of them:

* ``return <expr> -> return None`` — each function's return value is asserted as
  a *concrete* value, never merely as "changed" or "truthy". A pass-through path
  is asserted with ``is`` so returning ``None`` instead cannot satisfy it.
* numeric-literal and boolean-operator flips inside the token-shape regexes —
  each alternation branch is pinned with a positive case *and* a negative case
  one character outside its boundary, so widening or narrowing a quantifier
  fails a test.

Every credential literal here is obviously fake (CA-003).
"""
from __future__ import annotations

import json

import pytest

from ado2gh.audit import redact_payload
from ado2gh.audit.redaction import _is_secret_key, redact_text

MASK = "***"


# --------------------------------------------------------------------------
# The original three, kept and given concrete expected values
# --------------------------------------------------------------------------


def test_redact_github_token():
    out = redact_payload({"token": "ghp_abcdefghijklmnopqrst"})
    assert out == {"token": "ghp_***"}


def test_redact_nested():
    out = redact_payload({"nested": {"pat": "pat-abc123xyz"}})
    assert out == {"nested": {"pat": "pat-***"}}


def test_redact_auth_event_payload():
    out = redact_payload({"event": "user.login", "password": "secret123"})
    assert out == {"event": "user.login", "password": "secr***"}


# --------------------------------------------------------------------------
# Every _mask_match branch, with the exact masked value it produces
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        # pfx — the four-character triage prefix is kept, the rest is gone.
        ("ghp_abcdefghijklmnopqrst", "ghp_***"),
        ("gho_abcdefghijklmnopqrst", "gho_***"),
        ("ghu_abcdefghijklmnopqrst", "ghu_***"),
        ("ghs_abcdefghijklmnopqrst", "ghs_***"),
        ("ghr_abcdefghijklmnopqrst", "ghr_***"),
        ("github_pat_11ABCDEFG0123456789", "gith***"),
        ("pat-abc123xyz", "pat-***"),
        # opaque — a bare 52-character ADO PAT.
        ("q" * 52, "qqqq***"),
        # bearer — the scheme word survives, the credential does not.
        ("Bearer eyJhbGciOiJIUzI1NiJ9.abc.def", f"Bearer {MASK}"),
        ("bearer eyJhbGciOiJIUzI1NiJ9.abc.def", f"bearer {MASK}"),
        # jsonkv — the key and the quoting survive, the value is replaced.
        ('{"token": "abc123secret"}', '{"token": "***"}'),
        ('{"password":"abc123secret"}', '{"password":"***"}'),
        ('{"api_key" : "abc123secret"}', '{"api_key" : "***"}'),
        # kv — the key and the separator survive.
        ("password=hunter2000", f"password={MASK}"),
        ("api_key: abcdefgh", f"api_key: {MASK}"),
        ("apikey=abcdefghij", f"apikey={MASK}"),
        ("PAT = abcdefghij", f"PAT = {MASK}"),
    ],
)
def test_each_secret_shape_masks_to_exactly_this_value(raw, expected):
    assert redact_text(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        # opaque is anchored at exactly 52 characters: one either side is not a PAT.
        "a" * 51,
        "a" * 53,
        # …and the lookaround stops it firing inside a longer alphanumeric run.
        "x" + "a" * 52,
        "a" * 52 + "x",
        # kv needs at least 8 characters of value.
        "token=short",
        "password=1234567",
        # a non-secret key name with a value is left alone.
        "branch=feature/migrate-payments",
        "commit=abcdef1234567890",
        # ordinary prose that happens to contain the word token.
        "the token budget was exceeded",
    ],
)
def test_a_value_outside_every_shape_is_returned_unchanged(raw):
    assert redact_text(raw) == raw, "redaction fired on a value that is not a secret"


def test_the_kv_boundary_is_exactly_eight_characters():
    """One character either side of the ``{8,}`` quantifier, in one test."""
    assert redact_text("password=1234567") == "password=1234567"
    assert redact_text("password=12345678") == f"password={MASK}"


def test_the_opaque_boundary_is_exactly_fifty_two_characters():
    """One character either side of the ``{52}`` quantifier, in one test."""
    assert redact_text("b" * 51) == "b" * 51
    assert redact_text("b" * 52) == "bbbb***"
    assert redact_text("b" * 53) == "b" * 53


def test_several_secrets_in_one_string_are_all_masked():
    raw = f"pushing with ghp_aaaaaaaaaaaaaaaaaaaa then {'c' * 52} done"
    assert redact_text(raw) == "pushing with ghp_*** then cccc*** done"


def test_the_surrounding_text_is_preserved_around_a_mask():
    out = redact_text("clone https://dev.azure.com/org failed: password=hunter2000")
    assert out == f"clone https://dev.azure.com/org failed: password={MASK}"


# --------------------------------------------------------------------------
# redact_text: the short-string fast path returns the same string, not None
# --------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["", "a", "ab", "abc", "abcd"])
def test_a_string_shorter_than_five_characters_is_returned_as_it_was(raw):
    assert redact_text(raw) == raw


def test_the_length_guard_boundary_is_exactly_five_characters():
    """Four characters take the fast path, five go through the scan."""
    assert redact_text("abcd") == "abcd"
    assert redact_text("abcde") == "abcde"
    assert redact_text("pat-x") == "pat-***", "a five-character secret must still be masked"


# --------------------------------------------------------------------------
# redact_payload: scalars pass through as themselves, not as None
# --------------------------------------------------------------------------


def test_none_passes_through_as_none():
    assert redact_payload(None) is None


@pytest.mark.parametrize("value", [True, False])
def test_a_boolean_passes_through_as_the_same_boolean(value):
    assert redact_payload(value) is value


@pytest.mark.parametrize("value", [0, 1, -7, 5000])
def test_an_integer_passes_through_unchanged(value):
    result = redact_payload(value)
    assert result == value
    assert isinstance(result, int)


@pytest.mark.parametrize("value", [0.0, 3.5, -1.25])
def test_a_float_passes_through_unchanged(value):
    result = redact_payload(value)
    assert result == value
    assert isinstance(result, float)


def test_a_type_the_walker_does_not_recognise_is_returned_as_itself():
    sentinel = object()
    assert redact_payload(sentinel) is sentinel


def test_a_set_is_not_walked_and_is_returned_as_itself():
    payload = {"ghp_aaaaaaaaaaaaaaaaaaaa"}
    assert redact_payload(payload) is payload


# --------------------------------------------------------------------------
# redact_payload: container shapes are preserved
# --------------------------------------------------------------------------


def test_a_list_stays_a_list_with_every_element_scanned():
    out = redact_payload(["safe", "ghp_aaaaaaaaaaaaaaaaaaaa", 5, None])
    assert out == ["safe", "ghp_***", 5, None]
    assert isinstance(out, list)


def test_a_tuple_stays_a_tuple():
    out = redact_payload(("safe", "pat-abc123xyz"))
    assert out == ("safe", "pat-***")
    assert isinstance(out, tuple), "a log record's argument tuple became a list"


def test_an_empty_container_survives_the_walk():
    assert redact_payload({}) == {}
    assert redact_payload([]) == []
    assert redact_payload(()) == ()


def test_a_non_string_key_is_left_alone_and_its_value_still_walked():
    out = redact_payload({7: "ghp_aaaaaaaaaaaaaaaaaaaa"})
    assert out == {7: "ghp_***"}


# --------------------------------------------------------------------------
# Key-name matching: the truncation rule and the word-boundary negatives
# --------------------------------------------------------------------------


def test_a_secret_key_keeps_a_four_character_prefix_of_a_longer_value():
    assert redact_payload({"ado_pat": "abcdefghij"}) == {"ado_pat": "abcd***"}


def test_a_secret_key_holding_a_short_value_is_masked_entirely():
    """At four characters or fewer the prefix would *be* the value."""
    assert redact_payload({"ado_pat": "abcd"}) == {"ado_pat": MASK}
    assert redact_payload({"ado_pat": "ab"}) == {"ado_pat": MASK}


def test_the_prefix_boundary_is_exactly_four_characters():
    assert redact_payload({"token": "abcd"}) == {"token": MASK}
    assert redact_payload({"token": "abcde"}) == {"token": "abcd***"}


def test_a_secret_key_holding_a_non_string_value_is_still_walked():
    out = redact_payload({"credentials": {"nested": "ghp_aaaaaaaaaaaaaaaaaaaa"}})
    assert out == {"credentials": {"nested": "ghp_***"}}


@pytest.mark.parametrize(
    "key",
    [
        "pat", "ado_pat", "PAT", "password", "passwd", "secret", "token",
        "gh_token", "github_token", "access_token", "api_key", "api-key",
        "apikey", "credential", "credentials", "authorization",
        "clientSecret", "apiKey", "client_key", "private_key", "auth_token",
    ],
)
def test_a_secret_key_name_is_recognised(key):
    assert _is_secret_key(key) is True


@pytest.mark.parametrize(
    "key",
    [
        "db_path", "max_tokens", "monkeypatch", "patch", "repository",
        "pattern", "updated_at", "status", "tokenizer", "name", "org_url",
    ],
)
def test_an_ordinary_key_name_is_not_mistaken_for_a_secret(key):
    assert _is_secret_key(key) is False


def test_a_non_string_key_never_matches():
    assert _is_secret_key(7) is False
    assert _is_secret_key(None) is False


# --------------------------------------------------------------------------
# Depth guard: the fail-safe returns the mask, not None
# --------------------------------------------------------------------------


def _nest(depth: int, leaf: object) -> object:
    payload: object = leaf
    for _ in range(depth):
        payload = {"n": payload}
    return payload


def test_a_payload_nested_below_the_limit_is_walked_to_the_leaf():
    out = redact_payload(_nest(18, "ghp_aaaaaaaaaaaaaaaaaaaa"))
    assert json.dumps(out).count("ghp_***") == 1


def test_a_pathologically_nested_payload_is_masked_rather_than_recursed():
    out = redact_payload(_nest(40, "ghp_aaaaaaaaaaaaaaaaaaaa"))
    blob = json.dumps(out)
    assert MASK in blob
    assert "ghp_aaaaaaaaaaaaaaaaaaaa" not in blob, "the leaf secret escaped the depth guard"


def test_a_self_referential_payload_terminates_with_the_mask():
    cycle: dict = {}
    cycle["self"] = cycle
    assert MASK in json.dumps(redact_payload(cycle))
