"""Platform user and session models."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PlatformRole(str, Enum):
    """Role a platform user holds, which decides their permission set.

    The string values are the ones persisted in the ``platform_users`` table
    and returned by the API, so they must not change.
    """

    ADMIN = "admin"
    COORDINATOR = "coordinator"
    OPERATOR = "operator"
    APPROVER = "approver"


class PlatformUserStatus(str, Enum):
    """Lifecycle state of a platform account.

    Only ``ACTIVE`` accounts may log in. ``PENDING_APPROVAL`` is the state a
    self-registered operator sits in until an admin approves it, and
    ``DISABLED`` is set when an admin revokes access.
    """

    ACTIVE = "active"
    PENDING_APPROVAL = "pending_approval"
    DISABLED = "disabled"


@dataclass
class PlatformUser:
    """A platform account, without any credential material.

    Attributes:
        id: Opaque account identifier, generated at creation time.
        username: Normalised (lower-cased, stripped) login name.
        role: Role that decides the account's permissions.
        display_name: Human-readable name shown in the console; falls back to
            the username when the account was created without one.
    """

    id: str
    username: str
    role: PlatformRole
    display_name: str


@dataclass
class AuthSession:
    """An issued login session.

    Attributes:
        token: Opaque, URL-safe session token. It is the bearer credential for
            the session and must never be logged or echoed back in audit
            payloads.
        user: The account the session belongs to.
        expires_at: ISO-8601 timestamp after which the session is rejected and
            deleted.
    """

    token: str
    user: PlatformUser
    expires_at: str
