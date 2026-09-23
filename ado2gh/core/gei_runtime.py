"""Runtime environment for gh-ado2gh / .NET GitHub Enterprise Importer (GEI) extensions in Linux containers."""
from __future__ import annotations

import os

_DOTNET_INVARIANT = "DOTNET_SYSTEM_GLOBALIZATION_INVARIANT"


def ensure_gei_dotnet_env() -> None:
    """gh-ado2gh bundles .NET; without ICU libs, invariant mode is required."""
    os.environ[_DOTNET_INVARIANT] = "1"


def build_gei_subprocess_env(**extra: str) -> dict[str, str]:
    """Build the environment for subprocess calls to gh ado2gh / gh gei.

    Args:
        **extra: Additional environment variables to overlay on the current
            process environment. Callers use this to supply the credential
            variables the extensions expect, such as `ADO_PAT` and `GH_PAT`.

    Returns:
        A copy of the current process environment with `extra` applied and
        .NET globalization forced into invariant mode, ready to hand to
        `subprocess`. The caller's own environment is left untouched apart
        from the invariant-mode flag set by `ensure_gei_dotnet_env`.
    """
    ensure_gei_dotnet_env()
    env = {**os.environ, **extra}
    env[_DOTNET_INVARIANT] = "1"
    return env
