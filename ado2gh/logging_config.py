"""Shared logging and console setup."""
import logging

from rich.console import Console
from rich.logging import RichHandler

from ado2gh.audit import redact_payload


class SecretRedactingFilter(logging.Filter):
    """Mask credentials on every record reaching the root handler (CA-003).

    Delegates to the platform's single masking choke point (FR-025). Attached to
    the handler rather than a logger so records propagated up from any module's
    logger are covered. Never raises and never logs — a failure here would take
    logging down for the whole process.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Mask the record's message and arguments in place and always let it through."""
        try:
            if isinstance(record.msg, str):
                record.msg = redact_payload(record.msg)
            if record.args:
                record.args = redact_payload(record.args)
        except Exception:  # noqa: BLE001 - fail safe: drop content, keep logging alive
            record.msg = "<log record suppressed: redaction failed>"
            record.args = None
        return True


_handler = RichHandler(rich_tracebacks=True, show_path=False)
_handler.addFilter(SecretRedactingFilter())

logging.basicConfig(level=logging.INFO, format="%(message)s", handlers=[_handler])
log = logging.getLogger("ado2gh")
console = Console()
