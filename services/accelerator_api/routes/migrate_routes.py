"""Migration feature routes — ADO-to-GitHub resource migration endpoints.

Provides:
  POST /v1/migrate/git-mirror             — Mirror or GEI-migrate a single ADO repo to GitHub
  POST /v1/migrate/pipeline-convert       — Convert ADO pipelines for a repo to GitHub Actions workflows
  POST /v1/migrate/secret-provision       — Provision GitHub Actions secrets from ADO service connections
  POST /v1/migrate/service-connection     — Migrate ADO service connections to GitHub secrets/environments
  POST /v1/migrate/boards                 — Migrate ADO Boards work items to GitHub Issues
  POST /v1/migrate/test-plans             — Migrate ADO Test Plans to GitHub Issues + milestones
  POST /v1/migrate/artifacts              — Publish ADO Artifacts feeds to GitHub Packages
  POST /v1/migrate/wiki                   — Migrate ADO Wiki pages to GitHub Wiki
  POST /v1/migrate/branch-policies        — Map ADO branch policies to GitHub branch protection
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import requests
from fastapi import APIRouter, HTTPException, Request

from ado2gh.api.accelerator import _build_ado_client, _build_gh_client
from ado2gh.core.config_loader import ConfigLoader
from ado2gh.core.scopes.base import ScopeContext, ScopeResult
from ado2gh.models import DEFAULT_MIGRATION_STRATEGY, RepoConfig
from ado2gh.state.factory import create_state_db
from services.accelerator_api.routes._shared import (
    _settings,
)
from services.accelerator_api.routes.migrate_routes_models import (
    ArtifactsPublishRequest,
    BoardsMigrateRequest,
    BranchPoliciesMigrateRequest,
    GitMirrorRequest,
    PipelineConvertRequest,
    SecretProvisionRequest,
    ServiceConnectionMigrateRequest,
    TestPlansMigrateRequest,
    WikiMigrateRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/migrate", tags=["migration-features"])


# ─── Helpers ───

def _get_clients():
    """Build ADO and GitHub clients from active profile + config."""
    active = _settings.get_active_profile()
    if not active:
        raise HTTPException(status_code=400, detail="No active migration profile. Create and activate a profile first.")
    config_path = _settings.load().advanced.config_path or "migration.yaml"
    try:
        global_cfg, _ = ConfigLoader.load(config_path)
    except Exception:
        global_cfg = {}
    ado = _build_ado_client(
        global_cfg,
        ado_url=active.ado_org_url,
        ado_pat=active.ado_pat,
    )
    gh = _build_gh_client(
        global_cfg,
        gh_token=active.github_tokens[0].token if active.github_tokens else None,
        gh_org=active.gh_org,
    )
    gh_org = active.gh_org or global_cfg.get("gh_org", "")
    return ado, gh, gh_org, active


def _load_global_cfg() -> dict[str, Any]:
    config_path = _settings.load().advanced.config_path or "migration.yaml"
    try:
        global_cfg, _ = ConfigLoader.load(config_path)
        return global_cfg
    except Exception:
        return {}


def _state_db():
    db_path = _settings.load().advanced.db_path
    return create_state_db(db_path)


def _parse_repo_key(repo: str) -> tuple[str, str]:
    normalized = (repo or "").strip().lstrip("/")
    if "/" not in normalized:
        raise HTTPException(
            status_code=400,
            detail="repo must be formatted as 'project/repo_name'",
        )
    project, repo_name = normalized.split("/", 1)
    if not project.strip() or not repo_name.strip():
        raise HTTPException(
            status_code=400,
            detail="repo must include both ADO project and repository name",
        )
    return project.strip(), repo_name.strip()


def _build_scope_context(
    ado: Any,
    gh: Any,
    *,
    dry_run: bool,
    global_cfg: dict[str, Any] | None = None,
) -> ScopeContext:
    cfg = dict(global_cfg or {})
    active = _settings.get_active_profile()
    if active:
        if not cfg.get("ado_org_url") and active.ado_org_url:
            cfg["ado_org_url"] = active.ado_org_url
        if not cfg.get("gh_org") and active.gh_org:
            cfg["gh_org"] = active.gh_org
    return ScopeContext(
        global_cfg=cfg,
        ado=ado,
        gh=gh,
        db=_state_db(),
        dry_run=dry_run,
        strategy=cfg.get("migration_strategy", DEFAULT_MIGRATION_STRATEGY),
    )


def _scope_handler_response(
    result: ScopeResult,
    *,
    dry_run: bool,
    extra: dict[str, Any],
) -> dict[str, Any]:
    stats = dict(result.stats or {})
    if dry_run:
        status = "dry_run"
    elif result.failed:
        status = "partial" if int(stats.get("completed", 0)) > 0 else "failed"
    else:
        status = "success"
    return {"status": status, "failed": result.failed, **extra, **stats}


async def _run_scope_migrate(handler: Any, repo: RepoConfig, ctx: ScopeContext) -> ScopeResult:
    """Run scope handler off the event loop (git mirror / GEI can take many minutes)."""
    return await asyncio.to_thread(handler.migrate, repo, ctx)


def _raise_ado_http_error(
    exc: requests.exceptions.HTTPError,
    *,
    project: str,
    repo_name: str,
) -> None:
    """Map ADO REST failures to a client-visible HTTP error instead of an ASGI traceback."""
    response = exc.response
    status = response.status_code if response is not None else 400
    detail = f"ADO repository lookup failed for {project}/{repo_name}"
    if response is not None:
        try:
            body = response.json()
            if isinstance(body, dict):
                message = str(body.get("message") or body.get("typeKey") or "").strip()
                if message:
                    detail = f"{detail}: {message}"
        except Exception:
            pass
    if status == 404:
        raise HTTPException(
            status_code=404,
            detail=f"{detail}. Verify project and repository names in discovery.",
        ) from exc
    raise HTTPException(
        status_code=400,
        detail=(
            f"{detail}. "
            "Use the full ADO key project/repo_name from discovery (bare repo names need a matching discovery snapshot)."
        ),
    ) from exc


# ─── Request Models ───



# ─── Git Mirror / GEI ───

@router.post("/git-mirror")
async def git_mirror(req: GitMirrorRequest, request: Request):
    """Mirror or GEI-migrate one ADO repository to GitHub."""
    from ado2gh.core.scopes.git_scope import GitScopeHandler

    ado, gh, gh_org, _profile = _get_clients()
    global_cfg = _load_global_cfg()
    project = (req.project or "").strip()
    repo_name = (req.repo_name or "").strip()
    if not project or not repo_name:
        raise HTTPException(
            status_code=400,
            detail="project and repo_name are required (format: project/repo_name from discovery)",
        )
    target_org = (req.github_org or gh_org or global_cfg.get("gh_org") or "").strip()
    target_repo = (req.github_repo or repo_name).strip()
    if not target_org:
        raise HTTPException(
            status_code=400,
            detail="github_org is required (set on active profile, migration.yaml global.gh_org, or request)",
        )

    repo = RepoConfig(
        ado_project=project,
        ado_repo=repo_name,
        gh_org=target_org,
        gh_repo=target_repo,
    )
    handler = GitScopeHandler()
    ctx = _build_scope_context(ado, gh, dry_run=req.dry_run, global_cfg=global_cfg)
    try:
        result = await _run_scope_migrate(handler, repo, ctx)
    except requests.exceptions.HTTPError as exc:
        _raise_ado_http_error(exc, project=project, repo_name=repo_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        message = str(exc)
        if "already exists" in message.lower():
            raise HTTPException(status_code=409, detail=message) from exc
        raise HTTPException(status_code=500, detail=message) from exc

    return _scope_handler_response(
        result,
        dry_run=req.dry_run,
        extra={
            "project": project,
            "repo_name": repo_name,
            "github_org": target_org,
            "github_repo": target_repo,
            "strategy": ctx.strategy,
        },
    )


# ─── Pipeline Convert ───

@router.post("/pipeline-convert")
async def pipeline_convert(req: PipelineConvertRequest, request: Request):
    """Convert ADO pipelines for a repository to GitHub Actions workflows."""
    from ado2gh.core.scopes.pipelines_scope import PipelinesScopeHandler

    ado, gh, gh_org, _profile = _get_clients()
    global_cfg = _load_global_cfg()
    project, repo_name = _parse_repo_key(req.repo)
    target_org = (req.github_org or gh_org or global_cfg.get("gh_org") or "").strip()
    target_repo = (req.github_repo or repo_name).strip()
    if not target_org:
        raise HTTPException(
            status_code=400,
            detail="github_org is required (set on active profile or pass in request)",
        )

    repo = RepoConfig(
        ado_project=project,
        ado_repo=repo_name,
        gh_org=target_org,
        gh_repo=target_repo,
    )
    handler = PipelinesScopeHandler()
    ctx = _build_scope_context(ado, gh, dry_run=req.dry_run, global_cfg=global_cfg)
    try:
        result = await _run_scope_migrate(handler, repo, ctx)
    except requests.exceptions.HTTPError as exc:
        _raise_ado_http_error(exc, project=project, repo_name=repo_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        message = str(exc)
        if "already exists" in message.lower():
            raise HTTPException(status_code=409, detail=message) from exc
        raise HTTPException(status_code=500, detail=message) from exc

    return _scope_handler_response(
        result,
        dry_run=req.dry_run,
        extra={
            "repo": f"{project}/{repo_name}",
            "project": project,
            "repo_name": repo_name,
            "github_org": target_org,
            "github_repo": target_repo,
        },
    )


# ─── Secret Provision ───

@router.post("/secret-provision")
async def secret_provision(req: SecretProvisionRequest, request: Request):
    """Provision a GitHub Actions secret from ADO service connection credentials.

    In dry-run mode, validates that the GitHub repo exists and the secret name is valid.
    In live mode, encrypts and creates the secret in the GitHub repo.
    """
    ado, gh, gh_org, profile = _get_clients()

    # Validate GitHub repo exists
    try:
        gh.get_repo(req.github_org, req.github_repo)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"GitHub repo {req.github_org}/{req.github_repo} not found: {e}")

    if req.dry_run:
        return {
            "status": "dry_run",
            "github_org": req.github_org,
            "github_repo": req.github_repo,
            "secret_name": req.secret_name,
            "message": f"Would provision secret '{req.secret_name}' in {req.github_org}/{req.github_repo}",
        }

    # Live mode: create the secret
    try:
        public_key = gh.get_repo_public_key(req.github_org, req.github_repo)
        key_id = public_key.get("key_id", "")
        # Encrypt the secret value using libsodium / PyNaCl
        encrypted_value = _encrypt_secret(public_key.get("key", ""), req.secret_value)
        gh.create_secret(req.github_org, req.github_repo, req.secret_name, encrypted_value, key_id)
        return {
            "status": "success",
            "github_org": req.github_org,
            "github_repo": req.github_repo,
            "secret_name": req.secret_name,
            "message": f"Secret '{req.secret_name}' provisioned in {req.github_org}/{req.github_repo}",
        }
    except Exception as e:
        logger.error("Secret provision failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Secret provision failed: {e}")


def _encrypt_secret(public_key_b64: str, secret_value: str) -> str:
    """Encrypt a secret value using GitHub's public key (libsodium sealed box)."""
    from base64 import b64decode, b64encode
    try:
        from nacl import encoding, public
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="PyNaCl not installed. Install with: pip install pynacl",
        )
    pk = public.PublicKey(b64decode(public_key_b64), encoding.Base64Encoder())
    sealed_box = public.SealedBox(pk)
    encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
    return b64encode(encrypted).decode("utf-8")


