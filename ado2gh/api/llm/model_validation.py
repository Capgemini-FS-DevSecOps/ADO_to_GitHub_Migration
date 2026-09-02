"""LLM model connectivity validation with categorized failures."""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import httpx

from ado2gh.api.llm.http_llm import DEFAULT_TIMEOUT, build_llm_http_client
from ado2gh.api.llm.llm_model_store import LLMModelStore
from ado2gh.api.llm.llm_provider_registry import get_provider_spec
from ado2gh.api.local_hosts import resolve_local_service_url

_validate_lock = threading.Lock()
_validate_inflight: dict[str, threading.Event] = {}
_validate_results: dict[str, dict[str, Any]] = {}

_AGENT_CAPABILITY_MESSAGE = (
    "Model does not support agent reasoning or tool use. "
    "Choose a chat model that can call tools or return structured JSON with tool_calls "
    "(for example GPT-4o, Claude Sonnet, or Qwen3)."
)

_AGENT_JSON_PROBE_USER = (
    'Reply with ONLY valid JSON (no markdown): '
    '{"thinking":"ready","tool_calls":[{"name":"agent_capability_probe","arguments":{"ready":true}}]}'
)

_PROBE_TOOL_OPENAI = {
    "type": "function",
    "function": {
        "name": "agent_capability_probe",
        "description": "Verify tool-calling for the migration agent.",
        "parameters": {
            "type": "object",
            "properties": {"ready": {"type": "boolean"}},
            "required": ["ready"],
        },
    },
}

_PROBE_TOOL_ANTHROPIC = {
    "name": "agent_capability_probe",
    "description": "Verify tool-calling for the migration agent.",
    "input_schema": {
        "type": "object",
        "properties": {"ready": {"type": "boolean"}},
        "required": ["ready"],
    },
}


class AgentCapabilityProbeError(Exception):
    """Model responded but lacks agent tool/reasoning capability."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _result(
    status: str,
    *,
    category: str | None = None,
    message: str = "",
    capabilities: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out = {
        "status": status,
        "category": category,
        "message": message,
        "validated_at": _now_iso(),
    }
    if capabilities:
        out["capabilities"] = capabilities
    return out


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


def _parse_agent_json(text: str) -> dict[str, Any]:
    if not text:
        return {}
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    parsed: dict[str, Any] = {}
    try:
        loaded = json.loads(cleaned)
        if isinstance(loaded, dict):
            parsed = loaded
    except (json.JSONDecodeError, TypeError):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                loaded = json.loads(match.group())
                if isinstance(loaded, dict):
                    parsed = loaded
            except (json.JSONDecodeError, TypeError):
                pass
    if "action" in parsed and "tool_calls" not in parsed:
        parsed["tool_calls"] = [{
            "name": parsed.pop("action"),
            "arguments": parsed.pop("parameters", parsed.pop("arguments", {})),
        }]
    return parsed


def _extract_openai_message_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if choices:
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
    message = payload.get("message") or {}
    content = message.get("content")
    return content if isinstance(content, str) else ""


def _openai_payload_has_tool_calls(payload: dict[str, Any]) -> bool:
    choices = payload.get("choices") or []
    if choices:
        tool_calls = (choices[0].get("message") or {}).get("tool_calls") or []
        if tool_calls:
            return True
    message = payload.get("message") or {}
    return bool(message.get("tool_calls"))


def _anthropic_payload_has_tool_use(payload: dict[str, Any]) -> bool:
    for block in payload.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            return True
    return False


def _gemini_payload_has_function_call(payload: dict[str, Any]) -> bool:
    for candidate in payload.get("candidates") or []:
        content = candidate.get("content") or {}
        for part in content.get("parts") or []:
            if isinstance(part, dict) and part.get("functionCall"):
                return True
    return False


def _extract_gemini_text(payload: dict[str, Any]) -> str:
    for candidate in payload.get("candidates") or []:
        content = candidate.get("content") or {}
        for part in content.get("parts") or []:
            if isinstance(part, dict) and part.get("text"):
                return str(part["text"])
    return ""


def _infer_capabilities(provider: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Return agent capabilities when the model can think or act; otherwise None."""
    if provider == "anthropic":
        if _anthropic_payload_has_tool_use(payload):
            return {
                "supports_tool_calling": True,
                "supports_streaming": True,
                "supports_thinking": True,
            }
        text = ""
        for block in payload.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                text += str(block.get("text", ""))
        parsed = _parse_agent_json(text)
    elif provider == "google_gemini":
        if _gemini_payload_has_function_call(payload):
            return {
                "supports_tool_calling": True,
                "supports_streaming": True,
                "supports_thinking": False,
            }
        parsed = _parse_agent_json(_extract_gemini_text(payload))
    else:
        if _openai_payload_has_tool_calls(payload):
            return {
                "supports_tool_calling": True,
                "supports_streaming": True,
                "supports_thinking": False,
            }
        parsed = _parse_agent_json(_extract_openai_message_text(payload))

    tool_calls = parsed.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        return {
            "supports_tool_calling": True,
            "supports_streaming": True,
            "supports_thinking": bool(parsed.get("thinking")),
        }
    return None


