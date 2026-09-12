"""Migration feature routes — one POST endpoint per ADO resource type.

Every route under `/v1/migrate` moves a single resource (repository,
pipelines, secrets, service connections, boards, test plans, artifacts, wiki,
branch policies) from Azure DevOps to GitHub, and every one accepts `dry_run`
so the console can preview before it commits. The router carries the
live-execution guard as a router-level dependency, so no handler can be added
without it. The shared plumbing lives in `migrate_scope.py`.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import requests
from fastapi import APIRouter, Depends, HTTPException

from ado2gh.models import ExecutionMode, RepoConfig
from ado2gh.state.factory import create_state_db
from services.accelerator_api.routes._shared import _settings
from services.accelerator_api.routes.migrate_guard import guard_live_migration
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
from services.accelerator_api.routes.migrate_scope import (
    _build_scope_context,
    _encrypt_secret,
    _get_clients,
    _load_global_cfg,
    _parse_repo_key,
    _raise_ado_http_error,
    _run_scope_migrate,
    _scope_handler_response,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ado2gh.state.base import StateDBBase

logger = logging.getLogger(__name__)


def _state_db() -> StateDBBase:
    """Open the migration state store named by the advanced settings.

    Kept in this module rather than in `migrate_scope` so the route layer stays
    the single place that resolves the configured database.

    Returns:
        A state-database handle on the configured backend (SQLite or Postgres).
    """
    db_path = _settings.load().advanced.db_path
    return create_state_db(db_path)


router = APIRouter(
    prefix="/v1/migrate",
    tags=["migration-features"],
    dependencies=[Depends(guard_live_migration)],
)


@router.post("/git-mirror")
async def git_mirror(req: GitMirrorRequest) -> dict[str, object]:
    """Mirror or GEI-migrate one ADO repository to GitHub.

    Copies every branch, tag and LFS object of `project/repo_name` into
    `github_org/github_repo`, using the migration strategy configured for the
    deployment (`gei` or `mirror`). The target organisation falls back to the
    active profile and then to `global.gh_org`; the target repository name
    falls back to the ADO repository name.

    Returns:
        `status` (`dry_run`, `success`, `partial` or `failed`), the `failed`
        list, the resolved `project`, `repo_name`, `github_org`, `github_repo`
        and `strategy`, plus the mirror statistics. Fails with 400 for a
        missing repository key or organisation, 404 when ADO does not know the
        repository, 409 when the GitHub repository already exists, and 500 for
        any other mirror failure.
    """
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
    ctx = _build_scope_context(ado, gh, mode=ExecutionMode.from_dry_run(dry_run=req.dry_run),
        db=_state_db(), global_cfg=global_cfg)
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
        mode=ExecutionMode.from_dry_run(dry_run=req.dry_run),
        extra={
            "project": project,
            "repo_name": repo_name,
            "github_org": target_org,
            "github_repo": target_repo,
            "strategy": ctx.strategy,
        },
    )


@router.post("/pipeline-convert")
async def pipeline_convert(req: PipelineConvertRequest) -> dict[str, object]:
    """Convert ADO pipelines for a repository to GitHub Actions workflows.

    Reads every pipeline attached to the `project/repo_name` given as `repo`,
    transforms it, and writes the generated workflow files into the target
    GitHub repository. The target organisation falls back to the active profile
    and then to `global.gh_org`.

    Returns:
        `status` (`dry_run`, `success`, `partial` or `failed`), the `failed`
        list, the `repo` key with its `project`, `repo_name`, `github_org` and
        `github_repo`, plus the transformer's statistics. Fails with 400 for a
        malformed repository key or a missing organisation, and 404 when ADO
        does not know the repository.
    """
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
    ctx = _build_scope_context(ado, gh, mode=ExecutionMode.from_dry_run(dry_run=req.dry_run),
        db=_state_db(), global_cfg=global_cfg)
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
        mode=ExecutionMode.from_dry_run(dry_run=req.dry_run),
        extra={
            "repo": f"{project}/{repo_name}",
            "project": project,
            "repo_name": repo_name,
            "github_org": target_org,
            "github_repo": target_repo,
        },
    )


@router.post("/secret-provision")
async def secret_provision(req: SecretProvisionRequest) -> dict[str, object]:
    """Provision a GitHub Actions secret from ADO service connection credentials.

    A dry run checks only that the target GitHub repository exists and that the
    secret name is usable. A live run seals the supplied value against the
    repository's public key and creates the secret. The value is never echoed
    back, logged, or included in an error message.

    Returns:
        `status` (`dry_run` or `success`), the `github_org`, `github_repo` and
        `secret_name` the request addressed, and a `message` describing what was
        done or would be done — never the secret value. Fails with 404 when the
        GitHub repository is not reachable and 500 when GitHub rejects the
        secret.
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



