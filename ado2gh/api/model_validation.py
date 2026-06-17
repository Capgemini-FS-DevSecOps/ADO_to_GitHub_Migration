"""LLM model connectivity validation with categorized failures."""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import httpx

from ado2gh.api.http_llm import DEFAULT_TIMEOUT, build_llm_http_client
from ado2gh.api.local_hosts import resolve_local_service_url
from ado2gh.api.llm_model_store import LLMModelConfig, LLMModelStore

_validate_lock = threading.Lock()
_validate_inflight: dict[str, threading.Event] = {}
_validate_results: dict[str, dict[str, Any]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _result(
    status: str,
    *,
    category: str | None = None,
    message: str = "",
) -> dict[str, Any]:
    return {
        "status": status,
        "category": category,
        "message": message,
        "validated_at": _now_iso(),
    }


def _draft_key(body: dict[str, Any]) -> str:
    return "|".join(
        [
            body.get("provider", ""),
            body.get("model_id", ""),
            body.get("base_url", "") or "",
            (body.get("api_key") or "")[:8],
        ]
    )


def _anthropic_error_category(response: httpx.Response) -> tuple[str, str] | None:
    try:
        body = response.json()
    except Exception:
        return None
    err = body.get("error", body)
    if not isinstance(err, dict):
        return None
    err_type = str(err.get("type", ""))
    message = str(err.get("message", ""))
    if err_type in ("not_found_error", "invalid_request_error") and "model" in message.lower():
        return "model_not_found", "Model was not found at the provider. Choose a model from your account catalog."
    return None


def _classify_error(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout", "Validation timed out. Try again or check network latency."
    if isinstance(exc, httpx.ProxyError):
        return "proxy", "Could not reach provider through configured proxy."
    if isinstance(exc, httpx.ConnectError):
        return "network", "Could not reach the provider host."
    if isinstance(exc, ssl_error_type()):
        return "tls", "TLS trust failed. Check custom CA configuration."
    if isinstance(exc, httpx.HTTPStatusError):
        parsed = _anthropic_error_category(exc.response)
        if parsed:
            return parsed
        code = exc.response.status_code
        if code in (401, 403):
            try:
                detail = str(exc.response.json().get("detail", ""))
                if "not authenticated" in detail.lower():
                    return (
                        "credentials",
                        "Ollama rejected the request (401). Add a bearer token if Ollama auth is enabled.",
                    )
            except Exception:
                pass
            return "credentials", "Invalid or unauthorized API credentials."
        if code == 404:
            return "model_not_found", "Model was not found at the provider."
        if code == 407:
            return "proxy", "Proxy authentication failed."
        return "network", f"Provider returned HTTP {code}."
    return "network", "Validation failed due to a network error."


def ssl_error_type():
    import ssl

    return ssl.SSLError


def _validate_openai(api_key: str, model_id: str) -> None:
    with build_llm_http_client(for_cloud=True) as client:
        response = client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model_id,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 5,
            },
        )
        response.raise_for_status()


def _validate_anthropic(api_key: str, model_id: str) -> None:
    with build_llm_http_client(for_cloud=True) as client:
        response = client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model_id,
                "max_tokens": 5,
                "messages": [{"role": "user", "content": "ping"}],
            },
        )
        response.raise_for_status()


def _validate_ollama(base_url: str, model_id: str, api_key: str = "") -> None:
    resolved = resolve_local_service_url(base_url)
    headers: dict[str, str] | None = None
    if api_key:
        headers = {"Authorization": f"Bearer {api_key}"}
    with build_llm_http_client(for_cloud=False) as client:
        response = client.post(
            f"{resolved}/api/chat",
            headers=headers,
            json={
                "model": model_id,
                "messages": [{"role": "user", "content": "ping"}],
                "stream": False,
            },
        )
        response.raise_for_status()


def _run_provider_check(body: dict[str, Any]) -> dict[str, Any]:
    provider = body.get("provider", "")
    model_id = body.get("model_id", "")
    api_key = (body.get("api_key") or "").strip()
    base_url = body.get("base_url") or ""
    try:
        if provider == "openai":
            if not api_key:
                return _result("failed", category="credentials", message="API key is required.")
            _validate_openai(api_key, model_id)
        elif provider == "anthropic":
            if not api_key:
                return _result("failed", category="credentials", message="API key is required.")
            _validate_anthropic(api_key, model_id)
        elif provider == "ollama":
            if not base_url:
                return _result("failed", category="network", message="Base URL is required.")
            _validate_ollama(base_url, model_id, api_key)
        elif provider in ("stub", "offline"):
            return _result("passed", message="Stub provider requires no external validation.")
        else:
            return _result("failed", category="model_not_found", message=f"Unsupported provider: {provider}")
        return _result("passed", message="Model responded successfully.")
    except Exception as exc:
        category, message = _classify_error(exc)
        return _result("failed", category=category, message=message)


def _single_flight_validate(key: str, runner: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    with _validate_lock:
        if key in _validate_inflight:
            waiter = _validate_inflight[key]
            cached = _validate_results.get(key)
        else:
            event = threading.Event()
            _validate_inflight[key] = event
            waiter = None
            cached = None

    if waiter is not None:
        waiter.wait(timeout=DEFAULT_TIMEOUT + 5)
        if key in _validate_results:
            return dict(_validate_results[key])
        return _result("failed", category="timeout", message="Validation timed out waiting for in-flight check.")

    try:
        result = runner()
        with _validate_lock:
            _validate_results[key] = result
            event = _validate_inflight.pop(key, None)
            if event:
                event.set()
        return result
    except Exception as exc:
        with _validate_lock:
            event = _validate_inflight.pop(key, None)
            if event:
                event.set()
        category, message = _classify_error(exc)
        return _result("failed", category=category, message=message)


def validate_draft(body: dict[str, Any], *, store: Optional[LLMModelStore] = None) -> dict[str, Any]:
    """Validate an unsaved model configuration."""
    key = _draft_key(body)
    return _single_flight_validate(key, lambda: _run_provider_check(body))


def validate_saved(
    model_id: str,
    *,
    store: Optional[LLMModelStore] = None,
) -> dict[str, Any]:
    """Validate a saved model using stored secrets."""
    model_store = store or LLMModelStore()
    model = model_store.get(model_id)
    if not model:
        raise KeyError(model_id)
    body = {
        "provider": model.provider,
        "model_id": model.model_id,
        "api_key": model.api_key,
        "base_url": model.base_url,
    }
    key = f"saved|{model_id}"

    def _run() -> dict[str, Any]:
        result = _run_provider_check(body)
        if result["status"] == "passed":
            model_store.apply_validation_result(
                model_id,
                status="passed",
                category=None,
                message=result["message"],
                validated_at=result["validated_at"],
            )
        else:
            model_store.apply_validation_result(
                model_id,
                status="failed",
                category=result.get("category"),
                message=result["message"],
                validated_at=result["validated_at"],
            )
        return result

    return _single_flight_validate(key, _run)
