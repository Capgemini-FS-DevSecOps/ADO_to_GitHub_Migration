"""Deterministic migration planner for an entire Azure DevOps organisation."""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Iterable, Optional
from urllib.parse import urlsplit, urlunsplit

from ado2gh.logging_config import log
from ado2gh.models import MigrationScope, RepoConfig
from ado2gh.pev.contracts import (
    MigrationPlan,
    PlannedRepository,
    PlanIntegrityError,
    PlanTask,
    canonical_ref_snapshot,
    content_digest,
    compute_source_refs_digest,
)
from ado2gh.pev.source_integrity import (
    BOUND_NON_GIT_SCOPES,
    create_scope_snapshot,
    fetch_project_access_snapshots,
    fetch_project_work_item_payloads,
    fetch_scope_payload,
)
from ado2gh.pipelines.approvals import ManualApprovalManifest
from ado2gh.pipelines.inventory import PipelineInventoryBuilder
from ado2gh.pipelines.llm import normalize_pipeline_llm_settings
from ado2gh.governance import (
    authorize_plan_creation,
    governance_is_strict,
)


# Only keys whose *values* may contain credential material are removed from an
# approval artifact.  Policy fields and credential references such as
# ``allow_inline_secrets``, ``secret_names``, and ``token_secret`` must remain
# plan-bound; dropping them would let execution semantics change without
# changing the approved plan id.
_SECRET_VALUE_KEYS = {
    "ado_pat",
    "gh_token",
    "password",
    "passwd",
    "secret",
    "private_key",
    "api_key",
    "client_secret",
    "connection_string",
}
_INVALID_REPO_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def _non_secret_config(value: Any) -> Any:
    """Return a stable config view suitable for a digest and audit record."""
    if isinstance(value, dict):
        return {
            str(key): _non_secret_config(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key).casefold() not in _SECRET_VALUE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_non_secret_config(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _safe_org_url(value: str) -> str:
    parsed = urlsplit(value.rstrip("/"))
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise PlanIntegrityError("ADO organisation URL must be an absolute HTTPS URL")
    # Credentials in a URL must never enter a plan or audit artifact.
    host = parsed.hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path.rstrip("/"), "", ""))


def _safe_api_url(value: str, label: str = "API") -> str:
    parsed = urlsplit(str(value).rstrip("/"))
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise PlanIntegrityError(
            f"{label} URL must be absolute HTTPS without credentials, query, or fragment"
        )
    host = parsed.hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path.rstrip("/"), "", ""))


def _logical_identity(namespace: str, *values: str) -> str:
    return "logical:" + content_digest(
        {"namespace": namespace, "values": [value.casefold() for value in values]}
    )


def build_runtime_context(
    ado: Any,
    gh: Any,
    global_cfg: dict[str, Any],
    target_orgs: Iterable[str],
    *,
    source_org_url: Optional[str] = None,
) -> dict[str, Any]:
    """Capture canonical API authorities and immutable organization IDs."""
    source_api_url = _safe_org_url(
        source_org_url
        or str(getattr(ado, "org_url", "") or global_cfg.get("ado_org_url", ""))
    )
    target_api_url = _safe_api_url(
        str(
            getattr(gh, "BASE", "")
            or global_cfg.get("gh_api_url", "https://api.github.com")
        ),
        "GitHub API",
    )

    source_reader = getattr(ado, "get_organization_identity", None)
    if callable(source_reader):
        source_identity = {
            "kind": "immutable",
            "id": str(source_reader()).strip(),
        }
    else:
        source_identity = {
            "kind": "logical",
            "id": _logical_identity("ado-org", source_api_url),
        }
    if not source_identity["id"]:
        raise PlanIntegrityError("ADO organization identity is empty")

    org_reader = getattr(gh, "get_org", None)
    target_identities: dict[str, dict[str, str]] = {}
    for org in sorted({str(item).strip() for item in target_orgs}, key=str.casefold):
        if not org:
            raise PlanIntegrityError("GitHub target organization is empty")
        if callable(org_reader):
            metadata = org_reader(org)
            if not isinstance(metadata, dict):
                raise PlanIntegrityError(
                    f"GitHub organization {org!r} metadata is not an object"
                )
            identity = str(metadata.get("node_id") or metadata.get("id") or "").strip()
            kind = "immutable"
        else:
            identity = _logical_identity("github-org", target_api_url, org)
            kind = "logical"
        if not identity:
            raise PlanIntegrityError(
                f"GitHub organization {org!r} has no immutable identity"
            )
        target_identities[org] = {"kind": kind, "id": identity}

    return {
        "source_api_url": source_api_url,
        "source_org_identity": source_identity,
        "target_api_url": target_api_url,
        "target_org_identities": target_identities,
    }