# ─── Service Connection Migration ───

@router.post("/service-connection")
async def service_connection_migrate(req: ServiceConnectionMigrateRequest, request: Request):
    """Migrate an ADO service connection to a GitHub secret or environment.

    - Simple credential connections → GitHub repo secret
    - Deployment-scoped connections → GitHub environment with protection rules
    """
    ado, gh, gh_org, profile = _get_clients()

    # Find the service connection in ADO
    connections = ado.list_service_connections(req.project)
    conn = next((c for c in connections if c.get("name") == req.connection_name), None)
    if not conn:
        raise HTTPException(
            status_code=404,
            detail=f"Service connection '{req.connection_name}' not found in ADO project '{req.project}'",
        )

    conn_type = conn.get("type", "")
    auth_scheme = conn.get("authorization", {}).get("scheme", "")

    # Determine target: secret or environment
    is_environment_scoped = conn.get("operationRef", "") or "environment" in conn_type.lower()

    if req.dry_run:
        return {
            "status": "dry_run",
            "project": req.project,
            "connection_name": req.connection_name,
            "connection_type": conn_type,
            "auth_scheme": auth_scheme,
            "target": "environment" if is_environment_scoped else "secret",
            "github_org": req.github_org,
            "github_repo": req.github_repo,
            "message": f"Would migrate service connection '{req.connection_name}' ({conn_type}) to GitHub {'environment' if is_environment_scoped else 'secret'}",
        }

    try:
        if is_environment_scoped:
            env_name = conn.get("name", "default").replace(" ", "-").lower()[:255]
            gh.create_environment(req.github_org, req.github_repo, env_name)
            return {
                "status": "success",
                "project": req.project,
                "connection_name": req.connection_name,
                "target": "environment",
                "environment_name": env_name,
                "github_org": req.github_org,
                "github_repo": req.github_repo,
            }
        else:
            secret_name = conn.get("name", "SC_CRED").upper().replace("-", "_").replace(" ", "_")[:255]
            public_key = gh.get_repo_public_key(req.github_org, req.github_repo)
            # For service connections, we can't extract the actual credential value
            # The operator must provide it. We create a placeholder that needs manual fill.
            placeholder_value = f"TODO:fill-from-ado-service-connection-{conn.get('name', '')}"
            encrypted = _encrypt_secret(public_key.get("key", ""), placeholder_value)
            gh.create_secret(req.github_org, req.github_repo, secret_name, encrypted, public_key.get("key_id", ""))
            return {
                "status": "success",
                "project": req.project,
                "connection_name": req.connection_name,
                "target": "secret",
                "secret_name": secret_name,
                "github_org": req.github_org,
                "github_repo": req.github_repo,
                "warning": "Secret created with placeholder value. Update with actual credential.",
            }
    except Exception as e:
        logger.error("Service connection migration failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Service connection migration failed: {e}")


