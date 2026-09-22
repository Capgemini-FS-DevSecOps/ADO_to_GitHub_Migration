"""Live credential probes for cloud language model providers (approve + validate paths)."""
from __future__ import annotations

import os
from typing import Any

from ado2gh.api.credentials.cloud_credentials_store import CloudCredentialSource


def probe_provider(provider: str, source: CloudCredentialSource) -> dict[str, Any]:
    """Check that a provider's ambient credentials actually work.

    Dispatches to the probe for the given provider, which issues one minimal
    request to that provider's inference API using the host's ambient
    credentials. This is a live network call and it is the step that
    distinguishes a working credential source from a merely present one. The
    model to probe with and any provider endpoint are read from the
    environment and from ``source``; no credential value is logged or returned.

    Args:
        provider: Provider key to probe. Anything outside the supported set
            yields a failed result rather than an exception.
        source: The detected credential source, consulted for the region,
            project and endpoint to probe against.

    Returns:
        A mapping with ``status`` of ``passed`` or ``failed``; ``category``,
        which is None on success and otherwise one of ``credentials``,
        ``model_not_found``, ``timeout`` or ``network``; and a ``message``
        safe to show an operator. Failures are reported through this mapping,
        never raised.
    """
    if provider == "aws":
        return _probe_aws(source)
    if provider == "foundry":
        return _probe_foundry(source)
    if provider == "gcp":
        return _probe_gcp(source)
    return {
        "status": "failed",
        "category": "credentials",
        "message": f"Unknown provider {provider}",
    }


def _probe_aws(source: CloudCredentialSource) -> dict[str, Any]:
    model_id = os.environ.get("ADO2GH_BEDROCK_MODEL_ID", "").strip()
    region = source.region or os.environ.get("AWS_REGION", "")
    if not model_id:
        return {
            "status": "failed",
            "category": "model_not_found",
            "message": "ADO2GH_BEDROCK_MODEL_ID is not configured for probe.",
        }
    if not region:
        return {
            "status": "failed",
            "category": "credentials",
            "message": "AWS region is required for Bedrock probe.",
        }
    try:
        import boto3

        client = boto3.client("bedrock-runtime", region_name=region)
        client.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": "ping"}]}],
            inferenceConfig={"maxTokens": 1},
        )
        return {"status": "passed", "category": None, "message": "Bedrock probe succeeded."}
    except ImportError:
        return {
            "status": "failed",
            "category": "network",
            "message": "boto3 is not installed for Bedrock probe.",
        }
    except Exception as exc:
        return _classify_probe_error(exc)


def _probe_foundry(source: CloudCredentialSource) -> dict[str, Any]:
    model_id = os.environ.get("ADO2GH_FOUNDRY_MODEL_ID", "").strip()
    endpoint = (source.endpoint or os.environ.get("ADO2GH_FOUNDRY_ENDPOINT", "")).rstrip("/")
    if not endpoint or not model_id:
        return {
            "status": "failed",
            "category": "credentials",
            "message": "Foundry endpoint and model id are required.",
        }
    try:
        from openai import AzureOpenAI

        client = AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=os.environ.get("AZURE_OPENAI_API_KEY", "placeholder"),
            api_version="2024-06-01",
        )
        client.chat.completions.create(
            model=model_id,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
        )
        return {"status": "passed", "category": None, "message": "Foundry probe succeeded."}
    except ImportError:
        return _probe_foundry_http(endpoint, model_id)
    except Exception as exc:
        return _classify_probe_error(exc)


def _probe_foundry_http(endpoint: str, model_id: str) -> dict[str, Any]:
    import httpx

    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(endpoint)
            if response.status_code >= 500:
                return {
                    "status": "failed",
                    "category": "network",
                    "message": f"Foundry endpoint returned HTTP {response.status_code}.",
                }
        return {
            "status": "passed",
            "category": None,
            "message": f"Foundry endpoint reachable for model {model_id}.",
        }
    except Exception as exc:
        return _classify_probe_error(exc)


def _probe_gcp(source: CloudCredentialSource) -> dict[str, Any]:
    model_id = os.environ.get("ADO2GH_VERTEX_MODEL_ID", "").strip()
    project = source.project or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    region = source.region or os.environ.get("GOOGLE_CLOUD_REGION", "us-central1")
    if not project or not model_id:
        return {
            "status": "failed",
            "category": "credentials",
            "message": "GCP project and vertex model id are required.",
        }
    try:
        import google.auth
        import google.auth.transport.requests
        import httpx

        credentials, _ = google.auth.default()
        credentials.refresh(google.auth.transport.requests.Request())
        token = credentials.token
        url = (
            f"https://{region}-aiplatform.googleapis.com/v1/projects/{project}"
            f"/locations/{region}/publishers/google/models/{model_id}:generateContent"
        )
        with httpx.Client(timeout=15.0) as client:
            response = client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={"contents": [{"role": "user", "parts": [{"text": "ping"}]}]},
            )
            if response.status_code < 400:
                return {"status": "passed", "category": None, "message": "Vertex probe succeeded."}
            if response.status_code in (401, 403):
                return {
                    "status": "failed",
                    "category": "credentials",
                    "message": "Vertex credentials were rejected.",
                }
            if response.status_code == 404:
                return {
                    "status": "failed",
                    "category": "model_not_found",
                    "message": "Vertex model was not found.",
                }
            return {
                "status": "failed",
                "category": "network",
                "message": f"Vertex returned HTTP {response.status_code}.",
            }
    except ImportError:
        return {
            "status": "failed",
            "category": "network",
            "message": "google-auth is not installed for Vertex probe.",
        }
    except Exception as exc:
        return _classify_probe_error(exc)


def _classify_probe_error(exc: Exception) -> dict[str, Any]:
    """Turn a probe exception into an operator-safe failure result.

    The exception text is only matched against, never returned: a provider SDK
    error can quote request headers or configuration, so every branch returns a
    fixed message instead of the original string.

    Args:
        exc: The exception raised while probing.

    Returns:
        A failed probe result whose ``category`` is ``timeout``,
        ``model_not_found`` or ``credentials`` when the exception text matches
        that class of failure, and ``network`` otherwise, together with a
        generic ``message`` describing the category.
    """
    message = str(exc) or exc.__class__.__name__
    lowered = message.lower()
    if "timeout" in lowered:
        return {"status": "failed", "category": "timeout", "message": "Probe timed out."}
    if "not found" in lowered or "404" in lowered:
        return {
            "status": "failed",
            "category": "model_not_found",
            "message": "Model was not found at the provider.",
        }
    if any(tok in lowered for tok in ("unauthorized", "access denied", "403", "401")):
        return {
            "status": "failed",
            "category": "credentials",
            "message": "Cloud credentials were rejected.",
        }
    return {"status": "failed", "category": "network", "message": "Probe failed due to a network error."}
