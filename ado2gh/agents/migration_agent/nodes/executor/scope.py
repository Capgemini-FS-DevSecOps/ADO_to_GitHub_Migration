"""Deterministic per-scope migration execution via accelerator API."""
from __future__ import annotations

import re
from typing import Any, Awaitable, Callable

AccelPost = Callable[..., Awaitable[dict[str, Any]]]

SCOPE_ACCELERATOR_ENDPOINTS: dict[str, str] = {
    "repo": "POST /v1/migrate/git-mirror",
    "git": "POST /v1/migrate/git-mirror",
    "pipelines": "POST /v1/migrate/pipeline-convert",
    "secrets": "POST /v1/migrate/service-connection",
    "service_connections": "POST /v1/migrate/service-connection",
    "work_items": "POST /v1/migrate/boards",
    "boards": "POST /v1/migrate/boards",
    "wiki": "POST /v1/migrate/wiki",
    "branch_policies": "POST /v1/migrate/branch-policies",
    "test_plans": "POST /v1/migrate/test-plans",
    "artifacts": "POST /v1/migrate/artifacts",
    "bicep": "POST /v1/migrate/bicep-transform",
}


def scope_accelerator_endpoint(scope: str) -> str:
    """Accelerator HTTP call for a migration scope (for operator-facing errors)."""
    normalized = scope
    if scope == "git":
        normalized = "repo"
    elif scope in ("boards", "service_connections"):
        normalized = "secrets" if scope == "service_connections" else "work_items"
    return SCOPE_ACCELERATOR_ENDPOINTS.get(normalized, "")


def default_agent_enabled_scopes() -> list[str]:
    """Core migrate-tab scopes when per-repo detection is unavailable."""
    from ado2gh.models import MigrationScope

    return [
        MigrationScope.REPO.value,
        MigrationScope.PIPELINES.value,
    ]


def _project_for_repo_key(repo_key: str) -> str:
    return repo_key.split("/", 1)[0] if "/" in repo_key else ""


