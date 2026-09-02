"""Orchestrator chat messages — always Markdown for the UI (react-markdown).

No flow-specific templates. The orchestrator LLM composes user-facing text from
arbitrary structured context; offline fallbacks use generic structured rendering.
"""
from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

CHAT_CONTENT_FORMAT = "markdown"

# Broad emoji / symbol cleanup for operator-facing chat (LLMs often ignore prompts).
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U00002300-\U000023FF"
    "\U0001F600-\U0001F64F"
    "\u2705\u2713\u2714\u2716\u2717\u2718"
    "\u274c\u274e\u2753-\u2755"
    "\u2b50\u2b55"
    "\u25cb\u25cf\u25c9"
    "\u2298"
    "\u2713\u2714\u2716\u2717\u2718"
    "\u25c6\u25c7"
    "]+",
    flags=re.UNICODE,
)


def strip_emojis(text: str) -> str:
    """Remove emojis and common status symbols from operator-facing text."""
    cleaned = _EMOJI_PATTERN.sub("", text or "")
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def normalize_chat_markdown(text: str) -> str:
    """Normalize assistant chat content for Markdown rendering."""
    content = (text or "").strip()
    if content.startswith("{") and '"reply"' in content:
        try:
            parsed = json.loads(content)
            if isinstance(parsed.get("reply"), str):
                content = parsed["reply"].strip()
        except Exception:
            pass
    return strip_emojis(content)


def orchestrator_chat_already_published(session: dict[str, Any], reply: str) -> bool:
    """True when this orchestrator reply is already in session chat messages."""
    normalized = normalize_chat_markdown(reply)
    if not normalized:
        return False
    for msg in reversed(session.get("messages", [])[-12:]):
        if msg.get("role") != "assistant":
            continue
        if msg.get("subagent") not in (None, "orchestrator"):
            continue
        if normalize_chat_markdown(str(msg.get("content", ""))) == normalized:
            return True
    return False


def structured_context_to_markdown(
    data: Any,
    *,
    heading: str | None = None,
    max_depth: int = 4,
) -> str:
    """Generic structured data → Markdown (no migration-specific layout rules)."""

    def _render(value: Any, depth: int) -> list[str]:
        if depth > max_depth:
            return [str(value)]
        if value is None:
            return ["_none_"]
        if isinstance(value, bool):
            return [str(value).lower()]
        if isinstance(value, (int, float, str)):
            return [str(value)]
        if isinstance(value, list):
            if not value:
                return ["- _(empty)_"]
            lines: list[str] = []
            for item in value[:30]:
                if isinstance(item, (dict, list)):
                    lines.append(f"- {_render(item, depth + 1)[0]}")
                    for sub in _render(item, depth + 1)[1:]:
                        lines.append(f"  {sub}")
                else:
                    lines.append(f"- {item}")
            if len(value) > 30:
                lines.append(f"- …and {len(value) - 30} more")
            return lines
        if isinstance(value, dict):
            if not value:
                return ["_(empty)_"]
            lines = []
            for key, item in list(value.items())[:40]:
                if isinstance(item, (dict, list)):
                    lines.append(f"**{key}**")
                    lines.extend(_render(item, depth + 1))
                else:
                    lines.append(f"- **{key}**: {item}")
            return lines
        return [str(value)]

    parts: list[str] = []
    if heading:
        parts.append(f"### {heading}")
        parts.append("")
    parts.extend(_render(data, 0))
    return strip_emojis("\n".join(parts).strip())


def _failure_detail_line(failure: Any) -> tuple[str, str]:
    """Return (title, detail) for one validation failure record."""
    if not isinstance(failure, dict):
        text = str(failure).strip() or "Unknown validation error"
        return ("Validation error", text)

    repo = str(failure.get("repo") or "").strip()
    scope = str(failure.get("scope") or "").strip()
    title_parts = [p for p in (repo, scope) if p]
    title = " — ".join(title_parts) if title_parts else "Validation error"

    detail = str(
        failure.get("specific_failure")
        or failure.get("error")
        or failure.get("failure")
        or failure.get("message")
        or "Validation check failed."
    ).strip()
    remediation = str(
        failure.get("recommended_remediation")
        or failure.get("remediation")
        or ""
    ).strip()
    if remediation and remediation not in detail:
        detail = f"{detail} {remediation}".strip()
    return (title, detail)


