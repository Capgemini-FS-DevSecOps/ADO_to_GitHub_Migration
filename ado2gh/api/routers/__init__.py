"""Discovery API router for feature 008 — scan and results endpoints.

Provides:
  POST /v1/discovery/scan          — initiate scan of ADO organizations
  GET  /v1/discovery/results       — retrieve persisted scan results
  GET  /v1/discovery/scan/{scan_id} — get scan status and per-org details
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from typing import Optional

router = APIRouter(prefix="/v1/discovery", tags=["discovery"])
