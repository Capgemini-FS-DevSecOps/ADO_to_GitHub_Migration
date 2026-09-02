"""Per-thread HTTP session factory — avoids sharing requests.Session across threads."""
from __future__ import annotations

import threading

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