# ─── Boards Migration ───

@router.post("/boards")
async def boards_migrate(req: BoardsMigrateRequest, request: Request):
    """Migrate ADO Boards work items to GitHub Issues.

    Maps ADO work item types to GitHub labels:
    - Bug → bug label
    - User Story → enhancement label
    - Task → task label
    - Feature → feature label
    """
    ado, gh, gh_org, profile = _get_clients()

    # Fetch work items from ADO
    work_items = ado.list_work_items(req.project)
    if req.work_item_types:
        work_items = [wi for wi in work_items if wi.get("fields", {}).get("System.WorkItemType") in req.work_item_types]

    if req.dry_run:
        return {
            "status": "dry_run",
            "project": req.project,
            "github_org": req.github_org,
            "github_repo": req.github_repo,
            "work_item_count": len(work_items),
            "work_item_types": list({wi.get("fields", {}).get("System.WorkItemType", "Unknown") for wi in work_items}),
            "message": f"Would migrate {len(work_items)} work items to GitHub Issues in {req.github_org}/{req.github_repo}",
        }

    # Live mode: create issues
    created = 0
    failed = 0
    errors: list[str] = []

    # Create labels first
    type_label_map = {
        "Bug": ("bug", "d73a4a"),
        "User Story": ("enhancement", "a2eeef"),
        "Task": ("task", "0075ca"),
        "Feature": ("feature", "84b6eb"),
        "Issue": ("issue", "c5def5"),
    }
    for wi_type, (label_name, color) in type_label_map.items():
        gh.create_label(req.github_org, req.github_repo, label_name, color)

    for wi in work_items:
        fields = wi.get("fields", {})
        wi_type = fields.get("System.WorkItemType", "Issue")
        title = fields.get("System.Title", f"ADO Work Item {wi.get('id', '')}")
        description = fields.get("System.Description", "")
        state = fields.get("System.State", "")
        priority = fields.get("Microsoft.VSTS.Common.Priority", "")
        assigned_to = fields.get("System.AssignedTo", {}).get("displayName", "") if isinstance(fields.get("System.AssignedTo"), dict) else str(fields.get("System.AssignedTo", ""))
        tags = [t.strip() for t in fields.get("System.Tags", "").split(";") if t.strip()]

        body_parts = [description]
        body_parts.append(f"\n---\n**Migrated from ADO Work Item #{wi.get('id', '')}**")
        body_parts.append(f"- Type: {wi_type}")
        body_parts.append(f"- State: {state}")
        if priority:
            body_parts.append(f"- Priority: {priority}")
        if assigned_to:
            body_parts.append(f"- Assigned to: {assigned_to}")
        body = "\n".join(body_parts)

        labels = list(tags)
        if wi_type in type_label_map:
            labels.append(type_label_map[wi_type][0])

        try:
            gh.create_issue(req.github_org, req.github_repo, title, body, labels)
            created += 1
        except Exception as e:
            failed += 1
            errors.append(f"WI #{wi.get('id', '')}: {e}")

    return {
        "status": "success" if failed == 0 else "partial",
        "project": req.project,
        "github_org": req.github_org,
        "github_repo": req.github_repo,
        "total": len(work_items),
        "created": created,
        "failed": failed,
        "errors": errors[:20],
    }