def verify_plan_runtime_context(
    plan: MigrationPlan,
    global_cfg: dict[str, Any],
    ado: Any,
    gh: Any,
) -> None:
    approved = plan.policy.get("runtime_context")
    if not isinstance(approved, dict):
        raise PlanIntegrityError("Approved plan has no runtime authority context")
    target_identities = approved.get("target_org_identities", {})
    if not isinstance(target_identities, dict):
        raise PlanIntegrityError("Approved target organization identities are invalid")
    observed = build_runtime_context(
        ado,
        gh,
        global_cfg,
        target_identities.keys(),
    )
    if observed != approved:
        raise PlanIntegrityError(
            "Active ADO/GitHub API authority or organization identity does not "
            "match the approved plan"
        )


def _slug(value: str, lowercase: bool = True, max_length: int = 100) -> str:
    result = _INVALID_REPO_CHARS.sub("-", value.strip()).strip(".-_")
    result = re.sub(r"-{2,}", "-", result)
    if lowercase:
        result = result.lower()
    result = result[:max_length].rstrip(".-_")
    if not result or result in {".", ".."}:
        raise PlanIntegrityError(f"Cannot derive a valid GitHub repository name from {value!r}")
    return result


class MigrationPlanner:
    """Discover sources, resolve mappings, and emit an immutable task graph.

    LLMs are intentionally absent from repository mapping.  Names and
    collision policy are deterministic because an ambiguous repository target
    is a governance decision, not a text-generation problem.
    """

    def __init__(self, ado: Any, global_cfg: dict[str, Any], gh: Any = None, db: Any = None):
        self.ado = ado
        self.cfg = dict(global_cfg)
        pipeline_conversion = dict(self.cfg.get("pipeline_conversion", {}))
        pipeline_conversion.update(normalize_pipeline_llm_settings(
            pipeline_conversion,
            enforce_environment_match=False,
        ))
        approval_config = pipeline_conversion.get("manual_approval_manifest")
        if approval_config is not None:
            pipeline_conversion["manual_approval_manifest"] = (
                ManualApprovalManifest.from_config(approval_config).to_dict()
            )
        self.cfg["pipeline_conversion"] = pipeline_conversion
        self.gh = gh
        self.db = db

    def create_plan(
        self,
        repos: Optional[Iterable[RepoConfig]] = None,
        scopes: Optional[Iterable[str]] = None,
        planner_approval_envelope: Optional[dict[str, Any]] = None,
    ) -> MigrationPlan:
        source_org_url = _safe_org_url(
            str(getattr(self.ado, "org_url", "") or self.cfg.get("ado_org_url", ""))
        )
        mapping_cfg = dict(self.cfg.get("mapping", {}))
        target_org = str(mapping_cfg.get("target_org") or self.cfg.get("gh_org", "")).strip()
        if not target_org:
            raise PlanIntegrityError("global.gh_org or global.mapping.target_org is required")

        requested_scopes = tuple(
            scopes or self.cfg.get("default_scopes", [MigrationScope.REPO.value])
        )
        self._validate_scopes(requested_scopes)

        complete_org_inventory = repos is None
        if complete_org_inventory:
            planned = self._discover_and_map(target_org, requested_scopes, mapping_cfg)
        else:
            planned = self._map_explicit_repos(
                list(repos), target_org, requested_scopes, mapping_cfg
            )

        if callable(getattr(self.ado, "list_project_git_acls", None)):
            try:
                access_snapshots = fetch_project_access_snapshots(
                    self.ado, planned, require_stable=True
                )
            except Exception as exc:
                raise PlanIntegrityError(
                    f"Cannot capture stable ADO repository ACLs: {exc}"
                ) from exc
            planned = [
                replace(
                    repo,
                    source_access_snapshot=dict(
                        access_snapshots[repo.source_key]
                    ),
                )
                for repo in planned
            ]

        delivery_policy = {
            "mode": "pull_request",
            "branch": "ado2gh/migrated-workflows",
            "title": "Review migrated GitHub Actions workflows",
            **dict(self.cfg.get("pipeline_delivery", {})),
        }
        if delivery_policy["mode"] == "pull_request":
            staging_ref = f"refs/heads/{delivery_policy['branch']}"
            collisions = [
                repo.source_key
                for repo in planned
                if MigrationScope.PIPELINES.value in repo.scopes
                and (
                    repo.default_branch == delivery_policy["branch"]
                    or staging_ref in dict(repo.source_branch_refs)
                )
            ]
            if collisions:
                raise PlanIntegrityError(
                    "Pipeline staging branch collides with an approved source "
                    "branch for: " + ", ".join(sorted(collisions))
                )

        existing_targets = self._preflight_targets(planned, mapping_cfg)
        pipeline_snapshots = self._snapshot_pipelines(planned)
        non_git_snapshots = self._snapshot_non_git_scopes(planned)
        tasks = self._build_tasks(
            planned,
            existing_targets,
            pipeline_snapshots,
            non_git_snapshots,
        )
        safe_cfg = _non_secret_config(self.cfg)
        runtime_context = build_runtime_context(
            self.ado,
            self.gh,
            self.cfg,
            (repo.gh_org for repo in planned),
            source_org_url=source_org_url,
        )
        policy = {
            "mapping": {
                "strategy": mapping_cfg.get("strategy", "project-prefix"),
                "separator": mapping_cfg.get("separator", "-"),
                "lowercase": bool(mapping_cfg.get("lowercase", True)),
                "existing_target_policy": mapping_cfg.get(
                    "existing_target_policy", "fail"
                ),
                "allow_nonempty_target": bool(
                    mapping_cfg.get("allow_nonempty_target", False)
                ),
                "allowed_target_visibilities": list(
                    mapping_cfg.get(
                        "allowed_target_visibilities", ["private", "internal"]
                    )
                ),
                "preflight_targets": bool(
                    mapping_cfg.get("preflight_targets", True)
                ),
            },
            "pipeline_conversion": _non_secret_config(
                self.cfg.get("pipeline_conversion", {})
            ),
            "pipeline_delivery": _non_secret_config(delivery_policy),
            "validation": _non_secret_config(self.cfg.get("validation", {})),
            "cleanup": _non_secret_config(self.cfg.get("cleanup", {})),
            "governance": _non_secret_config(
                self.cfg.get("governance", {"mode": "disabled"})
            ),
            "source_selection": {
                "include_unlinked_work_items": bool(
                    self.cfg.get("include_unlinked_work_items", False)
                ),
            },
            "source_inventory": {
                "mode": (
                    "organization_snapshot"
                    if complete_org_inventory else "explicit_subset"
                ),
                "include_disabled": bool(
                    mapping_cfg.get("include_disabled", False)
                ),
                "repository_count": len(planned),
                "digest": content_digest([
                    {
                        "source_key": repo.source_key,
                        "source_repo_id": repo.source_repo_id,
                    }
                    for repo in sorted(
                        planned, key=lambda item: item.source_key.casefold()
                    )
                ]),
            },
            "runtime_context": runtime_context,
        }
        plan = MigrationPlan.create(
            source_org_url=source_org_url,
            target_org=target_org,
            repositories=sorted(planned, key=lambda item: item.source_key.casefold()),
            tasks=tasks,
            policy=policy,
            config_digest=content_digest(safe_cfg),
        )
        governance = self.cfg.get("governance", {"mode": "disabled"})
        if governance_is_strict(governance):
            authorize_plan_creation(
                plan,
                governance,
                planner_approval_envelope,
                self.db,
            )
        self._persist_mappings(plan, existing_targets)
        log.info(
            "PEV plan %s: %d repositories, %d tasks",
            plan.plan_id,
            len(plan.repositories),
            len(plan.tasks),
        )
        return plan

    def _discover_and_map(
        self,
        target_org: str,
        scopes: tuple[str, ...],
        mapping_cfg: dict[str, Any],
    ) -> list[PlannedRepository]:
        include_disabled = bool(mapping_cfg.get("include_disabled", False))
        planned: list[PlannedRepository] = []
        for project in self.ado.list_projects():
            project_name = str(project.get("name", "")).strip()
            if not project_name:
                continue
            for source in self.ado.list_repos(project_name):
                if source.get("isDisabled", False) and not include_disabled:
                    log.info("Skipping disabled ADO repo %s/%s", project_name, source.get("name"))
                    continue
                repo_name = str(source.get("name", "")).strip()
                if not repo_name:
                    continue
                gh_org, gh_repo = self._resolve_target(
                    project_name, repo_name, target_org, mapping_cfg
                )
                source_snapshot = self._snapshot_source_repository(
                    project_name, repo_name, initial=source
                )
                planned.append(
                    PlannedRepository(
                        ado_project=project_name,
                        ado_repo=repo_name,
                        gh_org=gh_org,
                        gh_repo=gh_repo,
                        scopes=scopes,
                        **source_snapshot,
                        pipeline_parallel=int(self.cfg.get("pipeline_parallel", 8)),
                    )
                )
        self._validate_mappings(planned)
        return planned

    def _map_explicit_repos(
        self,
        repos: list[RepoConfig],
        target_org: str,
        default_scopes: tuple[str, ...],
        mapping_cfg: dict[str, Any],
    ) -> list[PlannedRepository]:
        planned: list[PlannedRepository] = []
        apply_policy = bool(mapping_cfg.get("apply_to_explicit", False))
        for repo in repos:
            effective_scopes = tuple(repo.scopes or default_scopes)
            self._validate_scopes(effective_scopes)
            gh_org, gh_repo = repo.gh_org, repo.gh_repo
            if apply_policy or not gh_org or not gh_repo:
                gh_org, gh_repo = self._resolve_target(
                    repo.ado_project, repo.ado_repo, target_org, mapping_cfg
                )
            try:
                source = self.ado.get_repo(repo.ado_project, repo.ado_repo)
                source_snapshot = self._snapshot_source_repository(
                    repo.ado_project, repo.ado_repo, initial=source
                )
            except Exception as exc:
                raise PlanIntegrityError(
                    f"Cannot resolve source repo {repo.ado_project}/{repo.ado_repo}: {exc}"
                ) from exc
            mapped = RepoConfig(
                **{
                    **repo.__dict__,
                    "gh_org": gh_org,
                    "gh_repo": gh_repo,
                    "scopes": list(effective_scopes),
                }
            )
            planned.append(
                PlannedRepository.from_repo_config(
                    mapped,
                    **source_snapshot,
                )
            )
        self._validate_mappings(planned)
        return planned

    def _snapshot_source_repository(
        self,
        project: str,
        repo_name: str,
        *,
        initial: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Capture and stability-check the exact migratable source refs."""
        source = initial if isinstance(initial, dict) else self.ado.get_repo(
            project, repo_name
        )
        repo_id = str(source.get("id", "")).strip()
        if not repo_id:
            raise PlanIntegrityError(
                f"ADO source {project}/{repo_name} has no immutable repository ID"
            )
        default_branch = self._source_default_branch(source, project, repo_name)
        branch_refs, tag_refs = self._read_source_refs(project, repo_id)

        # Planning against an active repository can otherwise approve a mix of
        # two points in time (especially across continuation pages).  A second
        # read must be byte-for-byte stable; operators can retry after freezing
        # writes if it is not.
        confirmed = self.ado.get_repo(project, repo_id)
        confirmed_id = str(confirmed.get("id", "")).strip()
        confirmed_default = self._source_default_branch(
            confirmed, project, repo_name
        )
        confirmed_branches, confirmed_tags = self._read_source_refs(
            project, repo_id
        )
        if confirmed_id != repo_id:
            raise PlanIntegrityError(
                f"ADO source identity changed while planning {project}/{repo_name}"
            )
        if confirmed_default != default_branch:
            raise PlanIntegrityError(
                f"ADO default branch changed while planning {project}/{repo_name}"
            )
        if confirmed_branches != branch_refs or confirmed_tags != tag_refs:
            raise PlanIntegrityError(
                f"ADO refs changed while planning {project}/{repo_name}; "
                "freeze source writes and create a new plan"
            )

        branch_map = dict(branch_refs)
        head_sha = (
            branch_map.get(f"refs/heads/{default_branch}", "")
            if default_branch else ""
        )
        if branch_refs and not head_sha:
            raise PlanIntegrityError(
                f"ADO default branch {default_branch!r} is absent from the ref "
                f"snapshot for {project}/{repo_name}"
            )
        return {
            "source_repo_id": repo_id,
            "default_branch": default_branch,
            "source_head_sha": head_sha,
            "source_branch_refs": branch_refs,
            "source_tag_refs": tag_refs,
            "source_refs_digest": compute_source_refs_digest(
                branch_refs, tag_refs
            ),
        }

    def _read_source_refs(
        self, project: str, repo_id: str
    ) -> tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]:
        helper = getattr(self.ado, "list_refs", None)
        if not callable(helper):
            raise PlanIntegrityError(
                "ADO client must support complete paginated ref listing"
            )
        branches = canonical_ref_snapshot(
            helper(project, repo_id, "heads/"), "heads"
        )
        tags = canonical_ref_snapshot(
            helper(project, repo_id, "tags/"), "tags"
        )
        return branches, tags

    @staticmethod
    def _source_default_branch(
        source: dict[str, Any], project: str, repo_name: str
    ) -> str:
        raw = source.get("defaultBranch")
        if raw in (None, ""):
            return ""
        if not isinstance(raw, str) or not raw.startswith("refs/heads/") \
                or not raw[len("refs/heads/"):]:
            raise PlanIntegrityError(
                f"ADO source {project}/{repo_name} returned an invalid defaultBranch"
            )
        return raw[len("refs/heads/"):]

    @staticmethod
    def _validate_scopes(scopes: Iterable[str]) -> None:
        allowed = {scope.value for scope in MigrationScope}
        unknown = set(scopes) - allowed
        if unknown:
            raise PlanIntegrityError(f"Unsupported migration scopes: {sorted(unknown)}")

    def _resolve_target(
        self,
        project: str,
        repo: str,
        default_org: str,
        cfg: dict[str, Any],
    ) -> tuple[str, str]:
        source_key = f"{project}/{repo}"
        project_target_org = str(
            self._casefold_get(
                dict(cfg.get("project_to_org", {})), project, default_org
            )
        ).strip()
        project_cfg = self._casefold_get(dict(cfg.get("projects", {})), project, {})
        prefix = project
        if isinstance(project_cfg, str):
            prefix = project_cfg
        elif isinstance(project_cfg, dict):
            prefix = str(project_cfg.get("prefix", project))
            project_target_org = str(
                project_cfg.get("gh_org", project_target_org)
            ).strip()

        overrides = dict(cfg.get("repositories", {}))
        override = self._casefold_get(overrides, source_key)
        if override:
            if isinstance(override, str):
                if "/" in override:
                    org, name = override.split("/", 1)
                    return org.strip(), _slug(name, lowercase=False)
                return project_target_org, _slug(override, lowercase=False)
            if isinstance(override, dict):
                return (
                    str(override.get("gh_org") or project_target_org).strip(),
                    _slug(str(override["gh_repo"]), lowercase=False),
                )
            raise PlanIntegrityError(f"Invalid mapping override for {source_key}")

        strategy = str(cfg.get("strategy", "project-prefix"))
        lowercase = bool(cfg.get("lowercase", True))
        separator = str(cfg.get("separator", "-"))

        if strategy == "preserve":
            target_name = repo
        elif strategy == "project-prefix":
            target_name = f"{prefix}{separator}{repo}"
        else:
            raise PlanIntegrityError(
                f"Unknown mapping strategy {strategy!r}; use 'project-prefix' or 'preserve'"
            )
        return project_target_org, _slug(target_name, lowercase=lowercase)

    @staticmethod
    def _casefold_get(mapping: dict[str, Any], key: str, default: Any = None) -> Any:
        """Look up human-owned ADO mapping keys without case surprises."""
        folded = str(key).casefold()
        for candidate, value in mapping.items():
            if str(candidate).casefold() == folded:
                return value
        return default

    @staticmethod
    def _validate_mappings(repos: Iterable[PlannedRepository]) -> None:
        sources: dict[str, str] = {}
        targets: dict[str, str] = {}
        for repo in repos:
            source_folded = repo.source_key.casefold()
            target_folded = repo.target_key.casefold()
            if source_folded in sources:
                raise PlanIntegrityError(f"Duplicate source {repo.source_key}")
            if target_folded in targets:
                raise PlanIntegrityError(
                    f"Mapping collision: {targets[target_folded]} and {repo.source_key} "
                    f"both target {repo.target_key}"
                )
            sources[source_folded] = repo.source_key
            targets[target_folded] = repo.source_key

    def _preflight_targets(
        self,
        repos: Iterable[PlannedRepository],
        cfg: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        policy = str(cfg.get("existing_target_policy", "fail"))
        if policy not in {"fail", "reuse"}:
            raise PlanIntegrityError(
                "mapping.existing_target_policy must be 'fail' or 'reuse'"
            )
        existing: dict[str, dict[str, Any]] = {}
        if self.gh is None:
            raise PlanIntegrityError(
                "Enterprise planning requires a GitHub client for target preflight"
            )
        if not bool(cfg.get("preflight_targets", True)):
            raise PlanIntegrityError(
                "mapping.preflight_targets cannot be disabled for an approved PEV plan"
            )
        conflicts: list[str] = []
        for repo in repos:
            present = bool(self.gh.repo_exists(repo.gh_org, repo.gh_repo))
            if present and policy == "fail":
                conflicts.append(repo.target_key)
                existing[repo.source_key] = self._empty_target_evidence(True)
                continue
            if not present:
                existing[repo.source_key] = self._empty_target_evidence(False)
                continue
            target = self.gh.get_repo(repo.gh_org, repo.gh_repo)
            if not isinstance(target, dict):
                raise PlanIntegrityError(
                    f"GitHub target {repo.target_key} did not return repository metadata"
                )
            target_repo_id = str(
                target.get("node_id") or target.get("id") or ""
            ).strip()
            target_size = target.get("size")
            target_visibility = target.get("visibility")
            if not target_repo_id:
                raise PlanIntegrityError(
                    f"GitHub target {repo.target_key} has no immutable repository ID"
                )
            if isinstance(target_size, bool) or not isinstance(target_size, int) \
                    or target_size < 0:
                raise PlanIntegrityError(
                    f"GitHub target {repo.target_key} has invalid size metadata"
                )
            if target_visibility not in {"private", "internal", "public"}:
                raise PlanIntegrityError(
                    f"GitHub target {repo.target_key} has invalid visibility metadata"
                )
            allowed_visibilities = set(
                cfg.get("allowed_target_visibilities", ["private", "internal"])
            )
            if target_visibility not in allowed_visibilities:
                raise PlanIntegrityError(
                    f"GitHub target {repo.target_key} visibility "
                    f"{target_visibility!r} is not allowed by mapping policy"
                )
            branch_refs, tag_refs = self._snapshot_target_refs(repo)
            if (target_size > 0 or branch_refs or tag_refs) and not bool(
                cfg.get("allow_nonempty_target", False)
            ):
                raise PlanIntegrityError(
                    f"GitHub target {repo.target_key} is non-empty but "
                    "mapping.allow_nonempty_target is false"
                )
            target_default_branch = target.get("default_branch", "")
            if not isinstance(target_default_branch, str):
                raise PlanIntegrityError(
                    f"GitHub target {repo.target_key} has invalid default_branch metadata"
                )
            target_default_branch = target_default_branch.strip()
            if branch_refs and (
                not target_default_branch
                or f"refs/heads/{target_default_branch}" not in dict(branch_refs)
            ):
                raise PlanIntegrityError(
                    f"GitHub target {repo.target_key} default branch is absent "
                    "from its complete ref snapshot"
                )
            existing[repo.source_key] = {
                "target_exists": True,
                "target_repo_id": target_repo_id,
                "target_size": target_size,
                "target_visibility": target_visibility,
                "target_default_branch": target_default_branch,
                "target_branch_refs": dict(branch_refs),
                "target_tag_refs": dict(tag_refs),
                "target_refs_digest": compute_source_refs_digest(
                    branch_refs, tag_refs
                ),
            }
        if conflicts:
            raise PlanIntegrityError(
                "Target repositories already exist and policy is 'fail': "
                + ", ".join(sorted(conflicts))
            )
        return existing

    @staticmethod
    def _empty_target_evidence(target_exists: bool) -> dict[str, Any]:
        empty_digest = compute_source_refs_digest((), ())
        return {
            "target_exists": target_exists,
            "target_repo_id": "",
            "target_size": 0,
            "target_visibility": "private",
            "target_default_branch": "",
            "target_branch_refs": {},
            "target_tag_refs": {},
            "target_refs_digest": empty_digest,
        }

    def _snapshot_target_refs(
        self, repo: PlannedRepository
    ) -> tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]:
        helper = getattr(self.gh, "list_git_refs", None)
        if not callable(helper):
            raise PlanIntegrityError(
                "GitHub client must support complete paginated ref listing "
                "when adopting an existing target"
            )

        def read(namespace: str) -> tuple[tuple[str, str], ...]:
            rows = helper(repo.gh_org, repo.gh_repo, namespace)
            if not isinstance(rows, list):
                raise PlanIntegrityError(
                    f"GitHub refs/{namespace} response must be a complete list"
                )
            converted: list[dict[str, Any]] = []
            for index, row in enumerate(rows):
                if not isinstance(row, dict):
                    raise PlanIntegrityError(
                        f"GitHub refs/{namespace} item {index} must be an object"
                    )
                obj = row.get("object", {})
                if not isinstance(obj, dict):
                    raise PlanIntegrityError(
                        f"GitHub ref {row.get('ref')!r} has no object metadata"
                    )
                converted.append({
                    "name": row.get("ref"),
                    "objectId": obj.get("sha"),
                })
            return canonical_ref_snapshot(converted, namespace)

        return read("heads"), read("tags")

    @staticmethod
    def _task_id(kind: str, source_key: str, scope: str = "") -> str:
        suffix = content_digest({"kind": kind, "source": source_key, "scope": scope})[:16]
        return f"{kind}_{suffix}"

    def _build_tasks(
        self,
        repos: Iterable[PlannedRepository],
        existing_targets: dict[str, dict[str, Any]],
        pipeline_snapshots: Optional[dict[str, dict[str, Any]]] = None,
        non_git_snapshots: Optional[dict[str, dict[str, dict[str, Any]]]] = None,
    ) -> tuple[PlanTask, ...]:
        tasks: list[PlanTask] = []
        pipeline_snapshots = pipeline_snapshots or {}
        non_git_snapshots = non_git_snapshots or {}
        for repo in sorted(repos, key=lambda item: item.source_key.casefold()):
            preflight_id = self._task_id("preflight", repo.source_key)
            tasks.append(
                PlanTask(
                    task_id=preflight_id,
                    kind="preflight",
                    source_key=repo.source_key,
                    target_key=repo.target_key,
                    metadata=dict(existing_targets.get(repo.source_key, {})),
                )
            )
            inventory_id = ""
            if MigrationScope.PIPELINES.value in repo.scopes:
                inventory_id = self._task_id("inventory", repo.source_key, "pipelines")
                tasks.append(
                    PlanTask(
                        task_id=inventory_id,
                        kind="inventory",
                        source_key=repo.source_key,
                        target_key=repo.target_key,
                        scope=MigrationScope.PIPELINES.value,
                        dependencies=(preflight_id,),
                        metadata=dict(pipeline_snapshots.get(repo.source_key, {})),
                    )
                )

            execution_ids: list[str] = []
            repo_task_id = ""
            # Repository creation is a hard dependency for every other scope,
            # regardless of the order supplied by a CSV/config author.
            ordered_scopes = [
                scope.value for scope in MigrationScope if scope.value in repo.scopes
            ]
            for scope in ordered_scopes:
                task_id = self._task_id("execute", repo.source_key, scope)
                dependencies = [preflight_id]
                if scope != MigrationScope.REPO.value and repo_task_id:
                    dependencies.append(repo_task_id)
                if scope == MigrationScope.PIPELINES.value and inventory_id:
                    dependencies.append(inventory_id)
                execution_mode = "hybrid" if scope == MigrationScope.PIPELINES.value else "deterministic"
                metadata: dict[str, Any] = {}
                if scope in BOUND_NON_GIT_SCOPES:
                    snapshot = non_git_snapshots.get(repo.source_key, {}).get(scope)
                    if snapshot is None:
                        raise PlanIntegrityError(
                            f"Missing {scope} source snapshot for {repo.source_key}"
                        )
                    metadata["source_snapshot"] = dict(snapshot)
                tasks.append(
                    PlanTask(
                        task_id=task_id,
                        kind="execute",
                        source_key=repo.source_key,
                        target_key=repo.target_key,
                        scope=scope,
                        dependencies=tuple(dict.fromkeys(dependencies)),
                        execution_mode=execution_mode,
                        metadata=metadata,
                    )
                )
                execution_ids.append(task_id)
                if scope == MigrationScope.REPO.value:
                    repo_task_id = task_id

            tasks.append(
                PlanTask(
                    task_id=self._task_id("validate", repo.source_key),
                    kind="validate",
                    source_key=repo.source_key,
                    target_key=repo.target_key,
                    dependencies=tuple(execution_ids),
                )
            )
        return tuple(tasks)

    def _snapshot_pipelines(
        self, repos: Iterable[PlannedRepository]
    ) -> dict[str, dict[str, Any]]:
        pipeline_repos = [
            repo for repo in repos
            if MigrationScope.PIPELINES.value in repo.scopes
        ]
        if not pipeline_repos:
            return {}
        state = self.db
        if state is None:
            from ado2gh.state.db import StateDB
            state = StateDB(":memory:")
        builder = PipelineInventoryBuilder(
            self.ado,
            state,
            parallel=int(self.cfg.get("pipeline_parallel", 8)),
            dry_run=False,
            strict=True,
        )
        projects = sorted({repo.ado_project for repo in pipeline_repos})
        builder.build_for_projects(projects, include_releases=True)
        snapshots: dict[str, dict[str, Any]] = {}
        for repo in pipeline_repos:
            receipts = state.get_pipeline_inventory_receipts(
                repo.ado_project, repo.ado_repo
            )
            snapshots[repo.source_key] = {
                "pipeline_count": len(receipts),
                "inventory_digest": content_digest(receipts),
                "pipelines": receipts,
            }
        return snapshots

    def _snapshot_non_git_scopes(
        self, repos: Iterable[PlannedRepository]
    ) -> dict[str, dict[str, dict[str, Any]]]:
        """Capture only content digests/counts; never persist source payloads."""
        repos = tuple(repos)
        snapshots: dict[str, dict[str, dict[str, Any]]] = {}
        include_unlinked = bool(self.cfg.get("include_unlinked_work_items", False))
        work_item_repos = [
            repo for repo in repos
            if MigrationScope.WORK_ITEMS.value in repo.scopes
        ]
        work_item_payloads = (
            fetch_project_work_item_payloads(
                self.ado,
                work_item_repos,
                include_unlinked_work_items=include_unlinked,
            )
            if work_item_repos else {}
        )
        for repo in repos:
            for scope in repo.scopes:
                if scope not in BOUND_NON_GIT_SCOPES:
                    continue
                try:
                    if scope == MigrationScope.WORK_ITEMS.value:
                        payload = work_item_payloads[repo.source_key]
                    else:
                        payload = fetch_scope_payload(
                            self.ado,
                            repo,
                            scope,
                            source_repo_id=repo.source_repo_id,
                            include_unlinked_work_items=include_unlinked,
                        )
                    snapshot = create_scope_snapshot(scope, payload)
                except Exception as exc:
                    raise PlanIntegrityError(
                        f"Cannot snapshot {scope} for {repo.source_key}: {exc}"
                    ) from exc
                snapshots.setdefault(repo.source_key, {})[scope] = snapshot
        return snapshots

    def _persist_mappings(
        self, plan: MigrationPlan,
        existing_targets: dict[str, dict[str, Any]],
    ) -> None:
        if self.db is None or not hasattr(self.db, "upsert_repository_mapping"):
            return
        for repo in plan.repositories:
            try:
                self.db.upsert_repository_mapping(
                    source_org=plan.source_org_url,
                    ado_project=repo.ado_project,
                    ado_repo=repo.ado_repo,
                    gh_org=repo.gh_org,
                    gh_repo=repo.gh_repo,
                    policy=str(plan.policy.get("mapping", {}).get("strategy", "explicit")),
                    mapping_id=content_digest(
                        {"source": repo.source_key, "target": repo.target_key}
                    )[:32],
                    fingerprint=plan.plan_id,
                    status=(
                        "adopted"
                        if existing_targets.get(repo.source_key, {}).get(
                            "target_exists"
                        ) is True
                        else "planned"
                        if existing_targets.get(repo.source_key, {}).get(
                            "target_exists"
                        ) is False
                        else "unverified"
                    ),
                )
            except TypeError:
                # Older StateDBs remain usable while schema migrations are optional.
                log.debug("StateDB mapping API has an older signature; skipping registry write")
                return
