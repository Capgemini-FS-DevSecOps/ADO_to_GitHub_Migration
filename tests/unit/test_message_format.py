"""Tests for generic orchestrator Markdown chat formatting."""
import json

import pytest

from ado2gh.agents.migration_agent.message_format import (
    CHAT_CONTENT_FORMAT,
    append_completion_extras,
    compose_migration_completion_message,
    compose_orchestrator_chat_message,
    extract_pipeline_step_warnings,
    format_endpoint_skips_markdown,
    format_pipeline_warnings_markdown,
    format_validation_failure_message,
    normalize_chat_markdown,
    normalize_pipeline_warning,
    orchestrator_chat_already_published,
    strip_emojis,
    structured_context_to_markdown,
)


def test_chat_content_format_constant():
    assert CHAT_CONTENT_FORMAT == "markdown"


def test_normalize_chat_markdown_unwraps_json_reply():
    wrapped = json.dumps({"reply": "### Hello\n\n- item one"})
    assert normalize_chat_markdown(wrapped) == "### Hello\n\n- item one"


def test_strip_emojis_removes_status_symbols():
    text = "Done ✓ failed ✗ blocked ⊘ ready ○"
    cleaned = strip_emojis(text)
    assert "✓" not in cleaned
    assert "✗" not in cleaned
    assert "⊘" not in cleaned
    assert "○" not in cleaned
    assert "Done" in cleaned


@pytest.mark.asyncio
async def test_compose_migration_completion_message_uses_llm_guardrails():
    class FakeLLM:
        async def ainvoke(self, messages):
            return type("R", (), {"content": "### Migration complete\n\n- **Validation:** passed"})()

    state = {"llm": FakeLLM(), "llm_unconfigured": False}
    text = await compose_migration_completion_message(
        state,
        {"plan_repository_id": "Proj/app"},
        validation_result={"passed": True},
        executor_result={"dry_run": True, "per_repo_results": []},
        migration_plan={
            "work_items": [
                {"scope": "pipelines", "status": "blocked", "blocker": "inventory"},
            ]
        },
    )
    assert "Migration complete" in text
    assert "inventory" not in text.lower()


def test_structured_context_to_markdown_is_generic():
    text = structured_context_to_markdown(
        {"repos": [{"id": "A", "status": "ready"}], "dry_run": True},
        heading="Migration context",
    )
    assert "### Migration context" in text
    assert "**repos**" in text or "- **dry_run**" in text


def test_orchestrator_chat_already_published():
    session = {
        "messages": [
            {"role": "assistant", "content": "### Done\n\n- item", "subagent": "orchestrator"},
        ],
    }
    assert orchestrator_chat_already_published(session, "### Done\n\n- item")
    assert not orchestrator_chat_already_published(session, "### Other")


@pytest.mark.asyncio
async def test_compose_orchestrator_chat_message_llm_fallback():
    class FakeLLM:
        async def ainvoke(self, messages):
            return type("R", (), {"content": "### Done\n\n- repo `A`: skipped"})()

    state = {"llm": FakeLLM(), "llm_unconfigured": False}
    text = await compose_orchestrator_chat_message(
        state,
        {},
        instruction="Summarize results.",
        context={"passed": True},
    )
    assert "### Done" in text


@pytest.mark.asyncio
async def test_compose_orchestrator_chat_message_offline_fallback():
    state = {"llm": None, "llm_unconfigured": True}
    text = await compose_orchestrator_chat_message(
        state,
        {},
        instruction="Summarize results.",
        context={"status": "complete"},
    )
    assert "### Summarize results." in text
    assert "**status**" in text


def test_normalize_pipeline_warning_strips_symbol_keeps_repo_prefix():
    raw = "proj/repo: ⚠ Service connection 'sp-owner' — create matching GitHub secret"
    assert normalize_pipeline_warning(raw) == (
        "proj/repo: Service connection 'sp-owner' — create matching GitHub secret"
    )


def test_extract_pipeline_step_warnings_from_analyze_deps():
    run = {
        "steps": [
            {
                "id": "analyze_deps",
                "label": "Analyze dependencies",
                "status": "warn",
                "message": (
                    "Dependencies resolved: 1 repo(s) (0 dependencies) — 2 warning(s):\n"
                    "  • repo/a: ⚠ Service connection 'sp-owner' — create matching GitHub secret\n"
                    "  • repo/a: ⚠ Service connection '${{params.name}}' — create matching GitHub secret"
                ),
                "result": {
                    "warnings": [
                        "repo/a: ⚠ Service connection 'sp-owner' — create matching GitHub secret",
                        "repo/a: ⚠ Service connection '${{params.name}}' — create matching GitHub secret",
                    ]
                },
            },
            {
                "id": "migrate_repos",
                "label": "Migrate repository contents",
                "status": "warn",
                "message": "Dry run: 1 repo(s) validated — 2 service connection(s)",
            },
            {
                "id": "migrate_repos",
                "label": "Migrate repository contents",
                "status": "warn",
                "message": "Dry run: 1 repo(s) validated",
                "result": {
                    "warnings": [
                        "repo/a: Repository size 12.5 MB",
                        "No upstream repo dependencies in migration order",
                    ]
                },
            },
        ],
    }
    warnings = extract_pipeline_step_warnings(run)
    assert len(warnings) == 2
    assert warnings[0]["label"] == "Analyze dependencies"
    assert "sp-owner" in warnings[0]["warnings"][0]
    assert "⚠" not in warnings[0]["warnings"][0]
    assert warnings[1]["warnings"][0].startswith("repo/a: Repository size")


