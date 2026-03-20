"""Multi-token load balancer with rate-limit awareness and GitHub App auth."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Optional

import requests

log = logging.getLogger("ado2gh")


@dataclass
class TokenInfo:
    token: str
    remaining: int = 5000
    reset_at: float = 0.0
    last_checked: float = 0.0
    is_app_token: bool = False
    app_token_expiry: float = 0.0


@dataclass
class AppCredentials:
    app_id: str
    installation_id: str
    private_key_path: str


class TokenManager:
    """Round-robin token manager with rate-limit awareness."""

    def __init__(self):
        self._tokens: list[TokenInfo] = []
        self._lock = threading.Lock()
        self._idx = 0
        self._app_creds: Optional[AppCredentials] = None

    @classmethod
    def from_env(cls, token_env_vars: list[str]) -> "TokenManager":
        mgr = cls()
        for var in token_env_vars:
            val = os.environ.get(var, "")
            if val:
                mgr._tokens.append(TokenInfo(token=val))
            else:
                log.warning(f"Token env var {var} not set, skipping")
        if not mgr._tokens:
            raise ValueError(f"No tokens found from env vars: {token_env_vars}")
        return mgr

    @classmethod
    def from_single_token(cls, token: str) -> "TokenManager":
        mgr = cls()
        mgr._tokens.append(TokenInfo(token=token))
        return mgr

    @classmethod
    def from_json_config(cls, config_path: str,
                         token_key: str = "target") -> "TokenManager":
        mgr = cls()
        with open(config_path) as f:
            cfg = json.load(f)

        pat_envs = cfg.get("pat_token_envs", {}).get(token_key, [])
        for var in pat_envs:
            val = os.environ.get(var, "")
            if val:
                mgr._tokens.append(TokenInfo(token=val))

        app_envs = cfg.get("app_token_envs", {}).get(token_key, [])
        for app_triple in app_envs:
            if len(app_triple) == 3:
                app_id = os.environ.get(app_triple[0], "")
                install_id = os.environ.get(app_triple[1], "")
                key_path = os.environ.get(app_triple[2], "")
                if app_id and install_id and key_path:
                    mgr._app_creds = AppCredentials(app_id, install_id, key_path)

        if not mgr._tokens and not mgr._app_creds:
            raise ValueError(f"No tokens found in {config_path} for key={token_key}")
        return mgr

    def configure_app_auth(self, app_id: str, installation_id: str,
                           private_key_path: str):
        self._app_creds = AppCredentials(app_id, installation_id, private_key_path)

    def get_token(self) -> str:
        """Get the next available token via round-robin with rate-limit awareness."""
        with self._lock:
            if not self._tokens:
                if self._app_creds:
                    return self._get_app_token()
                raise ValueError("No tokens available")

            best = None
            for _ in range(len(self._tokens)):
                candidate = self._tokens[self._idx % len(self._tokens)]
                self._idx += 1

                if candidate.remaining > 50:
                    return candidate.token

                # Token may be rate-limited; check if reset has passed
                if time.time() > candidate.reset_at:
                    candidate.remaining = 5000
                    return candidate.token

                if best is None or candidate.reset_at < best.reset_at:
                    best = candidate

            # All tokens low — wait for the soonest reset
            if best:
                wait = max(0, best.reset_at - time.time()) + 2
                log.warning(f"All tokens rate-limited. Waiting {wait:.0f}s for reset...")
                time.sleep(wait)
                best.remaining = 5000
                return best.token

            raise ValueError("No tokens available")

    def update_rate_limit(self, token: str, remaining: int, reset_at: float):
        """Update rate limit info after an API call."""
        with self._lock:
            for t in self._tokens:
                if t.token == token:
                    t.remaining = remaining
                    t.reset_at = reset_at
                    t.last_checked = time.time()
                    break

    def check_rate_limits(self, api_base: str = "https://api.github.com"):
        """Proactively check rate limits for all tokens."""
        for t in self._tokens:
            if t.is_app_token:
                continue
            try:
                r = requests.get(
                    f"{api_base}/rate_limit",
                    headers={"Authorization": f"Bearer {t.token}"},
                    timeout=10,
                )
                if r.ok:
                    core = r.json().get("resources", {}).get("core", {})
                    t.remaining = core.get("remaining", 5000)
                    t.reset_at = core.get("reset", 0)
                    t.last_checked = time.time()
            except Exception:
                pass

    def _get_app_token(self) -> str:
        """Generate a GitHub App installation token via JWT."""
        if not self._app_creds:
            raise ValueError("No app credentials configured")

        # Check if we have a cached app token that's still valid
        for t in self._tokens:
            if t.is_app_token and time.time() < t.app_token_expiry - 60:
                return t.token

        try:
            import jwt
            from cryptography.hazmat.primitives import serialization

            with open(self._app_creds.private_key_path, "rb") as f:
                private_key = serialization.load_pem_private_key(f.read(), password=None)

            now = int(time.time())
            payload = {
                "iat": now - 60,
                "exp": now + (10 * 60),
                "iss": self._app_creds.app_id,
            }
            encoded_jwt = jwt.encode(payload, private_key, algorithm="RS256")

            r = requests.post(
                f"https://api.github.com/app/installations/"
                f"{self._app_creds.installation_id}/access_tokens",
                headers={
                    "Authorization": f"Bearer {encoded_jwt}",
                    "Accept": "application/vnd.github+json",
                },
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            token = data["token"]
            expires_at = data.get("expires_at", "")

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
        return len(self._tokens) + (1 if self._app_creds else 0)

    def summary(self) -> dict:
        return {
            "pat_count": len([t for t in self._tokens if not t.is_app_token]),
            "app_configured": self._app_creds is not None,
            "tokens": [
                {"remaining": t.remaining, "is_app": t.is_app_token}
                for t in self._tokens
            ],
        }
