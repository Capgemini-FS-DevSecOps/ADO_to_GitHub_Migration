"""Per-thread HTTP session factory — avoids sharing requests.Session across threads."""
from __future__ import annotations

import threading
from typing import Callable

import requests

from ado2gh.http_utils import make_session

_thread_local = threading.local()


def get_thread_session() -> requests.Session:
    """Return a session bound to the current thread."""
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = make_session()
        _thread_local.session = session
    return session


def clear_thread_session() -> None:
    session = getattr(_thread_local, "session", None)
    if session is not None:
        try:
            session.close()
        except Exception:
            pass
        _thread_local.session = None


def with_thread_session(fn: Callable) -> Callable:
    """Decorator ensuring fn runs with a thread-local session available."""

    def wrapper(*args, **kwargs):
        get_thread_session()
        return fn(*args, **kwargs)

    return wrapper
