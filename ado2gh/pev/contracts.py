"""Immutable contracts shared by the Planner, Executor, and Validator.

The plan is deliberately serialisable and content-addressed.  A production
operator can review a plan, approve its ``plan_id``, and be certain the
executor is running the exact repository/scope mapping that was reviewed.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Iterable, Optional
from urllib.parse import urlsplit

from ado2gh.models import MigrationScope, RepoConfig


PLAN_SCHEMA_VERSION = 8

_GIT_OBJECT_ID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PRINCIPAL_KEY = re.compile(r"^sha256:[0-9a-f]{64}$")
_TEAM_SLUG = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,99})$")
_BOUND_NON_GIT_SCOPES = {
    MigrationScope.WORK_ITEMS.value,
    MigrationScope.WIKI.value,
    MigrationScope.SECRETS.value,
    MigrationScope.BRANCH_POLICIES.value,
}


class PlanIntegrityError(ValueError):
    """Raised when a plan is malformed or its content digest is invalid."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def content_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def canonical_ref_snapshot(
    rows: Any,
    namespace: str,
) -> tuple[tuple[str, str], ...]:
    """Normalize one complete ADO ref response into a canonical snapshot.

    Ref names remain fully qualified because shortening them loses the exact
    namespace which is pushed by a mirror.  The function deliberately rejects
    malformed, duplicate, or out-of-namespace rows instead of silently
    producing a partial approval artifact.
    """
    if namespace not in {"heads", "tags"}:
        raise PlanIntegrityError(f"Unsupported ref namespace {namespace!r}")
    if isinstance(rows, dict) and "value" in rows:
        rows = rows["value"]
    if not isinstance(rows, list):
        raise PlanIntegrityError(
            f"ADO refs/{namespace} response must be a complete list"
        )

    prefix = f"refs/{namespace}/"
    refs: dict[str, str] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise PlanIntegrityError(
                f"ADO refs/{namespace} item {index} must be an object"
            )
        name = row.get("name")
        sha = row.get("objectId")
        if not isinstance(name, str) or not name.startswith(prefix) \
                or not name[len(prefix):]:
            raise PlanIntegrityError(
                f"ADO refs/{namespace} item {index} has an invalid full ref name"
            )
        if not isinstance(sha, str) or not sha.strip():
            raise PlanIntegrityError(f"ADO ref {name!r} has no object ID")
        sha = sha.strip().lower()
        if not _GIT_OBJECT_ID.fullmatch(sha):
            raise PlanIntegrityError(
                f"ADO ref {name!r} has an invalid Git object ID"
            )
        previous = refs.get(name)
        if previous is not None:
            raise PlanIntegrityError(
                f"ADO refs/{namespace} response contains duplicate ref {name!r}"
            )
        refs[name] = sha
    return tuple(sorted(refs.items()))


def compute_source_refs_digest(
    branch_refs: Iterable[tuple[str, str]],
    tag_refs: Iterable[tuple[str, str]],
) -> str:
    """Content digest for the only source ref namespaces migration pushes."""
    return content_digest({
        "branches": [list(item) for item in branch_refs],
        "tags": [list(item) for item in tag_refs],
    })


def _ref_pairs(value: Any, field_name: str) -> tuple[tuple[str, str], ...]:
    """Deserialize ref maps emitted by v3 while accepting canonical pairs."""
    if isinstance(value, dict):
        pairs = sorted(value.items())
    elif isinstance(value, (list, tuple)):
        pairs = value
    else:
        raise PlanIntegrityError(f"{field_name} must be a ref map")
    try:
        return tuple((str(name), str(sha)) for name, sha in pairs)
    except (TypeError, ValueError) as exc:
        raise PlanIntegrityError(f"{field_name} must contain name/SHA pairs") from exc


def _team_access_entries(value: Any) -> tuple[tuple[str, str, str], ...]:
    """Deserialize explicit, least-privilege team access mappings."""
    if not isinstance(value, dict):
        raise PlanIntegrityError("team_mapping must be an object")
    entries: list[tuple[str, str, str]] = []
    for source_team, raw in value.items():
        if not isinstance(source_team, str) \
                or not _PRINCIPAL_KEY.fullmatch(source_team.strip()):
            raise PlanIntegrityError(
                "team_mapping keys must be exact hashed ACL principal keys"
            )
        if not isinstance(raw, dict):
            raise PlanIntegrityError(
                "team_mapping values must declare github_team and permission; "
                "implicit write access is prohibited"
            )
        github_team = raw.get("github_team")
        permission = raw.get("permission")
        if not isinstance(github_team, str) \
                or not _TEAM_SLUG.fullmatch(github_team.strip()):
            raise PlanIntegrityError(
                "team_mapping github_team must be an exact GitHub team slug"
            )
        if permission not in {"pull", "triage", "push", "maintain", "admin"}:
            raise PlanIntegrityError(
                "team_mapping permission must be pull, triage, push, maintain, or admin"
            )
        entries.append((source_team.strip(), github_team.strip(), permission))
    return tuple(sorted(entries, key=lambda item: item[0].casefold()))


def access_disposition_digest(
    source_access_digest: str,
    team_mapping: Iterable[tuple[str, str, str]],
    excluded_principals: dict[str, str],
    identity_mapping: dict[str, str],
) -> str:
    """Bind every mapped/excluded principal decision to one access snapshot."""
    mapped = [
        {
            "principal_key": source,
            "github_team": target,
            "permission": permission,
        }
        for source, target, permission in sorted(
            team_mapping, key=lambda item: item[0]
        )
    ]
    excluded = [
        {"principal_key": principal, "rationale": rationale}
        for principal, rationale in sorted(excluded_principals.items())
    ]
    return "sha256:" + content_digest({
        "schema_version": 1,
        "source_access_digest": source_access_digest,
        "mapped": mapped,
        "excluded": excluded,
        "identity_mapping": [
            {"source_identity": source, "target_identity": target}
            for source, target in sorted(identity_mapping.items())
        ],
    })


