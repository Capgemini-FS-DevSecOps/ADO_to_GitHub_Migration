"""Where audit events are written, chosen by one environment variable.

The writer in ``ado2gh/audit/writer.py`` needs somewhere to append an event. For
most deployments that is the migration state database, which is what this module
returns when nothing is configured. A deployment whose state database cannot hold
audit events — DynamoDB job stores are the case that forced this, see GAP-069 in
``specs/013-clean-code-arch-remediation/gap-register.md`` — names another place
instead, and every event still passes through the same masking first.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Protocol

AUDIT_DESTINATION_ENV_VAR = "ADO2GH_AUDIT_DESTINATION"
"""Environment variable that names where audit events are written.

The name is spelled out a second time at the single ``os.environ.get`` call in
``create_audit_destination``. Both guards that freeze environment variable names
— the public surface snapshot and the ``.env.example`` check — find names by
reading the literal passed to that call, so a name reached only through this
constant would go unfrozen and undocumented. The tests set the variable through
this constant, so the two spellings cannot drift apart unnoticed.
"""

STATE_DESTINATION = "state"
"""Value that keeps audit events in the configured migration state database."""

DYNAMODB_SCHEME = "dynamodb"
"""Scheme that sends audit events to their own DynamoDB table."""

SQLITE_SCHEME = "sqlite"
"""Scheme that sends audit events to a SQLite file of their own."""

POSTGRES_SCHEMES = ("postgres", "postgresql")
"""Schemes that send audit events to a PostgreSQL database of their own."""

SCHEME_SEPARATOR = "://"
"""Text that separates the scheme from the table, file or connection string."""

SQLITE_IN_MEMORY = ":memory:"
"""SQLite path that holds nothing after the process exits, so it is refused here."""

DYNAMODB_ID_FIELD = "id"
"""Partition key of the audit table; it holds the event id."""

DYNAMODB_EVENT_TYPE_FIELD = "event_type"
"""Item field holding the short machine name of what happened."""

DYNAMODB_PROFILE_ID_FIELD = "profile_id"
"""Item field holding the profile the event belongs to."""

DYNAMODB_ACTOR_FIELD = "actor"
"""Item field holding who caused the event."""

DYNAMODB_PAYLOAD_FIELD = "payload_json"
"""Item field holding the already-masked event details, serialised as JSON."""

DYNAMODB_CREATED_AT_FIELD = "created_at"
"""Item field holding when the event was written, in coordinated universal time."""

DYNAMODB_NEW_ITEM_CONDITION = f"attribute_not_exists({DYNAMODB_ID_FIELD})"
"""Condition that makes a write fail rather than overwrite an existing event."""

DEFAULT_AWS_REGION = "us-east-1"
"""Region used when neither AWS region variable is set, as in storage_config.py."""


class AuditDestination(Protocol):
    """Anything that can append one audit event.

    The single method matches what the state stores already offer, so a
    ``SQLiteStateDB`` or a ``PostgresStateDB`` is an audit destination as it
    stands, with nothing wrapped around it.
    """

    def insert_audit_event(
        self,
        event_id: str,
        event_type: str,
        profile_id: str,
        actor: str,
        payload_json: str,
    ) -> None:
        """Append one audit event.

        Args:
            event_id: Caller-generated unique id.
            event_type: Short machine name of what happened.
            profile_id: The profile the event belongs to.
            actor: Who caused it; empty when unknown.
            payload_json: Already-masked event details, serialised as JSON.
        """


def _aws_region() -> str:
    """Return the AWS region, read exactly as ``StorageConfig.from_env`` reads it.

    Returns:
        ``AWS_REGION``, else ``AWS_DEFAULT_REGION``, else the default region.
    """
    return os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", DEFAULT_AWS_REGION))


class DynamoDbAuditDestination:
    """Audit destination that appends one item per event to its own DynamoDB table.

    This is never the job table: it holds audit events only. The table is never
    created and no expiry is ever set on an item, because an audit record that a
    deployment can delete by accident, or that quietly ages out, is not a record.
    """

    def __init__(self, table_name: str, region: str, endpoint_url: str | None = None) -> None:
        """Bind to an existing DynamoDB table.

        Args:
            table_name: The audit table, which must already exist.
            region: AWS region the table lives in.
            endpoint_url: Override for a local DynamoDB endpoint. The job store
                already honours ``ADO2GH_DYNAMODB_ENDPOINT``, so an audit table
                that ignored it would silently address a different service.
        """
        import boto3

        kwargs: dict = {"region_name": region}
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url
        self.table_name = table_name
        self._table: Any = boto3.resource("dynamodb", **kwargs).Table(table_name)

    def insert_audit_event(
        self,
        event_id: str,
        event_type: str,
        profile_id: str,
        actor: str,
        payload_json: str,
    ) -> None:
        """Append one audit event; see :meth:`AuditDestination.insert_audit_event`.

        Raises:
            RuntimeError: The table does not exist. It is never created here, so
                the message names the table and the variable that chose it.
        """
        from botocore.exceptions import ClientError

        try:
            self._table.put_item(
                Item={
                    DYNAMODB_ID_FIELD: event_id,
                    DYNAMODB_EVENT_TYPE_FIELD: event_type,
                    DYNAMODB_PROFILE_ID_FIELD: profile_id,
                    DYNAMODB_ACTOR_FIELD: actor,
                    DYNAMODB_PAYLOAD_FIELD: payload_json,
                    DYNAMODB_CREATED_AT_FIELD: datetime.now(timezone.utc).isoformat(),
                },
                ConditionExpression=DYNAMODB_NEW_ITEM_CONDITION,
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
                raise
            raise RuntimeError(
                f"The DynamoDB audit table {self.table_name!r} does not exist. "
                f"{AUDIT_DESTINATION_ENV_VAR} named it, and this destination never "
                f"creates a table: create it with the partition key "
                f"{DYNAMODB_ID_FIELD!r}, or point the variable somewhere else."
            ) from exc


def create_audit_destination() -> AuditDestination:
    """Return the audit destination the environment asks for.

    ``ADO2GH_AUDIT_DESTINATION`` is read on its own, without resolving the state
    backend first, so a deployment can send audit events somewhere the state
    backend could not go.

    Returns:
        The configured state store when the variable is unset or ``state``; a
        DynamoDB table for ``dynamodb://<table-name>``; a SQLite file of its own
        for ``sqlite://<path>``; a PostgreSQL database of its own for a
        ``postgres://`` or ``postgresql://`` connection string.

    Raises:
        ValueError: The value is none of the accepted forms, or it names an
            in-memory SQLite database, which would lose every event on exit.
    """
    setting = (os.environ.get("ADO2GH_AUDIT_DESTINATION") or "").strip()

    if not setting or setting.lower() == STATE_DESTINATION:
        from ado2gh.state.factory import create_state_db

        return create_state_db()

    scheme, separator, target = setting.partition(SCHEME_SEPARATOR)
    scheme = scheme.lower()
    if separator:
        if scheme in POSTGRES_SCHEMES:
            # The whole setting is the connection string, and an empty one after
            # the scheme is a valid request for libpq's own defaults.
            from ado2gh.state.postgres_db import PostgresStateDB

            return PostgresStateDB(setting)
        if target and scheme == DYNAMODB_SCHEME:
            return DynamoDbAuditDestination(
                target, _aws_region(), os.environ.get("ADO2GH_DYNAMODB_ENDPOINT"),
            )
        if target and scheme == SQLITE_SCHEME:
            if target.strip() == SQLITE_IN_MEMORY:
                raise ValueError(
                    f"{AUDIT_DESTINATION_ENV_VAR}={setting!r} names an in-memory database, "
                    f"which loses every audit event when the process exits. Name a file."
                )
            from ado2gh.state.sqlite_db import SQLiteStateDB

            return SQLiteStateDB(target)

    raise ValueError(
        f"Invalid {AUDIT_DESTINATION_ENV_VAR}={setting!r}. Use {STATE_DESTINATION!r} (or "
        f"leave it unset) for the configured state database, {DYNAMODB_SCHEME}://<table-name>, "
        f"{SQLITE_SCHEME}://<file-path>, or a "
        f"{POSTGRES_SCHEMES[0]}://|{POSTGRES_SCHEMES[1]}:// connection string."
    )
