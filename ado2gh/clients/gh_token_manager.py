"""GitHub multi-token load balancer with rate-limit awareness and GitHub App authentication."""
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
    """One GitHub credential in the rotation pool and its last known rate-limit state.

    Attributes:
        token: The credential value. It is never written to logs or messages.
        remaining: Requests left in the current rate-limit window, as last reported.
        reset_at: Unix timestamp at which GitHub resets the window.
        last_checked: Unix timestamp of the last rate-limit update.
        is_app_token: True when the credential is a short-lived App installation token.
        app_token_expiry: Unix timestamp at which an App installation token expires.
    """

    token: str
    remaining: int = 5000
    reset_at: float = 0.0
    last_checked: float = 0.0
    is_app_token: bool = False
    app_token_expiry: float = 0.0


@dataclass
class AppCredentials:
    """GitHub App identity used to mint installation tokens.

    Attributes:
        app_id: Numeric GitHub App identifier, as a string.
        installation_id: Installation identifier for the target organisation.
        private_key_path: Filesystem path to the App's PEM private key.
    """

    app_id: str
    installation_id: str
    private_key_path: str


class TokenManager:
    """Round-robin GitHub token manager with rate-limit awareness.

    Tokens are handed out in turn; one whose remaining quota is low is skipped until
    its window resets, and when every token is exhausted ``get_token`` sleeps until
    the soonest reset. A GitHub App may be configured instead of, or alongside,
    personal access tokens: installation tokens are minted on demand and cached until
    shortly before they expire.
    """

    def __init__(self) -> None:
        """Create an empty pool; the ``from_*`` constructors populate it."""
        self._tokens: list[TokenInfo] = []
        self._lock = threading.Lock()
        self._idx = 0
        self._app_creds: Optional[AppCredentials] = None

    @classmethod
    def from_env(cls, token_env_vars: list[str]) -> "TokenManager":
        """Build a pool from the tokens held in the named environment variables.

        Args:
            token_env_vars: Environment variable names to read, in rotation order.
                Unset or empty variables are skipped with a warning naming the variable.

        Returns:
            A manager holding one token per populated variable.

        Raises:
            ValueError: If none of the variables is set.
        """
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
        """Build a pool holding exactly one token.

        Args:
            token: The credential value.

        Returns:
            A manager that always returns that token.
        """
        mgr = cls()
        mgr._tokens.append(TokenInfo(token=token))
        return mgr

    @classmethod
    def from_json_config(cls, config_path: str,
                         token_key: str = "target") -> "TokenManager":
        """Build a pool from a JSON token configuration file.

        The file maps ``pat_token_envs`` and ``app_token_envs`` to per-key lists of
        environment variable names; the credentials themselves stay in the
        environment. Each ``app_token_envs`` entry is a triple naming the variables
        that hold the App id, the installation id and the private-key path.

        Args:
            config_path: Path to the JSON file.
            token_key: Which entry to read, for example ``"target"`` for the GitHub side.

        Returns:
            A manager holding every token found, plus App credentials when all three
            App variables are set.

        Raises:
            ValueError: If the file yields neither tokens nor App credentials for the key.
        """
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
                           private_key_path: str) -> None:
        """Enable GitHub App authentication for this pool.

        Args:
            app_id: Numeric GitHub App identifier, as a string.
            installation_id: Installation identifier for the target organisation.
            private_key_path: Filesystem path to the App's PEM private key.
        """
        self._app_creds = AppCredentials(app_id, installation_id, private_key_path)

    def get_token(self) -> str:
        """Return the next usable token, rotating round-robin and honouring rate limits.

        A token with more than 50 requests remaining is returned at once; one whose
        reset time has passed is treated as replenished. When every token is
        exhausted this call sleeps until the soonest reset and then returns that
        token. With no tokens in the pool but App credentials configured, an
        installation token is minted instead.

        Returns:
            The credential value to send as a bearer token.

        Raises:
            ValueError: If the pool has neither tokens nor App credentials.
        """
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

    def update_rate_limit(self, token: str, remaining: int, reset_at: float) -> None:
        """Record the rate-limit state GitHub reported for one token.

        Args:
            token: The credential the response was made with; unknown values are ignored.
            remaining: Value of the ``x-ratelimit-remaining`` response header.
            reset_at: Value of the ``x-ratelimit-reset`` header, a Unix timestamp.
        """
        with self._lock:
            for t in self._tokens:
                if t.token == token:
                    t.remaining = remaining
                    t.reset_at = reset_at
                    t.last_checked = time.time()
                    break

    def check_rate_limits(self, api_base: str = "https://api.github.com") -> None:
        """Refresh the rate-limit state of every personal access token from GitHub.

        App installation tokens are skipped. A failed request leaves that token's
        previous state untouched.

        Args:
            api_base: Base URL of the GitHub REST API.
        """
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
        """Mint, cache and return a GitHub App installation token.

        A cached token is reused until one minute before it expires. Minting signs a
        ten-minute JWT with the App's private key and exchanges it for an
        installation token, which replaces any earlier App token in the pool.

        Returns:
            The installation token value.

        Raises:
            ValueError: If no App credentials are configured.
            ImportError: If ``cryptography`` or ``PyJWT`` is not installed.
            requests.HTTPError: If GitHub rejects the token request.
        """
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
        """Number of credentials available: pooled tokens plus one for a configured App."""
        return len(self._tokens) + (1 if self._app_creds else 0)

    def get_token_status(self) -> dict:
        """Describe the pool for the ``token-status`` command.

        Returns:
            A mapping with ``pat_count`` (personal access tokens in the pool),
            ``app_configured`` (whether App credentials are set) and ``tokens``, a list
            with one entry per pooled token giving its ``remaining`` quota and whether
            it ``is_app``. Token values are never included.
        """
        return {
            "pat_count": len([t for t in self._tokens if not t.is_app_token]),
            "app_configured": self._app_creds is not None,
            "tokens": [
                {"remaining": t.remaining, "is_app": t.is_app_token}
                for t in self._tokens
            ],
        }
