"""One reader for settings shared by both FastAPI services.

Both ``services/agent/main.py`` and ``services/accelerator_api/main.py`` read
``CORS_ORIGINS`` and split it on commas to build their CORS middleware's
allowed-origins list. Centralising the split rule here keeps the two services
from drifting apart if that parsing ever needs to change, without forcing
either service to give up the default it already ships with.
"""
from __future__ import annotations

import os

DEFAULT_CORS_ORIGIN = "http://localhost:3000"
"""Fallback CORS origin used when a caller does not supply its own default."""


def cors_origins(default: str = DEFAULT_CORS_ORIGIN) -> list[str]:
    """Read and split the CORS_ORIGINS environment variable.

    Args:
        default: Value to use when CORS_ORIGINS is unset. Callers keep their
            own historical default (the agent service defaults to
            ``DEFAULT_CORS_ORIGIN``; the accelerator defaults to ``"*"``) by
            passing it here, so behaviour is unchanged for each service.

    Returns:
        The comma-separated origins from CORS_ORIGINS, or ``[default]`` when
        the environment variable is unset.
    """
    return os.environ.get("CORS_ORIGINS", default).split(",")
