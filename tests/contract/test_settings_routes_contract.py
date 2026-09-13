"""Contract tests for ``services/accelerator_api/routes/settings_routes.py`` (COV-DRIFT-001).

``services/`` carries the whole externally reachable surface of the accelerator
and sat outside coverage measurement entirely. These tests pin the response
*shape* and the capability gate of every settings route so a refactor of the
route layer cannot silently change what the console receives.

No network: the two routes that make an outbound call (the connectivity probe
and the provider catalog lookup) have their HTTP client and catalog function
replaced. Every credential literal here is obviously fake (CA-003).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from ado2gh.auth.service import AuthService
from ado2gh.state.factory import create_state_db

FAKE_API_KEY = "sk-fake-not-a-real-key-0001"
FAKE_PROXY_PASSWORD = "fake-proxy-password-0002"


@pytest.fixture
def anon(tmp_path, monkeypatch) -> TestClient:
    """Accelerator client with auth enabled and nobody signed in."""
    monkeypatch.setenv("ADO2GH_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADO2GH_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "settings_contract.db"
    monkeypatch.setenv("ADO2GH_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ADO2GH_DATA_DIR", str(tmp_path))
    from services.accelerator_api import auth_routes
    from services.accelerator_api.main import app

    auth_routes._svc = AuthService(db=create_state_db(str(db_path)))
    return TestClient(app)


@pytest.fixture
def admin(anon: TestClient) -> TestClient:
    """The same client with a bootstrapped administrator signed in."""
    resp = anon.post(
        "/v1/auth/bootstrap",
        json={"username": "admin", "password": "twelve-char-pass", "display_name": "Admin"},
    )
    assert resp.status_code == 201, resp.text
    return anon


# --------------------------------------------------------------------------
# Capability gate
# --------------------------------------------------------------------------

# Every route reached without an identity, with a body where one is required.
GUARDED = [
    ("GET", "/v1/settings/connectivity", None),
    ("PUT", "/v1/settings/connectivity", {"proxy_enabled": True}),
    ("POST", "/v1/settings/connectivity/test", None),
    ("GET", "/v1/settings/llm-models/providers", None),
    ("POST", "/v1/settings/llm-models/catalog", {"provider": "openai"}),
    ("POST", "/v1/settings/llm-models/validate", {"provider": "openai"}),
    ("POST", "/v1/settings/llm-models/m1/validate", None),
    ("POST", "/v1/settings/llm-models", {"provider": "openai", "model_id": "gpt"}),
    ("PUT", "/v1/settings/llm-models/m1", {"display_name": "x"}),
    ("DELETE", "/v1/settings/llm-models/m1", None),
    ("GET", "/v1/settings/cloud-credentials", None),
    ("POST", "/v1/settings/cloud-credentials/scan", None),
    ("PATCH", "/v1/settings/cloud-credentials/aws", {"region": "eu-west-1"}),
    ("POST", "/v1/settings/cloud-credentials/aws/approve", None),
    ("POST", "/v1/settings/cloud-credentials/aws/reject", None),
    ("DELETE", "/v1/settings/cloud-credentials/aws", None),
]


@pytest.mark.parametrize(
    "method,path,body", GUARDED, ids=[f"{m}-{p}" for m, p, _ in GUARDED],
)
def test_settings_route_refuses_anonymous_caller(anon, method, path, body):
    """No settings route answers a caller with no platform identity."""
    resp = anon.request(method, path, json=body)
    assert resp.status_code in (401, 403), (
        f"{method} {path} answered an anonymous caller with HTTP {resp.status_code}"
    )


# --------------------------------------------------------------------------
# Connectivity
# --------------------------------------------------------------------------

CONNECTIVITY_KEYS = {
    "proxy_enabled",
    "proxy_host",
    "proxy_port",
    "proxy_username",
    "proxy_password",
    "custom_ca_configured",
    "allow_custom_model_id",
    "updated_at",
    "updated_by",
}


def test_get_connectivity_returns_the_documented_shape(admin):
    resp = admin.get("/v1/settings/connectivity")
    assert resp.status_code == 200
    body = resp.json()
    assert CONNECTIVITY_KEYS <= set(body)
    # Nothing is configured yet, so the password mask is the empty string.
    assert body["proxy_password"] == ""
    assert body["custom_ca_configured"] is False


def test_put_connectivity_masks_the_password_and_never_echoes_it(admin):
    resp = admin.put(
        "/v1/settings/connectivity",
        json={
            "proxy_enabled": True,
            "proxy_host": "proxy.internal",
            "proxy_port": 8080,
            "proxy_username": "svc-migration",
            "proxy_password": FAKE_PROXY_PASSWORD,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["proxy_enabled"] is True
    assert body["proxy_host"] == "proxy.internal"
    assert body["proxy_password"] == "***"
    assert FAKE_PROXY_PASSWORD not in resp.text

    reread = admin.get("/v1/settings/connectivity").json()
    assert reread["proxy_password"] == "***"
    assert FAKE_PROXY_PASSWORD not in admin.get("/v1/settings/connectivity").text


def test_put_connectivity_round_trips_the_mask_without_clearing_the_secret(admin):
    """Sending the mask back is the console round-trip; the stored value survives."""
    admin.put(
        "/v1/settings/connectivity",
        json={"proxy_enabled": True, "proxy_password": FAKE_PROXY_PASSWORD},
    )
    resp = admin.put(
        "/v1/settings/connectivity",
        json={"proxy_host": "proxy2.internal", "proxy_password": "***"},
    )
    assert resp.status_code == 200
    assert resp.json()["proxy_host"] == "proxy2.internal"
    assert resp.json()["proxy_password"] == "***", "the mask round-trip cleared the password"


def test_put_connectivity_applies_only_the_keys_supplied(admin):
    admin.put("/v1/settings/connectivity", json={"proxy_host": "first.internal"})
    resp = admin.put("/v1/settings/connectivity", json={"proxy_username": "someone"})
    assert resp.json()["proxy_host"] == "first.internal"
    assert resp.json()["proxy_username"] == "someone"


def test_connectivity_test_reports_passed_when_the_probe_succeeds(admin):
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.get.return_value = MagicMock(raise_for_status=MagicMock(return_value=None))
    with patch(
        "ado2gh.api.llm.http_llm.build_cloud_llm_http_client", return_value=client,
    ):
        resp = admin.post("/v1/settings/connectivity/test")
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "passed",
        "category": None,
        "message": "Outbound TLS and proxy path succeeded.",
    }


def test_connectivity_test_classifies_a_failure_instead_of_raising(admin):
    with patch(
        "ado2gh.api.llm.http_llm.build_cloud_llm_http_client",
        side_effect=OSError("proxy refused the connection"),
    ):
        resp = admin.post("/v1/settings/connectivity/test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert body["category"], "a failed probe must carry a classified category"
    assert isinstance(body["message"], str)


# --------------------------------------------------------------------------
# LLM models
# --------------------------------------------------------------------------


def test_provider_listing_names_every_registered_provider(admin):
    resp = admin.get("/v1/settings/llm-models/providers")
    assert resp.status_code == 200
    providers = resp.json()["providers"]
    assert isinstance(providers, list) and providers
    names = {p["id"] for p in providers}
    # The registry is the source of truth; these three are documented in CLAUDE.md.
    assert {"openai", "anthropic", "ollama"} <= names
    spec = next(p for p in providers if p["id"] == "openai")
    assert {"label", "kind", "requires_api_key", "requires_base_url"} <= set(spec)


def test_catalog_lookup_rejects_an_unknown_provider_with_400(admin):
    resp = admin.post(
        "/v1/settings/llm-models/catalog",
        json={"provider": "not-a-provider", "api_key": FAKE_API_KEY},
    )
    assert resp.status_code == 400
    assert FAKE_API_KEY not in resp.text, "the credential was echoed into the error"


def test_catalog_lookup_returns_the_provider_entries(admin):
    with patch(
        "ado2gh.api.llm.model_catalog.list_catalog",
        return_value={"models": [{"id": "gpt-fake"}], "source": "presets"},
    ) as listing:
        resp = admin.post(
            "/v1/settings/llm-models/catalog",
            json={"provider": "openai", "api_key": FAKE_API_KEY, "base_url": ""},
        )
    assert resp.status_code == 200
    assert resp.json()["source"] == "presets"
    # The credential is forwarded to the provider lookup, never persisted.
    assert listing.call_args.kwargs["api_key"] == FAKE_API_KEY


def test_draft_validation_returns_the_outcome_without_storing_the_model(admin):
    with patch(
        "ado2gh.api.llm.model_validation.validate_draft",
        return_value={"status": "failed", "category": "auth", "message": "bad key"},
    ):
        resp = admin.post(
            "/v1/settings/llm-models/validate",
            json={"provider": "openai", "model_id": "gpt-draft-only", "api_key": FAKE_API_KEY},
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"
    stored = admin.get("/v1/settings/llm-models").json()["models"]
    assert all(m["model_id"] != "gpt-draft-only" for m in stored), (
        "a draft validation persisted the model it was only supposed to test"
    )


def test_llm_model_crud_round_trip_masks_the_credential(admin):
    created = admin.post(
        "/v1/settings/llm-models",
        json={
            "display_name": "Fake OpenAI",
            "provider": "openai",
            "model_id": "gpt-fake",
            "api_key": FAKE_API_KEY,
        },
    )
    assert created.status_code == 200, created.text
    model = created.json()
    model_id = model["id"]
    assert FAKE_API_KEY not in created.text, "the stored credential came back in the response"

    listed = admin.get("/v1/settings/llm-models")
    assert listed.status_code == 200
    assert model_id in [m["id"] for m in listed.json()["models"]]
    assert FAKE_API_KEY not in listed.text

    updated = admin.put(
        f"/v1/settings/llm-models/{model_id}",
        json={
            "display_name": "Renamed",
            "provider": "openai",
            "model_id": "gpt-fake",
            "api_key": "***",
            "id": "some-other-model",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["display_name"] == "Renamed"
    assert updated.json()["id"] == model_id, "the path id must win over the body"

    deleted = admin.delete(f"/v1/settings/llm-models/{model_id}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] == model_id
    remaining = [m["id"] for m in admin.get("/v1/settings/llm-models").json()["models"]]
    assert model_id not in remaining


def test_putting_an_unknown_model_id_creates_it(admin):
    """``PUT`` is an upsert: an id the store does not hold is created, not refused.

    The handler documents a 404 for an unknown identifier, but the id in the path
    is written into the body and ``LLMModelStore.upsert`` is called without its
    ``model_id`` argument, so the not-found branch is unreachable. Pinned as the
    behaviour the console actually gets; the docstring divergence is carried as a
    follow-up rather than fixed here.
    """
    resp = admin.put(
        "/v1/settings/llm-models/put-creates-this-one",
        json={
            "display_name": "Created By Put",
            "provider": "openai",
            "model_id": "gpt-fake",
            "api_key": FAKE_API_KEY,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["display_name"] == "Created By Put"
    assert FAKE_API_KEY not in resp.text
    admin.delete(f"/v1/settings/llm-models/{resp.json()['id']}")


def test_validating_an_unknown_saved_model_is_404(admin):
    resp = admin.post("/v1/settings/llm-models/never-created-model-id/validate")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Model not found"


# --------------------------------------------------------------------------
# Cloud credentials
# --------------------------------------------------------------------------


def test_cloud_credentials_listing_returns_sources(admin):
    resp = admin.get("/v1/settings/cloud-credentials")
    assert resp.status_code == 200
    assert isinstance(resp.json()["sources"], list)


def test_cloud_credentials_scan_reports_the_probe_result(admin):
    from services.accelerator_api.routes import _shared

    source = MagicMock()
    source.provider = "aws"
    source.to_public.return_value = {"provider": "aws", "status": "detected"}
    with patch.object(_shared._cloud_credentials, "scan", return_value=[source]):
        resp = admin.post("/v1/settings/cloud-credentials/scan")
    assert resp.status_code == 200
    assert resp.json()["sources"] == [{"provider": "aws", "status": "detected"}]


def test_platform_model_status_is_404_when_none_is_shipped(admin):
    resp = admin.get("/v1/settings/cloud-credentials/platform-model")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "platform_model_not_configured"


def test_platform_model_status_is_returned_when_one_is_configured(admin):
    with patch(
        "ado2gh.api.llm.platform_managed_model.platform_model_status",
        return_value={"provider": "bedrock", "model_id": "fake-model", "enabled": False},
    ):
        resp = admin.get("/v1/settings/cloud-credentials/platform-model")
    assert resp.status_code == 200
    assert resp.json()["provider"] == "bedrock"


# --------------------------------------------------------------------------
# Settings, advanced settings and phases
# --------------------------------------------------------------------------


def test_settings_payload_carries_profiles_and_advanced_defaults(admin):
    resp = admin.get("/v1/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert {"active_profile_id", "migration_profiles", "advanced"} <= set(body)
    assert isinstance(body["migration_profiles"], list)
    assert "dry_run_default" in body["advanced"]


def test_advanced_settings_update_applies_only_the_fields_sent(admin):
    before = admin.get("/v1/settings").json()["advanced"]
    assert before["dry_run_default"] is True
    resp = admin.put("/v1/settings/advanced", json={"dry_run_default": False})
    assert resp.status_code == 200
    after = resp.json()
    assert after["dry_run_default"] is False
    # Scalar defaults that were not sent must survive the partial update.
    for key in ("migration_strategy", "config_path", "max_workers"):
        if key in before and key in after:
            assert after[key] == before[key], f"{key} changed although it was not sent"
    assert admin.get("/v1/settings").json()["advanced"]["dry_run_default"] is False


def test_phases_payload_reports_coverage_and_counts(admin):
    resp = admin.get("/v1/settings/phases")
    assert resp.status_code == 200
    body = resp.json()
    assert "phases" in body
    assert isinstance(body["phases"], list) and body["phases"]
    first = body["phases"][0]
    assert {"id", "name", "risk_max"} <= set(first)


def test_phase_update_replaces_the_whole_set(admin):
    resp = admin.put(
        "/v1/settings/phases",
        json={
            "phases": [
                {"id": "poc", "name": "POC", "risk_max": 100.0, "repo_cap": 10, "order": 0},
            ],
            "removals": [],
            "span_to_scan": False,
        },
    )
    assert resp.status_code == 200, resp.text
    assert [p["id"] for p in resp.json()["phases"]] == ["poc"]


def test_phase_update_rejects_an_inconsistent_set_with_400(admin):
    resp = admin.put(
        "/v1/settings/phases",
        json={
            "phases": [
                {"id": "poc", "name": "POC", "risk_max": 25.0, "repo_cap": 10, "order": 0},
            ],
            "removals": [],
            "span_to_scan": False,
        },
    )
    # The last phase must reach 100 or scored repositories fall outside every band.
    assert resp.status_code == 400
    assert resp.json()["detail"]
