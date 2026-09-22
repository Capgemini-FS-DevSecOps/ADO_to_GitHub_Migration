"""Admin-managed cloud language model credential sources (presence, approval, no secrets persisted)."""
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
    """Return the default on-disk location of the credential source registry.

    Returns:
        The credential registry JSON file inside the directory named by
        ``ADO2GH_DATA_DIR``, or inside the current directory when that variable
        is unset. The file records approval decisions and detection metadata
        only; no credential value is ever written to it.
    """
    base = os.environ.get("ADO2GH_DATA_DIR", ".")
    return Path(base) / "cloud_credentials.json"


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string for stored timestamps."""
    return datetime.now(timezone.utc).isoformat()


def _snapshot(source: "CloudCredentialSource") -> dict[str, Any]:
    """Capture the fields whose change should retract an existing approval.

    Args:
        source: The credential source to snapshot.

    Returns:
        A mapping of the source's primary authentication method, region,
        project, endpoint, completeness and admin-supplied fields, with absent
        values normalised to empty strings so two snapshots compare cleanly.
        A later scan diffs against this to decide whether the environment has
        changed enough that a previously approved source must go back to
        pending. Contains configuration only, never credential values.
    """
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
    """One cloud provider's ambient credentials as detected and adjudicated.

    Describes the shape of what was found on the host — which authentication
    method is in play, which region, project or endpoint it targets, which
    settings are still missing, and which of those an administrator supplied by
    hand — together with the approval decision and the outcome of the last live
    probe. It deliberately holds no credential value: detection records that a
    method is available, never the material behind it, so this record is safe to
    persist and to return from the API.
    """

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
        """Render the source for API responses.

        Returns:
            A mapping of the provider key and its service name, the approval
            ``status`` and detection ``completeness``, the primary and alternate
            authentication methods, the region, project and endpoint, the
            fields still missing and those an administrator supplied, the last
            scan time, the last probe's status, category and message, and the
            approval or rejection actor, timestamp and reason. Every value is
            configuration or an audit fact; no credential value is included,
            because none is held.
        """
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
    """Registry of cloud credential sources and their approval decisions.

    Persists what a scan detected about each provider and whether an
    administrator has approved it for use, so that an ambient credential can
    never back a model until someone has signed off on it. Approval state is
    the only gate: the store records and adjudicates credential sources, it
    never reads or holds the credential values themselves.
    """

    def __init__(self, path: Path | None = None) -> None:
        """Bind the store to a registry file.

        Args:
            path: Registry file to read and write. When omitted the location is
                derived from ``ADO2GH_DATA_DIR`` on every access, not here —
                the route layer keeps one store for the process lifetime
                (``services/accelerator_api/routes/_shared.py``), so resolving
                the directory in the constructor froze it at import time and
                every later change to the variable was ignored.
        """
        self._path = path

    @property
    def path(self) -> Path:
        """Registry file this store reads and writes, resolved on every access.

        Returns:
            The explicit path this store was constructed with, or the current
            location derived from ``ADO2GH_DATA_DIR``.
        """
        return self._path or _path()

    def load(self) -> dict[str, Any]:
        """Read the raw registry document.

        Returns:
            The registry as stored: a ``sources`` list of per-provider records
            and ``last_full_scan_at``, the time of the last complete scan. An
            empty registry with a null scan time when no file exists yet.

        Raises:
            json.JSONDecodeError: The registry file exists but is not valid JSON.
        """
        if not self.path.exists():
            return {"sources": [], "last_full_scan_at": None}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, data: dict[str, Any]) -> None:
        """Write the registry document, creating its directory if needed.

        Args:
            data: The full registry document to persist, replacing any existing
                content. Callers are responsible for passing records that carry
                no credential values.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def list_sources(self) -> list[CloudCredentialSource]:
        """List every credential source in the registry.

        Returns:
            One parsed source per stored record, in registry order, each with
            its detection metadata and approval state. Empty when nothing has
            been scanned yet.
        """
        data = self.load()
        return [_parse_source(raw) for raw in data.get("sources", [])]

    def get_source(self, provider: str) -> CloudCredentialSource | None:
        """Look up one provider's credential source.

        Args:
            provider: Provider key to look up.

        Returns:
            The stored source for that provider, or None when the registry has
            no record for it.
        """
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
        """Merge detection results into the registry and persist them.

        An approved source whose environment has materially changed is demoted
        back to pending, so that an approval never silently carries over to a
        different credential configuration than the one it was granted for.

        Args:
            detections: Detection records to apply. A fresh presence scan is run
                when omitted.
            rescan_pending_audit: Called as ``(provider, changed_fields)`` for
                each approved source this call demotes to pending, so the caller
                can record which settings changed. Not called when nothing was
                demoted.

        Returns:
            The merged sources, one per detection, carrying their new
            completeness and approval status. The registry's last-scan
            timestamp is updated as a side effect.
        """
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
        """Run a presence scan and apply it, refusing to run two at once.

        Args:
            rescan_pending_audit: Passed through to ``apply_scan``; called for
                each approved source the scan demotes to pending.

        Returns:
            The merged sources produced by the scan, as ``apply_scan`` returns
            them.

        Raises:
            ScanInFlightError: Another scan is already running. The caller
                should surface this rather than retry, since the in-flight scan
                will produce the same result.
        """
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
        """Supply the non-secret settings a detection could not find on its own.

        Lets an administrator fill in the routing details — region, endpoint,
        project, tenant — that complete an otherwise incomplete source. Only
        those four keys are accepted: anything that names a secret, and anything
        outside the allowed set, is rejected before it can reach the registry,
        which is what keeps credential values out of this path.

        Args:
            provider: Provider key to patch.
            body: Values to record, keyed by ``region``, ``endpoint``,
                ``project`` or ``tenant_id``.

        Returns:
            The updated source, re-merged against a fresh detection so its
            completeness reflects the new settings.

        Raises:
            KeyError: The provider is not supported, or a scan returned no
                detection for it.
            ValueError: The body names a secret-bearing or unrecognised key, or
                the source is still incomplete once the values are applied.
        """
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
        """Approve a credential source for use, but only if it probes clean.

        Approval is not granted on the administrator's say-so alone: the source
        must first be complete, and the supplied probe must succeed against the
        live provider. A failed probe leaves the source unapproved and records
        why, so an unusable credential cannot be signed off by mistake.

        Args:
            provider: Provider key to approve.
            actor: Username of the approving administrator, recorded on the
                source as the approver.
            probe_fn: Called as ``(provider, source)`` to verify the credentials
                live. Must return a probe result mapping with a ``status`` and,
                on failure, a ``category`` and ``message``.

        Returns:
            The source with the probe outcome recorded. Its status is
            ``approved`` with the approver and timestamp set, and any earlier
            rejection cleared, when the probe passed; otherwise the status is
            left as it was and only the probe fields are updated.

        Raises:
            KeyError: The registry has no source for that provider.
            ValueError: The source is not complete enough to approve.
        """
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
        """Refuse a credential source, barring it from backing any model.

        Args:
            provider: Provider key to reject.
            actor: Username of the rejecting administrator, recorded on the
                source.
            reason: Optional explanation stored alongside the decision.

        Returns:
            The source with status ``rejected``, the rejecting actor, timestamp
            and reason recorded, and any earlier approval cleared. Every model
            using this provider's ambient credentials is invalidated as a side
            effect, so nothing keeps running on a rejected source.

        Raises:
            KeyError: The registry has no source for that provider.
        """
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

    def revoke(self, provider: str) -> CloudCredentialSource:
        """Withdraw a previously granted approval for a credential source.

        Unlike ``approve`` and ``reject``, this takes no actor: the record has
        no revoker field to hold one. Who revoked is captured by the audit event
        the calling route writes.

        Args:
            provider: Provider key to revoke.

        Returns:
            The source with status ``revoked`` and the earlier approval actor
            and timestamp cleared. Every model using this provider's ambient
            credentials is invalidated as a side effect, so nothing keeps
            running on a revoked source.

        Raises:
            KeyError: The registry has no source for that provider.
        """
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
        """Report whether a provider's ambient credentials may be used.

        Args:
            provider: Provider key to check.

        Returns:
            True only when the registry holds a source for that provider and
            its status is ``approved``. False for pending, rejected and revoked
            sources and for providers with no record at all, so an unknown
            provider is denied rather than assumed usable.
        """
        source = self.get_source(provider)
        return bool(source and source.status == "approved")


class ScanInFlightError(Exception):
    """Raised when a concurrent scan is attempted."""


def get_ambient_credentials(provider: str) -> CloudCredentialSource:
    """Resolve a provider's ambient credential source, requiring approval.

    The gate every consumer of ambient cloud credentials goes through: it fails
    closed, so a provider that was never scanned, is still pending, or has been
    rejected or revoked is refused rather than used.

    Args:
        provider: Provider key whose credential source is wanted.

    Returns:
        The approved source, carrying the region, project or endpoint the
        caller needs to reach that provider. Not the credentials themselves —
        those stay ambient on the host and are resolved by the provider SDK.

    Raises:
        CloudCredentialsError: No source exists for the provider, or it is not
            in the approved state.
    """
    store = CloudCredentialsStore()
    source = store.get_source(provider)
    if not source or source.status != "approved":
        raise CloudCredentialsError(f"Cloud credentials for {provider} are not approved")
    return source
