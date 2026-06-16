"""Authentication service — bootstrap, login, sessions."""
from __future__ import annotations

import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ado2gh.auth.models import AuthSession, PlatformRole, PlatformUser
from ado2gh.auth.password import hash_password, validate_password_strength, verify_password
from ado2gh.state.factory import create_state_db

SESSION_COOKIE = "ado2gh_session"
SESSION_HOURS = int(os.environ.get("ADO2GH_SESSION_HOURS", "8"))


def auth_enabled() -> bool:
    return os.environ.get("ADO2GH_AUTH_ENABLED", "").lower() in ("1", "true", "yes")


def _db() -> Any:
    return create_state_db(os.environ.get("ADO2GH_SQLITE_PATH", "migration_state.db"))


def _user_from_row(row: dict) -> PlatformUser:
    return PlatformUser(
        id=row["id"],
        username=row["username"],
        role=PlatformRole(row["role"]),
        display_name=row.get("display_name") or row["username"],
    )


def permissions_for(role: PlatformRole) -> dict[str, bool]:
    return {
        "can_coordinate": role in (PlatformRole.ADMIN, PlatformRole.COORDINATOR),
        "can_operate": role in (PlatformRole.ADMIN, PlatformRole.COORDINATOR, PlatformRole.OPERATOR),
        "can_approve": role in (PlatformRole.ADMIN, PlatformRole.APPROVER),
        "can_manage_users": role == PlatformRole.ADMIN,
    }


class AuthService:
    """Platform login and session management."""

    def __init__(self, db: Optional[Any] = None):
        self.db = db or _db()

    def needs_bootstrap(self) -> bool:
        return self.db.count_platform_users() == 0

    def bootstrap_admin(
        self, username: str, password: str, display_name: str,
    ) -> AuthSession:
        if not self.needs_bootstrap():
            raise PermissionError("Bootstrap not allowed — users already exist")
        validate_password_strength(password)
        user_id = f"usr_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        self.db.create_platform_user(
            user_id=user_id,
            username=username.strip().lower(),
            password_hash=hash_password(password),
            role=PlatformRole.ADMIN.value,
            display_name=display_name or username,
            created_at=now,
        )
        user = PlatformUser(user_id, username.strip().lower(), PlatformRole.ADMIN, display_name or username)
        return self._create_session(user)

    def login(self, username: str, password: str) -> AuthSession:
        row = self.db.get_platform_user_by_username(username.strip().lower())
        if not row or not verify_password(password, row["password_hash"]):
            raise ValueError("Invalid credentials")
        user = _user_from_row(row)
        return self._create_session(user)

    def logout(self, token: str) -> None:
        self.db.delete_auth_session(token)

    def get_session(self, token: str) -> Optional[AuthSession]:
        row = self.db.get_auth_session(token)
        if not row:
            return None
        expires_raw = row["expires_at"]
        if expires_raw.endswith("Z"):
            expires = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
        else:
            expires = datetime.fromisoformat(expires_raw)
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
        if expires < datetime.now(timezone.utc):
            self.db.delete_auth_session(token)
            return None
        user_row = self.db.get_platform_user_by_id(row["user_id"])
        if not user_row:
            return None
        user = _user_from_row(user_row)
        return AuthSession(token=token, user=user, expires_at=row["expires_at"])

    def _create_session(self, user: PlatformUser) -> AuthSession:
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(hours=SESSION_HOURS)).isoformat()
        self.db.create_auth_session(
            token=token,
            user_id=user.id,
            expires_at=expires,
            created_at=now.isoformat(),
        )
        return AuthSession(token=token, user=user, expires_at=expires)