def target_access_policy_digest(
    base_repository_permission: str,
    team_mapping: Iterable[tuple[str, str, str]],
    target_team_members: dict[str, list[str]],
    target_team_parents: dict[str, str],
    target_deploy_keys: dict[str, bool],
    target_github_apps: dict[str, Any],
) -> str:
    """Bind the exhaustive repository access surface approved for GitHub."""
    if base_repository_permission not in {"none", "read", "write", "admin"}:
        raise PlanIntegrityError(
            "target base repository permission must be none, read, write, or admin"
        )
    teams: dict[str, str] = {}
    for _, slug, permission in team_mapping:
        folded = slug.casefold()
        previous = teams.get(folded)
        if previous is not None and previous != permission:
            raise PlanIntegrityError(
                f"GitHub team {slug!r} has conflicting approved permissions"
            )
        teams[folded] = permission
    if not isinstance(target_team_members, dict):
        raise PlanIntegrityError("target_team_members must be an object")
    canonical_members: dict[str, list[str]] = {}
    for raw_slug, raw_members in target_team_members.items():
        if not isinstance(raw_slug, str) \
                or not _TEAM_SLUG.fullmatch(raw_slug.strip()):
            raise PlanIntegrityError(
                "target_team_members keys must be exact GitHub team slugs"
            )
        slug = raw_slug.strip().casefold()
        if slug in canonical_members or not isinstance(raw_members, list):
            raise PlanIntegrityError(
                "target_team_members contains duplicate or invalid teams"
            )
        if (
            raw_members != sorted(set(raw_members))
            or any(
                not isinstance(member, str)
                or not _PRINCIPAL_KEY.fullmatch(member)
                for member in raw_members
            )
        ):
            raise PlanIntegrityError(
                f"Target members for GitHub team {raw_slug!r} are invalid"
            )
        canonical_members[slug] = list(raw_members)
    if set(canonical_members) != set(teams):
        raise PlanIntegrityError(
            "target_team_members must cover every mapped GitHub team exactly"
        )
    if not isinstance(target_team_parents, dict):
        raise PlanIntegrityError("target_team_parents must be an object")
    canonical_parents: dict[str, str] = {}
    for raw_slug, parent_key in target_team_parents.items():
        if not isinstance(raw_slug, str) \
                or not _TEAM_SLUG.fullmatch(raw_slug.strip()):
            raise PlanIntegrityError(
                "target_team_parents keys must be exact GitHub team slugs"
            )
        slug = raw_slug.strip().casefold()
        if slug in canonical_parents or not isinstance(parent_key, str) or (
            parent_key and not _PRINCIPAL_KEY.fullmatch(parent_key)
        ):
            raise PlanIntegrityError(
                f"Target parent for GitHub team {raw_slug!r} is invalid"
            )
        canonical_parents[slug] = parent_key
    if set(canonical_parents) != set(teams):
        raise PlanIntegrityError(
            "target_team_parents must cover every mapped GitHub team exactly"
        )
    if not isinstance(target_deploy_keys, dict) or any(
        not isinstance(key, str)
        or not _PRINCIPAL_KEY.fullmatch(key)
        or not isinstance(read_only, bool)
        for key, read_only in target_deploy_keys.items()
    ):
        raise PlanIntegrityError(
            "target_deploy_keys must map hashed immutable key IDs to read_only flags"
        )
    if target_github_apps != {}:
        raise PlanIntegrityError(
            "target_github_apps must be empty until exhaustive GitHub App "
            "installation access can be inventoried"
        )
    return "sha256:" + content_digest({
        "schema_version": 1,
        "base_repository_permission": base_repository_permission,
        "teams": [
            {
                "slug": slug,
                "permission": teams[slug],
                "member_keys": canonical_members[slug],
                "parent_key": canonical_parents[slug],
            }
            for slug in sorted(teams)
        ],
        "deploy_keys": [
            {"key": key, "read_only": target_deploy_keys[key]}
            for key in sorted(target_deploy_keys)
        ],
        # Direct and outside collaborators are deliberately unsupported. A
        # source principal must map through an explicitly reviewed team.
        "direct_collaborators": [],
        "pending_invitations": [],
        "github_app_installations": [],
    })


def github_user_identity_key(node_id: str) -> str:
    """Return the non-reversible plan key for an immutable GitHub user ID."""
    value = str(node_id).strip()
    if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise PlanIntegrityError("GitHub user node ID is invalid")
    return "sha256:" + content_digest({
        "schema_version": 1,
        "kind": "github_user_node_id",
        "value": value,
    })


def github_team_identity_key(node_id: str) -> str:
    """Return the non-reversible plan key for an immutable GitHub team ID."""
    value = str(node_id).strip()
    if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise PlanIntegrityError("GitHub team node ID is invalid")
    return "sha256:" + content_digest({
        "schema_version": 1,
        "kind": "github_team_node_id",
        "value": value,
    })


def github_deploy_key_identity_key(key_id: str) -> str:
    """Return the non-reversible plan key for an immutable deploy-key ID."""
    value = str(key_id).strip()
    if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise PlanIntegrityError("GitHub deploy-key ID is invalid")
    return "sha256:" + content_digest({
        "schema_version": 1,
        "kind": "github_deploy_key_id",
        "value": value,
    })


