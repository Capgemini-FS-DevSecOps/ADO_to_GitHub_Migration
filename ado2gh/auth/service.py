"""Authentication service — bootstrap, login, sessions."""
from __future__ import annotations

import os
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from ado2gh.auth.models import AuthSession, PlatformRole, PlatformUser, PlatformUserStatus
from ado2gh.auth.password import hash_password, validate_password_strength, verify_password
from ado2gh.state.factory import StateStore, create_state_db

SESSION_COOKIE = "ado2gh_session"
SESSION_HOURS = int(os.environ.get("ADO2GH_SESSION_HOURS", "8"))
LOGIN_MAX_ATTEMPTS = int(os.environ.get("ADO2GH_LOGIN_MAX_ATTEMPTS", "5"))
LOGIN_LOCKOUT_SECONDS = int(os.environ.get("ADO2GH_LOGIN_LOCKOUT_SECONDS", "300"))


def auth_enabled() -> bool:
    """Report whether platform authentication is switched on.

    Returns:
        True when ``ADO2GH_AUTH_ENABLED`` is set to ``1``, ``true`` or ``yes``.
        When it is False the API runs unauthenticated, which is the local
        development default.
    """
    return os.environ.get("ADO2GH_AUTH_ENABLED", "").lower() in ("1", "true", "yes")


def _db() -> StateStore:
    """Open the configured state store using the environment's database path.

    Returns:
        The state store selected by ``ADO2GH_STORAGE_BACKEND``, opened at
        ``ADO2GH_SQLITE_PATH`` when the backend is SQLite.
    """
    return create_state_db(os.environ.get("ADO2GH_SQLITE_PATH", "migration_state.db"))


def _user_from_row(row: dict) -> PlatformUser:
    """Build a ``PlatformUser`` from a ``platform_users`` database row.

    Args:
        row: The stored row. Any ``password_hash`` it carries is ignored — the
            returned object holds no credential material.

    Returns:
        The account, with ``display_name`` falling back to the username when
        the stored value is empty.
    """
    return PlatformUser(
        id=row["id"],
        username=row["username"],
        role=PlatformRole(row["role"]),
        display_name=row.get("display_name") or row["username"],
    )


def _user_status(row: dict) -> PlatformUserStatus:
    """Read the account status out of a ``platform_users`` row.

    Args:
        row: The stored row.

    Returns:
        The parsed status, defaulting to ``ACTIVE`` when the column is empty or
        holds a value this version does not recognise.
    """
    raw = row.get("status") or PlatformUserStatus.ACTIVE.value
    try:
        return PlatformUserStatus(raw)
    except ValueError:
        return PlatformUserStatus.ACTIVE


def _user_public(row: dict) -> dict:
    """Reduce a ``platform_users`` row to the fields the API may return.

    Args:
        row: The stored row.

    Returns:
        A dict with the id, username, role, display name, status and creation
        timestamp. The password hash is deliberately left out.
    """
    return {
        "id": row["id"],
        "username": row["username"],
        "role": row["role"],
        "display_name": row.get("display_name") or row["username"],
        "status": _user_status(row).value,
        "created_at": row.get("created_at"),
    }


def _ensure_user_can_authenticate(row: dict) -> None:
    """Reject an account whose status forbids logging in.

    Args:
        row: The stored ``platform_users`` row.

    Raises:
        ValueError: The account is awaiting admin approval
            (``account_pending_approval``) or has been disabled
            (``account_disabled``).
    """
    status = _user_status(row)
    if status == PlatformUserStatus.PENDING_APPROVAL:
        raise ValueError("account_pending_approval")
    if status == PlatformUserStatus.DISABLED:
        raise ValueError("account_disabled")


def permissions_for(role: PlatformRole) -> dict[str, bool]:
    """Expand a role into the permission flags the console and API check.

    Args:
        role: The role held by the account.

    Returns:
        A flag per permission. Admins hold every permission; coordinators may
        coordinate and operate; operators may only operate; approvers may
        approve, including live execution.
    """
    return {
        "can_coordinate": role in (PlatformRole.ADMIN, PlatformRole.COORDINATOR),
        "can_operate": role in (
            PlatformRole.ADMIN, PlatformRole.COORDINATOR, PlatformRole.OPERATOR,
        ),
        "can_approve": role in (PlatformRole.ADMIN, PlatformRole.APPROVER),
        "can_approve_live_execution": role in (PlatformRole.ADMIN, PlatformRole.APPROVER),
        "can_manage_users": role == PlatformRole.ADMIN,
        "can_manage_settings": role == PlatformRole.ADMIN,
        "can_manage_models": role == PlatformRole.ADMIN,
    }


