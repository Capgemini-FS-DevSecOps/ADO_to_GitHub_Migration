"""Admin-managed cloud LLM credential sources (presence, approval, no secrets persisted)."""
from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ado2gh.api.credentials.cloud_credential_detector import PROVIDER_SERVICES, scan_all_presence

PROVIDERS = ("aws", "foundry", "gcp")
STATUSES = ("pending", "approved", "rejected", "revoked")
COMPLETENESS = ("complete", "incomplete", "absent")

FORBIDDEN_PATCH_KEY = re.compile(
    r"(secret|password|token|api_key|client_secret|private_key)",
    re.IGNORECASE,
)
FORBIDDEN_PATCH_EXACT = {
    "AWS_SECRET_ACCESS_KEY",
    "AZURE_CLIENT_SECRET",
    "GOOGLE_APPLICATION_CREDENTIALS",
}

_scan_lock = threading.Lock()
_scan_inflight = False


class CloudCredentialsError(Exception):
    """Raised when ambient credentials are unavailable."""


def _path() -> Path:
    base = os.environ.get("ADO2GH_DATA_DIR", ".")
    return Path(base) / "cloud_credentials.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _snapshot(source: "CloudCredentialSource") -> dict[str, Any]:
    return {
        "primary_method": source.primary_method,
        "region": source.region or "",
        "project": source.project or "",
        "endpoint": source.endpoint or "",
        "completeness": source.completeness,
        "admin_supplied_fields": dict(source.admin_supplied_fields),
    }


@dataclass
class CloudCredentialSource:
    provider: str
    service: str = ""
    status: str = "pending"
    completeness: str = "absent"
    primary_method: str = ""
    alternate_methods: list[str] = field(default_factory=list)
    region: str | None = None
    project: str | None = None
    endpoint: str | None = None
    missing_fields: list[str] = field(default_factory=list)
    admin_supplied_fields: dict[str, str] = field(default_factory=dict)
    last_scan_at: str = ""
    scan_snapshot: dict[str, Any] = field(default_factory=dict)
    approved_at: str | None = None
    approved_by: str | None = None
    rejected_at: str | None = None
    rejected_by: str | None = None
    rejection_reason: str | None = None
    last_probe_at: str | None = None
    last_probe_status: str | None = None
    last_probe_category: str | None = None
    last_probe_message: str | None = None

    def to_public(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "service": self.service or PROVIDER_SERVICES.get(self.provider, ""),
            "status": self.status,
            "completeness": self.completeness,
            "primary_method": self.primary_method or None,
            "alternate_methods": list(self.alternate_methods),
            "region": self.region,
            "project": self.project,
            "endpoint": self.endpoint,
            "missing_fields": list(self.missing_fields),
            "admin_supplied_fields": dict(self.admin_supplied_fields),
            "last_scan_at": self.last_scan_at or None,
            "last_probe_status": self.last_probe_status,
            "last_probe_category": self.last_probe_category,
            "last_probe_message": self.last_probe_message,
            "approved_at": self.approved_at,
            "approved_by": self.approved_by,
            "rejected_at": self.rejected_at,
            "rejected_by": self.rejected_by,
            "rejection_reason": self.rejection_reason,
        }


def _parse_source(raw: dict[str, Any]) -> CloudCredentialSource:
    return CloudCredentialSource(
        provider=raw["provider"],
        service=raw.get("service", PROVIDER_SERVICES.get(raw["provider"], "")),
        status=raw.get("status", "pending"),
        completeness=raw.get("completeness", "absent"),
        primary_method=raw.get("primary_method", ""),
        alternate_methods=list(raw.get("alternate_methods", [])),
        region=raw.get("region"),
        project=raw.get("project"),
        endpoint=raw.get("endpoint"),
        missing_fields=list(raw.get("missing_fields", [])),
        admin_supplied_fields=dict(raw.get("admin_supplied_fields", {})),
        last_scan_at=raw.get("last_scan_at", ""),
        scan_snapshot=dict(raw.get("scan_snapshot", {})),
        approved_at=raw.get("approved_at"),
        approved_by=raw.get("approved_by"),
        rejected_at=raw.get("rejected_at"),
        rejected_by=raw.get("rejected_by"),
        rejection_reason=raw.get("rejection_reason"),
        last_probe_at=raw.get("last_probe_at"),
        last_probe_status=raw.get("last_probe_status"),
        last_probe_category=raw.get("last_probe_category"),
        last_probe_message=raw.get("last_probe_message"),
    )