@dataclass(frozen=True)
class PlannedRepository:
    ado_project: str
    ado_repo: str
    gh_org: str
    gh_repo: str
    scopes: tuple[str, ...] = (MigrationScope.REPO.value,)
    source_repo_id: str = ""
    default_branch: str = ""
    source_head_sha: str = ""
    source_branch_refs: tuple[tuple[str, str], ...] = ()
    source_tag_refs: tuple[tuple[str, str], ...] = ()
    source_refs_digest: str = ""
    team_mapping: tuple[tuple[str, str, str], ...] = ()
    access_policy_approved: bool = False
    access_policy_evidence: dict[str, Any] = field(default_factory=dict)
    source_access_snapshot: dict[str, Any] = field(default_factory=dict)
    skip_lfs: bool = False
    archive_source: bool = False
    tags: tuple[str, ...] = ()
    pipeline_parallel: int = 8
    pipeline_filter: str = ""
    risk_score: float = 0.0
    phase: str = ""

    @property
    def source_key(self) -> str:
        return f"{self.ado_project}/{self.ado_repo}"

    @property
    def target_key(self) -> str:
        return f"{self.gh_org}/{self.gh_repo}"

    @property
    def source_branch_map(self) -> dict[str, str]:
        return dict(self.source_branch_refs)

    @property
    def source_tag_map(self) -> dict[str, str]:
        return dict(self.source_tag_refs)

    def to_repo_config(self) -> RepoConfig:
        return RepoConfig(
            ado_project=self.ado_project,
            ado_repo=self.ado_repo,
            gh_org=self.gh_org,
            gh_repo=self.gh_repo,
            scopes=list(self.scopes),
            team_mapping={
                source: {"github_team": target, "permission": permission}
                for source, target, permission in self.team_mapping
            },
            access_policy_approved=self.access_policy_approved,
            access_policy_evidence=dict(self.access_policy_evidence),
            source_access_snapshot=dict(self.source_access_snapshot),
            skip_lfs=self.skip_lfs,
            archive_source=self.archive_source,
            tags=list(self.tags),
            pipeline_parallel=self.pipeline_parallel,
            pipeline_filter=self.pipeline_filter,
            risk_score=self.risk_score,
            phase=self.phase,
        )

    @classmethod
    def from_repo_config(
        cls,
        repo: RepoConfig,
        source_repo_id: str = "",
        default_branch: str = "",
        source_head_sha: str = "",
        source_branch_refs: Iterable[tuple[str, str]] = (),
        source_tag_refs: Iterable[tuple[str, str]] = (),
        source_refs_digest: str = "",
    ) -> "PlannedRepository":
        branch_refs = tuple(source_branch_refs)
        tag_refs = tuple(source_tag_refs)
        return cls(
            ado_project=repo.ado_project,
            ado_repo=repo.ado_repo,
            gh_org=repo.gh_org,
            gh_repo=repo.gh_repo,
            scopes=tuple(repo.scopes),
            source_repo_id=source_repo_id,
            default_branch=default_branch,
            source_head_sha=source_head_sha,
            source_branch_refs=branch_refs,
            source_tag_refs=tag_refs,
            source_refs_digest=(
                source_refs_digest
                or compute_source_refs_digest(branch_refs, tag_refs)
            ),
            team_mapping=_team_access_entries(repo.team_mapping),
            access_policy_approved=bool(repo.access_policy_approved),
            access_policy_evidence=dict(repo.access_policy_evidence),
            source_access_snapshot=dict(repo.source_access_snapshot),
            skip_lfs=repo.skip_lfs,
            archive_source=repo.archive_source,
            tags=tuple(repo.tags),
            pipeline_parallel=repo.pipeline_parallel,
            pipeline_filter=repo.pipeline_filter,
            risk_score=repo.risk_score,
            phase=repo.phase,
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["scopes"] = list(self.scopes)
        data["team_mapping"] = {
            source: {"github_team": target, "permission": permission}
            for source, target, permission in self.team_mapping
        }
        data["tags"] = list(self.tags)
        data["source_branch_refs"] = dict(self.source_branch_refs)
        data["source_tag_refs"] = dict(self.source_tag_refs)
        return data

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PlannedRepository":
        return cls(
            ado_project=str(value["ado_project"]),
            ado_repo=str(value["ado_repo"]),
            gh_org=str(value["gh_org"]),
            gh_repo=str(value["gh_repo"]),
            scopes=tuple(value.get("scopes", [MigrationScope.REPO.value])),
            source_repo_id=str(value.get("source_repo_id", "")),
            default_branch=str(value.get("default_branch", "")),
            source_head_sha=str(value.get("source_head_sha", "")),
            source_branch_refs=_ref_pairs(
                value.get("source_branch_refs", {}), "source_branch_refs"
            ),
            source_tag_refs=_ref_pairs(
                value.get("source_tag_refs", {}), "source_tag_refs"
            ),
            source_refs_digest=str(value.get("source_refs_digest", "")),
            team_mapping=_team_access_entries(value.get("team_mapping", {})),
            access_policy_approved=bool(
                value.get("access_policy_approved", False)
            ),
            access_policy_evidence=dict(
                value.get("access_policy_evidence", {})
            ),
            source_access_snapshot=dict(
                value.get("source_access_snapshot", {})
            ),
            skip_lfs=bool(value.get("skip_lfs", False)),
            archive_source=bool(value.get("archive_source", False)),
            tags=tuple(value.get("tags", [])),
            pipeline_parallel=int(value.get("pipeline_parallel", 8)),
            pipeline_filter=str(value.get("pipeline_filter", "")),
            risk_score=float(value.get("risk_score", 0.0)),
            phase=str(value.get("phase", "")),
        )


@dataclass(frozen=True)
class PlanTask:
    task_id: str
    kind: str
    source_key: str
    target_key: str
    scope: str = ""
    dependencies: tuple[str, ...] = ()
    execution_mode: str = "deterministic"
    validation_policy: str = "required"
    metadata: dict[str, Any] = field(default_factory=dict, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "kind": self.kind,
            "source_key": self.source_key,
            "target_key": self.target_key,
            "scope": self.scope,
            "dependencies": list(self.dependencies),
            "execution_mode": self.execution_mode,
            "validation_policy": self.validation_policy,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PlanTask":
        return cls(
            task_id=str(value["task_id"]),
            kind=str(value["kind"]),
            source_key=str(value.get("source_key", "")),
            target_key=str(value.get("target_key", "")),
            scope=str(value.get("scope", "")),
            dependencies=tuple(value.get("dependencies", [])),
            execution_mode=str(value.get("execution_mode", "deterministic")),
            validation_policy=str(value.get("validation_policy", "required")),
            metadata=dict(value.get("metadata", {})),
        )


@dataclass(frozen=True)
class MigrationPlan:
    plan_id: str
    source_org_url: str
    target_org: str
    repositories: tuple[PlannedRepository, ...]
    tasks: tuple[PlanTask, ...]
    policy: dict[str, Any]
    config_digest: str
    created_at: str
    schema_version: int = PLAN_SCHEMA_VERSION

    @staticmethod
    def _identity_payload(
        source_org_url: str,
        target_org: str,
        repositories: Iterable[PlannedRepository],
        tasks: Iterable[PlanTask],
        policy: dict[str, Any],
        config_digest: str,
        schema_version: int = PLAN_SCHEMA_VERSION,
    ) -> dict[str, Any]:
        return {
            "schema_version": schema_version,
            "source_org_url": source_org_url.rstrip("/"),
            "target_org": target_org,
            "repositories": [r.to_dict() for r in repositories],
            "tasks": [t.to_dict() for t in tasks],
            "policy": policy,
            "config_digest": config_digest,
        }

    @classmethod
    def create(
        cls,
        source_org_url: str,
        target_org: str,
        repositories: Iterable[PlannedRepository],
        tasks: Iterable[PlanTask],
        policy: dict[str, Any],
        config_digest: str,
    ) -> "MigrationPlan":
        repos_tuple = tuple(repositories)
        tasks_tuple = tuple(tasks)
        # Programmatic callers constructing an absent-target preflight under
        # the new schema inherit the only visibility the executor creates.
        # Existing targets must still supply observed visibility explicitly.
        tasks_tuple = tuple(
            replace(
                task,
                metadata={**task.metadata, "target_visibility": "private"},
            )
            if task.kind == "preflight"
            and task.metadata.get("target_exists") is False
            and "target_visibility" not in task.metadata
            else task
            for task in tasks_tuple
        )
        normalized_policy = dict(policy)
        normalized_mapping = dict(normalized_policy.get("mapping", {}))
        normalized_mapping.setdefault(
            "allowed_target_visibilities", ["private", "internal"]
        )
        normalized_policy["mapping"] = normalized_mapping
        normalized_source_selection = dict(
            normalized_policy.get("source_selection", {})
        )
        normalized_source_selection.setdefault(
            "include_unlinked_work_items", False
        )
        normalized_policy["source_selection"] = normalized_source_selection
        normalized_policy.setdefault(
            "source_inventory",
            {
                "mode": "explicit_subset",
                "include_disabled": False,
                "repository_count": len(repos_tuple),
                "digest": content_digest([
                    {
                        "source_key": repo.source_key,
                        "source_repo_id": repo.source_repo_id,
                    }
                    for repo in sorted(
                        repos_tuple, key=lambda item: item.source_key.casefold()
                    )
                ]),
            },
        )
        # Programmatic/test-double callers may not expose organization lookup
        # APIs. Preserve an explicit lower-assurance logical authority instead
        # of leaving the runtime context implicit. The production planner
        # replaces this with immutable ADO/GitHub organization identifiers.
        normalized_policy.setdefault(
            "runtime_context",
            {
                "source_api_url": source_org_url.rstrip("/"),
                "source_org_identity": {
                    "kind": "logical",
                    "id": "logical:" + content_digest({
                        "namespace": "ado-org",
                        "values": [source_org_url.rstrip("/").casefold()],
                    }),
                },
                "target_api_url": "https://api.github.com",
                "target_org_identities": {
                    org: {
                        "kind": "logical",
                        "id": "logical:" + content_digest({
                            "namespace": "github-org",
                            "values": ["https://api.github.com", org.casefold()],
                        }),
                    }
                    for org in sorted(
                        {repo.gh_org for repo in repos_tuple}, key=str.casefold
                    )
                },
            },
        )
        payload = cls._identity_payload(
            source_org_url,
            target_org,
            repos_tuple,
            tasks_tuple,
            normalized_policy,
            config_digest,
        )
        plan = cls(
            plan_id=f"plan_{content_digest(payload)[:24]}",
            source_org_url=source_org_url.rstrip("/"),
            target_org=target_org,
            repositories=repos_tuple,
            tasks=tasks_tuple,
            policy=normalized_policy,
            config_digest=config_digest,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        plan.validate()
        return plan

    def validate(self) -> None:
        if self.schema_version != PLAN_SCHEMA_VERSION:
            raise PlanIntegrityError(
                f"Unsupported plan schema {self.schema_version}; expected {PLAN_SCHEMA_VERSION}"
            )

        # Authenticate the complete content-addressed artifact before reporting
        # any semantic detail from it.  This gives operators one unambiguous
        # signal for every post-approval mutation, while the checks below still
        # reject malformed plans whose digest was recomputed by their author.
        payload = self._identity_payload(
            self.source_org_url,
            self.target_org,
            self.repositories,
            self.tasks,
            self.policy,
            self.config_digest,
            self.schema_version,
        )
        expected = f"plan_{content_digest(payload)[:24]}"
        if self.plan_id != expected:
            raise PlanIntegrityError(
                f"Plan digest mismatch: expected {expected}, received {self.plan_id}"
            )

        if not self.source_org_url or not self.target_org:
            raise PlanIntegrityError("Plan requires source_org_url and target_org")
        if not self.repositories:
            raise PlanIntegrityError("Plan contains no repositories")

        runtime = self.policy.get("runtime_context")
        if not isinstance(runtime, dict):
            raise PlanIntegrityError("Plan requires a runtime authority context")
        source_api_url = runtime.get("source_api_url")
        target_api_url = runtime.get("target_api_url")
        for label, raw_url in (
            ("source_api_url", source_api_url),
            ("target_api_url", target_api_url),
        ):
            parsed = urlsplit(str(raw_url or ""))
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise PlanIntegrityError(
                    f"Runtime {label} must be absolute HTTPS without credentials, "
                    "query, or fragment"
                )
        if str(source_api_url).rstrip("/").casefold() != (
            self.source_org_url.rstrip("/").casefold()
        ):
            raise PlanIntegrityError(
                "Runtime source API URL does not match plan source organization"
            )
        source_identity = runtime.get("source_org_identity")
        if not isinstance(source_identity, dict):
            raise PlanIntegrityError("Runtime source organization identity is invalid")
        if source_identity.get("kind") not in {"immutable", "logical"} \
                or not str(source_identity.get("id") or "").strip():
            raise PlanIntegrityError("Runtime source organization identity is incomplete")
        target_identities = runtime.get("target_org_identities")
        if not isinstance(target_identities, dict):
            raise PlanIntegrityError("Runtime target organization identities are invalid")
        expected_orgs = {repo.gh_org.casefold() for repo in self.repositories}
        observed_orgs = {str(org).casefold() for org in target_identities}
        if expected_orgs != observed_orgs:
            raise PlanIntegrityError(
                "Runtime target organization identities do not cover exact plan targets"
            )
        for org, identity in target_identities.items():
            if not str(org).strip() or not isinstance(identity, dict):
                raise PlanIntegrityError("Runtime target organization identity is invalid")
            if identity.get("kind") not in {"immutable", "logical"} \
                    or not str(identity.get("id") or "").strip():
                raise PlanIntegrityError(
                    f"Runtime target organization identity is incomplete for {org!r}"
                )
        source_selection = self.policy.get("source_selection")
        if (
            not isinstance(source_selection, dict)
            or set(source_selection) != {"include_unlinked_work_items"}
            or not isinstance(
                source_selection.get("include_unlinked_work_items"), bool
            )
        ):
            raise PlanIntegrityError("Plan source-selection policy is invalid")
        source_inventory = self.policy.get("source_inventory")
        if not isinstance(source_inventory, dict) or set(source_inventory) != {
            "mode", "include_disabled", "repository_count", "digest"
        }:
            raise PlanIntegrityError("Plan source-inventory policy is invalid")
        if source_inventory.get("mode") not in {
            "organization_snapshot", "explicit_subset"
        } or not isinstance(source_inventory.get("include_disabled"), bool):
            raise PlanIntegrityError("Plan source-inventory mode is invalid")
        if (
            isinstance(source_inventory.get("repository_count"), bool)
            or not isinstance(source_inventory.get("repository_count"), int)
            or source_inventory["repository_count"] != len(self.repositories)
            or not isinstance(source_inventory.get("digest"), str)
            or not _SHA256.fullmatch(source_inventory["digest"])
        ):
            raise PlanIntegrityError("Plan source-inventory receipt is invalid")
        expected_inventory_digest = content_digest([
            {
                "source_key": repo.source_key,
                "source_repo_id": repo.source_repo_id,
            }
            for repo in sorted(
                self.repositories, key=lambda item: item.source_key.casefold()
            )
        ])
        if source_inventory["digest"] != expected_inventory_digest:
            raise PlanIntegrityError("Plan source-inventory digest is inconsistent")

        sources: set[str] = set()
        targets: set[str] = set()
        allowed_scopes = {scope.value for scope in MigrationScope}
        for repo in self.repositories:
            source = repo.source_key.casefold()
            target = repo.target_key.casefold()
            if source in sources:
                raise PlanIntegrityError(f"Duplicate source mapping: {repo.source_key}")
            if target in targets:
                raise PlanIntegrityError(f"Target collision: {repo.target_key}")
            unknown = set(repo.scopes) - allowed_scopes
            if unknown:
                raise PlanIntegrityError(
                    f"Unknown scopes for {repo.source_key}: {sorted(unknown)}"
                )
            if not repo.ado_project.strip() or not repo.ado_repo.strip() \
                    or not repo.gh_org.strip() or not repo.gh_repo.strip():
                raise PlanIntegrityError("Repository mappings cannot contain empty names")
            if not repo.source_repo_id.strip():
                raise PlanIntegrityError(
                    f"Source repository ID is required for {repo.source_key}"
                )
            canonical_branches = canonical_ref_snapshot(
                [
                    {"name": name, "objectId": sha}
                    for name, sha in repo.source_branch_refs
                ],
                "heads",
            )
            canonical_tags = canonical_ref_snapshot(
                [
                    {"name": name, "objectId": sha}
                    for name, sha in repo.source_tag_refs
                ],
                "tags",
            )
            if canonical_branches != repo.source_branch_refs \
                    or canonical_tags != repo.source_tag_refs:
                raise PlanIntegrityError(
                    f"Source refs for {repo.source_key} are not canonical"
                )
            expected_ref_digest = compute_source_refs_digest(
                canonical_branches, canonical_tags
            )
            if repo.source_refs_digest != expected_ref_digest:
                raise PlanIntegrityError(
                    f"Source ref digest mismatch for {repo.source_key}"
                )
            if repo.default_branch.startswith("refs/"):
                raise PlanIntegrityError(
                    f"default_branch for {repo.source_key} must be a short name"
                )
            if canonical_branches:
                if not repo.default_branch:
                    raise PlanIntegrityError(
                        f"Default branch is required for non-empty {repo.source_key}"
                    )
                expected_head = dict(canonical_branches).get(
                    f"refs/heads/{repo.default_branch}"
                )
                if expected_head is None:
                    raise PlanIntegrityError(
                        f"Default branch {repo.default_branch!r} is absent from "
                        f"the source snapshot for {repo.source_key}"
                    )
                if repo.source_head_sha != expected_head:
                    raise PlanIntegrityError(
                        f"Source HEAD does not match the approved default-branch ref "
                        f"for {repo.source_key}"
                    )
            elif repo.source_head_sha or repo.default_branch:
                raise PlanIntegrityError(
                    f"Empty source {repo.source_key} cannot have a default branch "
                    "or HEAD SHA"
                )
            if not repo.scopes or len(set(repo.scopes)) != len(repo.scopes):
                raise PlanIntegrityError(
                    f"Scopes for {repo.source_key} must be non-empty and unique"
                )
            if repo.pipeline_parallel < 1:
                raise PlanIntegrityError(
                    f"pipeline_parallel for {repo.source_key} must be positive"
                )
            team_sources: set[str] = set()
            if not isinstance(repo.access_policy_approved, bool):
                raise PlanIntegrityError(
                    f"access_policy_approved for {repo.source_key} must be boolean"
                )
            access_snapshot = repo.source_access_snapshot
            if access_snapshot:
                if (
                    not isinstance(access_snapshot, dict)
                    or set(access_snapshot) != {
                        "schema_version", "mode", "digest", "acl_digest",
                        "membership_digest", "acl_count", "principal_count",
                        "group_count", "membership_edge_count", "principal_keys",
                        "principal_memberships",
                    }
                    or access_snapshot.get("schema_version") != 2
                    or access_snapshot.get("mode")
                    != "ado_git_acl_expanded_membership"
                    or any(
                        not isinstance(access_snapshot.get(field), str)
                        or not _SHA256.fullmatch(access_snapshot[field])
                        for field in (
                            "digest", "acl_digest", "membership_digest"
                        )
                    )
                ):
                    raise PlanIntegrityError(
                        f"Source access snapshot for {repo.source_key} is invalid"
                    )
                for count_field in (
                    "acl_count", "principal_count", "group_count",
                    "membership_edge_count",
                ):
                    count = access_snapshot.get(count_field)
                    if isinstance(count, bool) or not isinstance(count, int) \
                            or count < 0:
                        raise PlanIntegrityError(
                            f"Source access snapshot for {repo.source_key} "
                            f"has invalid {count_field}"
                        )
                principal_keys = access_snapshot.get("principal_keys")
                principal_memberships = access_snapshot.get(
                    "principal_memberships"
                )
                if (
                    not isinstance(principal_keys, list)
                    or principal_keys != sorted(set(principal_keys))
                    or any(
                        not isinstance(key, str)
                        or not _PRINCIPAL_KEY.fullmatch(key)
                        for key in principal_keys
                    )
                    or access_snapshot["principal_count"] != len(principal_keys)
                    or access_snapshot["group_count"]
                    > access_snapshot["principal_count"]
                    or not isinstance(principal_memberships, dict)
                    or set(principal_memberships) != set(principal_keys or [])
                    or any(
                        not isinstance(members, list)
                        or members != sorted(set(members))
                        or any(
                            not isinstance(member, str)
                            or not _PRINCIPAL_KEY.fullmatch(member)
                            for member in members
                        )
                        for members in (
                            principal_memberships.values()
                            if isinstance(principal_memberships, dict) else []
                        )
                    )
                ):
                    raise PlanIntegrityError(
                        f"Source access principals for {repo.source_key} are invalid"
                    )
            evidence = repo.access_policy_evidence
            if not isinstance(evidence, dict):
                raise PlanIntegrityError(
                    f"Access policy evidence for {repo.source_key} must be an object"
                )
            for entry in repo.team_mapping:
                if not isinstance(entry, tuple) or len(entry) != 3:
                    raise PlanIntegrityError(
                        f"Team access for {repo.source_key} must declare source, "
                        "GitHub team, and permission"
                    )
                source_team, github_team, permission = entry
                if not isinstance(source_team, str) or not isinstance(
                    github_team, str
                ) or not _PRINCIPAL_KEY.fullmatch(source_team) \
                        or not _TEAM_SLUG.fullmatch(github_team.strip()):
                    raise PlanIntegrityError(
                        f"Team access for {repo.source_key} has an invalid principal "
                        "key or GitHub team"
                    )
                folded_team = source_team.casefold()
                if folded_team in team_sources:
                    raise PlanIntegrityError(
                        f"Team access for {repo.source_key} contains a duplicate source team"
                    )
                team_sources.add(folded_team)
                if permission not in {
                    "pull", "triage", "push", "maintain", "admin"
                }:
                    raise PlanIntegrityError(
                        f"Team access for {repo.source_key} has invalid permission"
                    )
            if repo.access_policy_approved:
                required_evidence = {
                    "approver", "ticket", "approved_at",
                    "source_acl_digest", "review_evidence_digest",
                    "excluded_principals", "disposition_digest",
                    "target_base_repository_permission", "target_access_digest",
                    "target_team_members", "target_team_parents",
                    "target_deploy_keys",
                    "identity_mapping",
                    "target_github_apps",
                }
                if set(evidence) != required_evidence or not access_snapshot:
                    raise PlanIntegrityError(
                        f"Approved access policy for {repo.source_key} requires "
                        "exact ACL-and-membership-bound disposition evidence"
                    )
                if any(
                    not isinstance(evidence.get(field), str)
                    or not evidence[field].strip()
                    or len(evidence[field]) > 2_048
                    or any(
                        ord(char) < 32 or ord(char) == 127
                        for char in evidence[field]
                    )
                    for field in ("approver", "ticket", "approved_at")
                ) or evidence.get("source_acl_digest") != access_snapshot["digest"] \
                        or not _PRINCIPAL_KEY.fullmatch(
                            str(evidence.get("review_evidence_digest", ""))
                        ) or not _PRINCIPAL_KEY.fullmatch(
                            str(evidence.get("disposition_digest", ""))
                        ) or not _PRINCIPAL_KEY.fullmatch(
                            str(evidence.get("target_access_digest", ""))
                        ):
                    raise PlanIntegrityError(
                        f"Access policy evidence for {repo.source_key} does not "
                        "match the exact source access snapshot"
                    )
                try:
                    approved_at = datetime.fromisoformat(
                        evidence["approved_at"].replace("Z", "+00:00")
                    )
                except ValueError as exc:
                    raise PlanIntegrityError(
                        f"Access approval time for {repo.source_key} is invalid"
                    ) from exc
                if approved_at.tzinfo is None:
                    raise PlanIntegrityError(
                        f"Access approval time for {repo.source_key} must include "
                        "a UTC offset"
                    )
                excluded = evidence.get("excluded_principals")
                if not isinstance(excluded, dict):
                    raise PlanIntegrityError(
                        f"Excluded access principals for {repo.source_key} must be "
                        "an object"
                    )
                for principal, rationale in excluded.items():
                    if (
                        not isinstance(principal, str)
                        or not _PRINCIPAL_KEY.fullmatch(principal)
                        or not isinstance(rationale, str)
                        or not rationale.strip()
                        or len(rationale) > 2_048
                        or any(ord(char) < 32 or ord(char) == 127 for char in rationale)
                    ):
                        raise PlanIntegrityError(
                            f"Excluded access disposition for {repo.source_key} "
                            "is invalid"
                        )
                identity_mapping = evidence.get("identity_mapping")
                if not isinstance(identity_mapping, dict) or any(
                    not isinstance(source_identity, str)
                    or not _PRINCIPAL_KEY.fullmatch(source_identity)
                    or not isinstance(target_identity, str)
                    or not _PRINCIPAL_KEY.fullmatch(target_identity)
                    for source_identity, target_identity
                    in identity_mapping.items()
                ) or len(set(identity_mapping.values())) != len(identity_mapping):
                    raise PlanIntegrityError(
                        f"Source-to-target identity mapping for {repo.source_key} "
                        "is invalid or not one-to-one"
                    )
                mapped = {source for source, _, _ in repo.team_mapping}
                excluded_keys = set(excluded)
                approved_principals = set(access_snapshot["principal_keys"])
                if mapped & excluded_keys:
                    raise PlanIntegrityError(
                        f"Access principals for {repo.source_key} cannot be both "
                        "mapped and excluded"
                    )
                if mapped | excluded_keys != approved_principals:
                    missing = sorted(approved_principals - mapped - excluded_keys)
                    unexpected = sorted(
                        (mapped | excluded_keys) - approved_principals
                    )
                    raise PlanIntegrityError(
                        f"Access principal dispositions for {repo.source_key} are "
                        f"not exhaustive (missing={missing}, unexpected={unexpected})"
                    )
                expected_disposition = access_disposition_digest(
                    access_snapshot["digest"], repo.team_mapping, excluded,
                    identity_mapping,
                )
                if evidence["disposition_digest"] != expected_disposition:
                    raise PlanIntegrityError(
                        f"Access disposition digest for {repo.source_key} does not "
                        "bind the exact snapshot, mappings, and exclusions"
                    )
                required_source_identities = {
                    identity
                    for principal in mapped
                    for identity in access_snapshot["principal_memberships"][principal]
                }
                if set(identity_mapping) != required_source_identities:
                    raise PlanIntegrityError(
                        f"Identity mapping for {repo.source_key} does not cover "
                        "every effective member of every mapped source principal"
                    )
                expected_target_members: dict[str, set[str]] = {}
                for principal, github_team, _ in repo.team_mapping:
                    target_set = expected_target_members.setdefault(
                        github_team.casefold(), set()
                    )
                    target_set.update(
                        identity_mapping[source_identity]
                        for source_identity
                        in access_snapshot["principal_memberships"][principal]
                    )
                configured_target_members = evidence.get("target_team_members")
                if not isinstance(configured_target_members, dict) or any(
                    not isinstance(slug, str) or not isinstance(members, list)
                    for slug, members in (
                        configured_target_members.items()
                        if isinstance(configured_target_members, dict) else []
                    )
                ) or {
                    slug.casefold(): sorted(members)
                    for slug, members in configured_target_members.items()
                } != {
                    slug: sorted(members)
                    for slug, members in expected_target_members.items()
                }:
                    raise PlanIntegrityError(
                        f"Target team members for {repo.source_key} do not derive "
                        "exactly from the approved source identity mapping"
                    )
                expected_target_access = target_access_policy_digest(
                    str(evidence.get("target_base_repository_permission", "")),
                    repo.team_mapping,
                    evidence.get("target_team_members", {}),
                    evidence.get("target_team_parents", {}),
                    evidence.get("target_deploy_keys", {}),
                    evidence.get("target_github_apps", {}),
                )
                if evidence["target_access_digest"] != expected_target_access:
                    raise PlanIntegrityError(
                        f"Target access digest for {repo.source_key} does not "
                        "bind the exact base permission, exhaustive teams, and "
                        "empty direct-collaborator policy"
                    )
            elif evidence or repo.team_mapping:
                raise PlanIntegrityError(
                    f"Unapproved access policy for {repo.source_key} cannot carry "
                    "approval evidence or mutate team access"
                )
            try:
                re.compile(repo.pipeline_filter)
            except re.error as exc:
                raise PlanIntegrityError(
                    f"Invalid pipeline filter for {repo.source_key}: {exc}"
                ) from exc
            sources.add(source)
            targets.add(target)

        task_ids = {task.task_id for task in self.tasks}
        if len(task_ids) != len(self.tasks):
            raise PlanIntegrityError("Plan contains duplicate task IDs")
        for task in self.tasks:
            missing = set(task.dependencies) - task_ids
            if missing:
                raise PlanIntegrityError(
                    f"Task {task.task_id} has unknown dependencies: {sorted(missing)}"
                )
            if task.task_id in task.dependencies:
                raise PlanIntegrityError(f"Task {task.task_id} depends on itself")

        repos_by_source = {
            repo.source_key: repo for repo in self.repositories
        }
        tasks_by_source: dict[str, list[PlanTask]] = {}
        for task in self.tasks:
            repo = repos_by_source.get(task.source_key)
            if repo is None:
                raise PlanIntegrityError(
                    f"Task {task.task_id} references unplanned source {task.source_key!r}"
                )
            if task.target_key != repo.target_key:
                raise PlanIntegrityError(
                    f"Task {task.task_id} target does not match its repository mapping"
                )
            if task.kind not in {"preflight", "inventory", "execute", "validate"}:
                raise PlanIntegrityError(
                    f"Task {task.task_id} has unsupported kind {task.kind!r}"
                )
            tasks_by_source.setdefault(task.source_key, []).append(task)

        for repo in self.repositories:
            grouped = tasks_by_source.get(repo.source_key, [])
            preflights = [task for task in grouped if task.kind == "preflight"]
            validators = [task for task in grouped if task.kind == "validate"]
            executions = [task for task in grouped if task.kind == "execute"]
            inventories = [task for task in grouped if task.kind == "inventory"]
            if len(preflights) != 1 or len(validators) != 1:
                raise PlanIntegrityError(
                    f"{repo.source_key} requires exactly one preflight and validator"
                )
            execution_scopes = [task.scope for task in executions]
            if sorted(execution_scopes) != sorted(repo.scopes):
                raise PlanIntegrityError(
                    f"Execute tasks for {repo.source_key} do not match approved scopes"
                )
            needs_inventory = MigrationScope.PIPELINES.value in repo.scopes
            if len(inventories) != (1 if needs_inventory else 0):
                raise PlanIntegrityError(
                    f"Pipeline inventory task coverage is invalid for {repo.source_key}"
                )
            preflight_id = preflights[0].task_id
            inventory_id = inventories[0].task_id if inventories else ""
            if preflights[0].dependencies or preflights[0].scope:
                raise PlanIntegrityError(
                    f"Preflight task shape is invalid for {repo.source_key}"
                )
            target_evidence = preflights[0].metadata
            target_exists = target_evidence.get("target_exists")
            target_repo_id = target_evidence.get("target_repo_id")
            target_size = target_evidence.get("target_size")
            target_visibility = target_evidence.get("target_visibility")
            target_default = target_evidence.get("target_default_branch")
            target_branch_refs = target_evidence.get("target_branch_refs")
            target_tag_refs = target_evidence.get("target_tag_refs")
            target_refs_digest = target_evidence.get("target_refs_digest")
            if (
                set(target_evidence) != {
                    "target_exists", "target_repo_id", "target_size",
                    "target_visibility",
                    "target_default_branch", "target_branch_refs",
                    "target_tag_refs", "target_refs_digest",
                }
                or not isinstance(target_exists, bool)
                or not isinstance(target_repo_id, str)
                or isinstance(target_size, bool)
                or not isinstance(target_size, int)
                or target_size < 0
                or target_visibility not in {"private", "internal", "public"}
                or not isinstance(target_default, str)
                or not isinstance(target_branch_refs, dict)
                or not isinstance(target_tag_refs, dict)
                or not isinstance(target_refs_digest, str)
                or (target_exists and not target_repo_id.strip())
            ):
                raise PlanIntegrityError(
                    f"Preflight target identity evidence is invalid for "
                    f"{repo.source_key}"
                )
            try:
                canonical_target_branches = canonical_ref_snapshot(
                    [
                        {"name": name, "objectId": sha}
                        for name, sha in target_branch_refs.items()
                    ],
                    "heads",
                )
                canonical_target_tags = canonical_ref_snapshot(
                    [
                        {"name": name, "objectId": sha}
                        for name, sha in target_tag_refs.items()
                    ],
                    "tags",
                )
            except PlanIntegrityError as exc:
                raise PlanIntegrityError(
                    f"Preflight target refs are invalid for {repo.source_key}: {exc}"
                ) from exc
            if (
                dict(canonical_target_branches) != target_branch_refs
                or dict(canonical_target_tags) != target_tag_refs
                or target_refs_digest != compute_source_refs_digest(
                    canonical_target_branches, canonical_target_tags
                )
                or (
                    canonical_target_branches
                    and (
                        not target_default
                        or f"refs/heads/{target_default}"
                        not in dict(canonical_target_branches)
                    )
                )
                or (
                    not target_exists
                    and (
                        target_repo_id
                        or target_size != 0
                        or target_visibility != "private"
                        or target_default
                        or canonical_target_branches
                        or canonical_target_tags
                    )
                )
            ):
                raise PlanIntegrityError(
                    f"Preflight target ref evidence is inconsistent for "
                    f"{repo.source_key}"
                )
            mapping_policy = self.policy.get("mapping", {})
            allowed_visibilities = (
                mapping_policy.get("allowed_target_visibilities")
                if isinstance(mapping_policy, dict) else None
            )
            if (
                not isinstance(allowed_visibilities, list)
                or not allowed_visibilities
                or any(
                    value not in {"private", "internal", "public"}
                    for value in allowed_visibilities
                )
                or target_visibility not in allowed_visibilities
            ):
                raise PlanIntegrityError(
                    f"Target visibility policy is invalid for {repo.source_key}"
                )
            if inventories and (
                inventories[0].scope != MigrationScope.PIPELINES.value
                or set(inventories[0].dependencies) != {preflight_id}
            ):
                raise PlanIntegrityError(
                    f"Inventory task shape is invalid for {repo.source_key}"
                )
            if inventories:
                metadata = inventories[0].metadata
                receipts = metadata.get("pipelines")
                digest = metadata.get("inventory_digest")
                count = metadata.get("pipeline_count")
                if (
                    not isinstance(receipts, list)
                    or not isinstance(count, int)
                    or count != len(receipts)
                    or not isinstance(digest, str)
                    or digest != content_digest(receipts)
                ):
                    raise PlanIntegrityError(
                        f"Pipeline snapshot is invalid for {repo.source_key}"
                    )
            if validators[0].scope:
                raise PlanIntegrityError(
                    f"Validator task shape is invalid for {repo.source_key}"
                )
            repo_execution = next(
                (
                    task for task in executions
                    if task.scope == MigrationScope.REPO.value
                ),
                None,
            )
            for task in executions:
                required = {preflight_id}
                if task.scope == MigrationScope.PIPELINES.value:
                    required.add(inventory_id)
                if task.scope != MigrationScope.REPO.value and repo_execution:
                    required.add(repo_execution.task_id)
                if set(task.dependencies) != required:
                    raise PlanIntegrityError(
                        f"Task {task.task_id} dependencies do not match the "
                        "approved execution graph"
                    )
                if task.scope in _BOUND_NON_GIT_SCOPES:
                    snapshot = task.metadata.get("source_snapshot")
                    if not isinstance(snapshot, dict) or set(snapshot) != {
                        "schema_version", "scope", "digest", "item_count", "counts"
                    }:
                        raise PlanIntegrityError(
                            f"{task.scope} source snapshot is missing or malformed "
                            f"for {repo.source_key}"
                        )
                    counts = snapshot.get("counts")
                    item_count = snapshot.get("item_count")
                    if (
                        set(task.metadata) != {"source_snapshot"}
                        or snapshot.get("schema_version") != 1
                        or snapshot.get("scope") != task.scope
                        or not isinstance(snapshot.get("digest"), str)
                        or not _SHA256.fullmatch(snapshot["digest"])
                        or isinstance(item_count, bool)
                        or not isinstance(item_count, int)
                        or item_count < 0
                        or not isinstance(counts, dict)
                        or counts.get("item_count") != item_count
                        or any(
                            not isinstance(key, str)
                            or isinstance(count, bool)
                            or not isinstance(count, int)
                            or count < 0
                            for key, count in counts.items()
                        )
                    ):
                        raise PlanIntegrityError(
                            f"{task.scope} source snapshot evidence is invalid "
                            f"for {repo.source_key}"
                        )
            if set(validators[0].dependencies) != {
                task.task_id for task in executions
            }:
                raise PlanIntegrityError(
                    f"Validator for {repo.source_key} must depend on every execution task"
                )

        # Cycle detection also catches dependency chains that can never execute.
        deps = {task.task_id: set(task.dependencies) for task in self.tasks}
        ready = [task_id for task_id, values in deps.items() if not values]
        visited = 0
        while ready:
            current = ready.pop()
            visited += 1
            for task_id, values in deps.items():
                if current in values:
                    values.remove(current)
                    if not values:
                        ready.append(task_id)
        if visited != len(deps):
            raise PlanIntegrityError("Plan task graph contains a dependency cycle")

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._identity_payload(
                self.source_org_url,
                self.target_org,
                self.repositories,
                self.tasks,
                self.policy,
                self.config_digest,
                self.schema_version,
            ),
            "plan_id": self.plan_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MigrationPlan":
        plan = cls(
            plan_id=str(value["plan_id"]),
            source_org_url=str(value["source_org_url"]).rstrip("/"),
            target_org=str(value["target_org"]),
            repositories=tuple(
                PlannedRepository.from_dict(item)
                for item in value.get("repositories", [])
            ),
            tasks=tuple(PlanTask.from_dict(item) for item in value.get("tasks", [])),
            policy=dict(value.get("policy", {})),
            config_digest=str(value.get("config_digest", "")),
            created_at=str(value.get("created_at", "")),
            schema_version=int(value.get("schema_version", 0)),
        )
        plan.validate()
        return plan


@dataclass
class ExecutionResult:
    run_id: str
    plan_id: str
    status: str
    repository_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    started_at: str = ""
    completed_at: str = ""
    resumed: bool = False


@dataclass
class ValidationReport:
    run_id: str
    plan_id: str
    status: str
    repository_results: list[dict[str, Any]] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    validated_at: str = ""

    @property
    def passed(self) -> bool:
        return self.status == "passed"


@dataclass
class OrchestrationResult:
    plan_id: str
    run_id: str
    status: str
    execution: ExecutionResult
    validation: Optional[ValidationReport] = None
    repair_attempts: int = 0