def format_validation_failure_message(
    failures: list[Any],
    *,
    validation_result: dict[str, Any] | None = None,
    executor_result: dict[str, Any] | None = None,
    session: dict[str, Any] | None = None,
) -> str:
    """Deterministic operator-facing summary when migration validation fails."""
    session = session or {}
    lines = ["### Migration could not complete", ""]

    repo_id = (
        session.get("plan_repository_id")
        or (validation_result or {}).get("repository_id")
        or ""
    )
    run_id = str(
        session.get("run_id")
        or session.get("pipeline_run_id")
        or (executor_result or {}).get("pipeline_run_id")
        or ""
    ).strip()
    dry_run = bool(
        (executor_result or {}).get("dry_run", session.get("dry_run", True))
    )
    mode = "dry-run" if dry_run else "live"

    if repo_id:
        lines.append(f"Repository: `{repo_id}` ({mode})")
    else:
        lines.append(f"Mode: {mode}")
    if run_id:
        lines.append(f"Pipeline run: `{run_id}` — check **Settings → History** for step details.")
    lines.append("")

    pipeline_run = session.get("pipeline_run_snapshot")
    if isinstance(pipeline_run, dict):
        pipeline_lines: list[str] = []
        for warning in extract_pipeline_step_warnings(pipeline_run):
            step = warning.get("label") or warning.get("step_id") or "pipeline"
            for text in warning.get("warnings") or []:
                if text:
                    pipeline_lines.append(f"- **{step}**: {normalize_pipeline_warning(str(text))}")
            summary = str(warning.get("summary") or "").strip()
            if summary and not warning.get("warnings"):
                pipeline_lines.append(f"- **{step}**: {normalize_pipeline_warning(summary)}")
        for step in pipeline_run.get("steps") or []:
            if not isinstance(step, dict):
                continue
            if str(step.get("status") or "").lower() != "failed":
                continue
            step_label = str(step.get("label") or step.get("id") or "pipeline step")
            step_msg = str(step.get("message") or "Step failed").strip()
            line = f"- **{step_label}**: {normalize_pipeline_warning(step_msg.split(chr(10))[0])}"
            if line not in pipeline_lines:
                pipeline_lines.append(line)
        if pipeline_lines:
            lines.append("**Pipeline step issues**")
            lines.extend(pipeline_lines)
            lines.append("")

    if failures:
        lines.append("**What failed**")
        seen: set[str] = set()
        for failure in failures[:12]:
            title, detail = _failure_detail_line(failure)
            key = f"{title}|{detail}"
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- **{title}**: {detail}")
        if len(failures) > 12:
            lines.append(f"- …and {len(failures) - 12} more issue(s)")
    else:
        lines.append("The validator reported a failure but no detailed reason was recorded.")
        lines.append("Open **Settings → History**, select the pipeline run above, and review the failed step.")

    lines.extend([
        "",
        "**Next steps**",
        "- Fix the issue above (credentials, repo lock, or pipeline step error), then confirm the plan again.",
        "- If you intended a **live** run, ensure live execution is approved under Settings → Agent.",
    ])
    return normalize_chat_markdown("\n".join(lines))


async def compose_orchestrator_chat_message(
    state: dict[str, Any],
    session: dict[str, Any],
    *,
    instruction: str,
    context: dict[str, Any] | None = None,
) -> str:
    """Compose a user-facing chat message in Markdown via the orchestrator LLM."""
    from ado2gh.agents.migration_agent.prompts import get_prompt

    llm = state.get("llm")
    llm_unconfigured = state.get("llm_unconfigured", False)
    payload = dict(context or {})
    if "migration_plan" in payload:
        from ado2gh.agents.migration_agent.hitl.blockers import sanitize_plan_for_operator_view

        plan = payload.get("migration_plan")
        if isinstance(plan, dict):
            payload["migration_plan"] = sanitize_plan_for_operator_view(plan, session)
    if session.get("migration_plan") and "migration_plan" not in payload:
        from ado2gh.agents.migration_agent.hitl.blockers import sanitize_plan_for_operator_view

        plan = session.get("migration_plan")
        if isinstance(plan, dict):
            payload["migration_plan"] = sanitize_plan_for_operator_view(plan, session)

    if llm and not llm_unconfigured:
        system = (
            get_prompt("orchestrator")
            + "\n\n## Compose user chat message\n"
            "Write ONLY the Markdown body for the operator chat (no JSON wrapper).\n"
            "Use `###` headings, `-` bullet lists, and `` `inline code` `` for ids.\n"
            "Keep it concise and scannable.\n"
            "**Language:** English only. Do not use Chinese or any non-English text.\n"
            "**Tone:** Plain text only — do not use emojis or emoticons.\n\n"
            f"**Instruction:** {instruction}\n"
        )
        if payload:
            system += f"\n**Context (JSON):**\n```json\n{json.dumps(payload, default=str)[:8000]}\n```\n"
        try:
            response = await llm.ainvoke([
                SystemMessage(content=system),
                HumanMessage(content=instruction),
            ])
            raw = response.content if hasattr(response, "content") else str(response)
            if isinstance(raw, list):
                raw = "".join(
                    block.get("text", "") if isinstance(block, dict) else str(block)
                    for block in raw
                )
            normalized = normalize_chat_markdown(str(raw))
            if normalized:
                return normalized
        except Exception:
            pass

    return structured_context_to_markdown(payload, heading=instruction)


