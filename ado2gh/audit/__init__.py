"""Audit event writer, its destinations, its event-name registry, and the secret-masking choke point."""

from ado2gh.audit.destinations import AuditDestination, create_audit_destination
from ado2gh.audit.events import AuditEvent
from ado2gh.audit.redaction import redact_payload, redact_text
from ado2gh.audit.writer import AuditWriter

__all__ = [
    "AuditDestination",
    "AuditEvent",
    "AuditWriter",
    "create_audit_destination",
    "redact_payload",
    "redact_text",
]
