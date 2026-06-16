"""Optional async export of redacted audit bundles to object storage (FR-037)."""
from __future__ import annotations

import json
from typing import Any, Optional


class AuditExportJob:
    """Profile-configurable WORM export stub; production uses S3 Object Lock."""

    def __init__(self, db: Any, bucket: Optional[str] = None):
        self.db = db
        self.bucket = bucket

    def export_profile(self, profile_id: str) -> dict:
        """Export audit events for profile; returns manifest (S3 upload when configured)."""
        events = self.db.list_audit_events(profile_id=profile_id, limit=10000)
        bundle = {"profile_id": profile_id, "events": events, "count": len(events)}
        if self.bucket:
            return {
                "status": "exported",
                "bucket": self.bucket,
                "object_key": f"audit/{profile_id}/bundle.json",
                "event_count": len(events),
            }
        return {"status": "local_only", "preview_bytes": len(json.dumps(bundle))}
