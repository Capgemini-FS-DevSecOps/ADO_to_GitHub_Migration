"""Backward-compatible re-exports for the state persistence layer.

The concrete SQLite implementation now lives in ``ado2gh.state.sqlite_db``.
This module re-exports ``StateDB`` as an alias for ``SQLiteStateDB`` so
existing imports continue to work (per FR-013).
"""
from __future__ import annotations

from ado2gh.state.sqlite_db import SQLiteStateDB as StateDB

__all__ = ["StateDB"]
