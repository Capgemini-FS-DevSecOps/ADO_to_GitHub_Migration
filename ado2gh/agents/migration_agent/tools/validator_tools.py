"""LangChain tools for the Validator agent."""
from __future__ import annotations

from typing import Any, Callable
from urllib.parse import quote, urlencode

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ado2gh.agents.migration_agent.tools.orchestrator_tools import (
    AdoApiQueryArgs,
    GitHubApiQueryArgs,
)
from ado2gh.agents.migration_agent.tools.shared_tools import (
    append_shared_tools,
    join_api_path,
    tool_error,
)
from ado2gh.audit.redaction import redact_text
from ado2gh.pipelines.validation import WorkflowValidator

# Repository file content is attacker-controllable and lands in the validator's
# prompt (THR-01-002), so the tool caps it rather than trusting the prompt
# builder's own truncation.
MAX_WORKFLOW_CONTENT_CHARS = 20000


class ValidateWorkflowConversionArgs(BaseModel):
    """Arguments for comparing an ADO pipeline against its converted workflow."""

    ado_pipeline_yaml: str = Field(description="ADO pipeline YAML content")
    github_workflow_yaml: str = Field(description="Converted GitHub Actions workflow YAML")


class ValidateWorkflowSyntaxArgs(BaseModel):
    """Arguments for validating GitHub Actions workflow YAML."""

    workflow_yaml: str = Field(description="GitHub Actions workflow YAML content")


class ListAdoPipelinesArgs(BaseModel):
    """Arguments for listing the ADO pipelines attached to a repository."""

    project: str = Field(description="ADO project name")
    repo_name: str = Field(description="ADO repository name")


class ListGitHubWorkflowsArgs(BaseModel):
    """Arguments for listing the workflow files in a GitHub repository."""

    github_org: str = Field(description="GitHub organization or owner")
    github_repo: str = Field(description="GitHub repository name")
    ref: str = Field(default="HEAD", description="Git ref")


class FetchGitHubWorkflowArgs(BaseModel):
    """Arguments for fetching one workflow file from a GitHub repository."""

    github_org: str = Field(description="GitHub organization or owner")
    github_repo: str = Field(description="GitHub repository name")
    workflow_path: str = Field(description="Path e.g. .github/workflows/ci.yml")
    ref: str = Field(default="HEAD", description="Git ref")


