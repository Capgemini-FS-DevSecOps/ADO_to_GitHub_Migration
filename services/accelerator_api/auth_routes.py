"""Platform authentication REST routes.

Covers the whole account lifecycle behind ``/v1/auth``: bootstrapping the first
admin, self-registration and admin approval, sign-in and sign-out, and the
admin-only user administration endpoints.

Sessions are carried in an HTTP-only, same-site cookie rather than in the
response body, so no token value is ever readable from JavaScript or visible in
a payload. Passwords are accepted only on the way in and are stored hashed;
neither a password nor a session token appears in any response documented here.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ado2gh.auth.models import PlatformRole, PlatformUser
from ado2gh.auth.service import (
    SESSION_COOKIE,
    AuthService,
    auth_enabled,
    permissions_for,
)

router = APIRouter(prefix="/v1/auth", tags=["auth"])
_svc = AuthService()


class BootstrapBody(BaseModel):
    """Request body of ``POST /v1/auth/bootstrap`` — the first admin account.

    Attributes:
        username: Login name for the admin account, 3 to 64 characters.
        password: Plaintext password of at least 12 characters. It is checked
            against the strength policy and only ever kept hashed.
        display_name: Human-readable name shown in the console; falls back to
            the username when left empty.
    """

    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12)
    display_name: str = ""


class LoginBody(BaseModel):
    """Request body of ``POST /v1/auth/login`` — the sign-in credentials.

    Attributes:
        username: Login name of the account signing in.
        password: Plaintext password, compared against the stored hash and
            never echoed back or written to a log.
    """

    username: str
    password: str


class RegisterBody(BaseModel):
    """Request body of ``POST /v1/auth/register`` — a self-service account request.

    Attributes:
        username: Login name for the requested account, 3 to 64 characters.
        password: Plaintext password of at least 12 characters. It is checked
            against the strength policy and only ever kept hashed.
        display_name: Human-readable name shown in the console; falls back to
            the username when left empty.
    """

    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12)
    display_name: str = ""


class CreateUserBody(BaseModel):
    """Request body of ``POST /v1/auth/users`` — an account created by an admin.

    Attributes:
        username: Login name for the new account, 3 to 64 characters.
        password: Plaintext password of at least 12 characters. It is checked
            against the strength policy and only ever kept hashed.
        role: Platform role to grant: ``coordinator``, ``operator`` or
            ``approver``. Admins cannot be created this way.
        display_name: Human-readable name shown in the console; falls back to
            the username when left empty.
    """

    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12)
    role: str = Field(pattern="^(coordinator|operator|approver)$")
    display_name: str = ""


class UpdateUserBody(BaseModel):
    """Request body of ``PATCH /v1/auth/users/{user_id}`` — the fields to change.

    Every field is optional and defaults to ``None``; leaving one out keeps
    that part of the account exactly as it is.

    Attributes:
        role: New platform role: ``coordinator``, ``operator`` or ``approver``.
        status: New account status: ``active``, ``disabled`` or
            ``pending_approval``.
        display_name: New human-readable name shown in the console.
    """

    role: str | None = Field(default=None, pattern="^(coordinator|operator|approver)$")
    status: str | None = Field(default=None, pattern="^(active|disabled|pending_approval)$")
    display_name: str | None = None


def _onboarding_redirect() -> str | None:
    """Work out whether the console must send the caller through onboarding.

    Returns:
        The console path to redirect to when no migration profile has been set
        up yet, otherwise ``None``.
    """
    from ado2gh.api.profile_governance import needs_profile_setup
    from ado2gh.api.settings_store import SettingsStore

    profiles = SettingsStore().load().migration_profiles
    if needs_profile_setup(profiles):
        return "/onboarding/profile"
    return None


def _set_session_cookie(response: Response, token: str) -> None:
    """Attach the session cookie to an outgoing response.

    The cookie is HTTP-only and same-site ``lax``, scoped to the whole site and
    expiring after eight hours, so the session is not readable from JavaScript
    and is not carried on cross-site requests.

    Args:
        response: Outgoing response the cookie is written to.
        token: Session token issued by the auth service. Its value goes into
            the cookie only — never into a log line or a response body.
    """
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=8 * 3600,
    )


def _clear_session_cookie(response: Response) -> None:
    """Delete the session cookie so the browser stops sending it.

    Args:
        response: Outgoing response the cookie deletion is written to.
    """
    response.delete_cookie(SESSION_COOKIE, path="/")


def _user_payload(user: PlatformUser) -> dict[str, object]:
    """Render an account in the public JSON shape the console consumes.

    Args:
        user: Account to render.

    Returns:
        The account's identifier, username, role value and display name.
        Nothing derived from the password or the session is included.
    """
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role.value,
        "display_name": user.display_name,
    }


@router.get("/bootstrap-status")
def bootstrap_status() -> dict[str, object]:
    """Report whether the platform still needs its first admin account.

    The sign-in screen calls this before collecting anything from the visitor,
    so it needs no session of its own.

    Returns:
        ``needs_bootstrap`` — no admin account exists yet; ``auth_enabled`` —
        whether platform authentication is enforced at all; ``registration_enabled``
        — whether self-service registration is open; ``pending_user_approvals``
        — how many accounts are waiting for an admin; and ``message``, the line
        the console shows the visitor.
    """
    needs = _svc.needs_bootstrap()
    pending = 0
    if not needs:
        pending = sum(
            1 for u in _svc.list_users()
            if u.get("status") == "pending_approval"
        )
    return {
        "needs_bootstrap": needs,
        "auth_enabled": auth_enabled(),
        "registration_enabled": not needs,
        "pending_user_approvals": pending,
        "message": "Create the first admin account to continue" if needs else "Sign in to continue",
    }


@router.post("/bootstrap", status_code=201)
def bootstrap(body: BootstrapBody, response: Response) -> dict[str, object]:
    """Create the platform's first admin account and sign it straight in.

    Only works while no account exists; once the platform is bootstrapped the
    endpoint is permanently closed. On success the session cookie is set on the
    response, so the caller is signed in without a second round trip.

    Args:
        body: Username, password and display name for the admin account.
        response: Outgoing response the session cookie is attached to.

    Returns:
        ``user`` — the new account's public fields; ``session_expires_at`` —
        when the session stops being accepted; ``redirect_path`` — where the
        console should go next, or ``None`` when onboarding is already done.

    Raises:
        HTTPException: 403 when the platform has already been bootstrapped,
            400 when the username or password is rejected.
    """
    try:
        session = _svc.bootstrap_admin(body.username, body.password, body.display_name)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _set_session_cookie(response, session.token)
    redirect = _onboarding_redirect()
    return {
        "user": _user_payload(session.user),
        "session_expires_at": session.expires_at,
        "redirect_path": redirect,
    }


@router.post("/register", status_code=201)
def register(body: RegisterBody) -> dict[str, object]:
    """Request an operator account that an admin must then approve.

    No session is issued and no cookie is set: the account stays in
    ``pending_approval`` and sign-in keeps failing until an administrator
    approves it.

    Args:
        body: Username, password and display name for the requested account.

    Returns:
        ``pending_approval`` — always ``True``; ``user`` — the created
        account's public fields; ``message`` — the explanation the console
        shows the applicant.

    Raises:
        HTTPException: 403 when self-service registration is closed, 400 when
            the request is rejected. The 400 detail is deliberately generic so
            the endpoint cannot be used to discover which usernames exist.
    """
    try:
        user = _svc.register_operator(body.username, body.password, body.display_name)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError:
        raise HTTPException(status_code=400, detail="Registration failed")
    return {
        "pending_approval": True,
        "user": user,
        "message": (
            "Account created. A platform administrator must approve your access "
            "before you can sign in."
        ),
    }


@router.post("/login")
def login(body: LoginBody, response: Response) -> dict[str, object]:
    """Sign in with a username and password and start a session.

    On success the session cookie is set on the response. The token itself is
    never part of the body, so the browser is the only place it lives.

    Args:
        body: The sign-in credentials.
        response: Outgoing response the session cookie is attached to.

    Returns:
        ``user`` — the signed-in account's public fields;
        ``session_expires_at`` — when the session stops being accepted;
        ``redirect_path`` — the onboarding path, set only for an admin with
        onboarding still to finish and ``None`` for everyone else.

    Raises:
        HTTPException: 403 with ``account_pending_approval`` or
            ``account_disabled`` when the account exists but may not sign in;
            401 for every other failure, with a message that does not reveal
            which half of the credentials was wrong.
    """
    try:
        session = _svc.login(body.username, body.password)
    except ValueError as exc:
        detail = str(exc)
        if detail in ("account_pending_approval", "account_disabled"):
            raise HTTPException(status_code=403, detail=detail) from exc
        raise HTTPException(status_code=401, detail="Invalid credentials") from exc
    _set_session_cookie(response, session.token)
    redirect = _onboarding_redirect()
    if session.user.role.value == "admin" and redirect:
        pass
    elif session.user.role.value != "admin":
        redirect = None
    return {
        "user": _user_payload(session.user),
        "session_expires_at": session.expires_at,
        "redirect_path": redirect,
    }


@router.get("/users")
def list_users(request: Request) -> dict[str, object]:
    """List every platform account. Admin only.

    Args:
        request: Incoming request; the caller is identified from its session
            cookie.

    Returns:
        ``users`` — one entry per account carrying its public fields and
        current status.

    Raises:
        HTTPException: 403 when the request carries no valid session or the
            caller is not an admin.
    """
    token = request.cookies.get(SESSION_COOKIE, "")
    session = _svc.get_session(token) if token else None
    if not session or session.user.role != PlatformRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin only")
    return {"users": _svc.list_users()}


@router.post("/users", status_code=201)
def create_user(body: CreateUserBody, request: Request) -> dict[str, object]:
    """Create an account on someone's behalf. Admin only.

    Unlike self-service registration the account is active immediately and
    needs no separate approval step.

    Args:
        body: Username, password, role and display name for the new account.
        request: Incoming request; the caller is identified from its session
            cookie and recorded as the actor on the audit trail.

    Returns:
        ``user`` — the new account's public fields.

    Raises:
        HTTPException: 403 when the request carries no valid session or the
            caller is not an admin; 400 when the username is already taken or
            a field is rejected.
    """
    token = request.cookies.get(SESSION_COOKIE, "")
    session = _svc.get_session(token) if token else None
    if not session or session.user.role != PlatformRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin only")
    try:
        user = _svc.create_user(
            body.username,
            body.password,
            body.role,
            body.display_name,
            actor=session.user.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"user": _user_payload(user)}


@router.patch("/users/{user_id}")
def update_user(user_id: str, body: UpdateUserBody, request: Request) -> dict[str, object]:
    """Change an account's role, status or display name. Admin only.

    An admin cannot disable their own account here, so the platform can never
    be left without a way in.

    Args:
        user_id: Identifier of the account to change.
        body: Fields to change; anything omitted is left as it is.
        request: Incoming request; the caller is identified from its session
            cookie and recorded as the actor on the audit trail.

    Returns:
        ``user`` — the account as stored after the change.

    Raises:
        HTTPException: 403 when the request carries no valid session or the
            caller is not an admin; 400 when an admin tries to disable their
            own account or a field is rejected; 404 when no account carries
            that identifier.
    """
    token = request.cookies.get(SESSION_COOKIE, "")
    session = _svc.get_session(token) if token else None
    if not session or session.user.role != PlatformRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin only")
    if session.user.id == user_id and body.status == "disabled":
        raise HTTPException(status_code=400, detail="Cannot disable your own account")
    try:
        user = _svc.update_user(
            user_id,
            role=body.role,
            status=body.status,
            display_name=body.display_name,
            actor=session.user.username,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="User not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"user": user}


@router.post("/users/{user_id}/approve")
def approve_user(user_id: str, request: Request) -> dict[str, object]:
    """Approve a pending registration so the account can sign in. Admin only.

    Args:
        user_id: Identifier of the account to approve.
        request: Incoming request; the caller is identified from its session
            cookie and recorded as the actor on the audit trail.

    Returns:
        ``user`` — the account as stored after approval.

    Raises:
        HTTPException: 403 when the request carries no valid session or the
            caller is not an admin; 400 when the account is not awaiting
            approval; 404 when no account carries that identifier.
    """
    token = request.cookies.get(SESSION_COOKIE, "")
    session = _svc.get_session(token) if token else None
    if not session or session.user.role != PlatformRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin only")
    try:
        user = _svc.approve_user(user_id, actor=session.user.username)
    except KeyError:
        raise HTTPException(status_code=404, detail="User not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"user": user}


@router.post("/users/{user_id}/disable")
def disable_user(user_id: str, request: Request) -> dict[str, object]:
    """Disable an account so it can no longer sign in. Admin only.

    An admin cannot disable their own account, so the platform can never be
    left without a way in. Existing sessions for the account stop being
    accepted.

    Args:
        user_id: Identifier of the account to disable.
        request: Incoming request; the caller is identified from its session
            cookie and recorded as the actor on the audit trail.

    Returns:
        ``user`` — the account as stored after being disabled.

    Raises:
        HTTPException: 403 when the request carries no valid session or the
            caller is not an admin; 400 when an admin targets their own
            account; 404 when no account carries that identifier.
    """
    token = request.cookies.get(SESSION_COOKIE, "")
    session = _svc.get_session(token) if token else None
    if not session or session.user.role != PlatformRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin only")
    if session.user.id == user_id:
        raise HTTPException(status_code=400, detail="Cannot disable your own account")
    try:
        user = _svc.disable_user(user_id, actor=session.user.username)
    except KeyError:
        raise HTTPException(status_code=404, detail="User not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"user": user}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict[str, object]:
    """End the caller's session and clear the session cookie.

    Safe to call without a session: the cookie is cleared and the same body is
    returned either way, so the console can always offer a sign-out.

    Args:
        request: Incoming request; the session to revoke is read from its
            cookie.
        response: Outgoing response the cookie deletion is written to.

    Returns:
        ``ok`` — always ``True``.
    """
    token = request.cookies.get(SESSION_COOKIE, "")
    if token:
        _svc.logout(token)
    _clear_session_cookie(response)
    return {"ok": True}


@router.get("/session")
def current_session(request: Request) -> dict[str, object]:
    """Describe the caller's session: who they are and what they may do.

    The console calls this on load to decide which parts of the UI to render.

    Args:
        request: Incoming request; the caller is identified from its session
            cookie.

    Returns:
        ``authenticated`` — always ``True`` on success; ``user`` — the
        account's public fields; ``expires_at`` — when the session stops being
        accepted; ``permissions`` — the permission map for the account's role.

    Raises:
        HTTPException: 401 when the request carries no session cookie, or the
            session has expired or been revoked.
    """
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    session = _svc.get_session(token)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    role = session.user.role
    return {
        "authenticated": True,
        "user": _user_payload(session.user),
        "expires_at": session.expires_at,
        "permissions": permissions_for(role),
    }
