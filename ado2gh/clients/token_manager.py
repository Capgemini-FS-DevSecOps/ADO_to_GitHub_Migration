"""Multi-token load balancer with rate-limit awareness and GitHub App auth."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit

import requests

log = logging.getLogger("ado2gh")


@dataclass
class TokenInfo:
    token: str = field(repr=False)
    remaining: int = 5000
    reset_at: float = 0.0
    last_checked: float = 0.0
    is_app_token: bool = False
    app_token_expiry: float = 0.0
    limit: int = 5000
    blocked_until: float = 0.0
    reset_window_floor: float = 0.0
    window_generation: int = 0
    last_response_sequence: int = 0
    in_flight: set[int] = field(default_factory=set, repr=False)


@dataclass(frozen=True)
class TokenReservation:
    """One atomically reserved GitHub API request slot.

    ``sequence`` lets the manager identify duplicate completions and merge
    concurrent responses without allowing an older response to replenish a
    newer quota view.
    """

    token: str = field(repr=False)
    sequence: int
    reset_at_when_issued: float
    window_generation_when_issued: int


@dataclass
class AppCredentials:
    app_id: str
    installation_id: str
    private_key_path: str
    api_base: str = "https://api.github.com"


class TokenManager:
    """Thread-safe token scheduler with conservative rate-limit accounting."""

    def __init__(self, max_rate_limit_wait_sec: float = 300.0,
                 rate_limit_buffer: int = 50,
                 secondary_cooldown_sec: float = 60.0,
                 reset_grace_sec: float = 1.0):
        self._tokens: list[TokenInfo] = []
        self._lock = threading.Lock()
        self._app_refresh_lock = threading.Lock()
        self._idx = 0
        self._next_sequence = 1
        self._app_creds: Optional[AppCredentials] = None
        self.max_rate_limit_wait_sec = max(0.0, max_rate_limit_wait_sec)
        self.rate_limit_buffer = max(0, int(rate_limit_buffer))
        self.secondary_cooldown_sec = max(1.0, float(secondary_cooldown_sec))
        self.reset_grace_sec = max(0.0, float(reset_grace_sec))

    def _append_pat(self, token: str, source: str) -> None:
        value = str(token).strip()
        if not value:
            return
        if any(info.token == value for info in self._tokens):
            log.warning("Duplicate GitHub credential from %s ignored", source)
            return
        self._tokens.append(TokenInfo(token=value))

    @classmethod
    def from_env(cls, token_env_vars: list[str]) -> "TokenManager":
        mgr = cls()
        for var in token_env_vars:
            val = os.environ.get(var, "").strip()
            if val:
                mgr._append_pat(val, var)
            else:
                log.warning(f"Token env var {var} not set, skipping")
        if not mgr._tokens:
            raise ValueError(f"No tokens found from env vars: {token_env_vars}")
        return mgr

    @classmethod
    def from_single_token(cls, token: str) -> "TokenManager":
        if not isinstance(token, str) or not token.strip():
            raise ValueError("GitHub token must be a non-empty string")
        mgr = cls()
        mgr._append_pat(token, "direct configuration")
        return mgr

    @staticmethod
    def _https_api_base(value: str) -> str:
        base = str(value).strip().rstrip("/")
        parsed = urlsplit(base)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "GitHub API base must be absolute HTTPS without credentials, "
                "query, or fragment"
            )
        return base

    @classmethod
    def from_json_config(cls, config_path: str,
                         token_key: str = "target") -> "TokenManager":
        mgr = cls()
        with open(config_path) as f:
            cfg = json.load(f)

        pat_envs = cfg.get("pat_token_envs", {}).get(token_key, [])
        for var in pat_envs:
            val = os.environ.get(var, "").strip()
            if val:
                mgr._append_pat(val, str(var))

        app_envs = cfg.get("app_token_envs", {}).get(token_key, [])
        for app_triple in app_envs:
            if len(app_triple) == 3:
                app_id = os.environ.get(app_triple[0], "").strip()
                install_id = os.environ.get(app_triple[1], "").strip()
                key_path = os.environ.get(app_triple[2], "").strip()
                if app_id and install_id and key_path:
                    mgr._app_creds = AppCredentials(app_id, install_id, key_path)

        if not mgr._tokens and not mgr._app_creds:
            raise ValueError(f"No tokens found in {config_path} for key={token_key}")
        return mgr

    def configure_app_auth(self, app_id: str, installation_id: str,
                           private_key_path: str,
                           api_base: str = "https://api.github.com"):
        values = tuple(
            str(value).strip()
            for value in (app_id, installation_id, private_key_path)
        )
        if not all(values):
            raise ValueError(
                "GitHub App ID, installation ID, and private key path are required"
            )
        with self._lock:
            self._app_creds = AppCredentials(
                values[0],
                values[1],
                values[2],
                self._https_api_base(api_base),
            )

    def _refresh_window_locked(self, info: TokenInfo, now: float) -> None:
        """Advance a locally expired window without forgetting its boundary."""
        if (
            info.reset_at > 0.0
            and now >= info.reset_at + self.reset_grace_sec
        ):
            info.reset_window_floor = max(
                info.reset_window_floor, info.reset_at
            )
            info.reset_at = 0.0
            info.window_generation += 1
            # Requests already in flight may be charged to the fresh window.
            info.remaining = max(0, info.limit - len(info.in_flight))
        if info.blocked_until > 0.0 and now >= info.blocked_until:
            info.blocked_until = 0.0

    def _try_reserve_locked(
        self, now: float, excluded_tokens: frozenset[str]
    ) -> tuple[Optional[TokenReservation], Optional[float]]:
        """Reserve one unit, returning the earliest known future availability."""
        earliest_ready: Optional[float] = None
        token_count = len(self._tokens)
        for _ in range(token_count):
            info = self._tokens[self._idx % token_count]
            self._idx += 1
            if info.token in excluded_tokens:
                continue
            if (
                info.is_app_token
                and info.app_token_expiry > 0.0
                and now >= info.app_token_expiry - 60.0
            ):
                continue

            self._refresh_window_locked(info, now)
            if (
                now >= info.blocked_until
                and info.remaining > self.rate_limit_buffer
            ):
                sequence = self._next_sequence
                self._next_sequence += 1
                info.remaining -= 1
                info.in_flight.add(sequence)
                return TokenReservation(
                    token=info.token,
                    sequence=sequence,
                    reset_at_when_issued=info.reset_at,
                    window_generation_when_issued=info.window_generation,
                ), earliest_ready

            ready_at = now
            has_known_ready_time = False
            if info.blocked_until > now:
                ready_at = max(ready_at, info.blocked_until)
                has_known_ready_time = True
            if info.remaining <= self.rate_limit_buffer and info.reset_at > 0.0:
                ready_at = max(
                    ready_at, info.reset_at + self.reset_grace_sec
                )
                has_known_ready_time = True
            if has_known_ready_time and (
                earliest_ready is None or ready_at < earliest_ready
            ):
                earliest_ready = ready_at
        return None, earliest_ready

    def try_reserve_token(
        self, excluded_tokens: Optional[set[str]] = None
    ) -> Optional[TokenReservation]:
        """Atomically reserve an immediately available token, without waiting."""
        excluded = frozenset(excluded_tokens or ())
        with self._lock:
            reservation, _ = self._try_reserve_locked(time.time(), excluded)
            return reservation

    def reserve_token(self) -> TokenReservation:
        """Atomically reserve one API request slot, waiting only within bounds."""
        while True:
            needs_app_token = False
            with self._lock:
                now = time.time()
                reservation, earliest_ready = self._try_reserve_locked(
                    now, frozenset()
                )
                if reservation is not None:
                    return reservation
                has_nonexpired_app_token = any(
                    t.is_app_token
                    and (
                        not t.app_token_expiry
                        or now < t.app_token_expiry - 60.0
                    )
                    for t in self._tokens
                )
                needs_app_token = bool(
                    self._app_creds and not has_nonexpired_app_token
                )
                if not self._tokens and not self._app_creds:
                    raise ValueError("No tokens available")

            if needs_app_token:
                # App-token creation performs network I/O, so it must remain
                # outside the state lock. A separate lock prevents a refresh
                # stampede when many workers start together.
                self._get_app_token()
                continue

            if earliest_ready is None:
                raise RuntimeError(
                    "All GitHub tokens have exhausted their reserved quota "
                    "without usable reset metadata"
                )
            wait = max(0.0, earliest_ready - time.time())
            if wait > self.max_rate_limit_wait_sec:
                raise RuntimeError(
                    f"All GitHub tokens are rate-limited for {wait:.0f}s; "
                    f"bounded wait is {self.max_rate_limit_wait_sec:.0f}s"
                )
            if wait > 0.0:
                log.warning(
                    "All GitHub tokens are rate-limited. Waiting %.1fs...", wait
                )
                time.sleep(wait)

    def get_token(self) -> str:
        """Reserve one quota unit and return a token for an external operation.

        Callers that can observe HTTP responses should use ``reserve_token``
        and ``complete_reservation``. Raw token consumers (git/GEI) still
        decrement capacity atomically, but are detached from in-flight response
        tracking because they cannot report GitHub response headers.
        """
        reservation = self.reserve_token()
        self.abandon_reservation(reservation)
        return reservation.token

    @staticmethod
    def _coerce_nonnegative_int(value: Optional[int]) -> Optional[int]:
        if value is None or isinstance(value, bool):
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return max(0, parsed)

    @staticmethod
    def _coerce_nonnegative_float(value: Optional[float]) -> Optional[float]:
        if value is None or isinstance(value, bool):
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return max(0.0, parsed)

    def abandon_reservation(self, reservation: TokenReservation) -> None:
        """Release in-flight tracking while retaining the reserved quota cost."""
        with self._lock:
            for info in self._tokens:
                if info.token == reservation.token:
                    info.in_flight.discard(reservation.sequence)
                    return

    def complete_reservation(
        self,
        reservation: TokenReservation,
        *,
        remaining: Optional[int] = None,
        reset_at: Optional[float] = None,
        limit: Optional[int] = None,
        primary_rate_limited: bool = False,
        secondary_rate_limited: bool = False,
        retry_after_at: Optional[float] = None,
    ) -> None:
        """Merge one response into quota state and release its in-flight slot."""
        self.update_rate_limit(
            reservation.token,
            remaining,
            reset_at,
            reservation=reservation,
            limit=limit,
            primary_rate_limited=primary_rate_limited,
            secondary_rate_limited=secondary_rate_limited,
            retry_after_at=retry_after_at,
        )

    def update_rate_limit(
        self,
        token: str,
        remaining: Optional[int],
        reset_at: Optional[float],
        *,
        reservation: Optional[TokenReservation] = None,
        limit: Optional[int] = None,
        primary_rate_limited: bool = False,
        secondary_rate_limited: bool = False,
        retry_after_at: Optional[float] = None,
    ) -> None:
        """Monotonically merge response state for one token.

        Within a reset window, remaining capacity can only decrease. A larger
        reset timestamp starts a new window; a smaller/expired timestamp is a
        stale response and cannot overwrite the current window. Pending
        reservations are subtracted whenever a fresh window is accepted.
        """
        observed_remaining = self._coerce_nonnegative_int(remaining)
        observed_reset = self._coerce_nonnegative_float(reset_at)
        observed_limit = self._coerce_nonnegative_int(limit)
        observed_retry_at = self._coerce_nonnegative_float(retry_after_at)
        with self._lock:
            info = next((t for t in self._tokens if t.token == token), None)
            if info is None:
                return
            if reservation is not None:
                if reservation.sequence not in info.in_flight:
                    # Duplicate completions must not mutate quota/cooldown state.
                    return
                info.in_flight.remove(reservation.sequence)

            now = time.time()
            self._refresh_window_locked(info, now)
            stale_generation = bool(
                reservation is not None
                and reservation.window_generation_when_issued
                < info.window_generation
                and not observed_reset
            )
            stale_window = bool(
                observed_reset
                and (
                    observed_reset <= info.reset_window_floor
                    or (info.reset_at > 0.0 and observed_reset < info.reset_at)
                )
            )

            if observed_limit is not None and observed_limit > 0 and not stale_window:
                info.limit = observed_limit
                info.remaining = min(
                    info.remaining, max(0, info.limit - len(info.in_flight))
                )

            if not stale_generation and not stale_window:
                if observed_reset and (
                    info.reset_at == 0.0 or observed_reset > info.reset_at
                ):
                    if info.reset_at > 0.0:
                        info.reset_window_floor = max(
                            info.reset_window_floor, info.reset_at
                        )
                        info.window_generation += 1
                    info.reset_at = observed_reset
                    if observed_remaining is not None:
                        info.remaining = max(
                            0, observed_remaining - len(info.in_flight)
                        )
                elif observed_remaining is not None:
                    # Same-window responses may arrive out of order. Taking
                    # the minimum ensures an older, larger value cannot restore
                    # quota already reserved or reported by a newer response.
                    info.remaining = min(info.remaining, observed_remaining)

            if reservation is not None:
                info.last_response_sequence = max(
                    info.last_response_sequence, reservation.sequence
                )
            info.last_checked = now

            cooldown_until = (
                (observed_retry_at or 0.0)
                if primary_rate_limited or secondary_rate_limited
                else 0.0
            )
            if primary_rate_limited and info.reset_at > now:
                cooldown_until = max(
                    cooldown_until, info.reset_at + self.reset_grace_sec
                )
            if (primary_rate_limited or secondary_rate_limited) and (
                cooldown_until <= now
            ):
                cooldown_until = now + self.secondary_cooldown_sec
            if cooldown_until > now:
                # Cooldowns are monotonic too: a late response cannot shorten
                # a newer Retry-After or primary-reset fence.
                info.blocked_until = max(info.blocked_until, cooldown_until)

    def check_rate_limits(self, api_base: str = "https://api.github.com"):
        """Proactively check rate limits for all tokens."""
        api_base = self._https_api_base(api_base)
        with self._lock:
            tokens = [t.token for t in self._tokens if not t.is_app_token]
        for token in tokens:
            try:
                r = requests.get(
                    f"{api_base}/rate_limit",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=10,
                    allow_redirects=False,
                )
                if r.ok:
                    core = r.json().get("resources", {}).get("core", {})
                    self.update_rate_limit(
                        token,
                        core.get("remaining"),
                        core.get("reset"),
                        limit=core.get("limit"),
                    )
            except Exception:
                log.debug("GitHub rate-limit preflight failed", exc_info=True)

    def _get_app_token(self) -> str:
        """Generate a GitHub App installation token via JWT."""
        with self._app_refresh_lock:
            return self._get_app_token_without_refresh_lock()

    def _get_app_token_without_refresh_lock(self) -> str:
        with self._lock:
            creds = self._app_creds
            # Check if we have a cached app token that's still valid.
            for t in self._tokens:
                if t.is_app_token and time.time() < t.app_token_expiry - 60:
                    return t.token
        if not creds:
            raise ValueError("No app credentials configured")

        try:
            import jwt
            from cryptography.hazmat.primitives import serialization

            with open(creds.private_key_path, "rb") as f:
                private_key = serialization.load_pem_private_key(f.read(), password=None)

            now = int(time.time())
            payload = {
                "iat": now - 60,
                "exp": now + (10 * 60),
                "iss": creds.app_id,
            }
            encoded_jwt = jwt.encode(payload, private_key, algorithm="RS256")

            r = requests.post(
                f"{creds.api_base}/app/installations/"
                f"{creds.installation_id}/access_tokens",
                headers={
                    "Authorization": f"Bearer {encoded_jwt}",
                    "Accept": "application/vnd.github+json",
                },
                timeout=30,
                allow_redirects=False,
            )
            r.raise_for_status()
            data = r.json()
            token = data["token"]
            # Cache the token
            info = TokenInfo(
                token=token, is_app_token=True,
                app_token_expiry=time.time() + 3500,
            )
            with self._lock:
                # Remove old app tokens
                self._tokens = [t for t in self._tokens if not t.is_app_token]
                self._tokens.append(info)

            log.info("Generated GitHub App installation token")
            return token

        except ImportError:
            raise ImportError(
                "GitHub App auth requires: pip install cryptography PyJWT"
            )

    @property
    def token_count(self) -> int:
        with self._lock:
            # Configured app credentials do not represent an additional token
            # once their installation token is already cached.
            return len(self._tokens) + int(
                self._app_creds is not None
                and not any(t.is_app_token for t in self._tokens)
            )

    def summary(self) -> dict:
        with self._lock:
            now = time.time()
            for info in self._tokens:
                self._refresh_window_locked(info, now)
            return {
                "pat_count": len(
                    [t for t in self._tokens if not t.is_app_token]
                ),
                "app_configured": self._app_creds is not None,
                "tokens": [
                    {
                        "limit": t.limit,
                        "remaining": t.remaining,
                        "reset_at": t.reset_at,
                        "blocked_until": t.blocked_until,
                        "in_flight": len(t.in_flight),
                        "available": (
                            t.remaining > self.rate_limit_buffer
                            and now >= t.blocked_until
                            and (
                                not t.is_app_token
                                or not t.app_token_expiry
                                or now < t.app_token_expiry - 60.0
                            )
                        ),
                        "is_app": t.is_app_token,
                    }
                    for t in self._tokens
                ],
            }
