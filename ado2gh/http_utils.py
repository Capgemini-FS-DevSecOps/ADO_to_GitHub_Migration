"""Shared HTTP session factory.

Retries are intentionally limited to safe, idempotent reads.  Retrying a
timed-out POST such as "create issue" can duplicate production data because a
server may have committed the first request before the client lost the reply.
"""
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def make_session(retries: int = 5, pool_size: int = 32,
                 retry_rate_limits: bool = True) -> requests.Session:
    s = requests.Session()
    # API clients carry bearer/PAT credentials.  Refuse redirects rather than
    # relying on library heuristics about when Authorization should be stripped
    # across host or scheme changes.
    s.max_redirects = 0
    retry = Retry(
        total=retries,
        connect=retries,
        read=retries,
        status=retries,
        backoff_factor=2.0,
        status_forcelist=(
            [429, 500, 502, 503, 504]
            if retry_rate_limits else [500, 502, 503, 504]
        ),
        allowed_methods=frozenset(["HEAD", "GET", "OPTIONS"]),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=pool_size,
        pool_maxsize=pool_size,
        pool_block=True,
    )
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s
