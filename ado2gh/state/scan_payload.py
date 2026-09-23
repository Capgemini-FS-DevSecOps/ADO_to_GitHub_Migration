"""Shape discovery-scan payloads for the ``profile_scans`` table.

Both state backends persist a scan the same way — phase buckets in
``summary_json`` with the discovery inventory nested under one key, operator
phase assignments preserved across a re-scan — so the shaping lives here rather
than in either backend's mixin. Keeping it in ``ado2gh/state/`` is what lets the
mixins stop importing ``ado2gh/api/`` (GAP-021).
"""
from __future__ import annotations

from typing import Any

DISCOVERY_DETAIL_KEY = "__discovery_detail__"

DISCOVERY_DETAIL_FIELDS = (
    "project_details",
    "org_inventory",
    "inventory_gaps",
    "warnings",
    "status",
    "pipeline_inventory",
    "artifacts",
    "boards",
    "test_plans",
)


def manual_phase_overrides(repos: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    """Detect repos where assigned_phase differs from suggested_phase (manual overrides)."""
    overrides: dict[tuple[str, str], str] = {}
    for r in repos:
        assigned = r.get("assigned_phase")
        suggested = r.get("suggested_phase")
        if assigned and suggested and assigned != suggested:
            overrides[(r.get("project", ""), r.get("repo_name", ""))] = assigned
    return overrides


def pack_scan_summary_json(raw: dict[str, Any]) -> dict[str, Any]:
    """Phase buckets + embedded discovery inventory for profile_scans.summary_json."""
    summary = {
        k: {kk: vv for kk, vv in v.items() if kk != "repos"}
        for k, v in raw.get("recommendations", {}).items()
    }
    detail = {k: raw[k] for k in DISCOVERY_DETAIL_FIELDS if raw.get(k) is not None}
    if detail:
        summary[DISCOVERY_DETAIL_KEY] = detail
    return summary


def extract_discovery_fields(data: dict[str, Any] | None) -> dict[str, Any]:
    """Read service-connection inventory and related fields from scan payloads."""
    if not data:
        return {}
    out: dict[str, Any] = {}
    for key in DISCOVERY_DETAIL_FIELDS:
        if data.get(key) is not None:
            out[key] = data[key]
    nested = data.get(DISCOVERY_DETAIL_KEY)
    if isinstance(nested, dict):
        for key, value in nested.items():
            if value is not None:
                out[key] = value
    return out
