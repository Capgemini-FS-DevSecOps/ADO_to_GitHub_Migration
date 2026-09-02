"""Local agent profile loader from YAML and environment.

Moved from ado2gh/agents/local/profiles.py during spec 012 cleanup.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

_DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "local-profiles.yaml"


@dataclass
class LocalAgentProfile:
    """Named runtime profile for local IDE agent development."""

    profile_id: str
    accelerator_url: str
    agent_url: str
    storage_backend: str
    sqlite_path: str
    auth_enabled: bool
    llm_provider: str
    lightweight_mode: bool
    dry_run_default: bool
    redis_url: str = ""

    def validate(self) -> None:
        """Raise ValueError if profile constraints are violated."""
        if self.lightweight_mode and self.redis_url:
            raise ValueError(f"lightweight profile {self.profile_id} must not set redis_url")
        if self.profile_id == "lightweight" and not self.lightweight_mode:
            raise ValueError("profile_id lightweight requires lightweight_mode=true")
        if self.profile_id == "prod-like" and not self.auth_enabled:
            raise ValueError("profile_id prod-like requires auth_enabled=true")


def _load_yaml(path: Path) -> dict:
    if not path.is_file():
        return {"profiles": {}}
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data if isinstance(data, dict) else {"profiles": {}}


def _from_dict(profile_id: str, raw: dict) -> LocalAgentProfile:
    return LocalAgentProfile(
        profile_id=profile_id,
        accelerator_url=str(raw.get("accelerator_url", "http://localhost:8080")),
        agent_url=str(raw.get("agent_url", "http://localhost:8090")),
        storage_backend=str(raw.get("storage_backend", "sqlite")),
        sqlite_path=str(raw.get("sqlite_path", "./migration_state.db")),
        auth_enabled=bool(raw.get("auth_enabled", False)),
        llm_provider=str(raw.get("llm_provider", "stub")),
        lightweight_mode=bool(raw.get("lightweight_mode", False)),
        dry_run_default=bool(raw.get("dry_run_default", True)),
        redis_url=str(raw.get("redis_url", "") or ""),
    )


def _apply_env(profile: LocalAgentProfile) -> LocalAgentProfile:
    """Overlay environment variables onto profile fields."""
    accel = os.environ.get("ACCELERATOR_URL")
    if accel:
        profile.accelerator_url = accel
    llm = os.environ.get("LLM_PROVIDER") or os.environ.get("ADO2GH_LLM_BACKEND")
    if llm:
        profile.llm_provider = llm
    if os.environ.get("ADO2GH_AUTH_ENABLED", "").lower() in ("1", "true", "yes"):
        profile.auth_enabled = True
    if os.environ.get("ADO2GH_LIGHTWEIGHT_MODE", "").lower() in ("1", "true", "yes"):
        profile.lightweight_mode = True
    sqlite = os.environ.get("ADO2GH_SQLITE_PATH")
    if sqlite:
        profile.sqlite_path = sqlite
    backend = os.environ.get("ADO2GH_STORAGE_BACKEND")
    if backend:
        profile.storage_backend = backend
    redis = os.environ.get("REDIS_URL")
    if redis:
        profile.redis_url = redis
    return profile


def list_profile_ids(config_path: Optional[Path] = None) -> list[str]:
    """List profile ids defined in config."""
    path = config_path or _DEFAULT_CONFIG
    data = _load_yaml(path)
    profiles = data.get("profiles", {})
    return sorted(profiles.keys())


def get_profile(
    profile_id: Optional[str] = None,
    config_path: Optional[Path] = None,
) -> LocalAgentProfile:
    """Load profile by id; defaults to lightweight.

    Applies environment overrides after YAML load.
    """
    path = config_path or _DEFAULT_CONFIG
    data = _load_yaml(path)
    profiles: Dict[str, Any] = data.get("profiles", {})
    pid = profile_id or os.environ.get("ADO2GH_LOCAL_PROFILE", "lightweight")
    if pid not in profiles:
        raise KeyError(f"Unknown local profile: {pid}")
    profile = _from_dict(pid, profiles[pid])
    profile = _apply_env(profile)
    profile.validate()
    return profile


def capability_matrix(profile: LocalAgentProfile) -> dict:
    """Degraded-mode matrix for health endpoint."""
    enqueue_mode = "inline_stub" if profile.lightweight_mode else "async_worker"
    return {
        "enqueue_job": enqueue_mode,
        "llm": profile.llm_provider,
        "auth": "required" if profile.auth_enabled else "disabled",
        "redis": "omitted" if profile.lightweight_mode else "required",
    }
