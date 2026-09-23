"""Shared HTTP session factory."""
import threading

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def make_session(retries: int = 5) -> requests.Session:
    """Build a ``requests`` session that retries transient failures with backoff.

    Args:
        retries: Maximum retries per request before the error is raised.
            Retries cover 429 and the common 5xx responses on every method and
            honour ``Retry-After``.

    Returns:
        A session with the retry adapter mounted for HTTP and HTTPS.
    """
    s = requests.Session()
    retry = Retry(
        total=retries, backoff_factor=2.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        respect_retry_after_header=True,
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    return s


_thread_local = threading.local()


def get_thread_session() -> requests.Session:
    """Return a session bound to the current thread.

    Returns:
        The calling thread's requests session, created on first use and reused
        by every later call on the same thread.
    """
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = make_session()
        _thread_local.session = session
    return session