def repo_dependency_report(
    repo_key: str,
    *,
    repo_discovery: dict[str, Any] | None = None,
    session: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Per-repo dependency facts from analyze_deps, discovery, and feature probes."""
    report: dict[str, Any] = {
        "service_connections": [],
        "variable_groups": [],
        "repo_features": {},
    }
    discovery = repo_discovery or {}
    project = _project_for_repo_key(repo_key)

    if isinstance(session, dict):
        analyze = session.get("analyze_deps_result") or {}
        if isinstance(analyze, dict):
            repo_deps = (analyze.get("dependencies") or {}).get(repo_key) or {}
            if isinstance(repo_deps, dict):
                report["service_connections"] = list(repo_deps.get("service_connections") or [])
                report["variable_groups"] = list(repo_deps.get("variable_groups") or [])
                report["repo_features"] = dict(repo_deps.get("repo_features") or {})

        discovery_snapshot = session.get("discovery_snapshot") or {}
        if isinstance(discovery_snapshot, dict):
            for gap in discovery_snapshot.get("inventory_gaps") or []:
                if not isinstance(gap, dict) or gap.get("project") != project:
                    continue
                if gap.get("type") == "service_connection":
                    report["service_connections"].append({
                        "name": gap.get("name", ""),
                        "type": gap.get("connection_type", "unknown"),
                        "status": "operator_required",
                    })
                elif gap.get("type") == "variable_group":
                    report["variable_groups"].append({
                        "name": gap.get("name", ""),
                        "status": "operator_required",
                    })

        probed = (session.get("repo_feature_detection") or {}).get(repo_key) or {}
        if isinstance(probed, dict):
            report["repo_features"] = {**report["repo_features"], **probed}

    if int(discovery.get("service_connections") or 0) > 0 and not report["service_connections"]:
        report["service_connections"] = [{"detected": True, "count": discovery["service_connections"]}]
    if int(discovery.get("variable_groups") or 0) > 0 and not report["variable_groups"]:
        report["variable_groups"] = [{"detected": True, "count": discovery["variable_groups"]}]

    features = discovery.get("repo_features") or {}
    if isinstance(features, dict):
        report["repo_features"] = {**features, **report["repo_features"]}

    return report


def has_secret_dependencies(
    report: dict[str, Any],
    *,
    pipeline_count: int = 0,
) -> bool:
    """True when dependency analysis found service connections or variable groups."""
    if pipeline_count <= 0:
        return False
    if report.get("service_connections") or report.get("variable_groups"):
        return True
    secrets_feat = (report.get("repo_features") or {}).get("secrets") or {}
    return bool(isinstance(secrets_feat, dict) and secrets_feat.get("detected"))


async def ensure_repo_feature_detection(
    session: dict[str, Any],
    repo_keys: list[str],
    accel_get: Any | None,
    session_token: str | None = None,
) -> None:
    """Probe ADO (when available) and cache wiki / branch-policy / secret signals per repo."""
    cache = session.setdefault("repo_feature_detection", {})
    discovery = session.get("discovery_snapshot") or {}
    inventory_gaps = discovery.get("inventory_gaps") or [] if isinstance(discovery, dict) else []

    for repo_key in repo_keys:
        if repo_key in cache:
            continue
        project, repo_name = (repo_key.split("/", 1) + [""])[:2]
        features: dict[str, Any] = {}

        sc_gaps = [
            g for g in inventory_gaps
            if isinstance(g, dict) and g.get("project") == project and g.get("type") == "service_connection"
        ]
        vg_gaps = [
            g for g in inventory_gaps
            if isinstance(g, dict) and g.get("project") == project and g.get("type") == "variable_group"
        ]
        if sc_gaps or vg_gaps:
            features["secrets"] = {
                "detected": True,
                "service_connections": len(sc_gaps),
                "variable_groups": len(vg_gaps),
            }

        if accel_get and project:
            try:
                wiki_resp = await accel_get(
                    f"/v1/ado/{project}/_apis/wiki/wikis?api-version=7.0",
                    session_token=session_token,
                )
                wiki_list = wiki_resp.get("value", []) if isinstance(wiki_resp, dict) else []
                if wiki_list:
                    features["wiki"] = {"detected": True, "count": len(wiki_list)}
            except Exception:
                pass

            if repo_name:
                try:
                    repo_resp = await accel_get(
                        f"/v1/ado/projects/{project}/repos/{repo_name}",
                        session_token=session_token,
                    )
                    repo_id = repo_resp.get("id") if isinstance(repo_resp, dict) else None
                    if repo_id:
                        pol_resp = await accel_get(
                            f"/v1/ado/projects/{project}/_apis/policy/configurations?api-version=7.0",
                            session_token=session_token,
                        )
                        policies = pol_resp.get("value", []) if isinstance(pol_resp, dict) else []
                        repo_policies = [
                            p for p in policies
                            if isinstance(p, dict)
                            and ((p.get("settings") or {}).get("scope") or [{}])[0].get("repositoryId") == repo_id
                        ]
                        if repo_policies:
                            features["branch_policies"] = {
                                "detected": True,
                                "count": len(repo_policies),
                            }
                except Exception:
                    pass

        cache[repo_key] = features


def discovery_repo_lookup(discovery: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Map Project/RepoName → discovery repo dict."""
    lookup: dict[str, dict[str, Any]] = {}
    if not isinstance(discovery, dict):
        return lookup
    for repo in discovery.get("repos") or []:
        if not isinstance(repo, dict):
            continue
        project = str(repo.get("project") or "").strip()
        name = str(repo.get("repo_name") or repo.get("name") or "").strip()
        repo_id = str(repo.get("id") or "").strip()
        if not repo_id and project and name:
            repo_id = f"{project}/{name}"
        if repo_id:
            lookup[repo_id] = repo
    return lookup


def resolve_agent_enabled_scopes(
    repo_key: str,
    *,
    repo_discovery: dict[str, Any] | None = None,
    pipeline_count: int = 0,
    session: dict[str, Any] | None = None,
    plan: dict[str, Any] | None = None,
) -> list[str]:
    """Scopes to include in the plan for one repository (dynamic, not all scopes)."""
    from ado2gh.models import MigrationScope

    migration_plan = plan
    if migration_plan is None and session:
        migration_plan = session.get("migration_plan")
    if isinstance(migration_plan, str):
        import json
        try:
            migration_plan = json.loads(migration_plan)
        except Exception:
            migration_plan = None

    if isinstance(migration_plan, dict):
        explicit = migration_plan.get("enabled_scopes")
        if explicit:
            return [str(s) for s in explicit]
        for repo in migration_plan.get("repos") or []:
            if not isinstance(repo, dict):
                continue
            if str(repo.get("id") or "").strip() == repo_key:
                repo_scopes = repo.get("enabled_scopes") or repo.get("scopes")
                if repo_scopes:
                    return [str(s) for s in repo_scopes]

    scopes: list[str] = [MigrationScope.REPO.value]
    if pipeline_count > 0:
        scopes.append(MigrationScope.PIPELINES.value)

    deps = repo_dependency_report(
        repo_key,
        repo_discovery=repo_discovery,
        session=session,
    )
    if has_secret_dependencies(deps, pipeline_count=pipeline_count):
        scopes.append(MigrationScope.SECRETS.value)

    discovery = repo_discovery or {}
    features = dict(deps.get("repo_features") or {})
    if isinstance(discovery.get("repo_features"), dict):
        features = {**discovery.get("repo_features", {}), **features}

    def _detected(feature: str, *legacy_keys: str) -> bool:
        feat = features.get(feature) or {}
        if isinstance(feat, dict) and feat.get("detected"):
            return True
        for key in legacy_keys:
            val = discovery.get(key)
            if val is True:
                return True
            if isinstance(val, int) and val > 0:
                return True
        return False

    if _detected("work_items", "has_work_items", "work_item_count"):
        scopes.append(MigrationScope.WORK_ITEMS.value)
    if _detected("wiki", "has_wiki", "wiki_count"):
        scopes.append(MigrationScope.WIKI.value)
    if _detected("branch_policies", "has_branch_policies", "branch_policy_count", "has_policies"):
        scopes.append(MigrationScope.BRANCH_POLICIES.value)

    # De-duplicate while preserving order
    seen: set[str] = set()
    ordered: list[str] = []
    for scope in scopes:
        if scope not in seen:
            seen.add(scope)
            ordered.append(scope)
    return ordered


def build_agent_work_items_for_session(
    repo_configs: list[Any],
    session: dict[str, Any],
    *,
    db: Any = None,
    repo_pipeline_counts: dict[str, int] | None = None,
    plan: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build plan work items with per-repo scope detection from discovery."""
    from ado2gh.agents.migration_agent.nodes.executor.plan import pipeline_counts_from_discovery
    from ado2gh.api.migration_work_plan import build_work_items_for_repos

    discovery = session.get("discovery_snapshot")
    if isinstance(discovery, str):
        import json
        try:
            discovery = json.loads(discovery)
        except Exception:
            discovery = {}
    lookup = discovery_repo_lookup(discovery if isinstance(discovery, dict) else None)
    pipeline_counts = (
        repo_pipeline_counts
        if repo_pipeline_counts is not None
        else pipeline_counts_from_discovery(discovery if isinstance(discovery, dict) else None)
    )

    per_repo: dict[str, list[str]] = {}
    for repo in repo_configs:
        repo_key = f"{repo.ado_project}/{repo.ado_repo}"
        per_repo[repo_key] = resolve_agent_enabled_scopes(
            repo_key,
            repo_discovery=lookup.get(repo_key),
            pipeline_count=int(pipeline_counts.get(repo_key, 0) or 0),
            session=session,
            plan=plan,
        )

    return build_work_items_for_repos(
        repo_configs,
        db=db,
        repo_pipeline_counts=pipeline_counts,
        enabled_scopes_per_repo=per_repo,
    )


def _discovery_from_session(session: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(session, dict):
        return {}
    discovery = session.get("discovery_snapshot") or {}
    if isinstance(discovery, str):
        import json
        try:
            discovery = json.loads(discovery)
        except Exception:
            return {}
    return discovery if isinstance(discovery, dict) else {}


def _resolve_ado_coords_from_discovery(
    repo_id: str,
    project: str,
    repo_name: str,
    session: dict[str, Any] | None,
) -> tuple[str, str, str]:
    """Fill missing ADO project/repo from discovery when only a repo name was requested."""
    from ado2gh.agents.migration_agent.utils import find_discovery_repo, normalize_repo_key

    lookup = repo_id or (f"{project}/{repo_name}" if project and repo_name else repo_name)
    if project and repo_name:
        return project, repo_name, normalize_repo_key(f"{project}/{repo_name}")

    discovery = _discovery_from_session(session)
    repos = discovery.get("repos") or []
    if not repos or not lookup:
        return project, repo_name, repo_id

    match = find_discovery_repo(lookup, repos)
    if not match:
        return project, repo_name, repo_id

    resolved_project = str(match.get("project") or project).strip()
    resolved_repo = str(match.get("repo_name") or match.get("name") or repo_name).strip()
    resolved_id = (
        f"{resolved_project}/{resolved_repo}"
        if resolved_project and resolved_repo
        else repo_id
    )
    return resolved_project, resolved_repo, normalize_repo_key(resolved_id)


def resolve_repo_context(
    work_item: dict[str, Any],
    plan: dict[str, Any] | None = None,
    session: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Derive ADO/GitHub coordinates from a work item and optional plan."""
    from ado2gh.agents.migration_agent.nodes.executor.plan import resolve_github_org
    from ado2gh.agents.migration_agent.utils import normalize_repo_key

    repo_id = normalize_repo_key(str(work_item.get("repo") or ""))
    project = str(work_item.get("project") or "").strip()
    repo_name = str(work_item.get("repo_name") or "").strip()
    if "/" in repo_id:
        ado_project, ado_repo = repo_id.split("/", 1)
        project = project or ado_project
        repo_name = repo_name or ado_repo
    else:
        repo_name = repo_name or repo_id

    project, repo_name, repo_id = _resolve_ado_coords_from_discovery(
        repo_id,
        project,
        repo_name,
        session,
    )

    gh_org = str(work_item.get("github_org") or work_item.get("gh_org") or "").strip()
    gh_repo = str(work_item.get("github_repo") or work_item.get("gh_repo") or "").strip()
    gh_target = str(work_item.get("gh_target") or "").strip()
    if "/" in gh_target:
        target_org, target_repo = gh_target.split("/", 1)
        if target_org.strip():
            gh_org = gh_org or target_org.strip()
        if target_repo.strip():
            gh_repo = gh_repo or target_repo.strip()
    elif gh_target and not gh_repo:
        gh_repo = gh_target

    if not gh_repo:
        gh_repo = repo_name

    if not gh_org:
        gh_org = resolve_github_org(
            repo=work_item if isinstance(work_item, dict) else None,
            session=session,
            plan=plan,
            repo_key=repo_id,
        )

    if plan and not gh_org:
        for repo in plan.get("repos") or []:
            if not isinstance(repo, dict):
                continue
            rid = normalize_repo_key(str(repo.get("id") or ""))
            if rid and rid != repo_id:
                continue
            gh_org = str(repo.get("gh_org") or repo.get("github_org") or gh_org).strip()
            gh_repo = str(repo.get("gh_repo") or repo.get("github_repo") or gh_repo).strip()
            break

    return {
        "project": project,
        "repo_name": repo_name,
        "github_org": gh_org,
        "github_repo": gh_repo,
        "repo_id": repo_id,
    }


def _parse_secret_mapping_field(field: str) -> tuple[str, str]:
    """Return (project, connection_name) from secret_mapping__Project__Name keys."""
    text = str(field or "").strip()
    if text.startswith("secret_mapping__"):
        parts = text.split("__", 2)
        if len(parts) == 3:
            return parts[1], parts[2]
        if len(parts) == 2:
            return "", parts[1]
    match = re.search(r"Service connection '([^']+)'", text)
    if match:
        return "", match.group(1)
    return "", text


async def _execute_secrets_scope(
    ctx: dict[str, str],
    *,
    accel_post: AccelPost,
    session_token: str | None,
    dry_run: bool,
    secret_mappings: dict[str, str] | None,
) -> dict[str, Any]:
    mappings = {
        str(k): str(v).strip()
        for k, v in (secret_mappings or {}).items()
        if str(v).strip()
    }
    if not mappings:
        return {
            "status": "skipped",
            "message": "No operator secret mappings — provide GitHub secret names via operator input",
        }

    results: list[dict[str, Any]] = []
    for field, secret_name in mappings.items():
        project, connection_name = _parse_secret_mapping_field(field)
        project = project or ctx["project"]
        try:
            sc_result = await accel_post(
                "/v1/migrate/service-connection",
                {
                    "project": project,
                    "connection_name": connection_name,
                    "github_org": ctx["github_org"],
                    "github_repo": ctx["github_repo"],
                    "dry_run": dry_run,
                },
                session_token=session_token,
            )
            results.append({"connection": connection_name, "service_connection": sc_result})
            if not dry_run:
                provision = await accel_post(
                    "/v1/migrate/secret-provision",
                    {
                        "github_org": ctx["github_org"],
                        "github_repo": ctx["github_repo"],
                        "secret_name": secret_name,
                        "secret_value": "",
                        "dry_run": False,
                    },
                    session_token=session_token,
                )
                results[-1]["secret_provision"] = provision
        except Exception as exc:
            results.append({"connection": connection_name, "error": str(exc)})

    failed = sum(1 for r in results if r.get("error"))
    return {
        "status": "success" if failed == 0 else "partial",
        "mapped": len(mappings),
        "results": results,
    }


async def execute_migration_scope(
    scope: str,
    work_item: dict[str, Any],
    plan: dict[str, Any],
    *,
    accel_post: AccelPost | None,
    session_token: str | None,
    dry_run: bool,
    secret_mappings: dict[str, str] | None = None,
    session: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one migration scope through the accelerator API."""
    if not accel_post:
        return {"status": "skipped", "error": "accelerator_unavailable"}

    ctx = resolve_repo_context(work_item, plan, session)
    normalized = scope
    if scope == "git":
        normalized = "repo"
    if scope == "boards":
        normalized = "work_items"
    if scope == "service_connections":
        normalized = "secrets"

    if not ctx.get("project") or not ctx.get("repo_name"):
        return {
            "status": "failed",
            "error_code": "invalid_repo_key",
            "error": (
                f"ADO project/repo could not be resolved for '{ctx.get('repo_id') or work_item.get('repo')}'. "
                "Use project/repo_name from discovery or reload discovery in Settings."
            ),
        }

    try:
        if normalized == "repo":
            return await accel_post(
                "/v1/migrate/git-mirror",
                {
                    "project": ctx["project"],
                    "repo_name": ctx["repo_name"],
                    "github_org": ctx["github_org"],
                    "github_repo": ctx["github_repo"],
                    "dry_run": dry_run,
                },
                session_token=session_token,
            )

        if normalized == "pipelines":
            return await accel_post(
                "/v1/migrate/pipeline-convert",
                {
                    "repo": ctx["repo_id"],
                    "github_org": ctx["github_org"],
                    "github_repo": ctx["github_repo"],
                    "dry_run": dry_run,
                },
                session_token=session_token,
            )

        if normalized == "secrets":
            return await _execute_secrets_scope(
                ctx,
                accel_post=accel_post,
                session_token=session_token,
                dry_run=dry_run,
                secret_mappings=secret_mappings,
            )

        if normalized == "work_items":
            return await accel_post(
                "/v1/migrate/boards",
                {
                    "project": ctx["project"],
                    "github_org": ctx["github_org"],
                    "github_repo": ctx["github_repo"],
                    "work_item_types": [],
                    "dry_run": dry_run,
                },
                session_token=session_token,
            )

        if normalized == "wiki":
            return await accel_post(
                "/v1/migrate/wiki",
                {
                    "project": ctx["project"],
                    "wiki_name": ctx["project"],
                    "github_org": ctx["github_org"],
                    "github_repo": ctx["github_repo"],
                    "dry_run": dry_run,
                },
                session_token=session_token,
            )

        if normalized == "branch_policies":
            return await accel_post(
                "/v1/migrate/branch-policies",
                {
                    "project": ctx["project"],
                    "repo_name": ctx["repo_name"],
                    "github_org": ctx["github_org"],
                    "github_repo": ctx["github_repo"],
                    "dry_run": dry_run,
                },
                session_token=session_token,
            )

        return {"status": "skipped", "message": f"Scope '{scope}' is not enabled for agent execution"}
    except Exception as exc:
        endpoint = scope_accelerator_endpoint(scope)
        endpoint_note = f" ({endpoint})" if endpoint else ""
        if "404" in str(exc) or "Not Found" in str(exc):
            if dry_run:
                return {
                    "status": "skipped",
                    "message": f"Accelerator endpoint unavailable for scope '{scope}'{endpoint_note}",
                    "endpoint": endpoint,
                    "detail": str(exc),
                }
            return {
                "status": "failed",
                "message": f"Accelerator endpoint unavailable for scope '{scope}'{endpoint_note}",
                "endpoint": endpoint,
                "error": str(exc),
            }
        if "409" in str(exc) or "already exists" in str(exc).lower():
            return {
                "status": "failed",
                "error": str(exc),
                "error_code": "target_repo_conflict",
                "endpoint": endpoint,
                "remediation": (
                    "Delete the existing GitHub target repo and retry, or rename github_repo. "
                    "If the repo was already migrated, re-run after restarting the accelerator "
                    "so idempotent HEAD verification can skip GEI."
                ),
            }
        return {"status": "failed", "error": str(exc), "error_code": f"{normalized}_error"}