def _merge_detection(
    existing: CloudCredentialSource | None,
    detection: dict[str, Any],
) -> CloudCredentialSource:
    provider = detection["provider"]
    admin_fields = existing.admin_supplied_fields if existing else {}
    region = detection.get("region") or admin_fields.get("region")
    project = detection.get("project") or admin_fields.get("project")
    endpoint = detection.get("endpoint") or admin_fields.get("endpoint")
    if admin_fields.get("region"):
        region = admin_fields["region"]
    if admin_fields.get("project"):
        project = admin_fields["project"]
    if admin_fields.get("endpoint"):
        endpoint = admin_fields["endpoint"]
    tenant_id = admin_fields.get("tenant_id")
    merged_admin = dict(admin_fields)
    completeness = detection.get("completeness", "absent")
    missing = list(detection.get("missing_fields", []))
    if completeness == "incomplete" and tenant_id and "tenant_id" in missing:
        missing = [m for m in missing if m != "AZURE_TENANT_ID"]
        if not missing:
            completeness = "complete"
    source = CloudCredentialSource(
        provider=provider,
        service=PROVIDER_SERVICES.get(provider, ""),
        status=existing.status if existing else "pending",
        completeness=completeness,
        primary_method=detection.get("primary_method", ""),
        alternate_methods=list(detection.get("alternate_methods", [])),
        region=region,
        project=project,
        endpoint=endpoint,
        missing_fields=missing,
        admin_supplied_fields=merged_admin,
        last_scan_at=_now_iso(),
        approved_at=existing.approved_at if existing else None,
        approved_by=existing.approved_by if existing else None,
        rejected_at=existing.rejected_at if existing else None,
        rejected_by=existing.rejected_by if existing else None,
        rejection_reason=existing.rejection_reason if existing else None,
        last_probe_at=existing.last_probe_at if existing else None,
        last_probe_status=existing.last_probe_status if existing else None,
        last_probe_category=existing.last_probe_category if existing else None,
        last_probe_message=existing.last_probe_message if existing else None,
    )
    source.scan_snapshot = _snapshot(source)
    if completeness == "absent":
        source.status = "pending"
        source.completeness = "absent"
    elif existing and existing.status == "approved":
        if source.scan_snapshot != existing.scan_snapshot:
            source.status = "pending"
            _invalidate_ambient_for_provider(provider)
    elif existing and existing.status in ("rejected", "revoked"):
        if completeness == "complete":
            source.status = existing.status
        else:
            source.status = "pending"
    elif completeness == "complete" and source.status not in ("approved", "rejected", "revoked"):
        source.status = "pending"
    return source


def _invalidate_ambient_for_provider(provider: str) -> None:
    from ado2gh.api.llm.llm_model_store import invalidate_ambient_models

    invalidate_ambient_models(provider)


