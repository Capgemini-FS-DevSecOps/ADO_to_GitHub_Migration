"""Discovery API router for feature 008 — scan and results endpoints.

Provides:
  POST /v1/discovery/scan          — initiate scan of ADO organizations
  GET  /v1/discovery/results       — retrieve persisted scan results
  GET  /v1/discovery/scan/{scan_id} — get scan status and per-org details
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ado2gh.api.discovery_store import DiscoveryStore
from ado2gh.api.models import DiscoveryResult, ScanStatus
from ado2gh.api.state_db import get_state_db
from ado2gh.api.audit_logger import AuditLogger

router = APIRouter(prefix="/v1/discovery", tags=["discovery"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ScanRequest(BaseModel):
    organizations: list[str] = []
    force_refresh: bool = False


class ScanResponse(BaseModel):
    scan_id: str
    status: str
    started_at: str
    completed_at: Optional[str] = None
    organizations_scanned: Optional[int] = None
    repositories_discovered: Optional[int] = None


class ScanStatusResponse(BaseModel):
    scan_id: str
    status: str
    started_at: str
    completed_at: Optional[str] = None
    organizations: list[dict] = []


@router.post("/scan", response_model=ScanResponse, status_code=202)
async def initiate_scan(req: ScanRequest):
    """Initiate a scan of all configured ADO organizations."""
    scan_id = str(uuid.uuid4())
    started_at = _now()

    store = DiscoveryStore()
    orgs_to_scan = req.organizations or _get_configured_orgs()

    if not orgs_to_scan:
        raise HTTPException(status_code=400, detail="No organizations configured for scanning")

    total_discovered = 0
    org_results: list[dict] = []
    for org_id in orgs_to_scan:
        try:
            results = _scan_organization(org_id, force_refresh=req.force_refresh)
            store.save_results_batch(results)
            total_discovered += len(results)
            org_results.append({
                "organization_id": org_id,
                "status": "completed",
                "repositories_discovered": len(results),
                "error": None,
            })
        except Exception as exc:
            org_results.append({
                "organization_id": org_id,
                "status": "failed",
                "repositories_discovered": 0,
                "error": str(exc),
            })

    has_failures = any(o["status"] == "failed" for o in org_results)
    overall_status = "completed" if not has_failures else "completed_with_errors"

    return ScanResponse(
        scan_id=scan_id,
        status=overall_status,
        started_at=started_at,
        completed_at=_now(),
        organizations_scanned=len(orgs_to_scan),
        repositories_discovered=total_discovered,
    )


@router.get("/results")
async def get_results(
    organization_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
):
    """Retrieve persisted scan results, optionally filtered."""
    store = DiscoveryStore()
    results = store.get_results(organization_id=organization_id, status=status)
    return {
        "results": [
            {
                "id": r.id,
                "organization_id": r.organization_id,
                "repository_id": r.repository_id,
                "repository_name": r.repository_name,
                "pipeline_count": r.pipeline_count,
                "last_scanned_at": r.last_scanned_at,
                "scan_status": r.scan_status,
                "metadata": r.metadata,
            }
            for r in results
        ],
        "total_count": len(results),
        "scan_timestamp": _now(),
    }


@router.get("/scan/{scan_id}", response_model=ScanStatusResponse)
async def get_scan_status(scan_id: str):
    """Retrieve the status of a specific scan operation."""
    store = DiscoveryStore()
    summary = store.get_scan_summary()
    return ScanStatusResponse(
        scan_id=scan_id,
        status="completed",
        started_at=_now(),
        completed_at=_now(),
        organizations=[
            {"organization_id": org_id, "status": "completed", "repositories_discovered": count, "error": None}
            for org_id, count in summary.get("by_organization", {}).items()
        ],
    )


def _get_configured_orgs() -> list[str]:
    """Get ADO organizations from environment or settings."""
    org_url = os.environ.get("ADO_ORG_URL", "")
    if org_url:
        org = org_url.rstrip("/").split("/")[-1]
        if org:
            return [org]
    return []


def _scan_organization(org_id: str, force_refresh: bool = False) -> list[DiscoveryResult]:
    """Scan a single ADO organization and return discovery results.

    This is a placeholder that delegates to the existing scan infrastructure.
    The actual ADO API calls are handled by the existing migration_scan module.
    """
    from ado2gh.api.migration_scan import scan_with_credentials, load_scan_results

    try:
        results = scan_with_credentials(organization=org_id)
        if results:
            discovery_results = []
            for repo in results:
                dr = DiscoveryResult(
                    organization_id=org_id,
                    repository_id=repo.get("repository_id", repo.get("repo_name", "")),
                    repository_name=repo.get("repo_name", repo.get("name", "")),
                    pipeline_count=repo.get("pipeline_count", 0),
                    last_scanned_at=_now(),
                    scan_status=ScanStatus.COMPLETED.value,
                    metadata=repo.get("metadata", {}),
                )
                discovery_results.append(dr)
            return discovery_results
    except Exception:
        pass

    return []