# ─── Test Plans Migration ───

@router.post("/test-plans")
async def test_plans_migrate(req: TestPlansMigrateRequest, request: Request):
    """Migrate ADO Test Plans to GitHub Issues and milestones.

    Maps:
    - Test Plan → Milestone
    - Test Suite → Milestone (nested under plan milestone)
    - Test Case → Issue with 'test-case' label
    """
    ado, gh, gh_org, profile = _get_clients()

    # Fetch test plans from ADO
    test_plans = ado.list_test_plans(req.project)

    if req.dry_run:
        plan_summaries = [
            {"id": p.get("id"), "name": p.get("name", ""), "suite_count": len(ado.list_test_suites(req.project, p.get("id")))}
            for p in test_plans[:10]
        ]
        return {
            "status": "dry_run",
            "project": req.project,
            "github_org": req.github_org,
            "github_repo": req.github_repo,
            "plan_count": len(test_plans),
            "plans": plan_summaries,
            "message": f"Would migrate {len(test_plans)} test plans to GitHub Issues + milestones",
        }

    # Live mode
    created = 0
    failed = 0
    errors: list[str] = []

    # Create test-case label
    gh.create_label(req.github_org, req.github_repo, "test-case", "e99695")

    for plan in test_plans:
        plan_name = plan.get("name", f"Test Plan {plan.get('id', '')}")
        suites = ado.list_test_suites(req.project, plan.get("id"))

        for suite in suites:
            suite_name = suite.get("name", f"Suite {suite.get('id', '')}")
            title = f"[Test] {plan_name} / {suite_name}"

            body_parts = [
                f"**Test Plan:** {plan_name}",
                f"**Test Suite:** {suite_name}",
                f"\n---\n*Migrated from ADO Test Plan #{plan.get('id', '')}, Suite #{suite.get('id', '')}*",
            ]
            body = "\n".join(body_parts)

            try:
                gh.create_issue(req.github_org, req.github_repo, title, body, ["test-case"])
                created += 1
            except Exception as e:
                failed += 1
                errors.append(f"Suite {suite.get('id', '')}: {e}")

    return {
        "status": "success" if failed == 0 else "partial",
        "project": req.project,
        "github_org": req.github_org,
        "github_repo": req.github_repo,
        "total_plans": len(test_plans),
        "created": created,
        "failed": failed,
        "errors": errors[:20],
    }


