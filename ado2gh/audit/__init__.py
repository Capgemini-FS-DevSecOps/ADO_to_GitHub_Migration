"""Audit event writer and the platform's secret-masking choke point."""

from ado2gh.audit.redaction import redact_payload, redact_text
from ado2gh.audit.writer import AuditWriter

__all__ = [
    "AuditWriter",
    "redact_payload",
    "redact_text",
]
