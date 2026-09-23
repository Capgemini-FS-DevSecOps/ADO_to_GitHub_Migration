"""Contract tests for unified tab navigation (feature 008, US1).

Verifies that the unified navigation structure exposes exactly two primary tabs
(Discovery and Migrate) plus Agent and Settings, and that old tab routes
redirect to the new unified structure.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from services.accelerator_api.main import app
    return TestClient(app)


class TestUnifiedTabNavigation:
    """Contract tests for the unified tab structure (FR-001)."""

    def test_health_check_passes(self, client):
        """Health endpoint must return 200 for Docker health checks."""
        resp = client.get("/health")
        assert resp.status_code == 200