SCOPE_LABELS = {
    "repo": "Repository migration",
    "pipelines": "Pipeline conversion",
    "secrets": "Secret / service-connection mapping",
    "wiki": "Wiki migration",
    "branch_policies": "Branch policies",
    "work_items": "Work items → GitHub issues",
}


def normalize_pipeline_warning(text: str) -> str:
    """Clean operator-facing pipeline warning text (strip symbols, keep repo prefix)."""
    cleaned = strip_emojis(str(text or "")).strip()
    cleaned = cleaned.replace(": ⚠ ", ": ").replace("⚠ ", "")
    cleaned = re.sub(r":\s{2,}", ": ", cleaned)
    return cleaned


def extract_pipeline_step_warnings(
    pipeline_run: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Warnings from accelerator pipeline run steps (analyze_deps, migrate_repos, etc.)."""
    if not isinstance(pipeline_run, dict):
        return []
    out: list[dict[str, Any]] = []
    for step in pipeline_run.get("steps") or []:
        if not isinstance(step, dict):
            continue
        status = str(step.get("status") or "").lower()
        result = step.get("result") if isinstance(step.get("result"), dict) else {}
        structured = [
            normalize_pipeline_warning(w)
            for w in (result.get("warnings") or [])
            if str(w).strip()
        ]
        message = str(step.get("message") or "").strip()
        bullet_warnings: list[str] = []
        for line in message.splitlines():
            stripped = line.strip()
            if stripped.startswith(("•", "-", "*")):
                bullet_warnings.append(
                    normalize_pipeline_warning(stripped.lstrip("•-* ").strip())
                )

        warnings = structured or bullet_warnings
        step_id = str(step.get("id") or "")
        summary = message.split("\n", 1)[0].strip() if message else ""
        # migrate_repos should surface repo migration notes only — not SC count summaries
        if step_id == "migrate_repos" and not warnings:
            if "service connection" in summary.lower():
                continue
            if status not in ("warn", "failed"):
                continue

        if status not in ("warn", "failed") and not warnings:
            continue
        if status not in ("warn", "failed") and "warning" not in message.lower():
            continue

        out.append({
            "step_id": step_id,
            "label": step.get("label") or step.get("id") or "step",
            "status": status or "warn",
            "summary": normalize_pipeline_warning(summary),
            "warnings": warnings,
        })
    return out


def format_pipeline_warnings_markdown(step_warnings: list[dict[str, Any]]) -> str:
    if not step_warnings:
        return ""
    lines = ["### Pipeline warnings", ""]
    for step in step_warnings:
        label = step.get("label") or "Step"
        status = step.get("status") or "warn"
        lines.append(f"**{label}** — `{status}`")
        warnings = step.get("warnings") or []
        if warnings:
            for warning in warnings:
                lines.append(f"- {warning}")
        else:
            summary = str(step.get("summary") or "").strip()
            if summary:
                lines.append(f"- {summary}")
        lines.append("")
    return strip_emojis("\n".join(lines).strip())


def format_endpoint_skips_markdown(executed_scopes: list[dict[str, Any]]) -> str:
    """List skipped accelerator calls with endpoint paths (deterministic)."""
    skips = [
        entry for entry in executed_scopes
        if str(entry.get("result_status") or "").lower() == "skipped"
        and (
            entry.get("endpoint")
            or "endpoint unavailable" in str(entry.get("message") or "").lower()
        )
    ]
    if not skips:
        return ""
    lines = ["### Skipped accelerator calls", ""]
    for entry in skips:
        label = entry.get("label") or entry.get("scope") or "scope"
        endpoint = entry.get("endpoint") or ""
        repo = entry.get("repo") or ""
        prefix = f"{repo}: " if repo else ""
        if endpoint:
            lines.append(f"- **{label}** — `{endpoint}`")
        else:
            lines.append(f"- **{label}** — {prefix}{entry.get('message', '')}")
        detail = str(entry.get("detail") or "").strip()
        if detail and detail not in str(entry.get("message") or ""):
            lines.append(f"  - {detail}")
    return strip_emojis("\n".join(lines).strip())


def append_completion_extras(reply: str, completion_facts: dict[str, Any]) -> str:
    """Append deterministic pipeline warnings and endpoint skips (LLM must not invent these)."""
    body = (reply or "").rstrip()
    extras: list[str] = []
    skips_md = format_endpoint_skips_markdown(completion_facts.get("executed_scopes") or [])
    if skips_md and "### Skipped accelerator calls" not in body:
        extras.append(skips_md)
    warnings_md = format_pipeline_warnings_markdown(
        completion_facts.get("pipeline_step_warnings") or [],
    )
    if warnings_md and "### Pipeline warnings" not in body:
        extras.append(warnings_md)
    if not extras:
        return body
    return f"{body}\n\n" + "\n\n".join(extras)


async def fetch_pipeline_run_for_completion(
    accel_get: Any,
    run_id: str,
    *,
    session_token: str | None = None,
    max_wait_seconds: float = 45.0,
) -> dict[str, Any] | None:
    """Fetch pipeline run; brief poll so async monitor steps finish before summary."""
    if not accel_get or not run_id:
        return None

    import asyncio
    import time

    terminal = frozenset({
        "completed", "failed", "cancelled", "dry_run_complete",
    })
    deadline = time.monotonic() + max_wait_seconds
    latest: dict[str, Any] | None = None

    while True:
        try:
            resp = await accel_get(
                f"/v1/pipeline/runs/{run_id}",
                session_token=session_token,
            )
            if isinstance(resp, dict):
                run = resp.get("run")
                if isinstance(run, dict):
                    latest = run
                    status = str(run.get("status") or "").lower()
                    if status in terminal:
                        return run
        except Exception:
            if latest:
                return latest
            return None

        if time.monotonic() >= deadline:
            return latest
        await asyncio.sleep(1.5)


_PIPELINE_STEP_SCOPES = {
    "migrate_repos": "repo",
    "convert_pipelines": "pipelines",
    "convert_metadata": "branch_policies",
}


async def fetch_migration_status_for_repo(
    accel_get: Any,
    repository_id: str,
    *,
    session_token: str | None = None,
) -> dict[str, Any] | None:
    """Load per-repo migration state from StateDB via accelerator (same source as Monitor)."""
    if not accel_get or not repository_id:
        return None
    try:
        report = await accel_get("/v1/migration/status", session_token=session_token)
    except Exception:
        return None
    if not isinstance(report, dict):
        return None
    repo_id = repository_id.strip()
    for entry in report.get("all_repos") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("ado_repo") == repo_id:
            return entry
    short = repo_id.split("/")[-1] if "/" in repo_id else repo_id
    for entry in report.get("all_repos") or []:
        if not isinstance(entry, dict):
            continue
        ado_repo = str(entry.get("ado_repo") or "")
        if ado_repo.endswith(f"/{short}") or ado_repo == short:
            return entry
    for outcome in report.get("recent_run_outcomes") or []:
        if not isinstance(outcome, dict):
            continue
        if outcome.get("repo") == repo_id:
            return {"ado_repo": repo_id, "recent_run_outcome": outcome}
    return None


def build_executed_scopes_from_pipeline_run(
    pipeline_run: dict[str, Any] | None,
    repo_id: str,
) -> list[dict[str, Any]]:
    """Map accelerator pipeline step results to executed scope rows (Monitor source)."""
    from ado2gh.agents.migration_agent.nodes.executor.scope import scope_accelerator_endpoint

    if not isinstance(pipeline_run, dict) or not repo_id:
        return []
    executed: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def _add(scope: str, *, status: str, message: str, detail: str = "") -> None:
        key = (repo_id, scope)
        if key in seen:
            return
        seen.add(key)
        text = (message or detail or "").strip()
        executed.append({
            "repo": repo_id,
            "scope": scope,
            "label": SCOPE_LABELS.get(scope, scope),
            "result_status": status,
            "message": text,
            "endpoint": scope_accelerator_endpoint(scope),
            "detail": (detail or message or "").strip(),
            "source": "pipeline_run",
        })

    for step in pipeline_run.get("steps") or []:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("id") or "")
        default_scope = _PIPELINE_STEP_SCOPES.get(step_id)
        if not default_scope:
            continue
        result = step.get("result") if isinstance(step.get("result"), dict) else {}
        step_status = str(step.get("status") or "").lower()
        matched_repo = False
        for detail in result.get("repo_details") or []:
            if not isinstance(detail, dict):
                continue
            detail_repo = str(detail.get("repo") or "")
            if detail_repo and detail_repo != repo_id:
                continue
            matched_repo = True
            for scope_row in detail.get("scopes") or []:
                if not isinstance(scope_row, dict):
                    continue
                scope_name = str(scope_row.get("scope") or default_scope)
                msg = str(
                    scope_row.get("error")
                    or scope_row.get("detail")
                    or detail.get("summary")
                    or ""
                ).strip()
                status = str(scope_row.get("status") or detail.get("status") or step_status)
                _add(scope_name, status=status, message=msg, detail=msg)
            errors = [str(e) for e in (detail.get("errors") or []) if str(e).strip()]
            if errors and not detail.get("scopes"):
                _add(
                    default_scope,
                    status=str(detail.get("status") or step_status),
                    message="; ".join(errors),
                )
        if not matched_repo and step_status in ("failed", "warn"):
            msg = str(step.get("message") or "").strip()
            if repo_id in msg or not result.get("repo_details"):
                _add(default_scope, status=step_status, message=msg)
    return executed


def _executed_scopes_from_executor_result(
    executor_result: dict[str, Any],
) -> list[dict[str, Any]]:
    """Fallback: scope rows from in-session executor_result."""
    from ado2gh.agents.migration_agent.nodes.executor.scope import scope_accelerator_endpoint

    executed: list[dict[str, Any]] = []
    for repo_result in executor_result.get("per_repo_results") or []:
        if not isinstance(repo_result, dict):
            continue
        repo_id = repo_result.get("repo", "")
        for scope_name, scope_result in (repo_result.get("scopes") or {}).items():
            if not isinstance(scope_result, dict):
                continue
            endpoint = str(
                scope_result.get("endpoint") or scope_accelerator_endpoint(str(scope_name))
            )
            detail = str(scope_result.get("detail") or scope_result.get("error") or "").strip()
            message = str(scope_result.get("message") or detail or "").strip()
            executed.append({
                "repo": repo_id,
                "scope": scope_name,
                "label": SCOPE_LABELS.get(str(scope_name), str(scope_name)),
                "result_status": scope_result.get("status", "unknown"),
                "message": message,
                "endpoint": endpoint,
                "detail": detail,
                "source": "executor_result",
            })
    return executed


def build_migration_completion_facts(
    migration_plan: dict[str, Any],
    executor_result: dict[str, Any],
    validation_result: dict[str, Any],
    session: dict[str, Any],
    *,
    pipeline_run: dict[str, Any] | None = None,
    database_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Structured scope summary for completion messages (no LLM invention)."""
    from ado2gh.agents.migration_agent.hitl.blockers import sanitize_plan_for_operator_view

    plan = sanitize_plan_for_operator_view(migration_plan, session)
    work_items = plan.get("work_items") or []
    dry_run = bool(executor_result.get("dry_run", session.get("dry_run", True)))

    planned: list[dict[str, Any]] = []
    executed: list[dict[str, Any]] = []
    not_in_plan: list[str] = []

    all_scope_names = set(SCOPE_LABELS)
    scopes_in_plan: set[str] = set()

    for wi in work_items:
        if not isinstance(wi, dict):
            continue
        scope = str(wi.get("scope") or "")
        if not scope:
            continue
        scopes_in_plan.add(scope)
        status = str(wi.get("status") or "ready")
        blocker = str(wi.get("blocker") or "")
        if status == "skipped" and "not included" in blocker.lower():
            continue
        entry = {
            "scope": scope,
            "label": SCOPE_LABELS.get(scope, scope),
            "status": status,
            "blocker": blocker,
        }
        planned.append(entry)

    repository_id = session.get("plan_repository_id") or plan.get("repository_id") or ""
    if pipeline_run is None and isinstance(session.get("pipeline_run_snapshot"), dict):
        pipeline_run = session["pipeline_run_snapshot"]

    executed = build_executed_scopes_from_pipeline_run(pipeline_run, str(repository_id))
    if not executed:
        executed = _executed_scopes_from_executor_result(executor_result)

    for scope in sorted(all_scope_names - scopes_in_plan):
        not_in_plan.append(SCOPE_LABELS.get(scope, scope))

    secret_deps = any(p["scope"] == "secrets" for p in planned)
    run_id = session.get("run_id") or session.get("pipeline_run_id")
    step_warnings = extract_pipeline_step_warnings(pipeline_run)
    data_sources: list[str] = []
    if pipeline_run:
        data_sources.append("pipeline_run")
    if database_status:
        data_sources.append("migration_status")
    if not pipeline_run and executed:
        data_sources.append("executor_result")
    return {
        "dry_run": dry_run,
        "validation_passed": bool(validation_result.get("passed")),
        "repository_id": repository_id,
        "run_id": run_id,
        "run_name": (pipeline_run or {}).get("name") if isinstance(pipeline_run, dict) else None,
        "planned_scopes": planned,
        "executed_scopes": executed,
        "database_status": database_status,
        "data_sources": data_sources,
        "secret_dependencies_detected": secret_deps,
        "scopes_not_in_plan": not_in_plan,
        "pipeline_step_warnings": step_warnings,
    }


async def compose_migration_completion_message(
    state: dict[str, Any],
    session: dict[str, Any],
    *,
    validation_result: dict[str, Any],
    executor_result: dict[str, Any],
    migration_plan: dict[str, Any],
) -> str:
    """LLM-composed completion summary with guardrails (English, no emojis, no hidden blockers)."""
    from ado2gh.agents.migration_agent.hitl.blockers import sanitize_plan_for_operator_view

    pipeline_run = await fetch_pipeline_run_for_completion(
        state.get("accel_get"),
        str(session.get("run_id") or session.get("pipeline_run_id") or ""),
        session_token=state.get("session_token"),
    )
    if pipeline_run:
        session["pipeline_run_snapshot"] = pipeline_run

    repository_id = str(
        session.get("plan_repository_id")
        or migration_plan.get("repository_id")
        or ""
    )
    database_status = await fetch_migration_status_for_repo(
        state.get("accel_get"),
        repository_id,
        session_token=state.get("session_token"),
    )

    completion_facts = build_migration_completion_facts(
        migration_plan,
        executor_result,
        validation_result,
        session,
        pipeline_run=pipeline_run,
        database_status=database_status,
    )
    context = {
        "completion_facts": completion_facts,
        "validation_result": validation_result,
        "executor_result": executor_result,
        "migration_plan": sanitize_plan_for_operator_view(migration_plan, session),
        "declined_resolutions": session.get("declined_blocker_resolutions") or [],
        "run_id": session.get("run_id"),
        "repository_id": session.get("plan_repository_id"),
        "dry_run": executor_result.get("dry_run", session.get("dry_run", True)),
    }
    reply = await compose_orchestrator_chat_message(
        state,
        session,
        instruction=(
            "Summarize the completed migration for the operator using ONLY completion_facts. "
            "executed_scopes and pipeline_step_warnings come from the accelerator pipeline run "
            "(same data as Migration Monitor). database_status comes from StateDB via /v1/migration/status. "
            "List scopes from planned_scopes and what happened in executed_scopes. "
            "For each executed scope use the exact endpoint string from executed_scopes — never invent paths "
            "(e.g. do not write /api/migrate/repo; use the endpoint field verbatim). "
            "If database_status shows rollup_status failed or scope errors, mention that platform state "
            "matches the pipeline outcome. "
            "If dry_run is true, say simulation only — no GitHub/ADO mutations. "
            "Do NOT include a Monitor link or run_id hyperlink. "
            "Do NOT write Pipeline warnings or Skipped accelerator calls sections — "
            "they are appended separately. "
            "In dry_run, do NOT tell the operator to fix accelerator endpoints for skipped scopes — "
            "note that skipped calls are listed below with endpoint paths. "
            "Use a **Next steps** section only for genuine follow-ups (e.g. switch to live mode). "
            "Do NOT mention scopes in scopes_not_in_plan — they were not part of this migration. "
            "Never say scopes were 'excluded' from the run. "
            "Only mention secret / service-connection mapping when "
            "secret_dependencies_detected is true; otherwise omit secrets entirely. "
            "If dependency analysis found no secrets, do not ask for GitHub secret names. "
            "Never invent generic secret placeholders like ADO_SERVICE_CONNECTION_1. "
            "Never label items as 'pending tool integration' or 'manual setup required' "
            "unless completion_facts shows a scope status of blocked with a blocker message."
        ),
        context=context,
    )
    return append_completion_extras(reply, completion_facts)
