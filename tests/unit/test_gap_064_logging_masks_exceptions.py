"""Regression check for register entry GAP-064: exception objects and tracebacks must not reach the handler unmasked.

``SecretRedactingFilter`` passed ``record.args`` through ``redact_payload``, a
shape-preserving walker that by design leaves types it does not recognise
untouched — an exception object among them. So ``log.error("...: %s", exc)``
rendered the exception's message, token and all, at the handler. The filter also
never looked at ``record.exc_info``, while the root handler is
``RichHandler(rich_tracebacks=True)``, which re-renders the traceback straight
from the live exception objects.

Every credential in this file is an obvious fake (CA-003).
"""
from __future__ import annotations

import logging
import sys

from ado2gh.logging_config import SecretRedactingFilter

FAKE_TOKEN = "ghp_" + "A" * 36


def _render(record: logging.LogRecord) -> str:
    """Filter a record and return what a plain formatter would print for it."""
    assert SecretRedactingFilter().filter(record) is True
    return logging.Formatter("%(message)s").format(record)


def _record(msg: str, args: object, exc_info: object = None) -> logging.LogRecord:
    """Build a log record the way ``Logger.error`` would."""
    return logging.LogRecord(
        "ado2gh.test", logging.ERROR, __file__, 1, msg, args, exc_info,  # type: ignore[arg-type]
    )


def _raised() -> tuple[type[BaseException], BaseException, object]:
    """Raise and catch an exception carrying a fake token so it has a traceback."""
    try:
        raise ValueError(f"401 for https://dev.azure.com/x?token={FAKE_TOKEN}")
    except ValueError:
        return sys.exc_info()  # type: ignore[return-value]


def test_exception_passed_as_a_log_argument_is_masked() -> None:
    """`log.error("scope failed: %s", exc)` must not render the token in clear."""
    exc = ValueError(f"push rejected: https://{FAKE_TOKEN}@github.com/o/r.git")
    rendered = _render(_record("scope %s failed: %s", ("git", exc)))

    assert FAKE_TOKEN not in rendered
    assert "ghp_***" in rendered
    assert "scope git failed" in rendered


def test_traceback_from_exc_info_is_masked() -> None:
    """A record logged with ``exc_info`` must not print the token in its traceback."""
    record = _record("wave failed", None, _raised())
    rendered = _render(record)

    assert FAKE_TOKEN not in rendered
    assert "token=***" in rendered, "the query-string PAT is masked, not dropped"
    assert "ValueError" in rendered, "the traceback itself must survive redaction"
    assert record.exc_info is None, (
        "RichHandler re-renders from exc_info, so it must be cleared once exc_text holds "
        "the redacted traceback"
    )


def test_plain_string_arguments_are_still_masked() -> None:
    """Regression: the pre-existing string and message masking keeps working."""
    rendered = _render(_record("auth failed for %s", (f"user:{FAKE_TOKEN}",)))

    assert FAKE_TOKEN not in rendered
    assert "ghp_***" in rendered


def test_secret_named_mapping_key_is_still_masked() -> None:
    """Regression: key-name masking inside a structured argument keeps working."""
    rendered = _render(_record("config %s", ({"gh_token": "s3cr3t-value-1234"},)))

    assert "s3cr3t-value-1234" not in rendered


def test_clean_record_keeps_its_arguments_and_traceback() -> None:
    """A record with no secret is left alone: same text, rich traceback intact."""
    try:
        raise RuntimeError("connection reset by peer")
    except RuntimeError:
        clean = sys.exc_info()

    record = _record("migrated %s repos", (7,), clean)
    assert SecretRedactingFilter().filter(record) is True

    assert record.args == (7,)
    assert record.msg == "migrated %s repos"
    assert record.exc_info is clean, "rich traceback must survive a record with no secret"
