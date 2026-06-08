"""ADO multi-PAT rotation — mirrors GitHub TokenManager pattern."""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass

log = logging.getLogger("ado2gh")


@dataclass
class ADOTokenInfo:
    pat: str
    last_used: float = 0.0


class ADOTokenManager:
    """Round-robin ADO PAT manager for inventory-scale workloads."""

    def __init__(self, pats: list[str] | None = None):
        self._tokens: list[ADOTokenInfo] = []
        self._lock = threading.Lock()
        self._idx = 0
        if pats:
            for p in pats:
                if p:
                    self._tokens.append(ADOTokenInfo(pat=p))

    @classmethod
    def from_env(cls, prefix: str = "ADO_PAT") -> ADOTokenManager:
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
        return cls([pat])

    def get_pat(self) -> str:
        with self._lock:
            if not self._tokens:
                raise ValueError("No ADO PATs configured")
            info = self._tokens[self._idx % len(self._tokens)]
            self._idx += 1
            info.last_used = time.time()
            return info.pat

    def get_token(self) -> str:
        """Alias for ADOClient compatibility."""
        return self.get_pat()

    @property
    def pat_count(self) -> int:
        return len(self._tokens)