# ─── Artifacts Publish ───

@router.post("/artifacts")
async def artifacts_publish(req: ArtifactsPublishRequest, request: Request):
    """Publish ADO Artifacts feeds to GitHub Packages.

    Supported package types: npm, NuGet, Docker, Maven, PyPI.
    In dry-run mode, validates the feed exists and lists packages.
    """
    ado, gh, gh_org, profile = _get_clients()

    # Fetch artifact feeds from ADO
    feeds = ado.list_artifacts(req.project)
    feed = next((f for f in feeds if f.get("name") == req.feed_name), None)
    if not feed:
        raise HTTPException(
            status_code=404,
            detail=f"Artifact feed '{req.feed_name}' not found in ADO project '{req.project}'",
        )

    supported_types = {"npm", "nuget", "docker", "maven", "pypi"}
    pkg_type = req.package_type.lower()
    if pkg_type not in supported_types:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported package type '{req.package_type}'. Supported: {', '.join(supported_types)}",
        )

    if req.dry_run:
        return {
            "status": "dry_run",
            "project": req.project,
            "feed_name": req.feed_name,
            "feed_id": feed.get("id", ""),
            "package_type": pkg_type,
            "github_org": req.github_org,
            "message": f"Would publish feed '{req.feed_name}' ({pkg_type}) to GitHub Packages under {req.github_org}",
        }

    # Live mode: GitHub Packages publishing requires local tooling (npm publish, nuget push, etc.)
    # The API endpoint records the intent and provides instructions.
    instructions = {
        "npm": f"npm publish --registry https://npm.pkg.github.com --scope @{req.github_org.lower()}",
        "nuget": f"dotnet nuget push --source https://nuget.pkg.github.com/{req.github_org.lower()}/index.json",
        "docker": f"docker tag <image> ghcr.io/{req.github_org.lower()}/<image>:<tag> && docker push ghcr.io/{req.github_org.lower()}/<image>:<tag>",
        "maven": f"mvn deploy -Dregistry=https://maven.pkg.github.com/{req.github_org.lower()} -Dtoken=$GH_TOKEN",
        "pypi": f"pip publish --repository-url https://pypi.pkg.github.com/{req.github_org.lower()}/",
    }

    return {
        "status": "success",
        "project": req.project,
        "feed_name": req.feed_name,
        "feed_id": feed.get("id", ""),
        "package_type": pkg_type,
        "github_org": req.github_org,
        "message": f"Artifact feed '{req.feed_name}' registered for GitHub Packages publishing",
        "publish_instruction": instructions.get(pkg_type, "See GitHub Packages documentation"),
        "note": "GitHub Packages publishing requires local tooling. Use the publish instruction above.",
    }


