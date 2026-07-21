"""Presence-only detection of ambient cloud LLM credentials (no live API calls)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

PROVIDER_SERVICES = {
    "aws": "bedrock",
    "foundry": "microsoft_foundry",
    "gcp": "vertex",
}

PROVIDERS = ("aws", "foundry", "gcp")


def _imds_reachable() -> bool:
    try:
        req = Request(
            "http://169.254.169.254/latest/meta-data/",
            headers={"User-Agent": "ado2gh-credential-scan"},
            method="GET",
        )
        with urlopen(req, timeout=1.0) as resp:
            return resp.status < 500
    except Exception:
        return False


def _gce_metadata_reachable() -> bool:
    try:
        req = Request(
            "http://metadata.google.internal/computeMetadata/v1/",
            headers={"Metadata-Flavor": "Google"},
            method="GET",
        )
        with urlopen(req, timeout=1.0) as resp:
            return resp.status < 500
    except Exception:
        return False


def _detect_aws() -> dict[str, Any]:
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    methods: list[str] = []
    if _imds_reachable():
        methods.append("instance_role")
    if os.environ.get("AWS_WEB_IDENTITY_TOKEN_FILE") and os.environ.get("AWS_ROLE_ARN"):
        methods.append("web_identity")
    if os.environ.get("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI") or os.environ.get(
        "AWS_CONTAINER_CREDENTIALS_FULL_URI"
    ):
        methods.append("ecs_task_role")
    if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
        methods.append("env_keys")
    primary = ""
    if "instance_role" in methods:
        primary = "instance_role"
    elif "web_identity" in methods:
        primary = "web_identity"
    elif "ecs_task_role" in methods:
        primary = "ecs_task_role"
    elif "env_keys" in methods:
        primary = "env_keys"
    alternate = [m for m in methods if m != primary]
    missing: list[str] = []
    if not region:
        missing.append("AWS_REGION")
    if not methods:
        missing.append("aws_credentials")
    completeness = "absent"
    if methods or region:
        completeness = "complete" if region and methods else "incomplete"
    if not methods and not region:
        completeness = "absent"
    return {
        "provider": "aws",
        "completeness": completeness,
        "primary_method": primary,
        "alternate_methods": alternate,
        "region": region,
        "project": None,
        "endpoint": None,
        "missing_fields": missing,
    }


def _detect_foundry() -> dict[str, Any]:
    endpoint = (
        os.environ.get("ADO2GH_FOUNDRY_ENDPOINT")
        or os.environ.get("AZURE_OPENAI_ENDPOINT")
        or ""
    ).strip()
    client_id = os.environ.get("AZURE_CLIENT_ID", "").strip()
    tenant_id = os.environ.get("AZURE_TENANT_ID", "").strip()
    has_secret = bool(os.environ.get("AZURE_CLIENT_SECRET", "").strip())
    has_msi = bool(os.environ.get("MSI_ENDPOINT") or os.environ.get("IDENTITY_ENDPOINT"))
    methods: list[str] = []
    if has_msi:
        methods.append("managed_identity")
    if client_id and tenant_id and has_secret:
        methods.append("service_principal")
    primary = methods[0] if methods else ""
    alternate = methods[1:]
    missing: list[str] = []
    if not endpoint:
        missing.append("endpoint")
    if not client_id and not has_msi:
        missing.append("AZURE_CLIENT_ID")
    if not tenant_id and not has_msi:
        missing.append("AZURE_TENANT_ID")
    if client_id and tenant_id and not has_secret and not has_msi:
        missing.append("AZURE_CLIENT_SECRET")
    completeness = "absent"
    if endpoint or methods or client_id:
        if endpoint and methods and (has_msi or (client_id and tenant_id)):
            completeness = "complete"
        else:
            completeness = "incomplete"
    return {
        "provider": "foundry",
        "completeness": completeness,
        "primary_method": primary,
        "alternate_methods": alternate,
        "region": os.environ.get("AZURE_REGION") or None,
        "project": None,
        "endpoint": endpoint or None,
        "missing_fields": missing,
    }


def _detect_gcp() -> dict[str, Any]:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    region = os.environ.get("GOOGLE_CLOUD_REGION", "").strip() or None
    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    methods: list[str] = []
    if creds_path and Path(creds_path).is_file():
        methods.append("adc_file")
    if _gce_metadata_reachable():
        methods.append("gce_metadata")
    primary = methods[0] if methods else ""
    alternate = methods[1:]
    missing: list[str] = []
    if not project:
        missing.append("GOOGLE_CLOUD_PROJECT")
    if not methods:
        missing.append("gcp_credentials")
    completeness = "absent"
    if project or methods:
        completeness = "complete" if project and methods else "incomplete"
    return {
        "provider": "gcp",
        "completeness": completeness,
        "primary_method": primary,
        "alternate_methods": alternate,
        "region": region,
        "project": project or None,
        "endpoint": None,
        "missing_fields": missing,
    }


def scan_all_presence() -> list[dict[str, Any]]:
    return [_detect_aws(), _detect_foundry(), _detect_gcp()]