def _audit_auth(event_type: str, actor: str, payload: dict | None = None) -> None:
    """Write an authentication event to the platform audit log.

    Args:
        event_type: Event name, for example ``user.login``.
        actor: Username the event is attributed to.
        payload: Extra event fields. Callers must keep passwords, password
            hashes and session tokens out of this dict.
    """
    from ado2gh.api.profile_governance import write_profile_audit

    write_profile_audit(
        event_type,
        profile_id="_platform",
        actor=actor,
        payload=payload or {},
        db_path=os.environ.get("ADO2GH_SQLITE_PATH", "migration_state.db"),
    )


class AuthService:
    """Platform login and session management."""

    def __init__(self, db: Optional[StateStore] = None) -> None:
        """Bind the service to a state store.

        Args:
            db: State store to read and write accounts and sessions through.
                When omitted, the store configured in the environment is
                opened.
        """
        self.db = db or _db()
        # ponytail: in-process counter, move to Redis if the API runs multi-replica
        self._login_failures: dict[str, tuple[int, float]] = {}

    def needs_bootstrap(self) -> bool:
        """Report whether the platform still has no accounts at all.

        Returns:
            True when no platform user exists, meaning the first admin has yet
            to be bootstrapped.
        """
        return self.db.count_platform_users() == 0

    def bootstrap_admin(
        self, username: str, password: str, display_name: str,
    ) -> AuthSession:
        """Create the first admin account and log it straight in.

        Args:
            username: Login name; it is stripped and lower-cased before use.
            password: Plaintext password; it is checked against the strength
                policy and only ever stored hashed.
            display_name: Human-readable name; falls back to the username when
                empty.

        Returns:
            A session for the new admin.

        Raises:
            PermissionError: An account already exists, so bootstrap is closed.
            ValueError: The password fails the strength policy.
        """
        if not self.needs_bootstrap():
            raise PermissionError("Bootstrap not allowed — users already exist")
        validate_password_strength(password)
        user_id = f"usr_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        user = PlatformUser(user_id, username.strip().lower(), PlatformRole.ADMIN, display_name or username)
        self.db.create_platform_user(
            user,
            password_hash=hash_password(password),
            status=PlatformUserStatus.ACTIVE.value,
            created_at=now,
        )
        session = self._create_session(user)
        _audit_auth("user.bootstrap", user.username, {"role": user.role.value})
        return session

    def register_operator(
        self, username: str, password: str, display_name: str = "",
    ) -> dict:
        """Create an operator account pending admin approval — no session issued.

        Args:
            username: Login name; it is stripped and lower-cased before use.
            password: Plaintext password; it is checked against the strength
                policy and only ever stored hashed.
            display_name: Human-readable name; falls back to the username when
                empty.

        Returns:
            The public view of the new account, with status
            ``pending_approval``.

        Raises:
            PermissionError: The platform has not been bootstrapped yet.
            ValueError: The password fails the strength policy, or the username
                is already taken. Both raise the same generic message so the
                response does not reveal which accounts exist.
        """
        if self.needs_bootstrap():
            raise PermissionError("Registration not allowed before bootstrap")
        validate_password_strength(password)
        normalized = username.strip().lower()
        if self.db.get_platform_user_by_username(normalized):
            raise ValueError("Registration failed")
        user_id = f"usr_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        self.db.create_platform_user(
            PlatformUser(user_id, normalized, PlatformRole.OPERATOR, display_name or username),
            password_hash=hash_password(password),
            status=PlatformUserStatus.PENDING_APPROVAL.value,
            created_at=now,
        )
        from ado2gh.api.profile_governance import write_profile_audit

        write_profile_audit(
            "user.registered",
            profile_id="_platform",
            actor=normalized,
            payload={
                "role": PlatformRole.OPERATOR.value,
                "status": PlatformUserStatus.PENDING_APPROVAL.value,
            },
            db_path=os.environ.get("ADO2GH_SQLITE_PATH", "migration_state.db"),
        )
        row = self.db.get_platform_user_by_id(user_id)
        return _user_public(row or {"id": user_id, "username": normalized, "role": "operator", "display_name": display_name or username})

    def login(self, username: str, password: str) -> AuthSession:
        """Authenticate an account and issue a session for it.

        Repeated failures for the same username lock it out for a cooldown
        window; a successful login clears the counter.

        Args:
            username: Login name; it is stripped and lower-cased before lookup.
            password: Plaintext password, checked against the stored hash.

        Returns:
            A session for the authenticated account.

        Raises:
            ValueError: The credentials are wrong, the username is currently
                locked out, or the account is pending approval or disabled.
                An unknown username, a wrong password and a lockout all raise
                the same message so the response does not reveal which.
        """
        normalized = username.strip().lower()
        self._check_not_locked_out(normalized)
        row = self.db.get_platform_user_by_username(normalized)
        if not row or not verify_password(password, row["password_hash"]):
            self._record_login_failure(normalized)
            raise ValueError("Invalid credentials")
        _ensure_user_can_authenticate(row)
        self._login_failures.pop(normalized, None)
        user = _user_from_row(row)
        session = self._create_session(user)
        _audit_auth("user.login", user.username, {"role": user.role.value})
        return session

    def _check_not_locked_out(self, username: str) -> None:
        """Reject logins for a username that has failed too many times recently.

        Args:
            username: Normalised login name to check the failure counter for.

        Raises:
            ValueError: The username is inside its lockout window. The message
                matches the bad-password one on purpose.
        """
        failures, locked_until = self._login_failures.get(username, (0, 0.0))
        if failures >= LOGIN_MAX_ATTEMPTS and time.monotonic() < locked_until:
            # Same message as a bad password so the response does not reveal
            # whether the username exists or is merely throttled.
            raise ValueError("Invalid credentials")

    def _record_login_failure(self, username: str) -> None:
        """Count a failed login and start a lockout once the limit is reached.

        A counter whose lockout window has already elapsed is reset before the
        new failure is counted.

        Args:
            username: Normalised login name that failed to authenticate.
        """
        failures, locked_until = self._login_failures.get(username, (0, 0.0))
        if failures >= LOGIN_MAX_ATTEMPTS and time.monotonic() >= locked_until:
            failures = 0
        failures += 1
        if failures >= LOGIN_MAX_ATTEMPTS:
            locked_until = time.monotonic() + LOGIN_LOCKOUT_SECONDS
        self._login_failures[username] = (failures, locked_until)

    def logout(self, token: str) -> None:
        """End a session by deleting it from the store.

        Deleting an unknown or already-expired token is not an error.

        Args:
            token: The session token to invalidate.
        """
        session = self.get_session(token)
        if session:
            _audit_auth("user.logout", session.user.username)
        self.db.delete_auth_session(token)

    def list_users(self) -> list[dict]:
        """List every platform account.

        Returns:
            The public view of each account, without password hashes.
        """
        return [_user_public(row) for row in self.db.list_platform_users()]

    def create_user(
        self,
        username: str,
        password: str,
        role: str,
        display_name: str = "",
        actor: str = "admin",
    ) -> PlatformUser:
        """Create an active account on an admin's behalf.

        Args:
            username: Login name; it is stripped and lower-cased before use.
            password: Plaintext password; it is checked against the strength
                policy and only ever stored hashed.
            role: Role value to assign. ``admin`` is refused — the only admin
                is the bootstrapped one.
            display_name: Human-readable name; falls back to the username when
                empty.
            actor: Username recorded as the actor on the audit event.

        Returns:
            The new account.

        Raises:
            ValueError: The role is ``admin`` or not a known role, the password
                fails the strength policy, or the username is already taken.
        """
        if role == PlatformRole.ADMIN.value:
            raise ValueError("Cannot create admin via API")
        try:
            platform_role = PlatformRole(role)
        except ValueError:
            raise ValueError("Invalid role")
        validate_password_strength(password)
        normalized = username.strip().lower()
        if self.db.get_platform_user_by_username(normalized):
            raise ValueError("User already exists")
        user_id = f"usr_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        user = PlatformUser(user_id, normalized, platform_role, display_name or username)
        self.db.create_platform_user(
            user,
            password_hash=hash_password(password),
            status=PlatformUserStatus.ACTIVE.value,
            created_at=now,
        )
        _audit_auth(
            "user.created",
            actor,
            {"username": normalized, "role": platform_role.value},
        )
        return user

    def update_user(
        self,
        user_id: str,
        *,
        role: str | None = None,
        status: str | None = None,
        display_name: str | None = None,
        actor: str = "admin",
    ) -> dict:
        """Change an account's role, status or display name.

        Moving an account to ``disabled`` or ``pending_approval`` also deletes
        its live sessions, so the change takes effect immediately.

        Args:
            user_id: Identifier of the account to update.
            role: New role value, or None to leave it as it is. Promoting to
                ``admin`` is refused.
            status: New status value, or None to leave it as it is.
            display_name: New display name, or None to leave it as it is.
            actor: Username recorded as the actor on the audit event.

        Returns:
            The public view of the updated account.

        Raises:
            KeyError: No account has that identifier.
            ValueError: The role or status value is not valid, or no field
                actually changed.
        """
        row = self.db.get_platform_user_by_id(user_id)
        if not row:
            raise KeyError(user_id)
        if role is not None:
            if role == PlatformRole.ADMIN.value:
                raise ValueError("Cannot promote to admin via API")
            try:
                PlatformRole(role)
            except ValueError:
                raise ValueError("Invalid role") from None
        if status is not None:
            try:
                PlatformUserStatus(status)
            except ValueError:
                raise ValueError("Invalid status") from None
        updated = self.db.update_platform_user(
            user_id,
            role=role,
            status=status,
            display_name=display_name,
        )
        if not updated:
            raise ValueError("No changes")
        if status in (PlatformUserStatus.DISABLED.value, PlatformUserStatus.PENDING_APPROVAL.value):
            if hasattr(self.db, "delete_auth_sessions_for_user"):
                self.db.delete_auth_sessions_for_user(user_id)
        fresh = self.db.get_platform_user_by_id(user_id)
        payload = {"user_id": user_id}
        if role is not None:
            payload["role"] = role
        if status is not None:
            payload["status"] = status
        _audit_auth("user.updated", actor, payload)
        return _user_public(fresh or row)

    def approve_user(self, user_id: str, *, actor: str = "admin") -> dict:
        """Approve a pending account by moving it to ``active``.

        Args:
            user_id: Identifier of the account to approve.
            actor: Username recorded as the actor on the audit event.

        Returns:
            The public view of the approved account.

        Raises:
            KeyError: No account has that identifier.
            ValueError: The account was already active.
        """
        return self.update_user(
            user_id,
            status=PlatformUserStatus.ACTIVE.value,
            actor=actor,
        )

    def disable_user(self, user_id: str, *, actor: str = "admin") -> dict:
        """Revoke an account's access and drop its live sessions.

        Args:
            user_id: Identifier of the account to disable.
            actor: Username recorded as the actor on the audit event.

        Returns:
            The public view of the disabled account.

        Raises:
            KeyError: No account has that identifier.
            ValueError: The account was already disabled.
        """
        return self.update_user(
            user_id,
            status=PlatformUserStatus.DISABLED.value,
            actor=actor,
        )

    def get_session(self, token: str) -> Optional[AuthSession]:
        """Resolve a session token to its session, if it is still valid.

        A token that has expired, or whose account has since been deleted,
        disabled or moved back to pending approval, is deleted from the store
        on the way out.

        Args:
            token: The session token presented by the caller.

        Returns:
            The session, or None when the token is unknown, expired, or its
            account may no longer authenticate.
        """
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
        try:
            _ensure_user_can_authenticate(user_row)
        except ValueError:
            self.db.delete_auth_session(token)
            return None
        user = _user_from_row(user_row)
        return AuthSession(token=token, user=user, expires_at=row["expires_at"])

    def _create_session(self, user: PlatformUser) -> AuthSession:
        """Issue and persist a new session for an authenticated account.

        Args:
            user: The account the session is for.

        Returns:
            The stored session, carrying a freshly generated URL-safe token and
            an expiry ``SESSION_HOURS`` from now.
        """
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
