"""Runtime environment for gh-ado2gh / .NET GEI extensions in Linux containers."""
from __future__ import annotations

import os

_DOTNET_INVARIANT = "DOTNET_SYSTEM_GLOBALIZATION_INVARIANT"


def ensure_gei_dotnet_env() -> None:
    """gh-ado2gh bundles .NET; without ICU libs, invariant mode is required."""
    os.environ[_DOTNET_INVARIANT] = "1"


def gei_subprocess_env(**extra: str) -> dict[str, str]:
    """Environment dict for subprocess calls to gh ado2gh / gh gei."""
    ensure_gei_dotnet_env()
    env = {**os.environ, **extra}
    env[_DOTNET_INVARIANT] = "1"
    return env
