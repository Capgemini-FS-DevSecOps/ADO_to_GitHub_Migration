from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

from ado2gh.clients.gh_client import GHClient
from ado2gh.clients.token_manager import TokenInfo, TokenManager


def _manager(*tokens: str, buffer: int = 0) -> TokenManager:
    manager = TokenManager(
        max_rate_limit_wait_sec=0,
        rate_limit_buffer=buffer,
        secondary_cooldown_sec=60,
    )
    manager._tokens.extend(TokenInfo(token=token) for token in tokens)
    return manager


class _Response:
    def __init__(self, status: int, headers=None, payload=None):
        self.status_code = status
        self.headers = dict(headers or {})
        self._payload = payload if payload is not None else {}
        self.text = str(self._payload)
        self.ok = 200 <= status < 400

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"HTTP {self.status_code}", response=self
            )


class _Session:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def _call(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def get(self, url, **kwargs):
        return self._call("get", url, **kwargs)

    def post(self, url, **kwargs):
        return self._call("post", url, **kwargs)

    def patch(self, url, **kwargs):
        return self._call("patch", url, **kwargs)

    def put(self, url, **kwargs):
        return self._call("put", url, **kwargs)

    def delete(self, url, **kwargs):
        return self._call("delete", url, **kwargs)


def test_concurrent_reservations_cannot_overbook_one_token():
    manager = _manager("token")
    manager._tokens[0].remaining = 10

    with ThreadPoolExecutor(max_workers=20) as pool:
        reservations = list(pool.map(lambda _: manager.try_reserve_token(), range(20)))

    acquired = [item for item in reservations if item is not None]
    assert len(acquired) == 10
    assert len({item.sequence for item in acquired}) == 10
    assert manager.summary()["tokens"][0]["remaining"] == 0
    assert manager.summary()["tokens"][0]["in_flight"] == 10

    for reservation in acquired:
        manager.complete_reservation(reservation)
    assert manager.summary()["tokens"][0]["in_flight"] == 0


def test_raw_get_token_reserves_capacity_without_leaking_in_flight_state():
    manager = _manager("token")

    assert manager.get_token() == "token"

    state = manager.summary()["tokens"][0]
    assert state["remaining"] == 4999
    assert state["in_flight"] == 0


def test_out_of_order_same_window_response_cannot_restore_quota():
    manager = _manager("token")
    reset = time.time() + 3600
    manager._tokens[0].remaining = 100
    manager._tokens[0].reset_at = reset
    first = manager.reserve_token()
    second = manager.reserve_token()

    manager.complete_reservation(second, remaining=98, reset_at=reset)
    manager.complete_reservation(first, remaining=99, reset_at=reset)

    state = manager.summary()["tokens"][0]
    assert state["remaining"] == 98
    assert state["in_flight"] == 0


def test_old_window_response_cannot_overwrite_new_reset_window():
    manager = _manager("token")
    old_reset = time.time() + 300
    new_reset = old_reset + 3600
    manager._tokens[0].remaining = 100
    manager._tokens[0].reset_at = old_reset
    first = manager.reserve_token()
    second = manager.reserve_token()

    manager.complete_reservation(
        second, remaining=5000, reset_at=new_reset, limit=5000
    )
    manager.complete_reservation(first, remaining=0, reset_at=old_reset)

    state = manager.summary()["tokens"][0]
    assert state["remaining"] == 4999
    assert state["reset_at"] == new_reset
    assert state["in_flight"] == 0


def test_primary_rate_limit_rotates_to_an_immediately_available_token():
    manager = _manager("first", "second")
    reset = int(time.time() + 300)
    session = _Session(
        _Response(
            403,
            {
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(reset),
                "X-RateLimit-Limit": "5000",
            },
            {"message": "API rate limit exceeded"},
        ),
        _Response(
            200,
            {
                "X-RateLimit-Remaining": "4999",
                "X-RateLimit-Reset": str(reset),
            },
            {"ok": True},
        ),
    )
    client = GHClient(manager)
    client.session = session

    assert client._get("/resource") == {"ok": True}

    auth = [call[2]["headers"]["Authorization"] for call in session.calls]
    assert auth == ["Bearer first", "Bearer second"]
    assert all(call[2]["allow_redirects"] is False for call in session.calls)
    states = manager.summary()["tokens"]
    assert states[0]["remaining"] == 0
    assert states[0]["blocked_until"] >= reset
    assert states[1]["in_flight"] == 0


def test_secondary_retry_after_is_fenced_and_rotated_without_sleeping():
    manager = _manager("first", "second")
    before = time.time()
    session = _Session(
        _Response(
            403,
            {"Retry-After": "120", "X-RateLimit-Remaining": "4999"},
            {"message": "You have exceeded a secondary rate limit."},
        ),
        _Response(200, {"X-RateLimit-Remaining": "4999"}, {"ok": True}),
    )
    client = GHClient(manager)
    client.session = session

    assert client._get("/resource") == {"ok": True}

    first_state = manager.summary()["tokens"][0]
    assert first_state["blocked_until"] >= before + 119
    assert len(session.calls) == 2


def test_transport_exception_always_releases_in_flight_reservation():
    manager = _manager("token")
    client = GHClient(manager)
    client.session = _Session(requests.ConnectionError("network failed"))

    with pytest.raises(requests.ConnectionError, match="network failed"):
        client._get("/resource")

    state = manager.summary()["tokens"][0]
    assert state["remaining"] == 4999
    assert state["in_flight"] == 0


def test_non_throttle_retry_after_header_does_not_block_token():
    manager = _manager("token")
    client = GHClient(manager)
    client.session = _Session(
        _Response(202, {"Retry-After": "30"}, {"accepted": True})
    )

    assert client._post("/accepted", {}) == {"accepted": True}

    assert manager.summary()["tokens"][0]["blocked_until"] == 0


def test_non_rate_limit_403_is_not_retried_with_another_credential():
    manager = _manager("first", "second")
    client = GHClient(manager)
    session = _Session(
        _Response(
            403,
            {"X-RateLimit-Remaining": "4999"},
            {"message": "Resource not accessible by integration"},
        )
    )
    client.session = session

    with pytest.raises(requests.HTTPError):
        client._get("/forbidden")

    assert len(session.calls) == 1
    assert manager.summary()["tokens"][0]["blocked_until"] == 0
