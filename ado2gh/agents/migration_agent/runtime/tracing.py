"""LangSmith / LangChain tracing helpers (2026 observability best practice).

Enable with environment variables (no code changes required in production):
  LANGCHAIN_TRACING_V2=true
  LANGCHAIN_API_KEY=...
  LANGCHAIN_PROJECT=ado2gh-migration-agent
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def configure_tracing_from_env() -> bool:
    """Log tracing status at agent startup. LangChain reads env vars automatically."""
    enabled = os.environ.get("LANGCHAIN_TRACING_V2", "").lower() in ("1", "true", "yes")
    if enabled:
        project = os.environ.get("LANGCHAIN_PROJECT", "ado2gh-migration-agent")
        logger.info("LangSmith tracing enabled (project=%s)", project)
    return enabled


def graph_run_config(
    thread_id: str,
    *,
    recursion_limit: int,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build LangGraph invoke config with thread_id for checkpoint + trace correlation."""
    run_metadata = {"thread_id": thread_id, "session_id": thread_id}
    if metadata:
        run_metadata.update(metadata)
    config: dict[str, Any] = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": recursion_limit,
        "metadata": run_metadata,
    }
    if tags:
        config["tags"] = tags
    return config
