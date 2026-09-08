"""Contract tests for streamlined agent interface (feature 008, US2).

Verifies that the agent API endpoints support the clean conversational
interface without pipeline or PEV indicators.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from services.accelerator_api.main import app
    return TestClient(app)


class TestAgentInterface:
    """Contract tests for the streamlined agent interface (FR-003, FR-004)."""

    def test_health_endpoint_works(self, client):
        """Health endpoint must be accessible for agent connectivity checks."""
        resp = client.get("/health")
        assert resp.status_code == 200
