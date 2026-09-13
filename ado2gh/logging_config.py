"""Shared logging and console setup."""
import logging
from collections.abc import Mapping
from typing import cast

from rich.console import Console
from rich.logging import RichHandler

from ado2gh.audit import redact_payload, redact_text

_EXC_FORMATTER = logging.Formatter()


def _redact_rendered_message(record: logging.LogRecord) -> None:
    """Redact secrets that only surface once the record's ``%`` arguments are applied.

    ``redact_payload`` is a shape-preserving walker: types it does not recognise
    pass through untouched, and an exception object is one of them. So
    ``log.error("scope failed: %s", exc)`` reached the handler with the
    exception's message — a URL carrying a token, say — in clear. Rendering the
    message here and redacting the text closes that, whatever the argument's
    type. The record is only rewritten when redaction changed something, so a
    record with no secret keeps its original ``msg``/``args`` pair.

    Args:
        record: The record being filtered; mutated in place.
    """
    try:
        rendered = record.getMessage()
    except Exception:  # malformed format string: leave it for the handler to report
        return
    redacted = redact_text(rendered)
    if redacted != rendered:
        record.msg = redacted
        record.args = None


def _redact_traceback(record: logging.LogRecord) -> None:
    """Pre-format and redact the traceback so the handler cannot render a raw one.

    ``RichHandler(rich_tracebacks=True)`` rebuilds the traceback from
    ``record.exc_info`` (``rich.logging.RichHandler.emit``), reaching the live
    exception objects and printing their arguments — a ``requests`` error
    carrying a token in its URL, for instance. Masking those arguments in place
    would not help: the handler holds the objects, not our copy of them. The
    traceback is therefore formatted here, redacted, and handed over as
    ``exc_text`` with ``exc_info`` cleared, which is the only state RichHandler
    will not re-render from. The cost is the rich traceback rendering, and only
    for the records that actually carry a secret.

    Args:
        record: The record being filtered; mutated in place.
    """
    exc_info = record.exc_info
    if not exc_info or exc_info[1] is None:
        return
    try:
        formatted = _EXC_FORMATTER.formatException(exc_info)
    except Exception:  # unformattable traceback: nothing safe to hand on
        return
    redacted = redact_text(formatted)
    if redacted != formatted:
        record.exc_text = redacted
        record.exc_info = None


class SecretRedactingFilter(logging.Filter):
    """Mask credentials on every record reaching the root handler (CA-003).

    Delegates to the platform's single masking choke point (FR-025). Attached to
    the handler rather than a logger so records propagated up from any module's
    logger are covered. Never raises and never logs — a failure here would take
    logging down for the whole process.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Mask the record's message, arguments and traceback in place; always let it through."""
        try:
            if isinstance(record.msg, str):
                record.msg = redact_payload(record.msg)
            if record.args:
                # redact_payload is a shape-preserving recursive walker (see
                # its docstring/body): tuple in -> tuple out, dict in -> dict
                # out. record.args is already guarded non-empty/non-None here.
                # This pass is what catches a secret *key name* in a structured
                # argument, which the rendered text below cannot see.
                record.args = cast(
                    "tuple[object, ...] | Mapping[str, object]",
                    redact_payload(record.args),
                )
            _redact_rendered_message(record)
            _redact_traceback(record)
        except Exception:  # fail safe: drop content, keep logging alive
            record.msg = "<log record suppressed: redaction failed>"
            record.args = None
            record.exc_info = None
            record.exc_text = None
        return True


_handler = RichHandler(rich_tracebacks=True, show_path=False)
_handler.addFilter(SecretRedactingFilter())

logging.basicConfig(level=logging.INFO, format="%(message)s", handlers=[_handler])
log = logging.getLogger("ado2gh")
console = Console()
