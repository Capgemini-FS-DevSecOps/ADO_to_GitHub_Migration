"""Platform authentication REST routes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ado2gh.auth.models import PlatformRole
from ado2gh.auth.service import (
    AuthService,
    SESSION_COOKIE,
    auth_enabled,
    permissions_for,
)

router = APIRouter(prefix="/v1/auth", tags=["auth"])
_svc = AuthService()


class BootstrapBody(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12)
    display_name: str = ""


class LoginBody(BaseModel):
    username: str
    password: str


class RegisterBody(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12)
    display_name: str = ""


class CreateUserBody(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12)
    role: str = Field(pattern="^(coordinator|operator|approver)$")
    display_name: str = ""


class UpdateUserBody(BaseModel):
    role: str | None = Field(default=None, pattern="^(coordinator|operator|approver)$")
    status: str | None = Field(default=None, pattern="^(active|disabled|pending_approval)$")
    display_name: str | None = None


def _onboarding_redirect() -> str | None:
    from ado2gh.api.settings_store import SettingsStore
    from ado2gh.api.profile_governance import needs_profile_setup

    profiles = SettingsStore().load().migration_profiles
    if needs_profile_setup(profiles):
        return "/onboarding/profile"
    return None


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=8 * 3600,
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def _user_payload(user) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role.value,
        "display_name": user.display_name,
    }


@router.get("/bootstrap-status")
def bootstrap_status():
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
def bootstrap(body: BootstrapBody, response: Response):
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
def register(body: RegisterBody):
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
def login(body: LoginBody, response: Response):
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
def list_users(request: Request):
    token = request.cookies.get(SESSION_COOKIE, "")
    session = _svc.get_session(token) if token else None
    if not session or session.user.role != PlatformRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin only")
    return {"users": _svc.list_users()}


@router.post("/users", status_code=201)
def create_user(body: CreateUserBody, request: Request):
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
def update_user(user_id: str, body: UpdateUserBody, request: Request):
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
def approve_user(user_id: str, request: Request):
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
def disable_user(user_id: str, request: Request):
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
def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE, "")
    if token:
        _svc.logout(token)
    _clear_session_cookie(response)
    return {"ok": True}


@router.get("/session")
def current_session(request: Request):
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