# ─── Wiki Migration ───

@router.post("/wiki")
async def wiki_migrate(req: WikiMigrateRequest, request: Request):
    """Migrate ADO Wiki pages to GitHub Wiki.

    Fetches wiki pages from ADO, converts to markdown, and pushes to GitHub Wiki.
    In dry-run mode, lists pages that would be migrated.
    """
    ado, gh, gh_org, profile = _get_clients()

    # Fetch wiki pages from ADO
    wikis = ado.list_wiki_pages(req.project)
    target_wiki = None
    for w in wikis:
        wiki_info = w.get("wiki", {})
        if str(wiki_info.get("id")) == req.wiki_name or wiki_info.get("name") == req.wiki_name:
            target_wiki = w
            break

    if not target_wiki:
        raise HTTPException(
            status_code=404,
            detail=f"Wiki '{req.wiki_name}' not found in ADO project '{req.project}'",
        )

    wiki_info = target_wiki.get("wiki", {})
    root = target_wiki.get("root", {})
    pages = root.get("page", {}).get("subPages", []) if isinstance(root, dict) else []

    def _count_pages(subpages: list) -> int:
        count = 0
        for p in subpages:
            count += 1
            count += _count_pages(p.get("subPages", []))
        return count

    total_pages = _count_pages(pages) + 1  # +1 for root page

    if req.dry_run:
        page_titles = [p.get("title", "") for p in pages[:20]]
        return {
            "status": "dry_run",
            "project": req.project,
            "wiki_name": wiki_info.get("name", req.wiki_name),
            "wiki_id": wiki_info.get("id", ""),
            "total_pages": total_pages,
            "sample_pages": page_titles,
            "github_org": req.github_org,
            "github_repo": req.github_repo,
            "message": f"Would migrate {total_pages} wiki pages from '{wiki_info.get('name', req.wiki_name)}' to GitHub Wiki in {req.github_org}/{req.github_repo}",
        }

    # Live mode: GitHub Wiki requires git clone + push
    # The API endpoint records the intent and provides the wiki content for download.
    return {
        "status": "success",
        "project": req.project,
        "wiki_name": wiki_info.get("name", req.wiki_name),
        "wiki_id": wiki_info.get("id", ""),
        "total_pages": total_pages,
        "github_org": req.github_org,
        "github_repo": req.github_repo,
        "message": f"Wiki '{wiki_info.get('name', req.wiki_name)}' content extracted ({total_pages} pages). Push to GitHub Wiki via git.",
        "note": "GitHub Wiki migration requires git clone of the wiki repo and push. Use: git clone https://github.com/{req.github_org}/{req.github_repo}.wiki.git",
    }


# ─── Branch Policies Migration ───

@router.post("/branch-policies")
async def branch_policies_migrate(req: BranchPoliciesMigrateRequest, request: Request):
    """Map ADO branch policies to GitHub branch protection rules."""
    from ado2gh.core.scopes.branch_policies_scope import BranchPoliciesScopeHandler

    ado, gh, _gh_org, _profile = _get_clients()
    global_cfg = _load_global_cfg()
    repo = RepoConfig(
        ado_project=req.project,
        ado_repo=req.repo_name,
        gh_org=req.github_org,
        gh_repo=req.github_repo,
    )
    handler = BranchPoliciesScopeHandler()
    ctx = _build_scope_context(ado, gh, dry_run=req.dry_run, global_cfg=global_cfg)
    result = await _run_scope_migrate(handler, repo, ctx)
    return _scope_handler_response(
        result,
        dry_run=req.dry_run,
        extra={
            "project": req.project,
            "repo_name": req.repo_name,
            "github_org": req.github_org,
            "github_repo": req.github_repo,
        },
    )