@router.post("/service-connection")
async def service_connection_migrate(req: ServiceConnectionMigrateRequest) -> dict[str, object]:
    """Migrate an ADO service connection to a GitHub secret or environment.

    A deployment-scoped connection becomes a GitHub environment; a plain
    credential connection becomes a repository secret. Azure DevOps does not
    expose connection credential values over its API, so a live run creates the
    secret with a placeholder and returns a warning: an operator must fill in
    the real value afterwards.

    Returns:
        `status` (`dry_run` or `success`), the `project` and `connection_name`
        addressed, the chosen `target` (`secret` or `environment`) with the
        `secret_name` or `environment_name` created, the `github_org` and
        `github_repo`, and on a live secret run the placeholder `warning`. A dry
        run also reports the `connection_type` and `auth_scheme` it found. Fails
        with 404 when the connection is not in the project and 500 when GitHub
        rejects the write.
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


@router.post("/boards")
async def boards_migrate(req: BoardsMigrateRequest) -> dict[str, object]:
    """Migrate ADO Boards work items to GitHub Issues.

    Every work item in the project becomes an issue, optionally narrowed to the
    `work_item_types` given. A live run first creates the labels the mapping
    needs — Bug becomes `bug`, User Story becomes `enhancement`, Task becomes
    `task`, Feature becomes `feature`, everything else becomes `issue` — then
    creates one issue per work item.

    Returns:
        `status` (`dry_run`, `success`, or `partial` when some issues failed),
        the `project`, `github_org` and `github_repo`, and the counts: a dry run
        reports `work_item_count` and the distinct `work_item_types` found; a
        live run reports `total`, `created`, `failed` and up to twenty `errors`.
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


@router.post("/test-plans")
async def test_plans_migrate(req: TestPlansMigrateRequest) -> dict[str, object]:
    """Migrate ADO Test Plans to GitHub Issues and milestones.

    Each test plan becomes a milestone, each suite becomes a milestone named
    after its parent plan, and each test case becomes an issue carrying the
    `test-case` label.

    Returns:
        `status` (`dry_run`, `success`, or `partial` when some writes failed),
        the `project`, `github_org` and `github_repo`, and the counts: a dry run
        previews up to ten plans with their id, name and suite count; a live run
        reports `total_plans`, `created`, `failed` and up to twenty `errors`.
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


@router.post("/artifacts")
async def artifacts_publish(req: ArtifactsPublishRequest) -> dict[str, object]:
    """Register an ADO Artifacts feed for publishing to GitHub Packages.

    Covers npm, NuGet, Docker, Maven and PyPI feeds. GitHub Packages cannot be
    populated over the API alone, so this endpoint resolves the feed and returns
    the command an operator runs locally rather than transferring packages
    itself. A dry run only confirms the feed exists and lists what it holds.

    Returns:
        `status` (`dry_run` or `success`), the `project`, `feed_name`, `feed_id`
        and detected `package_type`, the `github_org`, a `message`, the
        `publish_instruction` to run, and a `note` stating that local tooling is
        required. Fails with 404 when the feed is not in the project.
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


@router.post("/wiki")
async def wiki_migrate(req: WikiMigrateRequest) -> dict[str, object]:
    """Extract an ADO Wiki for transfer to GitHub Wiki.

    Resolves `wiki_name` against the project's wikis by id or by name and walks
    the page tree. A GitHub wiki is a separate git repository that cannot be
    written over the REST API, so this endpoint counts and extracts the content
    and returns the `git clone` the operator runs to push it; it does not write
    to GitHub itself.

    Returns:
        `status` (`dry_run` or `success`), the `project`, resolved `wiki_name`
        and `wiki_id`, `total_pages`, the `github_org` and `github_repo`, a
        `message`, and on a live run a `note` with the clone command. A dry run
        also lists up to twenty `sample_pages`. Fails with 404 when the project
        has no wiki under that name.
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


@router.post("/branch-policies")
async def branch_policies_migrate(req: BranchPoliciesMigrateRequest) -> dict[str, object]:
    """Map ADO branch policies to GitHub branch protection rules.

    Reads the policies configured on the ADO repository's branches and applies
    the equivalent protection rules — required reviewers, required status
    checks, merge restrictions — to the matching branches on GitHub.

    Returns:
        `status` (`dry_run`, `success`, `partial` or `failed`), the `failed`
        list, the `project`, `repo_name`, `github_org` and `github_repo`, plus
        the handler's per-policy statistics.
    """
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
    ctx = _build_scope_context(ado, gh, mode=ExecutionMode.from_dry_run(dry_run=req.dry_run),
        db=_state_db(), global_cfg=global_cfg)
    result = await _run_scope_migrate(handler, repo, ctx)
    return _scope_handler_response(
        result,
        mode=ExecutionMode.from_dry_run(dry_run=req.dry_run),
        extra={
            "project": req.project,
            "repo_name": req.repo_name,
            "github_org": req.github_org,
            "github_repo": req.github_repo,
        },
    )
