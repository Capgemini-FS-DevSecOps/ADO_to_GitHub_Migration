"""Platform user and session models."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PlatformRole(str, Enum):
    ADMIN = "admin"
    COORDINATOR = "coordinator"
    OPERATOR = "operator"
    APPROVER = "approver"


@dataclass
class PlatformUser:
    id: str
    username: str
    role: PlatformRole
    display_name: str


@dataclass
class AuthSession:
    token: str
    user: PlatformUser
    expires_at: str