def _require_agent_capabilities(provider: str, payload: dict[str, Any]) -> dict[str, Any]:
    caps = _infer_capabilities(provider, payload)
    if not caps or not caps.get("supports_tool_calling"):
        raise AgentCapabilityProbeError(_AGENT_CAPABILITY_MESSAGE)
    return caps


def _stub_agent_capabilities() -> dict[str, Any]:
    return {
        "supports_tool_calling": True,
        "supports_streaming": False,
        "supports_thinking": False,
    }


def _platform_agent_capabilities() -> dict[str, Any]:
    return {
        "supports_tool_calling": True,
        "supports_streaming": True,
        "supports_thinking": False,
    }


def _validate_openai_compatible(
    api_key: str,
    model_id: str,
    *,
    base_url: str,
    extra_headers: dict[str, str] | None = None,
    auth_style: str = "bearer",
    completions_path: str = "/chat/completions",
    azure_api_version: str = "2024-06-01",
    for_cloud: bool = True,
) -> dict[str, Any]:
    path = completions_path.replace("{model_id}", model_id)
    if not path.startswith("/"):
        path = f"/{path}"
    url = f"{base_url.rstrip('/')}{path}"
    headers = (
        {"api-key": api_key}
        if auth_style == "azure-api-key"
        else {"Authorization": f"Bearer {api_key}"}
    )
    if extra_headers:
        headers.update(extra_headers)
    if auth_style == "azure-api-key":
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}api-version={azure_api_version}"
    with build_llm_http_client(for_cloud=for_cloud) as client:
        response = client.post(
            url,
            headers=headers,
            json={
                "model": model_id,
                "messages": [
                    {
                        "role": "user",
                        "content": "Call agent_capability_probe with ready=true.",
                    }
                ],
                "tools": [_PROBE_TOOL_OPENAI],
                "tool_choice": "auto",
                "max_tokens": 128,
            },
        )
        response.raise_for_status()
        payload = response.json()
        try:
            return _require_agent_capabilities("openai", payload)
        except AgentCapabilityProbeError:
            response = client.post(
                url,
                headers=headers,
                json={
                    "model": model_id,
                    "messages": [{"role": "user", "content": _AGENT_JSON_PROBE_USER}],
                    "max_tokens": 256,
                },
            )
            response.raise_for_status()
            return _require_agent_capabilities("openai", response.json())


def _validate_openai(api_key: str, model_id: str) -> dict[str, Any]:
    return _validate_openai_compatible(
        api_key,
        model_id,
        base_url="https://api.openai.com/v1",
    )