def get_validator_tools(
    accel_get: Callable[..., Any] | None = None,
    session_token: str | None = None,
    session_getter: Callable[[], dict[str, Any]] | None = None,
) -> list[StructuredTool]:
    """Build the tool set for the Validator agent.

    Returns:
        The validator's read-only API tools plus the local YAML comparison and
        syntax checks, with the shared tools prepended.
    """

    async def ado_api_query(endpoint: str) -> dict[str, Any]:
        if not accel_get:
            return {"error": "accelerator_unavailable"}
        try:
            return await accel_get(join_api_path("/v1/ado", endpoint), session_token=session_token)
        except Exception as e:
            return tool_error(e)

    async def github_api_query(endpoint: str) -> dict[str, Any]:
        if not accel_get:
            return {"error": "accelerator_unavailable"}
        try:
            return await accel_get(join_api_path("/v1/github", endpoint), session_token=session_token)
        except Exception as e:
            return tool_error(e)

    async def list_ado_pipelines(project: str, repo_name: str) -> dict[str, Any]:
        if not accel_get:
            return {"error": "accelerator_unavailable"}
        try:
            encoded_project = quote(project, safe="")
            encoded_repo = quote(repo_name, safe="")
            repo_resp = await accel_get(
                join_api_path(
                    "/v1/ado",
                    f"{encoded_project}/_apis/git/repositories/{encoded_repo}?api-version=7.0",
                ),
                session_token=session_token,
            )
            repo_id = str(repo_resp.get("id") or "") if isinstance(repo_resp, dict) else ""
            pipelines_resp = await accel_get(
                join_api_path("/v1/ado", f"{encoded_project}/_apis/pipelines?api-version=7.0"),
                session_token=session_token,
            )
            pipelines: list[dict[str, Any]] = []
            if isinstance(pipelines_resp, dict):
                for row in pipelines_resp.get("value") or []:
                    if not isinstance(row, dict):
                        continue
                    repo = row.get("repository") or {}
                    repo_match = (
                        str(repo.get("name") or "").lower() == repo_name.lower()
                        or (repo_id and str(repo.get("id") or "") == repo_id)
                    )
                    if repo_match:
                        pipelines.append({
                            "id": row.get("id"),
                            "name": row.get("name"),
                            "folder": row.get("folder"),
                        })
            return {
                "project": project,
                "repo_name": repo_name,
                "pipeline_count": len(pipelines),
                "pipelines": pipelines[:50],
            }
        except Exception as e:
            return tool_error(e, project=project, repo_name=repo_name)

    async def list_github_workflows(
        github_org: str,
        github_repo: str,
        ref: str = "HEAD",
    ) -> dict[str, Any]:
        if not accel_get:
            return {"error": "accelerator_unavailable"}
        try:
            path = (
                f"repos/{quote(github_org, safe='')}/{quote(github_repo, safe='')}"
                f"/contents/.github/workflows?{urlencode({'ref': ref})}"
            )
            resp = await accel_get(join_api_path("/v1/github", path), session_token=session_token)
            files: list[dict[str, Any]] = []
            if isinstance(resp, list):
                for item in resp:
                    if isinstance(item, dict) and item.get("type") == "file":
                        files.append({
                            "name": item.get("name"),
                            "path": item.get("path"),
                            "sha": item.get("sha"),
                        })
            return {
                "github_org": github_org,
                "github_repo": github_repo,
                "workflow_count": len(files),
                "workflows": files,
            }
        except Exception as e:
            return tool_error(e)

    async def fetch_github_workflow(
        github_org: str,
        github_repo: str,
        workflow_path: str,
        ref: str = "HEAD",
    ) -> dict[str, Any]:
        if not accel_get:
            return {"error": "accelerator_unavailable"}
        try:
            import base64

            normalized = workflow_path.lstrip("/")
            path = (
                f"repos/{quote(github_org, safe='')}/{quote(github_repo, safe='')}"
                f"/contents/{quote(normalized)}?{urlencode({'ref': ref})}"
            )
            resp = await accel_get(join_api_path("/v1/github", path), session_token=session_token)
            content = ""
            if isinstance(resp, dict):
                raw = resp.get("content") or ""
                if raw and resp.get("encoding") == "base64":
                    content = base64.b64decode(raw).decode("utf-8", errors="replace")
                elif raw:
                    content = str(raw)
            size = len(content)
            # Redaction runs on the whole decoded file, then the cap. The other
            # order leaves a secret that straddles the boundary as a fragment
            # too short for any pattern to recognise, and the tool result is
            # persisted (THR-02-001).
            masked = redact_text(content)
            return {
                "workflow_path": normalized,
                "content": masked[:MAX_WORKFLOW_CONTENT_CHARS],
                "size": size,
                "truncated": len(masked) > MAX_WORKFLOW_CONTENT_CHARS,
            }
        except Exception as e:
            return tool_error(e, workflow_path=workflow_path)

    def validate_workflow_conversion(
        ado_pipeline_yaml: str,
        github_workflow_yaml: str,
    ) -> dict[str, Any]:
        import yaml

        issues: list[str] = []
        warnings: list[str] = []
        try:
            ado = yaml.safe_load(ado_pipeline_yaml) or {}
            gh = yaml.safe_load(github_workflow_yaml) or {}
        except yaml.YAMLError as e:
            return {"passed": False, "error": str(e), "issues": ["yaml_parse_error"]}

        if ado.get("trigger") and not (gh.get("on") or gh.get(True)):
            issues.append("Missing GitHub on: triggers")
        ado_steps = ado.get("steps") or []
        for job in ado.get("jobs") or []:
            if isinstance(job, dict):
                ado_steps.extend(job.get("steps") or [])
        gh_steps: list[Any] = []
        for job in (gh.get("jobs") or {}).values():
            if isinstance(job, dict):
                gh_steps.extend(job.get("steps") or [])
        if ado_steps and not gh_steps:
            issues.append("ADO steps present but GitHub workflow has no steps")
        return {
            "passed": not issues,
            "issues": issues,
            "warnings": warnings,
            "note": "Local validation only",
        }

    def validate_workflow_syntax(workflow_yaml: str) -> dict[str, Any]:
        result = WorkflowValidator().validate_content(workflow_yaml)
        out = result.to_dict()
        out["passed"] = result.validation_status == "valid"
        return out

    tools = [
        StructuredTool.from_function(
            coroutine=ado_api_query,
            name="ado_api_query",
            description="Read-only ADO API query",
            args_schema=AdoApiQueryArgs,
        ),
        StructuredTool.from_function(
            coroutine=github_api_query,
            name="github_api_query",
            description="Read-only GitHub API query",
            args_schema=GitHubApiQueryArgs,
        ),
        StructuredTool.from_function(
            coroutine=list_ado_pipelines,
            name="list_ado_pipelines",
            description="List ADO pipelines for a project/repo",
            args_schema=ListAdoPipelinesArgs,
        ),
        StructuredTool.from_function(
            coroutine=list_github_workflows,
            name="list_github_workflows",
            description="List .github/workflows files on GitHub",
            args_schema=ListGitHubWorkflowsArgs,
        ),
        StructuredTool.from_function(
            coroutine=fetch_github_workflow,
            name="fetch_github_workflow",
            description="Fetch workflow YAML from GitHub",
            args_schema=FetchGitHubWorkflowArgs,
        ),
        StructuredTool.from_function(
            func=validate_workflow_conversion,
            name="validate_workflow_conversion",
            description="Compare ADO vs GHA YAML locally",
            args_schema=ValidateWorkflowConversionArgs,
        ),
        StructuredTool.from_function(
            func=validate_workflow_syntax,
            name="validate_workflow_syntax",
            description="Validate GHA workflow YAML locally",
            args_schema=ValidateWorkflowSyntaxArgs,
        ),
    ]
    return append_shared_tools(
        tools,
        accel_get=accel_get,
        session_token=session_token,
        session_getter=session_getter,
    )
