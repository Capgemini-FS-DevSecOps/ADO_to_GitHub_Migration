"""Azure DevOps multi-PAT rotation, mirroring the GitHub ``TokenManager`` pattern."""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass

log = logging.getLogger("ado2gh")


@dataclass
class ADOTokenInfo:
    """One Azure DevOps personal access token in the rotation pool.

    Attributes:
        pat: The credential value. It is never written to logs or messages.
        last_used: Unix timestamp of the last time the token was handed out.
    """

    pat: str
    last_used: float = 0.0


class ADOTokenManager:
    """Round-robin ADO PAT manager for inventory-scale workloads."""

    def __init__(self, pats: list[str] | None = None) -> None:
        """Create a pool from the given tokens, skipping empty values.

        Args:
            pats: Personal access tokens in rotation order; may be empty or omitted.
        """
        self._tokens: list[ADOTokenInfo] = []
        self._lock = threading.Lock()
        self._idx = 0
        if pats:
            for p in pats:
                if p:
                    self._tokens.append(ADOTokenInfo(pat=p))

    @classmethod
    def from_env(cls, prefix: str = "ADO_PAT") -> ADOTokenManager:
        """Build a pool from ``<prefix>`` and ``<prefix>_1`` through ``<prefix>_19``.

        Args:
            prefix: Environment variable name, read bare and with each numeric suffix.

        Returns:
            A manager holding every token found, bare variable first.

        Raises:
            ValueError: If no variable with that prefix is set.
        """
        pats: list[str] = []
        single = os.environ.get(prefix, "")
        if single:
            pats.append(single)
        for i in range(1, 20):
            val = os.environ.get(f"{prefix}_{i}", "")
            if val:
                pats.append(val)
        if not pats:
            raise ValueError(f"No ADO PATs found (set {prefix} or {prefix}_1, ...)")
        log.info("Loaded %d ADO PAT(s) for rotation", len(pats))
        return cls(pats)

    @classmethod
    def from_single(cls, pat: str) -> ADOTokenManager:
        """Build a pool holding exactly one token.

        Args:
            pat: The credential value.

        Returns:
            A manager that always returns that token.
        """
        return cls([pat])

    def get_pat(self) -> str:
        """Return the next token in round-robin order and record when it was used.

        Returns:
            The credential value to send as the HTTP basic-auth password.

        Raises:
            ValueError: If the pool is empty.
        """
        with self._lock:
            if not self._tokens:
                raise ValueError("No ADO PATs configured")
            info = self._tokens[self._idx % len(self._tokens)]
            self._idx += 1
            info.last_used = time.time()
            return info.pat

    def get_token(self) -> str:
        """Return ``get_pat()`` under the name ``ADOClient`` calls on any token pool."""
        return self.get_pat()

    @property
    def pat_count(self) -> int:
        """Number of tokens in the pool."""
        return len(self._tokens)