def _validate_anthropic(api_key: str, model_id: str) -> dict[str, Any]:
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
                "max_tokens": 128,
                "tools": [_PROBE_TOOL_ANTHROPIC],
                "messages": [
                    {
                        "role": "user",
                        "content": "Call agent_capability_probe with ready=true.",
                    }
                ],
            },
        )
        response.raise_for_status()
        payload = response.json()
        try:
            return _require_agent_capabilities("anthropic", payload)
        except AgentCapabilityProbeError:
            response = client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model_id,
                    "max_tokens": 256,
                    "messages": [{"role": "user", "content": _AGENT_JSON_PROBE_USER}],
                },
            )
            response.raise_for_status()
            return _require_agent_capabilities("anthropic", response.json())


def _validate_ollama(base_url: str, model_id: str, api_key: str = "") -> dict[str, Any]:
    resolved = resolve_local_service_url(base_url)
    headers: dict[str, str] | None = None
    if api_key:
        headers = {"Authorization": f"Bearer {api_key}"}
    fallback_error: Exception | None = None
    try:
        return _validate_openai_compatible(
            api_key or "ollama",
            model_id,
            base_url=f"{resolved}/v1",
            for_cloud=False,
        )
    except AgentCapabilityProbeError as exc:
        fallback_error = exc
    except Exception as exc:
        fallback_error = exc
    with build_llm_http_client(for_cloud=False) as client:
        response = client.post(
            f"{resolved}/api/chat",
            headers=headers,
            json={
                "model": model_id,
                "messages": [{"role": "user", "content": _AGENT_JSON_PROBE_USER}],
                "stream": False,
            },
        )
        response.raise_for_status()
        try:
            return _require_agent_capabilities("openai", response.json())
        except AgentCapabilityProbeError:
            if isinstance(fallback_error, AgentCapabilityProbeError):
                raise fallback_error
            if fallback_error is not None:
                raise fallback_error
            raise


def _validate_gemini(api_key: str, model_id: str, base_url: str = "") -> dict[str, Any]:
    spec = get_provider_spec("google_gemini")
    root = (base_url or (spec.default_base_url if spec else "")).rstrip("/")
    with build_llm_http_client(for_cloud=True) as client:
        response = client.post(
            f"{root}/models/{model_id}:generateContent",
            params={"key": api_key},
            json={
                "contents": [{"parts": [{"text": "Call agent_capability_probe with ready=true."}]}],
                "tools": [{
                    "functionDeclarations": [{
                        "name": "agent_capability_probe",
                        "description": "Verify tool-calling for the migration agent.",
                        "parameters": {
                            "type": "object",
                            "properties": {"ready": {"type": "boolean"}},
                            "required": ["ready"],
                        },
                    }],
                }],
            },
        )
        response.raise_for_status()
        payload = response.json()
        try:
            return _require_agent_capabilities("google_gemini", payload)
        except AgentCapabilityProbeError:
            response = client.post(
                f"{root}/models/{model_id}:generateContent",
                params={"key": api_key},
                json={
                    "contents": [{"parts": [{"text": _AGENT_JSON_PROBE_USER}]}],
                    "generationConfig": {"maxOutputTokens": 256},
                },
            )
            response.raise_for_status()
            return _require_agent_capabilities("google_gemini", response.json())


def _validate_ambient_platform(body: dict[str, Any]) -> dict[str, Any]:
    from ado2gh.api.credentials.cloud_credential_probe import probe_provider
    from ado2gh.api.credentials.cloud_credentials_store import CloudCredentialsStore

    provider = body.get("provider", "")
    ambient_map = {"bedrock": "aws", "foundry": "foundry", "vertex": "gcp"}
    ambient = ambient_map.get(provider, "")
    store = CloudCredentialsStore()
    source = store.get_source(ambient)
    if not source or source.status != "approved":
        return _result(
            "failed",
            category="credentials",
            message="Cloud credentials must be approved before validating platform-supplied models.",
        )
    probe = probe_provider(ambient, source)
    if probe.get("status") == "passed":
        return _result(
            "passed",
            message="Platform credentials verified for agent-capable chat models.",
            capabilities=_platform_agent_capabilities(),
        )
    return _result(
        "failed",
        category=probe.get("category"),
        message=probe.get("message", "Validation failed."),
    )


