"""Platform authentication REST routes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

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
    return {
        "needs_bootstrap": needs,
        "auth_enabled": auth_enabled(),
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
    return {
        "user": _user_payload(session.user),
        "session_expires_at": session.expires_at,
    }


@router.post("/login")
def login(body: LoginBody, response: Response):
    try:
        session = _svc.login(body.username, body.password)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    _set_session_cookie(response, session.token)
    return {
        "user": _user_payload(session.user),
        "session_expires_at": session.expires_at,
    }


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
