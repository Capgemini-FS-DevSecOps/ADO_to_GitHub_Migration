"""Environment-level proxy, custom CA, and model override settings."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _path() -> Path:
    """Return the default on-disk location of the connectivity profile.

    Returns:
        The connectivity profile JSON file inside the directory named by
        ``ADO2GH_DATA_DIR``, or inside the current directory when that variable
        is unset.
    """
    base = os.environ.get("ADO2GH_DATA_DIR", ".")
    return Path(base) / "connectivity_profile.json"


@dataclass
class ConnectivityProfile:
    """Environment connectivity settings applied to outbound calls.

    Holds the outbound proxy configuration, an optional custom CA bundle in PEM
    form for environments that terminate TLS internally, and whether operators
    may type a model id that is not in the provider catalogue. The proxy
    password and the CA bundle are the profile's two sensitive fields: they are
    stored in a separate ``_secrets`` object on disk and are never included in
    the public representation returned by :meth:`to_public`. The ``updated_at``
    and ``updated_by`` fields record the last save.
    """

    proxy_enabled: bool = False
    proxy_host: str = ""
    proxy_port: int = 8080
    proxy_username: str = ""
    proxy_password: str = ""
    custom_ca_pem: str = ""
    allow_custom_model_id: bool = False
    updated_at: str = ""
    updated_by: str = ""

    @property
    def custom_ca_configured(self) -> bool:
        """Report whether a custom CA bundle has been supplied.

        Returns:
            True when the profile carries a non-blank PEM bundle. Exposed so
            callers can show that a bundle is present without reading it.
        """
        return bool(self.custom_ca_pem.strip())

    def to_public(self) -> dict[str, Any]:
        """Render the profile for API responses, with secrets withheld.

        Returns:
            A mapping of the proxy toggle, host, port and username, the custom
            CA presence flag, the custom-model-id toggle, and the last update
            timestamp and actor. The proxy password is replaced by a fixed mask
            when one is set and is an empty string otherwise, and the CA bundle
            is reduced to the boolean ``custom_ca_configured`` — neither secret
            is ever returned.
        """
        return {
            "proxy_enabled": self.proxy_enabled,
            "proxy_host": self.proxy_host,
            "proxy_port": self.proxy_port,
            "proxy_username": self.proxy_username,
            "proxy_password": "***" if self.proxy_password else "",
            "custom_ca_configured": self.custom_ca_configured,
            "allow_custom_model_id": self.allow_custom_model_id,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


class ConnectivityStore:
    """Load and save environment connectivity profile with secret sidecar."""

    def __init__(self, path: Path | None = None) -> None:
        """Bind the store to a profile file.

        Args:
            path: Profile file to read and write. Defaults to the location
                derived from ``ADO2GH_DATA_DIR``.
        """
        self.path = path or _path()

    def load(self) -> ConnectivityProfile:
        """Read the stored connectivity profile.

        Returns:
            The persisted profile, with the proxy password and custom CA bundle
            restored from the secret sidecar so callers get usable values. A
            profile of defaults — proxy disabled, no CA bundle — when no file
            has been written yet.

        Raises:
            json.JSONDecodeError: The profile file exists but is not valid JSON.
        """
        if not self.path.exists():
            return ConnectivityProfile()
        data = json.loads(self.path.read_text(encoding="utf-8"))
        secrets = data.get("_secrets", {})
        return ConnectivityProfile(
            proxy_enabled=bool(data.get("proxy_enabled", False)),
            proxy_host=data.get("proxy_host", ""),
            proxy_port=int(data.get("proxy_port", 8080)),
            proxy_username=data.get("proxy_username", ""),
            proxy_password=secrets.get("proxy_password", ""),
            custom_ca_pem=secrets.get("custom_ca_pem", ""),
            allow_custom_model_id=bool(data.get("allow_custom_model_id", False)),
            updated_at=data.get("updated_at", ""),
            updated_by=data.get("updated_by", ""),
        )

    def save(self, profile: ConnectivityProfile, *, actor: str = "system") -> ConnectivityProfile:
        """Persist profile; invalidate model validations when connectivity-sensitive fields change."""
        previous = self.load()
        profile.updated_at = datetime.now(timezone.utc).isoformat()
        profile.updated_by = actor
        self.path.parent.mkdir(parents=True, exist_ok=True)
        public = profile.to_public()
        payload = {k: v for k, v in public.items() if k not in ("proxy_password", "custom_ca_configured")}
        secrets = {
            "proxy_password": profile.proxy_password,
            "custom_ca_pem": profile.custom_ca_pem,
        }
        payload["_secrets"] = secrets
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        if _connectivity_sensitive_changed(previous, profile):
            from ado2gh.api.llm.llm_model_store import invalidate_all_model_validations

            invalidate_all_model_validations()
        return profile

    def update(self, data: dict[str, Any], *, actor: str = "system") -> ConnectivityProfile:
        """Apply partial update; retain secrets when masked or omitted."""
        current = self.load()
        if "proxy_enabled" in data:
            current.proxy_enabled = bool(data["proxy_enabled"])
        if "proxy_host" in data:
            current.proxy_host = str(data["proxy_host"] or "")
        if "proxy_port" in data:
            current.proxy_port = int(data["proxy_port"])
        if "proxy_username" in data:
            current.proxy_username = str(data["proxy_username"] or "")
        password = data.get("proxy_password")
        if password is not None and password != "***":
            current.proxy_password = str(password)
        ca = data.get("custom_ca_pem")
        if ca is not None:
            if ca == "***":
                pass
            else:
                current.custom_ca_pem = str(ca)
        if "allow_custom_model_id" in data:
            current.allow_custom_model_id = bool(data["allow_custom_model_id"])
        return self.save(current, actor=actor)


def _connectivity_sensitive_changed(
    previous: ConnectivityProfile,
    updated: ConnectivityProfile,
) -> bool:
    """Report whether a save changed anything that could affect model reachability.

    Args:
        previous: The profile as it was before the save.
        updated: The profile about to be written.

    Returns:
        True when any proxy setting, the custom CA bundle, or the custom-model-id
        toggle differs between the two. A True result makes the caller
        invalidate every cached model validation, because a validation recorded
        under the old connectivity settings no longer proves anything.
    """
    return (
        previous.proxy_enabled != updated.proxy_enabled
        or previous.proxy_host != updated.proxy_host
        or previous.proxy_port != updated.proxy_port
        or previous.proxy_username != updated.proxy_username
        or previous.proxy_password != updated.proxy_password
        or previous.custom_ca_pem != updated.custom_ca_pem
        or previous.allow_custom_model_id != updated.allow_custom_model_id
    )


def get_connectivity_profile() -> ConnectivityProfile:
    """Return the current connectivity profile from the default store."""
    return ConnectivityStore().load()