def _run_provider_check(body: dict[str, Any]) -> dict[str, Any]:
    provider = body.get("provider", "")
    model_id = body.get("model_id", "")
    api_key = (body.get("api_key") or "").strip()
    base_url = body.get("base_url") or ""
    try:
        spec = get_provider_spec(provider)
        capabilities: dict[str, Any] | None = None
        if provider == "openai":
            if not api_key:
                return _result("failed", category="credentials", message="API key is required.")
            capabilities = _validate_openai(api_key, model_id)
        elif provider == "anthropic":
            if not api_key:
                return _result("failed", category="credentials", message="API key is required.")
            capabilities = _validate_anthropic(api_key, model_id)
        elif provider == "github_copilot":
            if not api_key:
                return _result(
                    "failed",
                    category="credentials",
                    message="GitHub PAT with models:read scope is required.",
                )
            if not spec:
                return _result("failed", category="model_not_found", message="Unknown provider.")
            capabilities = _validate_openai_compatible(
                api_key,
                model_id,
                base_url=base_url or spec.default_base_url,
                extra_headers=spec.runtime_headers(),
            )
        elif provider == "openrouter":
            if not api_key:
                return _result("failed", category="credentials", message="API key is required.")
            if not spec:
                return _result("failed", category="model_not_found", message="Unknown provider.")
            capabilities = _validate_openai_compatible(
                api_key,
                model_id,
                base_url=base_url or spec.default_base_url,
                extra_headers=spec.runtime_headers(),
            )
        elif provider == "azure_openai":
            if not api_key:
                return _result("failed", category="credentials", message="API key is required.")
            if not base_url:
                return _result(
                    "failed",
                    category="network",
                    message="Azure resource base URL is required (e.g. https://{resource}.openai.azure.com/openai).",
                )
            if not spec:
                return _result("failed", category="model_not_found", message="Unknown provider.")
            capabilities = _validate_openai_compatible(
                api_key,
                model_id,
                base_url=base_url,
                auth_style=spec.auth_style,
                completions_path=spec.completions_path,
                azure_api_version=spec.azure_api_version,
            )
        elif provider == "google_gemini":
            if not api_key:
                return _result("failed", category="credentials", message="API key is required.")
            capabilities = _validate_gemini(api_key, model_id, base_url)
        elif provider == "ollama":
            if not base_url:
                return _result("failed", category="network", message="Base URL is required.")
            capabilities = _validate_ollama(base_url, model_id, api_key)
        elif provider in ("stub", "offline"):
            return _result(
                "passed",
                message="Stub provider requires no external validation.",
                capabilities=_stub_agent_capabilities(),
            )
        elif provider in ("bedrock", "foundry", "vertex"):
            return _validate_ambient_platform(body)
        else:
            return _result("failed", category="model_not_found", message=f"Unsupported provider: {provider}")
        return _result(
            "passed",
            message="Model supports agent tool use.",
            capabilities=capabilities,
        )
    except AgentCapabilityProbeError as exc:
        return _result("failed", category="agent_incapable", message=str(exc))
    except Exception as exc:
        category, message = _classify_error(exc)
        return _result("failed", category=category, message=message)


def _single_flight_validate(key: str, runner: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    with _validate_lock:
        if key in _validate_inflight:
            waiter = _validate_inflight[key]
        else:
            event = threading.Event()
            _validate_inflight[key] = event
            waiter = None

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
            caps = result.get("capabilities")
            if caps:
                model_store.upsert({"capabilities": caps}, model_id=model_id)
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