def test_format_pipeline_warnings_markdown():
    warnings_md = format_pipeline_warnings_markdown([
        {
            "label": "Analyze dependencies",
            "status": "warn",
            "warnings": ["repo/a: Service connection 'sp-owner'"],
        },
    ])
    assert "### Pipeline warnings" in warnings_md
    assert "Analyze dependencies" in warnings_md
    assert "sp-owner" in warnings_md


def test_format_endpoint_skips_markdown():
    md = format_endpoint_skips_markdown([
        {
            "repo": "sre-assets-development/OpenTelemetry",
            "label": "Repository migration",
            "scope": "repo",
            "result_status": "skipped",
            "endpoint": "POST /v1/migrate/git-mirror",
            "detail": "404 Client Error for url http://localhost:8080/v1/migrate/git-mirror",
        },
    ])
    assert "### Skipped accelerator calls" in md
    assert "POST /v1/migrate/git-mirror" in md
    assert "404" in md


def test_append_completion_extras_adds_warnings_and_endpoint_skips():
    facts = {
        "executed_scopes": [
            {
                "label": "Repository migration",
                "scope": "repo",
                "result_status": "skipped",
                "endpoint": "POST /v1/migrate/git-mirror",
                "message": "Accelerator endpoint unavailable",
            },
        ],
        "pipeline_step_warnings": [
            {
                "label": "Analyze dependencies",
                "status": "warn",
                "warnings": ["repo/a: warning one"],
            },
        ],
    }
    merged = append_completion_extras("### Done\n\nAll good.", facts)
    assert "Monitor" not in merged
    assert "### Skipped accelerator calls" in merged
    assert "git-mirror" in merged
    assert "### Pipeline warnings" in merged


def test_build_executed_scopes_from_pipeline_run_uses_repo_details():
    from ado2gh.agents.migration_agent.message_format import (
        build_executed_scopes_from_pipeline_run,
        build_migration_completion_facts,
    )

    repo = "azure-pipelines/azure-pipelines-build-migration"
    pipeline_run = {
        "id": "run-1",
        "steps": [
            {
                "id": "migrate_repos",
                "status": "failed",
                "result": {
                    "repo_details": [{
                        "repo": repo,
                        "status": "failed",
                        "errors": ["repo already has active live migration (FR-036)"],
                        "scopes": [{
                            "scope": "repo",
                            "label": "Repository migration",
                            "status": "failed",
                            "error": "repo already has active live migration (FR-036)",
                        }],
                    }],
                },
            },
            {
                "id": "convert_pipelines",
                "status": "failed",
                "result": {
                    "repo_details": [{
                        "repo": repo,
                        "status": "failed",
                        "errors": ["repo already has active live migration (FR-036)"],
                        "scopes": [{
                            "scope": "pipelines",
                            "label": "Pipeline conversion",
                            "status": "failed",
                            "error": "repo already has active live migration (FR-036)",
                        }],
                    }],
                },
            },
        ],
    }
    executed = build_executed_scopes_from_pipeline_run(pipeline_run, repo)
    assert len(executed) == 2
    assert executed[0]["endpoint"] == "POST /v1/migrate/git-mirror"
    assert executed[1]["endpoint"] == "POST /v1/migrate/pipeline-convert"
    assert "FR-036" in executed[0]["message"]
    assert all(row.get("source") == "pipeline_run" for row in executed)

    facts = build_migration_completion_facts(
        {"work_items": [{"scope": "repo", "status": "ready"}, {"scope": "pipelines", "status": "ready"}]},
        {"dry_run": False, "per_repo_results": [{"repo": repo, "scopes": {"repo": {"status": "failed", "endpoint": "POST /api/migrate/repo"}}}]},
        {"passed": False},
        {"plan_repository_id": repo, "dry_run": False},
        pipeline_run=pipeline_run,
        database_status={"ado_repo": repo, "rollup_status": "failed"},
    )
    assert facts["data_sources"] == ["pipeline_run", "migration_status"]
    assert facts["executed_scopes"][0]["endpoint"] == "POST /v1/migrate/git-mirror"
    assert facts["database_status"]["rollup_status"] == "failed"


def test_format_validation_failure_message_includes_failures_and_run():
    text = format_validation_failure_message(
        [{
            "repo": "azure-pipelines/app",
            "scope": "secrets",
            "specific_failure": "Missing service connection mapping for ADO-SC-1",
            "recommended_remediation": "Map the connection in plan notes or Settings.",
        }],
        session={
            "plan_repository_id": "azure-pipelines/app",
            "pipeline_run_id": "6f2aa517-873a-497d-9823-beddcbe5d900",
            "dry_run": True,
        },
    )
    assert "Migration could not complete" in text
    assert "azure-pipelines/app" in text
    assert "6f2aa517" in text
    assert "Missing service connection mapping" in text
    assert "Settings → History" in text