class CloudCredentialsStore:
    def __init__(self, path: Path | None = None):
        self.path = path or _path()

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"sources": [], "last_full_scan_at": None}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def list_sources(self) -> list[CloudCredentialSource]:
        data = self.load()
        return [_parse_source(raw) for raw in data.get("sources", [])]

    def get_source(self, provider: str) -> CloudCredentialSource | None:
        return next((s for s in self.list_sources() if s.provider == provider), None)

    def _write_sources(self, sources: list[CloudCredentialSource], *, last_scan: str | None = None) -> None:
        data = self.load()
        data["sources"] = [
            {**s.to_public(), "scan_snapshot": s.scan_snapshot}
            for s in sources
        ]
        if last_scan:
            data["last_full_scan_at"] = last_scan
        self.save(data)

    def apply_scan(
        self,
        detections: list[dict[str, Any]] | None = None,
        *,
        rescan_pending_audit: Callable[[str, list[str]], None] | None = None,
    ) -> list[CloudCredentialSource]:
        detections = detections or scan_all_presence()
        existing_map = {s.provider: s for s in self.list_sources()}
        updated: list[CloudCredentialSource] = []
        for det in detections:
            prev = existing_map.get(det["provider"])
            if prev and prev.status == "approved":
                old_snap = prev.scan_snapshot or _snapshot(prev)
            source = _merge_detection(prev, det)
            if prev and prev.status == "approved" and source.status == "pending":
                changed = [
                    k for k in source.scan_snapshot
                    if source.scan_snapshot.get(k) != old_snap.get(k)
                ]
                if rescan_pending_audit and changed:
                    rescan_pending_audit(source.provider, changed)
            updated.append(source)
        self._write_sources(updated, last_scan=_now_iso())
        return updated

    def scan(self, *, rescan_pending_audit: Callable[[str, list[str]], None] | None = None) -> list[CloudCredentialSource]:
        global _scan_inflight
        if not _scan_lock.acquire(blocking=False):
            raise ScanInFlightError("Cloud credential scan already in progress")
        try:
            if _scan_inflight:
                raise ScanInFlightError("Cloud credential scan already in progress")
            _scan_inflight = True
            return self.apply_scan(rescan_pending_audit=rescan_pending_audit)
        finally:
            _scan_inflight = False
            _scan_lock.release()

    def patch_provider(self, provider: str, body: dict[str, Any]) -> CloudCredentialSource:
        if provider not in PROVIDERS:
            raise KeyError(provider)
        for key in body:
            if key in FORBIDDEN_PATCH_EXACT or FORBIDDEN_PATCH_KEY.search(key):
                raise ValueError("invalid_field")
        allowed = {"region", "endpoint", "project", "tenant_id"}
        if any(k not in allowed for k in body):
            bad = [k for k in body if k not in allowed]
            raise ValueError(f"invalid_field:{','.join(bad)}")
        sources = self.list_sources()
        existing = next((s for s in sources if s.provider == provider), None)
        detections = scan_all_presence()
        det = next((d for d in detections if d["provider"] == provider), None)
        if not det:
            raise KeyError(provider)
        admin = dict(existing.admin_supplied_fields if existing else {})
        for key, value in body.items():
            admin[key] = str(value or "")
        if existing:
            existing.admin_supplied_fields = admin
        merged_det = dict(det)
        for key in ("region", "endpoint", "project"):
            if admin.get(key):
                merged_det[key] = admin[key]
        source = _merge_detection(existing, merged_det)
        source.admin_supplied_fields = admin
        if source.completeness == "incomplete":
            raise ValueError("incomplete_configuration")
        if existing:
            idx = next(i for i, s in enumerate(sources) if s.provider == provider)
            sources[idx] = source
        else:
            sources.append(source)
        self._write_sources(sources)
        return source

    def approve(
        self,
        provider: str,
        *,
        actor: str,
        probe_fn: Callable[[str, CloudCredentialSource], dict[str, Any]],
    ) -> CloudCredentialSource:
        source = self.get_source(provider)
        if not source:
            raise KeyError(provider)
        if source.completeness != "complete":
            raise ValueError("incomplete_configuration")
        probe = probe_fn(provider, source)
        now = _now_iso()
        source.last_probe_at = now
        source.last_probe_status = probe.get("status")
        source.last_probe_category = probe.get("category")
        source.last_probe_message = probe.get("message")
        if probe.get("status") != "passed":
            sources = self.list_sources()
            idx = next(i for i, s in enumerate(sources) if s.provider == provider)
            sources[idx] = source
            self._write_sources(sources)
            return source
        source.status = "approved"
        source.approved_at = now
        source.approved_by = actor
        source.rejected_at = None
        source.rejected_by = None
        source.rejection_reason = None
        sources = self.list_sources()
        idx = next(i for i, s in enumerate(sources) if s.provider == provider)
        sources[idx] = source
        self._write_sources(sources)
        return source

    def reject(self, provider: str, *, actor: str, reason: str = "") -> CloudCredentialSource:
        source = self.get_source(provider)
        if not source:
            raise KeyError(provider)
        now = _now_iso()
        source.status = "rejected"
        source.rejected_at = now
        source.rejected_by = actor
        source.rejection_reason = reason or None
        source.approved_at = None
        source.approved_by = None
        _invalidate_ambient_for_provider(provider)
        sources = self.list_sources()
        idx = next(i for i, s in enumerate(sources) if s.provider == provider)
        sources[idx] = source
        self._write_sources(sources)
        return source

    def revoke(self, provider: str, *, actor: str) -> CloudCredentialSource:
        source = self.get_source(provider)
        if not source:
            raise KeyError(provider)
        source.status = "revoked"
        source.approved_at = None
        source.approved_by = None
        _invalidate_ambient_for_provider(provider)
        sources = self.list_sources()
        idx = next(i for i, s in enumerate(sources) if s.provider == provider)
        sources[idx] = source
        self._write_sources(sources)
        return source

    def is_approved(self, provider: str) -> bool:
        source = self.get_source(provider)
        return bool(source and source.status == "approved")

    def to_list_response(self) -> dict[str, Any]:
        from ado2gh.api.llm.platform_managed_model import platform_model_payload

        data = self.load()
        sources = self.list_sources()
        payload = platform_model_payload()
        return {
            "last_full_scan_at": data.get("last_full_scan_at"),
            "sources": [s.to_public() for s in sources],
            "platform_model": payload,
        }


class ScanInFlightError(Exception):
    """Raised when a concurrent scan is attempted."""


def get_ambient_credentials(provider: str) -> CloudCredentialSource:
    store = CloudCredentialsStore()
    source = store.get_source(provider)
    if not source or source.status != "approved":
        raise CloudCredentialsError(f"Cloud credentials for {provider} are not approved")
    return source
