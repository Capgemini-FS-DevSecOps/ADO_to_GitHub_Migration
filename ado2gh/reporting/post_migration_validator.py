"""Post-migration validation — content-level comparison between ADO source and GitHub target.

v5.1: Compares commit SHAs (not just counts), default branch verification,
and generates actionable pass/fail report.
"""
from __future__ import annotations

import csv
from fnmatch import fnmatchcase
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Optional
from urllib.parse import quote

import yaml

from ado2gh.clients.ado_client import ADOClient
from ado2gh.clients.gh_client import GHClient
from ado2gh.logging_config import console, log
from ado2gh.models import RepoConfig
from ado2gh.pev.contracts import (
    content_digest,
    github_deploy_key_identity_key,
    github_team_identity_key,
    github_user_identity_key,
    target_access_policy_digest,
)
from ado2gh.pev.source_integrity import (
    fetch_scope_payload,
    validate_scope_snapshot,
    verify_scope_payload,
)
from ado2gh.state.db import StateDB

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
EVIDENCE_SCHEMA = "ado2gh.post-migration-validation/v1"


class PostMigrationValidator:
    """Content-level comparison between ADO source and GitHub target.

    Checks:
    1. Repo exists on GitHub
    2. Default branch matches and has same HEAD commit SHA
    3. Every branch name and tip SHA matches exactly
    4. Every tag name and ref SHA matches exactly
    5. Pipeline workflow files present
    6. Branch protection rules applied
    """

    def __init__(
        self,
        ado: ADOClient,
        gh: GHClient,
        db: StateDB,
        approved_source_snapshots: Optional[Mapping[str, Mapping[str, Any]]] = None,
        approved_pipeline_snapshots: Optional[
            Mapping[str, Mapping[str, Any]]
        ] = None,
        approved_wave_id: Optional[int] = None,
        approved_staging_branch: str = "",
        approved_plan_id: str = "",
        approved_run_id: str = "",
        approved_scope_snapshots: Optional[
            Mapping[str, Mapping[str, Mapping[str, Any]]]
        ] = None,
        approved_target_snapshots: Optional[
            Mapping[str, Mapping[str, Any]]
        ] = None,
        include_unlinked_work_items: bool = False,
    ):
        self.ado = ado
        self.gh = gh
        self.db = db
        self.approved_source_snapshots = dict(approved_source_snapshots or {})
        self._approved_pipeline_validation = approved_pipeline_snapshots is not None
        self.approved_pipeline_snapshots = dict(
            approved_pipeline_snapshots or {}
        )
        self.approved_wave_id = approved_wave_id
        self.approved_staging_branch = str(approved_staging_branch or "")
        self.approved_plan_id = str(approved_plan_id or "")
        self.approved_run_id = str(approved_run_id or "")
        self.approved_scope_snapshots = {
            str(source): dict(scopes)
            for source, scopes in (approved_scope_snapshots or {}).items()
        }
        self.approved_target_snapshots = {
            str(source): dict(snapshot)
            for source, snapshot in (approved_target_snapshots or {}).items()
        }
        self.include_unlinked_work_items = bool(include_unlinked_work_items)
        self.approved_access_snapshots: dict[str, dict[str, Any]] = {}
        self.live_access_snapshots: dict[str, dict[str, Any]] = {}
        # PEVValidator populates this with one fresh project-wide snapshot per
        # validation phase. It is deliberately not persisted or reused across
        # planning/execution/validation boundaries.
        self.work_item_payloads: dict[str, list[dict[str, Any]]] = {}
        if self._approved_pipeline_validation and (
            isinstance(approved_wave_id, bool)
            or not isinstance(approved_wave_id, int)
            or approved_wave_id < 0
        ):
            raise ValueError(
                "approved_wave_id must be a non-negative integer when an "
                "approved pipeline snapshot is supplied"
            )
        if self._approved_pipeline_validation and (
            not self.approved_plan_id or not self.approved_run_id
        ):
            raise ValueError(
                "approved plan and run ids are required for pipeline validation"
            )

    def validate(self, repos: list[RepoConfig],
                 output_path: str = None,
                 max_workers: int = 6) -> list[dict]:
        results: list[dict] = []

        console.print(f"[bold]Validating {len(repos)} repos...[/bold]")

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(self._validate_one, repo): repo
                for repo in repos
            }
            for future in as_completed(futures):
                repo = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as exc:
                    log.error("Validation error %s/%s: %s",
                              repo.ado_project, repo.ado_repo, exc)
                    results.append({
                        "ado_project": repo.ado_project,
                        "ado_repo": repo.ado_repo,
                        "gh_target": f"{repo.gh_org}/{repo.gh_repo}",
                        "validated_at": datetime.now(timezone.utc).isoformat(),
                        "evidence_schema": EVIDENCE_SCHEMA,
                        "overall": FAIL,
                        "error": self._safe_error(exc),
                        "checks": {},
                        "evidence_summary": {
                            "pass": 0, "warn": 0, "fail": 1,
                            "overall": FAIL,
                        },
                    })

        results.sort(key=lambda r: (r.get("ado_project", ""), r.get("ado_repo", "")))

        if output_path:
            self._write_report(results, output_path)

        return results

    def _validate_one(self, repo: RepoConfig) -> dict:
        result: dict[str, Any] = {
            "ado_project": repo.ado_project,
            "ado_repo": repo.ado_repo,
            "gh_target": f"{repo.gh_org}/{repo.gh_repo}",
            "validated_at": datetime.now(timezone.utc).isoformat(),
            "evidence_schema": EVIDENCE_SCHEMA,
            "source": {
                "system": "azure_devops",
                "project": repo.ado_project,
                "repository": repo.ado_repo,
            },
            "target": {
                "system": "github",
                "organization": repo.gh_org,
                "repository": repo.gh_repo,
            },
            "overall": PASS,
            "checks": {},
        }

        checks = result["checks"]

        # 1. Does the GH repo exist?
        checks["repo_exists"] = self._check_repo_exists(repo)

        if checks["repo_exists"]["verdict"] == FAIL:
            result["overall"] = FAIL
            result["evidence_summary"] = {
                "pass": 0, "warn": 0, "fail": 1, "overall": FAIL,
            }
            return result

        # Capture immutable source/target observations once per repository.
        # Re-reading the same refs for individual checks is both expensive at
        # enterprise scale and can create an internally inconsistent report if
        # a push happens between API calls.
        snapshot = self._collect_ref_snapshot(repo)

        # Workflow blobs are read by the captured immutable target commit, not
        # a mutable branch name. This closes the workflow-read/ref-read TOCTOU.
        approved_workflow_check = None
        if self._approved_pipeline_validation and "pipelines" in (
            repo.scopes or []
        ):
            immutable_target_ref = self._captured_default_target_sha(snapshot)
            approved_workflow_check = self._check_workflows(
                repo, immutable_ref=immutable_target_ref
            )
            checks["workflows"] = approved_workflow_check
        snapshot["approved_workflow_check"] = approved_workflow_check

        if self.approved_scope_snapshots.get(
            f"{repo.ado_project}/{repo.ado_repo}"
        ):
            checks["source_scope_integrity"] = self._check_live_scope_integrity(
                repo
            )

        # 2. Default branch match + HEAD commit SHA
        checks["default_branch"] = self._check_default_branch(repo, snapshot)

        # 3. HEAD commit SHA match (the real proof)
        checks["head_commit"] = self._check_head_commit_sha(repo, snapshot)

        # 4. Exact branch ref fidelity (name + SHA)
        checks["branches"] = self._check_branch_count(repo, snapshot)

        # 5. Exact tag ref fidelity (name + SHA)
        checks["tags"] = self._check_tags(repo, snapshot)

        # 6. Workflows present (if pipelines scope was migrated)
        if "pipelines" in (repo.scopes or []) and approved_workflow_check is None:
            checks["workflows"] = self._check_workflows(repo)

        # 7. Branch protection (if branch_policies scope was migrated)
        if "branch_policies" in (repo.scopes or []):
            checks["branch_protection"] = self._check_branch_protection(
                repo, snapshot
            )

        checks["source_access_integrity"] = self._check_source_access_integrity(
            repo
        )
        checks["target_access_policy"] = self._check_target_access_policy(repo)
        access_passed = (
            repo.access_policy_approved
            and checks["source_access_integrity"]["verdict"] == PASS
            and checks["target_access_policy"]["verdict"] == PASS
        )
        checks["access_control_attestation"] = {
            "verdict": (
                PASS if access_passed else
                FAIL if repo.access_policy_approved else WARN
            ),
            "approved": bool(repo.access_policy_approved),
            "detail": (
                "Source ACL/membership evidence and the exhaustive target base, "
                "team, and direct-collaborator policy match the immutable plan"
                if access_passed else
                "Approved source or target access evidence does not match live state"
                if repo.access_policy_approved else
                "Source ACL parity is not attested; destructive cutover is blocked"
            ),
        }

        checks["target_snapshot_stable"] = self._confirm_target_snapshot_unchanged(
            repo, snapshot
        )

        # Derive overall
        verdicts = [c["verdict"] for c in checks.values()]
        if FAIL in verdicts:
            result["overall"] = FAIL
        elif WARN in verdicts:
            result["overall"] = WARN

        result["evidence_summary"] = {
            "pass": sum(1 for verdict in verdicts if verdict == PASS),
            "warn": sum(1 for verdict in verdicts if verdict == WARN),
            "fail": sum(1 for verdict in verdicts if verdict == FAIL),
            "overall": result["overall"],
        }

        return result

    def _check_source_access_integrity(self, repo: RepoConfig) -> dict[str, Any]:
        source_key = f"{repo.ado_project}/{repo.ado_repo}"
        expected = self.approved_access_snapshots.get(source_key, {})
        observed = self.live_access_snapshots.get(source_key, {})
        if not expected:
            return {
                "verdict": FAIL if repo.access_policy_approved else WARN,
                "detail": "The plan has no content-addressed ADO Git ACL snapshot",
            }
        if not observed:
            return {
                "verdict": FAIL,
                "approved_digest": expected.get("digest", ""),
                "detail": "Live ADO Git ACL inventory is unavailable",
            }
        stable = expected == observed
        return {
            "verdict": PASS if stable else FAIL,
            "approved_digest": expected.get("digest", ""),
            "observed_digest": observed.get("digest", ""),
            "approved_principal_count": expected.get("principal_count", 0),
            "observed_principal_count": observed.get("principal_count", 0),
            "detail": (
                "Live ADO inherited/repository/branch ACLs and expanded principal "
                "memberships match the approved plan"
                if stable else
                "ADO Git ACLs or expanded principal memberships changed after "
                "access-policy approval"
            ),
        }

    def _check_team_permissions(self, repo: RepoConfig) -> dict:
        """Verify every explicitly approved team role exactly."""
        observed: dict[str, str] = {}
        try:
            getter = getattr(self.gh, "get_team_repo_permission", None)
            if not callable(getter):
                raise RuntimeError(
                    "GitHub client cannot read back team repository permissions"
                )
            for source_team, raw_access in sorted(repo.team_mapping.items()):
                if not isinstance(raw_access, Mapping):
                    raise ValueError(
                        f"Team mapping {source_team!r} lacks explicit permission"
                    )
                github_team = raw_access.get("github_team")
                permission = raw_access.get("permission")
                if not isinstance(github_team, str) or permission not in {
                    "pull", "triage", "push", "maintain", "admin"
                }:
                    raise ValueError(
                        f"Team mapping {source_team!r} is not canonical"
                    )
                actual = getter(repo.gh_org, github_team, repo.gh_repo)
                observed[github_team] = actual
                if actual != permission:
                    return {
                        "verdict": FAIL,
                        "expected": permission,
                        "actual": actual,
                        "github_team": github_team,
                        "detail": (
                            f"GitHub team {github_team!r} has {actual!r}, not "
                            f"the approved {permission!r} permission"
                        ),
                    }
        except Exception as exc:
            return self._api_failure(
                "Cannot verify GitHub team repository permissions", exc
            )
        return {
            "verdict": PASS,
            "permissions": observed,
            "detail": "All explicitly approved GitHub team permissions match",
        }

    def _check_target_access_policy(self, repo: RepoConfig) -> dict[str, Any]:
        """Compare the complete repository access surface with the plan."""
        if not repo.access_policy_approved:
            return {
                "verdict": WARN,
                "detail": "No immutable target access policy was approved",
            }
        evidence = repo.access_policy_evidence
        if not isinstance(evidence, Mapping):
            return {
                "verdict": FAIL,
                "detail": "Approved target access evidence is malformed",
            }
        base_permission = evidence.get("target_base_repository_permission")
        expected_teams: dict[str, str] = {}
        mapping_entries: list[tuple[str, str, str]] = []
        try:
            for principal, raw_access in sorted(repo.team_mapping.items()):
                if not isinstance(raw_access, Mapping):
                    raise ValueError("team mapping is not canonical")
                slug = str(raw_access.get("github_team") or "").strip()
                permission = str(raw_access.get("permission") or "").strip()
                folded = slug.casefold()
                if not slug or permission not in {
                    "pull", "triage", "push", "maintain", "admin",
                }:
                    raise ValueError("team mapping is incomplete")
                previous = expected_teams.get(folded)
                if previous is not None and previous != permission:
                    raise ValueError("team mapping contains conflicting roles")
                expected_teams[folded] = permission
                mapping_entries.append((principal, slug, permission))
            expected_members = evidence.get("target_team_members")
            expected_parents = evidence.get("target_team_parents")
            expected_deploy_keys = evidence.get("target_deploy_keys")
            expected_github_apps = evidence.get("target_github_apps")
            if (
                not isinstance(expected_members, dict)
                or not isinstance(expected_parents, dict)
                or not isinstance(expected_deploy_keys, dict)
                or expected_github_apps != {}
            ):
                raise ValueError("target access evidence is incomplete")
            expected_digest = target_access_policy_digest(
                str(base_permission or ""),
                mapping_entries,
                expected_members,
                expected_parents,
                expected_deploy_keys,
                expected_github_apps,
            )
            if evidence.get("target_access_digest") != expected_digest:
                raise ValueError("target access evidence digest is invalid")

            org = self.gh.get_org(repo.gh_org)
            if not isinstance(org, Mapping):
                raise TypeError("GitHub organization response is malformed")
            observed_base = org.get("default_repository_permission")
            team_reader = getattr(self.gh, "list_repo_teams", None)
            member_reader = getattr(self.gh, "list_team_members", None)
            direct_reader = getattr(
                self.gh, "list_repo_direct_collaborators", None
            )
            invitation_reader = getattr(self.gh, "list_repo_invitations", None)
            deploy_key_reader = getattr(self.gh, "list_deploy_keys", None)
            app_reader = getattr(
                self.gh, "list_repo_app_installations", None
            )
            if not all(callable(reader) for reader in (
                team_reader, member_reader, direct_reader, invitation_reader,
                deploy_key_reader,
            )):
                raise RuntimeError(
                    "GitHub client cannot inventory exhaustive repository access"
                )
            raw_teams = team_reader(repo.gh_org, repo.gh_repo)
            direct = direct_reader(repo.gh_org, repo.gh_repo)
            invitations = invitation_reader(repo.gh_org, repo.gh_repo)
            deploy_keys = deploy_key_reader(repo.gh_org, repo.gh_repo)
            if any(not isinstance(rows, list) for rows in (
                raw_teams, direct, invitations, deploy_keys,
            )):
                raise TypeError("GitHub access inventory is malformed")
            observed_teams: dict[str, str] = {}
            observed_parents: dict[str, str] = {}
            for row in raw_teams:
                if not isinstance(row, Mapping):
                    raise TypeError("GitHub repository team is malformed")
                slug = str(row.get("slug") or "").strip()
                permission = str(
                    row.get("role_name") or row.get("permission") or ""
                ).strip()
                folded = slug.casefold()
                if (
                    not slug
                    or permission not in {
                        "pull", "triage", "push", "maintain", "admin",
                    }
                    or folded in observed_teams
                ):
                    raise RuntimeError(
                        "GitHub repository team inventory is ambiguous"
                    )
                observed_teams[folded] = permission
                parent = row.get("parent")
                if parent is None:
                    observed_parents[folded] = ""
                elif isinstance(parent, Mapping):
                    parent_id = str(
                        parent.get("node_id") or parent.get("id") or ""
                    ).strip()
                    observed_parents[folded] = github_team_identity_key(parent_id)
                else:
                    raise RuntimeError("GitHub repository team parent is malformed")
            observed_members: dict[str, list[str]] = {}
            for slug in sorted(expected_teams):
                if slug not in observed_teams:
                    observed_members[slug] = []
                    continue
                members = member_reader(repo.gh_org, slug)
                if not isinstance(members, list):
                    raise TypeError("GitHub team-member inventory is malformed")
                member_keys = []
                for member in members:
                    if not isinstance(member, Mapping):
                        raise TypeError("GitHub team member is malformed")
                    member_id = str(
                        member.get("node_id") or member.get("id") or ""
                    ).strip()
                    member_keys.append(github_user_identity_key(member_id))
                if len(member_keys) != len(set(member_keys)):
                    raise RuntimeError("GitHub team-member inventory is duplicated")
                observed_members[slug] = sorted(member_keys)
            direct_ids = []
            for row in direct:
                if not isinstance(row, Mapping):
                    raise TypeError("GitHub direct collaborator is malformed")
                identity = str(row.get("node_id") or row.get("id") or "").strip()
                if not identity:
                    raise RuntimeError(
                        "GitHub direct collaborator has no immutable identity"
                    )
                direct_ids.append(identity)
            if len(direct_ids) != len(set(direct_ids)):
                raise RuntimeError(
                    "GitHub direct collaborator inventory is duplicated"
                )
            invitation_ids = []
            for row in invitations:
                if not isinstance(row, Mapping):
                    raise TypeError("GitHub repository invitation is malformed")
                invitation_id = str(row.get("id") or "").strip()
                if not invitation_id:
                    raise RuntimeError("GitHub repository invitation has no ID")
                invitation_ids.append(invitation_id)
            if len(invitation_ids) != len(set(invitation_ids)):
                raise RuntimeError("GitHub repository invitations are duplicated")
            observed_deploy_keys: dict[str, bool] = {}
            for row in deploy_keys:
                if not isinstance(row, Mapping):
                    raise TypeError("GitHub deploy key is malformed")
                key_id = str(row.get("id") or "").strip()
                read_only = row.get("read_only")
                if not key_id or not isinstance(read_only, bool):
                    raise RuntimeError("GitHub deploy key inventory is incomplete")
                key = github_deploy_key_identity_key(key_id)
                if key in observed_deploy_keys:
                    raise RuntimeError("GitHub deploy key inventory is duplicated")
                observed_deploy_keys[key] = read_only
            app_inventory_unavailable = not callable(app_reader)
            github_apps = (
                [] if app_inventory_unavailable
                else app_reader(repo.gh_org, repo.gh_repo)
            )
            if not isinstance(github_apps, list) or any(
                not isinstance(row, Mapping) for row in github_apps
            ):
                raise TypeError("GitHub App installation inventory is malformed")
        except Exception as exc:
            return self._api_failure(
                "Cannot verify exhaustive GitHub repository access", exc
            )

        stable = (
            observed_base == base_permission
            and observed_teams == expected_teams
            and observed_parents == expected_parents
            and observed_members == expected_members
            and not direct_ids
            and not invitation_ids
            and observed_deploy_keys == expected_deploy_keys
            and not app_inventory_unavailable
            and not github_apps
        )
        return {
            "verdict": PASS if stable else FAIL,
            "expected_base_permission": base_permission,
            "observed_base_permission": observed_base,
            "expected_team_count": len(expected_teams),
            "observed_team_count": len(observed_teams),
            "direct_collaborator_count": len(direct_ids),
            "pending_invitation_count": len(invitation_ids),
            "deploy_key_count": len(observed_deploy_keys),
            "github_app_inventory_unavailable": app_inventory_unavailable,
            "github_app_installation_count": len(github_apps),
            "observed_access_digest": content_digest({
                "base_repository_permission": observed_base,
                "teams": [
                    {
                        "slug": slug,
                        "permission": observed_teams[slug],
                        "parent_key": observed_parents[slug],
                        "member_keys": observed_members.get(slug, []),
                    }
                    for slug in sorted(observed_teams)
                ],
                "deploy_keys": [
                    {"key": key, "read_only": observed_deploy_keys[key]}
                    for key in sorted(observed_deploy_keys)
                ],
                "direct_collaborator_ids": sorted(
                    "sha256:" + hashlib.sha256(item.encode("utf-8")).hexdigest()
                    for item in direct_ids
                ),
                "pending_invitation_ids": sorted(
                    "sha256:" + hashlib.sha256(item.encode("utf-8")).hexdigest()
                    for item in invitation_ids
                ),
            }),
            "detail": (
                "GitHub base permission, flat/nested team roles and members, "
                "deploy keys, and empty collaborator/invitation surfaces match"
                if stable else
                "GitHub App access cannot be enumerated exhaustively"
                if app_inventory_unavailable else
                "One or more GitHub App installations can access the repository"
                if github_apps else
                "GitHub base permission, repository teams/members/parents, deploy "
                "keys, collaborators, or invitations differ from the approved policy"
            ),
        }

    def _captured_default_target_sha(self, snapshot: dict) -> str:
        repo_data = self._snapshot_value(snapshot, "gh_repo", lambda: {})
        default_branch = self._required_field(
            repo_data, "default_branch", "GitHub repository"
        )
        branches = self._snapshot_value(snapshot, "gh_branches", lambda: {})
        sha = branches.get(default_branch, "")
        if not isinstance(sha, str) or not re.fullmatch(
            r"(?:[0-9a-f]{40}|[0-9a-f]{64})", sha
        ):
            raise RuntimeError(
                "Captured GitHub default branch has no immutable commit SHA"
            )
        return sha

    def _confirm_target_snapshot_unchanged(
        self, repo: RepoConfig, snapshot: dict
    ) -> dict:
        """Re-read target identity/refs after all evidence reads."""
        try:
            before_repo = self._snapshot_value(snapshot, "gh_repo", lambda: {})
            before_branches = self._snapshot_value(
                snapshot, "gh_branches", lambda: {}
            )
            before_tags = self._snapshot_value(snapshot, "gh_tags", lambda: {})
            after_repo = self.gh.get_repo(repo.gh_org, repo.gh_repo)
            after_branches = self._list_github_branches(
                repo.gh_org, repo.gh_repo
            )
            after_tags = self._list_github_tags(repo.gh_org, repo.gh_repo)
            before_identity = (
                str(before_repo.get("node_id") or before_repo.get("id") or ""),
                before_repo.get("default_branch"),
                before_repo.get("visibility"),
            )
            after_identity = (
                str(after_repo.get("node_id") or after_repo.get("id") or ""),
                after_repo.get("default_branch"),
                after_repo.get("visibility"),
            )
            source_key = f"{repo.ado_project}/{repo.ado_repo}"
            approved = self.approved_target_snapshots.get(source_key)
            approved_identity = True
            identity_error = ""
            if approved is not None:
                expected_visibility = approved.get("target_visibility")
                if before_identity[2] != expected_visibility:
                    approved_identity = False
                    identity_error = "captured target visibility is not approved"
                elif approved.get("target_exists"):
                    expected_id = str(approved.get("target_repo_id") or "")
                    if not expected_id or before_identity[0] != expected_id:
                        approved_identity = False
                        identity_error = (
                            "captured target immutable ID is not the approved ID"
                        )
                elif not before_identity[0] or not self.db.repository_owned_by_run(
                    repo.gh_org,
                    repo.gh_repo,
                    plan_id=self.approved_plan_id,
                    run_id=self.approved_run_id,
                    target_repo_id=before_identity[0],
                ):
                    approved_identity = False
                    identity_error = (
                        "captured target is not owned by the approved plan/run"
                    )
            stable = (
                approved_identity
                and bool(before_identity[0])
                and before_identity == after_identity
                and before_branches == after_branches
                and before_tags == after_tags
            )
            return {
                "verdict": PASS if stable else FAIL,
                "before_ref_digest": content_digest({
                    "branches": before_branches,
                    "tags": before_tags,
                }),
                "after_ref_digest": content_digest({
                    "branches": after_branches,
                    "tags": after_tags,
                }),
                "detail": (
                    "GitHub target remained stable throughout validation"
                    if stable else identity_error or
                    "GitHub target changed while validation evidence was read"
                ),
            }
        except Exception as exc:
            return self._api_failure(
                "Cannot prove GitHub target stayed stable during validation", exc
            )

    def _check_live_scope_integrity(self, repo: RepoConfig) -> dict:
        """Re-read every approved non-Git source scope at validation time."""
        source_key = f"{repo.ado_project}/{repo.ado_repo}"
        approved_scopes = self.approved_scope_snapshots.get(source_key, {})
        approved_source = self.approved_source_snapshots.get(source_key, {})
        source_repo_id = str(approved_source.get("source_repo_id", "")).strip()
        observed: dict[str, dict[str, Any]] = {}
        try:
            if not source_repo_id:
                raise ValueError("approved source repository ID is missing")
            for scope, approved in sorted(approved_scopes.items()):
                if scope == "work_items" and source_key in self.work_item_payloads:
                    payload = self.work_item_payloads[source_key]
                else:
                    payload = fetch_scope_payload(
                        self.ado,
                        repo,
                        scope,
                        source_repo_id=source_repo_id,
                        include_unlinked_work_items=self.include_unlinked_work_items,
                    )
                observed[scope] = verify_scope_payload(
                    scope, payload, approved
                )
        except Exception as exc:
            return self._api_failure(
                "Non-Git ADO source changed after execution",
                exc,
                checked_scopes=sorted(approved_scopes),
            )
        return {
            "verdict": PASS,
            "checked_scopes": sorted(observed),
            "source_digests": {
                scope: evidence["digest"]
                for scope, evidence in sorted(observed.items())
            },
            "detail": "All approved non-Git ADO source scopes remain unchanged",
        }

    def _check_repo_exists(self, repo: RepoConfig) -> dict:
        try:
            exists = self.gh.repo_exists(repo.gh_org, repo.gh_repo)
            if not exists:
                return {
                    "verdict": FAIL,
                    "detail": "GitHub repo NOT FOUND",
                }
            source_key = f"{repo.ado_project}/{repo.ado_repo}"
            approved = self.approved_target_snapshots.get(source_key)
            if approved is not None:
                target = self.gh.get_repo(repo.gh_org, repo.gh_repo)
                if not isinstance(target, Mapping):
                    raise ValueError("GitHub repository metadata is not an object")
                live_id = str(
                    target.get("node_id") or target.get("id") or ""
                ).strip()
                live_visibility = target.get("visibility")
                expected_visibility = approved.get("target_visibility")
                if not live_id:
                    raise ValueError("GitHub repository immutable ID is missing")
                if live_visibility != expected_visibility:
                    return {
                        "verdict": FAIL,
                        "expected_visibility": expected_visibility,
                        "actual_visibility": live_visibility,
                        "detail": "GitHub repository visibility drifted after approval",
                    }
                if approved.get("target_exists"):
                    expected_id = str(approved.get("target_repo_id") or "")
                    if live_id != expected_id:
                        return {
                            "verdict": FAIL,
                            "expected_repo_id": expected_id,
                            "actual_repo_id": live_id,
                            "detail": (
                                "GitHub repository was deleted or replaced after "
                                "plan approval"
                            ),
                        }
                elif not self.db.repository_owned_by_run(
                    repo.gh_org,
                    repo.gh_repo,
                    plan_id=self.approved_plan_id,
                    run_id=self.approved_run_id,
                    target_repo_id=live_id,
                ):
                    return {
                        "verdict": FAIL,
                        "actual_repo_id": live_id,
                        "detail": (
                            "Created GitHub repository does not match the exact "
                            "run ownership receipt"
                        ),
                    }
            return {
                "verdict": PASS,
                "detail": "GitHub repo exists with approved immutable identity",
            }
        except Exception as exc:
            return self._api_failure("Cannot verify GitHub repository", exc)

    def _check_default_branch(self, repo: RepoConfig,
                              snapshot: dict = None) -> dict:
        """Verify default branch name matches between ADO and GH."""
        try:
            ado_repo = self._snapshot_value(
                snapshot, "ado_repo",
                lambda: self.ado.get_repo(repo.ado_project, repo.ado_repo),
            )
            ado_raw = ado_repo.get("defaultBranch")
            if not isinstance(ado_raw, str) or not ado_raw.strip():
                if self._both_repositories_empty(repo, snapshot):
                    return {
                        "verdict": PASS,
                        "ado_branch": None,
                        "gh_branch": None,
                        "expected": None,
                        "actual": None,
                        "not_applicable": True,
                        "detail": "Both repositories are empty; default branch is not applicable",
                    }
                raise ValueError("ADO response did not include defaultBranch")
            ado_default = self._short_ref_name(ado_raw, "heads")
        except Exception as exc:
            return self._api_failure(
                "Cannot verify ADO default branch", exc,
                ado_branch=None, gh_branch=None,
            )

        try:
            gh_repo = self._snapshot_value(
                snapshot, "gh_repo",
                lambda: self.gh.get_repo(repo.gh_org, repo.gh_repo),
            )
            gh_default = gh_repo.get("default_branch")
            if not isinstance(gh_default, str) or not gh_default.strip():
                if self._both_repositories_empty(repo, snapshot):
                    return {
                        "verdict": PASS,
                        "ado_branch": ado_default,
                        "gh_branch": None,
                        "expected": None,
                        "actual": None,
                        "not_applicable": True,
                        "detail": "Both repositories are empty; default branch is not applicable",
                    }
                raise ValueError("GitHub response did not include default_branch")
            gh_default = gh_default.strip()
        except Exception as exc:
            return self._api_failure(
                "Cannot verify GitHub default branch", exc,
                ado_branch=ado_default, gh_branch=None,
            )

        match = ado_default == gh_default
        return {
            "verdict": PASS if match else FAIL,
            "ado_branch": ado_default,
            "gh_branch": gh_default,
            "expected": ado_default,
            "actual": gh_default,
            "detail": ("Default branch matches" if match
                       else f"Branch mismatch: ADO={ado_default}, GH={gh_default}"),
        }

    def _check_head_commit_sha(self, repo: RepoConfig,
                               snapshot: dict = None) -> dict:
        """Compare the HEAD commit SHA of the default branch — proves code transferred."""
        try:
            ado_repo = self._snapshot_value(
                snapshot, "ado_repo",
                lambda: self.ado.get_repo(repo.ado_project, repo.ado_repo),
            )
            repo_id = self._required_field(ado_repo, "id", "ADO repository")
            ado_refs = self._snapshot_value(
                snapshot, "ado_branches",
                lambda: self._list_ado_refs(
                    repo.ado_project, str(repo_id), "heads"
                ),
            )
            if not ado_refs:
                gh_refs = self._snapshot_value(
                    snapshot, "gh_branches",
                    lambda: self._list_github_branches(
                        repo.gh_org, repo.gh_repo
                    ),
                )
                if not gh_refs:
                    return {
                        "verdict": PASS,
                        "ado_sha": "",
                        "gh_sha": "",
                        "expected": None,
                        "actual": None,
                        "not_applicable": True,
                        "detail": "Both repositories are empty; HEAD is not applicable",
                    }
            ado_raw = self._required_field(
                ado_repo, "defaultBranch", "ADO repository"
            )
            ado_default = self._short_ref_name(ado_raw, "heads")
            ado_sha = ado_refs.get(ado_default, "")
        except Exception as exc:
            return self._api_failure(
                "Cannot verify ADO HEAD commit", exc,
                ado_sha=None, gh_sha=None,
            )

        try:
            gh_refs = self._snapshot_value(
                snapshot, "gh_branches",
                lambda: self._list_github_branches(repo.gh_org, repo.gh_repo),
            )
            gh_sha = gh_refs.get(ado_default, "")
        except Exception as exc:
            return self._api_failure(
                "Cannot verify GitHub HEAD commit", exc,
                ado_sha=ado_sha, gh_sha=None,
            )

        if not ado_sha:
            return {
                "verdict": FAIL,
                "detail": f"ADO default branch {ado_default!r} has no ref SHA",
                "ado_sha": "",
                "gh_sha": gh_sha,
                "expected": "non-empty source SHA",
                "actual": gh_sha or None,
            }

        match = ado_sha == gh_sha
        overlay = None
        if not match and self._approved_pipeline_validation:
            overlay = self._check_default_branch_overlay(
                repo, snapshot, ado_sha, gh_sha
            )
            match = overlay.get("verdict") == PASS
        return {
            "verdict": PASS if match else FAIL,
            # Full SHAs are intentionally retained as validation evidence.
            "ado_sha": ado_sha,
            "gh_sha": gh_sha,
            "expected": ado_sha,
            "actual": gh_sha or None,
            "overlay": overlay,
            "detail": (
                "HEAD commit SHA matches — code verified"
                if ado_sha == gh_sha
                else "Default branch is an exact approved pipeline overlay"
                if match
                else (
                    f"SHA MISMATCH: ADO={ado_sha[:12]} "
                    f"GH={gh_sha[:12] if gh_sha else 'missing'}"
                )
            ),
        }

    def _check_branch_count(self, repo: RepoConfig,
                            snapshot: dict = None) -> dict:
        """Verify exact branch name and tip-SHA fidelity.

        The historical method name is retained for API compatibility.  Counts
        alone are not proof of migration: two equally-sized ref sets may point
        at entirely different commits.
        """
        try:
            ado_repo = self._snapshot_value(
                snapshot, "ado_repo",
                lambda: self.ado.get_repo(repo.ado_project, repo.ado_repo),
            )
            repo_id = self._required_field(ado_repo, "id", "ADO repository")
            ado_refs = self._snapshot_value(
                snapshot, "ado_branches",
                lambda: self._list_ado_refs(
                    repo.ado_project, str(repo_id), "heads"
                ),
            )
        except Exception as exc:
            return self._api_failure(
                "Cannot retrieve ADO branch refs", exc,
                ado_count=None, gh_count=None,
            )

        try:
            gh_refs = self._snapshot_value(
                snapshot, "gh_branches",
                lambda: self._list_github_branches(repo.gh_org, repo.gh_repo),
            )
        except Exception as exc:
            return self._api_failure(
                "Cannot retrieve GitHub branch refs", exc,
                ado_count=len(ado_refs), gh_count=None,
            )

        if not ado_refs:
            result = self._compare_ref_maps(ado_refs, gh_refs, "branches")
            result.update(
                ado_count=0,
                gh_count=len(gh_refs),
                allowed_overlay_branches=[],
                default_overlay=None,
                staging_overlay=None,
            )
            return result

        ado_default = self._short_ref_name(
            self._required_field(ado_repo, "defaultBranch", "ADO repository"),
            "heads",
        )
        source_default_sha = ado_refs.get(ado_default, "")
        target_default_sha = gh_refs.get(ado_default, "")
        source_other = dict(ado_refs)
        target_other = dict(gh_refs)
        source_other.pop(ado_default, None)
        target_other.pop(ado_default, None)

        allowed_staging: list[str] = []
        staging_validation = None
        staging = self.approved_staging_branch
        if (
            self._approved_pipeline_validation
            and staging
            and staging not in source_other
            and staging in target_other
        ):
            staging_sha = target_other.pop(staging)
            staging_validation = self._check_default_branch_overlay(
                repo, snapshot, source_default_sha, staging_sha
            )
            if staging_validation.get("verdict") == PASS:
                allowed_staging.append(staging)

        result = self._compare_ref_maps(
            source_other, target_other, "non-default branches"
        )
        default_overlay = None
        default_matches = bool(source_default_sha) and (
            source_default_sha == target_default_sha
        )
        if not default_matches and self._approved_pipeline_validation:
            default_overlay = self._check_default_branch_overlay(
                repo, snapshot, source_default_sha, target_default_sha
            )
            default_matches = default_overlay.get("verdict") == PASS
        if not default_matches:
            result["verdict"] = FAIL
            result.setdefault("mismatched", []).append({
                "name": ado_default,
                "expected_sha": source_default_sha,
                "actual_sha": target_default_sha,
                "overlay": default_overlay,
            })
            result["discrepancy_count"] = int(
                result.get("discrepancy_count", 0)
            ) + 1
        if staging_validation is not None and staging not in allowed_staging:
            result["verdict"] = FAIL
            result.setdefault("mismatched", []).append({
                "name": staging,
                "expected_sha": "approved pipeline overlay",
                "actual_sha": gh_refs.get(staging, ""),
                "overlay": staging_validation,
            })
            result["discrepancy_count"] = int(
                result.get("discrepancy_count", 0)
            ) + 1
        if result["verdict"] == PASS:
            result["detail"] = (
                f"All {len(ado_refs)} source branches match; default branch "
                "may contain only the exact approved pipeline overlay"
            )
        result.update(
            ado_count=len(ado_refs),
            gh_count=len(gh_refs),
            allowed_overlay_branches=allowed_staging,
            default_overlay=default_overlay,
            staging_overlay=staging_validation,
        )
        return result

    def _check_tags(self, repo: RepoConfig, snapshot: dict = None) -> dict:
        """Verify exact tag name and ref-object SHA fidelity."""
        try:
            ado_repo = self._snapshot_value(
                snapshot, "ado_repo",
                lambda: self.ado.get_repo(repo.ado_project, repo.ado_repo),
            )
            repo_id = self._required_field(ado_repo, "id", "ADO repository")
            ado_refs = self._snapshot_value(
                snapshot, "ado_tags",
                lambda: self._list_ado_refs(
                    repo.ado_project, str(repo_id), "tags"
                ),
            )
        except Exception as exc:
            return self._api_failure(
                "Cannot retrieve ADO tag refs", exc,
                ado_count=None, gh_count=None,
            )

        try:
            gh_refs = self._snapshot_value(
                snapshot, "gh_tags",
                lambda: self._list_github_tags(repo.gh_org, repo.gh_repo),
            )
        except Exception as exc:
            return self._api_failure(
                "Cannot retrieve GitHub tag refs", exc,
                ado_count=len(ado_refs), gh_count=None,
            )

        result = self._compare_ref_maps(ado_refs, gh_refs, "tags")
        result.update(ado_count=len(ado_refs), gh_count=len(gh_refs))
        return result

    def _collect_ref_snapshot(self, repo: RepoConfig) -> dict:
        values: dict[str, Any] = {}
        errors: dict[str, Exception] = {}

        def capture(key: str, loader):
            try:
                values[key] = loader()
            except Exception as exc:  # evidence is converted by each check
                errors[key] = exc

        approved = self.approved_source_snapshots.get(
            f"{repo.ado_project}/{repo.ado_repo}"
        )
        if approved is not None:
            try:
                expected = self._approved_ado_snapshot(approved)
                live_repo = self.ado.get_repo(
                    repo.ado_project, repo.ado_repo
                )
                live_repo_id = self._required_field(
                    live_repo, "id", "ADO repository"
                )
                live_branches = self._list_ado_refs(
                    repo.ado_project, live_repo_id, "heads"
                )
                live_tags = self._list_ado_refs(
                    repo.ado_project, live_repo_id, "tags"
                )
                expected_repo = expected["ado_repo"]
                if (
                    live_repo_id != expected_repo["id"]
                    or live_repo.get("defaultBranch", "")
                    != expected_repo["defaultBranch"]
                    or live_branches != expected["ado_branches"]
                    or live_tags != expected["ado_tags"]
                ):
                    raise RuntimeError(
                        "ADO source identity/default branch/refs changed after "
                        "plan approval; validation is stale"
                    )
                values.update({
                    "ado_repo": live_repo,
                    "ado_branches": live_branches,
                    "ado_tags": live_tags,
                })
            except Exception as exc:
                errors["ado_repo"] = exc
                errors["ado_branches"] = exc
                errors["ado_tags"] = exc
        else:
            capture(
                "ado_repo",
                lambda: self.ado.get_repo(repo.ado_project, repo.ado_repo),
            )
            if "ado_repo" in values:
                try:
                    repo_id = self._required_field(
                        values["ado_repo"], "id", "ADO repository"
                    )
                except Exception as exc:
                    errors["ado_branches"] = exc
                    errors["ado_tags"] = exc
                else:
                    capture(
                        "ado_branches",
                        lambda: self._list_ado_refs(
                            repo.ado_project, str(repo_id), "heads"
                        ),
                    )
                    capture(
                        "ado_tags",
                        lambda: self._list_ado_refs(
                            repo.ado_project, str(repo_id), "tags"
                        ),
                    )
            else:
                errors["ado_branches"] = errors["ado_repo"]
                errors["ado_tags"] = errors["ado_repo"]

        capture(
            "gh_repo",
            lambda: self.gh.get_repo(repo.gh_org, repo.gh_repo),
        )
        capture(
            "gh_branches",
            lambda: self._list_github_branches(repo.gh_org, repo.gh_repo),
        )
        capture(
            "gh_tags",
            lambda: self._list_github_tags(repo.gh_org, repo.gh_repo),
        )
        return {"values": values, "errors": errors}

    @staticmethod
    def _approved_ado_snapshot(
        approved: Mapping[str, Any]
    ) -> dict[str, Any]:
        repo_id = str(approved.get("source_repo_id", "")).strip()
        default_branch = str(approved.get("default_branch", ""))
        if not repo_id or default_branch.startswith("refs/"):
            raise ValueError("approved source identity/default branch is invalid")

        def short_refs(field: str, namespace: str) -> dict[str, str]:
            raw = approved.get(field)
            if not isinstance(raw, Mapping):
                raise TypeError(f"approved {field} must be a ref map")
            prefix = f"refs/{namespace}/"
            result: dict[str, str] = {}
            for raw_name, raw_sha in raw.items():
                name, sha = str(raw_name), str(raw_sha).strip().lower()
                if not name.startswith(prefix) or not name[len(prefix):]:
                    raise ValueError(f"approved ref {name!r} is invalid")
                short_name = name[len(prefix):]
                if short_name in result:
                    raise ValueError(f"approved ref {name!r} is duplicated")
                if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", sha):
                    raise ValueError(f"approved ref {name!r} has an invalid SHA")
                result[short_name] = sha
            return result

        branches = short_refs("source_branch_refs", "heads")
        tags = short_refs("source_tag_refs", "tags")
        return {
            "ado_repo": {
                "id": repo_id,
                "defaultBranch": (
                    f"refs/heads/{default_branch}" if default_branch else ""
                ),
            },
            "ado_branches": branches,
            "ado_tags": tags,
        }

    def _both_repositories_empty(self, repo: RepoConfig,
                                 snapshot: dict = None) -> bool:
        ado_refs = self._snapshot_value(
            snapshot, "ado_branches",
            lambda: self._list_ado_refs(
                repo.ado_project,
                self._required_field(
                    self.ado.get_repo(repo.ado_project, repo.ado_repo),
                    "id", "ADO repository",
                ),
                "heads",
            ),
        )
        gh_refs = self._snapshot_value(
            snapshot, "gh_branches",
            lambda: self._list_github_branches(repo.gh_org, repo.gh_repo),
        )
        return not ado_refs and not gh_refs

    @staticmethod
    def _snapshot_value(snapshot: dict, key: str, loader):
        if snapshot is None:
            return loader()
        errors = snapshot.get("errors", {})
        if key in errors:
            raise errors[key]
        values = snapshot.get("values", {})
        if key not in values:
            raise RuntimeError(f"validation snapshot is missing {key}")
        return values[key]

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        """Return bounded, single-line error evidence without a traceback."""
        message = " ".join(str(exc).split())
        if len(message) > 500:
            message = message[:497] + "..."
        return f"{type(exc).__name__}: {message}" if message else type(exc).__name__

    @classmethod
    def _api_failure(cls, detail: str, exc: Exception, **evidence) -> dict:
        # Validation is deliberately fail closed.  WARN is reserved for a
        # check which completed and found a non-blocking condition, never for
        # an unavailable or malformed source of truth.
        return {
            "verdict": FAIL,
            "detail": f"{detail}: {cls._safe_error(exc)}",
            "error_type": type(exc).__name__,
            **evidence,
        }

    @staticmethod
    def _required_field(payload: Any, field: str, source: str) -> str:
        if not isinstance(payload, dict):
            raise TypeError(f"{source} response must be an object")
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{source} response is missing {field!r}")
        return value.strip()

    @staticmethod
    def _short_ref_name(value: str, namespace: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("ref name must be a non-empty string")
        value = value.strip()
        prefix = f"refs/{namespace}/"
        return value[len(prefix):] if value.startswith(prefix) else value

    @staticmethod
    def _ref_digest(refs: dict[str, str]) -> str:
        canonical = json.dumps(
            sorted(refs.items()), separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def _normalize_ref_rows(cls, payload: Any, namespace: str) -> dict[str, str]:
        """Normalize ADO/GitHub ref payloads to ``short-name -> full SHA``."""
        if isinstance(payload, dict) and "value" in payload:
            payload = payload["value"]
        if not isinstance(payload, list):
            raise TypeError("refs response must be a list")

        refs: dict[str, str] = {}
        prefix = f"refs/{namespace}/"
        for index, row in enumerate(payload):
            if not isinstance(row, dict):
                raise TypeError(f"ref at index {index} must be an object")

            raw_name = row.get("name") or row.get("ref")
            # GitHub's tags collection uses short names; its git-ref endpoint
            # and ADO use fully qualified refs.
            if isinstance(raw_name, str) and raw_name.startswith("refs/"):
                if not raw_name.startswith(prefix):
                    continue
                name = raw_name[len(prefix):]
            else:
                name = raw_name

            sha = row.get("objectId")
            if not sha and isinstance(row.get("object"), dict):
                sha = row["object"].get("sha")
            if not sha and isinstance(row.get("commit"), dict):
                sha = row["commit"].get("sha")

            if not isinstance(name, str) or not name.strip():
                raise ValueError(f"ref at index {index} has no name")
            if not isinstance(sha, str) or not sha.strip():
                raise ValueError(f"ref {name!r} has no SHA")
            name, sha = name.strip(), sha.strip().lower()
            previous = refs.get(name)
            if previous is not None and previous != sha:
                raise ValueError(f"duplicate ref {name!r} has conflicting SHAs")
            refs[name] = sha
        return refs

    def _list_ado_refs(self, project: str, repo_id: str,
                       namespace: str) -> dict[str, str]:
        """List all ADO refs, including continuation pages.

        A future/public ``ADOClient.list_refs`` helper is used when available;
        otherwise the client's existing pagination primitive is used.  This
        keeps exact validation possible without silently truncating large repos.
        """
        if namespace not in {"heads", "tags"}:
            raise ValueError(f"unsupported ref namespace: {namespace}")

        helper = getattr(self.ado, "list_refs", None)
        if callable(helper):
            rows = helper(project, repo_id, f"{namespace}/")
            return self._normalize_ref_rows(rows, namespace)

        pager = getattr(self.ado, "_paged_values", None)
        org_url = getattr(self.ado, "org_url", "")
        if not callable(pager) or not isinstance(org_url, str) or not org_url:
            raise RuntimeError("ADO client does not support paginated ref listing")
        project_path = (
            self.ado._p(project) if callable(getattr(self.ado, "_p", None))
            else quote(project, safe="")
        )
        url = (
            f"{org_url.rstrip('/')}/{project_path}/_apis/git/repositories/"
            f"{quote(repo_id, safe='')}/refs"
        )
        rows = pager(url, {
            "api-version": "7.1",
            "filter": f"{namespace}/",
            "$top": 1000,
        })
        return self._normalize_ref_rows(rows, namespace)

    def _list_github_branches(self, org: str, repo: str) -> dict[str, str]:
        rows = self.gh.list_branches(org, repo)
        return self._normalize_ref_rows(rows, "heads")

    def _list_github_tags(self, org: str, repo: str) -> dict[str, str]:
        # Prefer exact Git ref objects.  Unlike the convenient tags collection,
        # this preserves the ref-object SHA for annotated tags as well.
        helper = getattr(self.gh, "list_tag_refs", None)
        if callable(helper):
            return self._normalize_ref_rows(helper(org, repo), "tags")

        get = getattr(self.gh, "_get", None)
        if callable(get):
            rows: list[dict] = []
            page = 1
            while True:
                batch = get(
                    f"/repos/{org}/{repo}/git/matching-refs/tags/",
                    params={"per_page": 100, "page": page},
                )
                if not isinstance(batch, list):
                    raise TypeError("GitHub matching-refs response must be a list")
                rows.extend(batch)
                if len(batch) < 100:
                    break
                page += 1
            return self._normalize_ref_rows(rows, "tags")

        # Compatibility for embedders which expose only a high-level helper.
        helper = getattr(self.gh, "list_tags", None)
        if callable(helper):
            return self._normalize_ref_rows(helper(org, repo), "tags")
        raise RuntimeError("GitHub client does not support tag ref listing")

    @classmethod
    def _compare_ref_maps(cls, source: dict[str, str], target: dict[str, str],
                          label: str) -> dict:
        missing = sorted(set(source) - set(target))
        unexpected = sorted(set(target) - set(source))
        mismatched = [
            {"name": name, "expected_sha": source[name], "actual_sha": target[name]}
            for name in sorted(set(source) & set(target))
            if source[name] != target[name]
        ]
        match = not missing and not unexpected and not mismatched
        matched_count = sum(
            1 for name, sha in source.items() if target.get(name) == sha
        )
        discrepancy_count = len(missing) + len(unexpected) + len(mismatched)
        return {
            "verdict": PASS if match else FAIL,
            "detail": (
                f"All {len(source)} {label} match by name and SHA"
                if match else
                f"{label.capitalize()} differ: {len(missing)} missing, "
                f"{len(unexpected)} unexpected, {len(mismatched)} SHA mismatch(es)"
            ),
            "source_count": len(source),
            "target_count": len(target),
            "matched_count": matched_count,
            "discrepancy_count": discrepancy_count,
            "source_digest": cls._ref_digest(source),
            "target_digest": cls._ref_digest(target),
            "missing": missing,
            "unexpected": unexpected,
            "mismatched": mismatched,
        }

    def _check_default_branch_overlay(
        self,
        repo: RepoConfig,
        snapshot: Optional[dict],
        source_sha: str,
        target_sha: str,
    ) -> dict:
        """Prove a target tip is source plus only approved pipeline artifacts."""
        cache = snapshot.setdefault("overlay_validations", {}) if snapshot else {}
        cache_key = (source_sha, target_sha)
        if cache_key in cache:
            return cache[cache_key]

        workflow_check = (
            snapshot.get("approved_workflow_check") if snapshot else None
        )
        if not isinstance(workflow_check, dict) or workflow_check.get(
            "verdict"
        ) != PASS:
            result = {
                "verdict": FAIL,
                "detail": (
                    "Default-branch overlay is not allowed unless exact approved "
                    "workflow and evidence blobs have already been verified"
                ),
            }
            cache[cache_key] = result
            return result
        manifest = workflow_check.get("artifact_manifest")
        if not isinstance(manifest, list) or not manifest:
            result = {
                "verdict": FAIL,
                "detail": "Default-branch SHA changed without an approved artifact manifest",
            }
            cache[cache_key] = result
            return result

        expected_artifacts: dict[str, str] = {}
        try:
            for index, item in enumerate(manifest):
                if not isinstance(item, dict):
                    raise TypeError(f"artifact manifest item {index} must be an object")
                path = item.get("path")
                blob_sha = item.get("blob_sha")
                kind = item.get("kind")
                if (
                    not isinstance(path, str)
                    or not path
                    or path.startswith("/")
                    or ".." in Path(path).parts
                    or not (
                        path.startswith(".github/workflows/")
                        or path.startswith(".ado2gh/pipeline-evidence/")
                    )
                ):
                    raise ValueError(f"artifact path {path!r} is not allowlisted")
                expected_kind = (
                    "workflow" if path.startswith(".github/workflows/")
                    else "evidence"
                )
                if kind != expected_kind:
                    raise ValueError(
                        f"artifact {path!r} has inconsistent kind {kind!r}"
                    )
                if not isinstance(blob_sha, str) or not re.fullmatch(
                    r"[0-9a-fA-F]{40}", blob_sha
                ):
                    raise ValueError(f"artifact {path!r} has an invalid Git blob SHA")
                if path in expected_artifacts:
                    raise ValueError(f"artifact path {path!r} is duplicated")
                expected_artifacts[path] = blob_sha.lower()
            if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", source_sha):
                raise ValueError("approved source commit SHA is invalid")
            if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", target_sha):
                raise ValueError("target commit SHA is invalid")

            get = getattr(self.gh, "_get", None)
            if not callable(get):
                raise RuntimeError(
                    "GitHub client cannot verify commit ancestry and complete trees"
                )
            compare = get(
                f"/repos/{repo.gh_org}/{repo.gh_repo}/compare/"
                f"{quote(source_sha, safe='')}...{quote(target_sha, safe='')}",
                params={"per_page": 100, "page": 1},
            )
            if not isinstance(compare, dict):
                raise TypeError("GitHub comparison response must be an object")
            merge_base = compare.get("merge_base_commit")
            merge_base_sha = (
                merge_base.get("sha") if isinstance(merge_base, dict) else ""
            )
            if compare.get("status") != "ahead" or merge_base_sha != source_sha:
                raise ValueError(
                    "target default branch is not a strict descendant of the "
                    "approved source commit"
                )

            commits = compare.get("commits")
            total_commits = compare.get("total_commits")
            if (
                not isinstance(commits, list)
                or isinstance(total_commits, bool)
                or not isinstance(total_commits, int)
                or total_commits < 1
                or total_commits > 100
                or len(commits) != total_commits
            ):
                raise RuntimeError(
                    "Overlay history must contain at most 100 completely returned "
                    "commits; squash/rebase the reviewed pipeline PR"
                )

            source_tree = self._complete_git_tree(repo, source_sha)
            target_tree = self._complete_git_tree(repo, target_sha)
            changed_paths = sorted(
                path
                for path in set(source_tree) | set(target_tree)
                if source_tree.get(path) != target_tree.get(path)
            )
            unexpected_paths = sorted(
                set(changed_paths) - set(expected_artifacts)
            )
            missing_artifacts: list[str] = []
            mismatched_artifacts: list[dict[str, str]] = []
            for path, expected_sha in expected_artifacts.items():
                entry = target_tree.get(path)
                if entry is None:
                    missing_artifacts.append(path)
                elif (
                    entry[0] != "blob"
                    or entry[1] != "100644"
                    or entry[2] != expected_sha
                ):
                    mismatched_artifacts.append({
                        "path": path,
                        "expected_blob_sha": expected_sha,
                        "actual_type": entry[0],
                        "actual_mode": entry[1],
                        "actual_blob_sha": entry[2],
                    })
            history_violations: list[dict[str, Any]] = []
            introduced_commits: list[str] = []
            for index, commit in enumerate(commits):
                commit_sha = (
                    commit.get("sha") if isinstance(commit, dict) else None
                )
                if not isinstance(commit_sha, str) or not re.fullmatch(
                    r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit_sha
                ):
                    raise ValueError(f"overlay commit {index} has an invalid SHA")
                introduced_commits.append(commit_sha)
                commit_tree = self._complete_git_tree(repo, commit_sha)
                commit_changed = {
                    path
                    for path in set(source_tree) | set(commit_tree)
                    if source_tree.get(path) != commit_tree.get(path)
                }
                bad_paths = sorted(commit_changed - set(expected_artifacts))
                bad_artifacts: list[str] = []
                for path in sorted(commit_changed & set(expected_artifacts)):
                    entry = commit_tree.get(path)
                    if entry != ("blob", "100644", expected_artifacts[path]):
                        bad_artifacts.append(path)
                if bad_paths or bad_artifacts:
                    history_violations.append({
                        "commit": commit_sha,
                        "unexpected_paths": bad_paths,
                        "non_approved_artifact_versions": bad_artifacts,
                    })
            passed = not unexpected_paths and not missing_artifacts \
                and not mismatched_artifacts and not history_violations
            result = {
                "verdict": PASS if passed else FAIL,
                "source_commit": source_sha,
                "target_commit": target_sha,
                "merge_base_commit": merge_base_sha,
                "changed_paths": changed_paths,
                "allowlisted_paths": sorted(expected_artifacts),
                "unexpected_paths": unexpected_paths,
                "missing_artifacts": missing_artifacts,
                "mismatched_artifacts": mismatched_artifacts,
                "introduced_commits": introduced_commits,
                "history_violations": history_violations,
                "detail": (
                    "Every introduced commit and the final tree contain only "
                    "exact approved pipeline artifacts"
                    if passed
                    else "Target overlay history contains missing, modified, or "
                    "unapproved content"
                ),
            }
        except Exception as exc:
            result = self._api_failure(
                "Cannot prove exact default-branch overlay", exc
            )
        cache[cache_key] = result
        return result

    def _complete_git_tree(
        self, repo: RepoConfig, commit_sha: str
    ) -> dict[str, tuple[str, str, str]]:
        """Return a complete recursive Git tree, rejecting API truncation."""
        get = getattr(self.gh, "_get", None)
        if not callable(get):
            raise RuntimeError("GitHub client does not expose Git object reads")
        commit = get(
            f"/repos/{repo.gh_org}/{repo.gh_repo}/git/commits/"
            f"{quote(commit_sha, safe='')}"
        )
        if not isinstance(commit, dict) or not isinstance(commit.get("tree"), dict):
            raise TypeError("GitHub commit response has no tree object")
        tree_sha = commit["tree"].get("sha")
        if not isinstance(tree_sha, str) or not tree_sha:
            raise ValueError("GitHub commit tree has no SHA")
        payload = get(
            f"/repos/{repo.gh_org}/{repo.gh_repo}/git/trees/"
            f"{quote(tree_sha, safe='')}",
            params={"recursive": "1"},
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("tree"), list):
            raise TypeError("GitHub recursive tree response is malformed")
        if payload.get("truncated") is True:
            return self._walk_complete_git_tree(repo, tree_sha)
        if payload.get("truncated") is not False:
            raise RuntimeError("GitHub recursive tree completeness is unknown")
        result: dict[str, tuple[str, str, str]] = {}
        for index, item in enumerate(payload["tree"]):
            if not isinstance(item, dict):
                raise TypeError(f"Git tree entry {index} must be an object")
            item_type = item.get("type")
            if item_type == "tree":
                continue
            path, mode, sha = item.get("path"), item.get("mode"), item.get("sha")
            if (
                not isinstance(path, str)
                or not path
                or not isinstance(mode, str)
                or not isinstance(sha, str)
                or not sha
            ):
                raise ValueError(f"Git tree entry {index} is incomplete")
            if path in result:
                raise ValueError(f"Git tree path {path!r} is duplicated")
            result[path] = (str(item_type), mode, sha.lower())
        return result

    def _walk_complete_git_tree(
        self, repo: RepoConfig, root_tree_sha: str
    ) -> dict[str, tuple[str, str, str]]:
        """Traverse subtrees when GitHub truncates its recursive shortcut."""
        get = getattr(self.gh, "_get", None)
        if not callable(get):
            raise RuntimeError("GitHub client does not expose Git tree reads")
        pending: list[tuple[str, str]] = [(root_tree_sha, "")]
        result: dict[str, tuple[str, str, str]] = {}
        requests_seen = 0
        while pending:
            tree_sha, prefix = pending.pop()
            requests_seen += 1
            if requests_seen > 1_000_000:
                raise RuntimeError("Git tree traversal exceeded its safety bound")
            payload = get(
                f"/repos/{repo.gh_org}/{repo.gh_repo}/git/trees/"
                f"{quote(tree_sha, safe='')}"
            )
            if not isinstance(payload, dict) or not isinstance(
                payload.get("tree"), list
            ):
                raise TypeError("GitHub tree response is malformed")
            if payload.get("truncated") is True:
                raise RuntimeError("A non-recursive GitHub tree response was truncated")
            for index, item in enumerate(payload["tree"]):
                if not isinstance(item, dict):
                    raise TypeError(f"Git tree entry {index} must be an object")
                name = item.get("path")
                item_type = item.get("type")
                mode = item.get("mode")
                sha = item.get("sha")
                if (
                    not isinstance(name, str)
                    or not name
                    or "/" in name
                    or not isinstance(mode, str)
                    or not isinstance(sha, str)
                    or not sha
                ):
                    raise ValueError(f"Git tree entry {index} is incomplete")
                path = f"{prefix}/{name}" if prefix else name
                if item_type == "tree":
                    pending.append((sha, path))
                    continue
                if path in result:
                    raise ValueError(f"Git tree path {path!r} is duplicated")
                result[path] = (str(item_type), mode, sha.lower())
        return result

    def _check_workflows(
        self, repo: RepoConfig, immutable_ref: str = ""
    ) -> dict:
        if self._approved_pipeline_validation:
            if not immutable_ref:
                try:
                    branch = self.gh.get_default_branch(
                        repo.gh_org, repo.gh_repo
                    )
                    immutable_ref = self.gh.get_branch_sha(
                        repo.gh_org, repo.gh_repo, branch
                    )
                except Exception as exc:
                    return self._api_failure(
                        "Cannot capture immutable workflow validation commit", exc
                    )
            return self._check_approved_workflows(
                repo, immutable_ref=immutable_ref
            )

        inventory_run = None
        try:
            ado_count = self.db.inventory_count_for_repo(
                repo.ado_project, repo.ado_repo
            )
            if isinstance(ado_count, bool) or not isinstance(ado_count, int) \
                    or ado_count < 0:
                raise ValueError("pipeline inventory count must be a non-negative integer")
            get_inventory_run = getattr(
                self.db, "get_latest_pipeline_inventory_run", None
            )
            if callable(get_inventory_run):
                inventory_run = get_inventory_run(repo.ado_project)
        except Exception as exc:
            return self._api_failure(
                "Cannot read pipeline inventory", exc,
                ado_pipelines=None, gh_workflows=None,
            )
        if inventory_run and (
            inventory_run.get("status") != "completed"
            or int(inventory_run.get("failed_count", 0)) > 0
            or int(inventory_run.get("unmapped_count", 0)) > 0
        ):
            return {
                "verdict": FAIL,
                "ado_pipelines": ado_count,
                "gh_workflows": None,
                "inventory_run": inventory_run.get("run_id"),
                "inventory_status": inventory_run.get("status"),
                "detail": "The latest project pipeline inventory is incomplete",
            }
        if ado_count == 0 and callable(
            getattr(self.db, "get_latest_pipeline_inventory_run", None)
        ) and not inventory_run:
            return {
                "verdict": FAIL,
                "ado_pipelines": 0,
                "gh_workflows": None,
                "inventory_status": "not_run",
                "detail": (
                    "Zero stored pipelines cannot be treated as authoritative "
                    "because no completed project inventory receipt exists"
                ),
            }
        try:
            gh_workflows = self._list_github_workflows(
                repo.gh_org, repo.gh_repo
            )
            gh_count = len(gh_workflows)
        except Exception as exc:
            return self._api_failure(
                "Cannot read GitHub workflows", exc,
                ado_pipelines=ado_count, gh_workflows=None,
            )

        if ado_count == 0:
            return {"verdict": PASS, "ado_pipelines": 0, "gh_workflows": gh_count,
                    "detail": "Pipeline inventory confirms no pipelines to convert",
                    "expected": 0, "actual": gh_count, "missing": 0,
                    "comparison": "inventory_count"}

        # When the standard StateDB is available, validate the actual planned
        # filenames and conversion states.  A count can otherwise be satisfied
        # by unrelated pre-existing workflows.
        try:
            migration_rows = self._pipeline_migration_rows(repo)
        except Exception as exc:
            return self._api_failure(
                "Cannot read pipeline migration evidence", exc,
                ado_pipelines=ado_count, gh_workflows=gh_count,
            )
        if migration_rows is not None:
            missing_attempt_ids = [
                row["pipeline_id"] for row in migration_rows
                if row["status"] is None
            ]
            incomplete = [
                {
                    "pipeline_id": row["pipeline_id"],
                    "pipeline_name": row["pipeline_name"],
                    "status": row["status"],
                }
                for row in migration_rows
                if row["status"] not in {None, "completed"}
            ]
            completed = [
                row for row in migration_rows if row["status"] == "completed"
            ]
            missing_attempts = len(missing_attempt_ids)
            missing_artifacts = [
                {
                    "pipeline_id": row["pipeline_id"],
                    "pipeline_name": row["pipeline_name"],
                }
                for row in completed if not str(row["workflow_file"] or "").strip()
            ]
            expected_paths = sorted({
                f".github/workflows/{Path(str(row['workflow_file'])).name}"
                for row in completed if str(row["workflow_file"] or "").strip()
            })
            expected_blob_shas: dict[str, str] = {}
            for row in completed:
                raw_stats = row.get("transform_stats") or "{}"
                try:
                    parsed_stats = (
                        raw_stats if isinstance(raw_stats, dict)
                        else json.loads(raw_stats)
                    )
                except (TypeError, ValueError):
                    parsed_stats = {}
                blob_sha = (
                    parsed_stats.get("workflow_blob_sha", "")
                    if isinstance(parsed_stats, dict) else ""
                )
                if blob_sha and str(row.get("workflow_file") or "").strip():
                    expected_blob_shas[
                        f".github/workflows/{Path(str(row['workflow_file'])).name}"
                    ] = str(blob_sha)
            actual_paths: set[str] = set()
            malformed_workflows: list[int] = []
            for index, workflow in enumerate(gh_workflows):
                if not isinstance(workflow, dict) or not isinstance(
                    workflow.get("path"), str
                ) or not workflow["path"].strip():
                    malformed_workflows.append(index)
                    continue
                actual_paths.add(workflow["path"].lstrip("/"))
            missing_paths = sorted(set(expected_paths) - actual_paths)
            mismatched_paths: list[dict[str, str]] = []
            if expected_blob_shas:
                try:
                    default_branch = self.gh.get_default_branch(
                        repo.gh_org, repo.gh_repo
                    )
                    for path, expected_sha in expected_blob_shas.items():
                        observed_sha = self.gh.get_file_sha(
                            repo.gh_org, repo.gh_repo, path, default_branch
                        )
                        if observed_sha != expected_sha:
                            mismatched_paths.append({
                                "path": path,
                                "expected_blob_sha": expected_sha,
                                "actual_blob_sha": observed_sha,
                            })
                except Exception as exc:
                    return self._api_failure(
                        "Cannot verify workflow content digests",
                        exc,
                        ado_pipelines=ado_count,
                        gh_workflows=gh_count,
                    )
            if (missing_attempts or incomplete or missing_artifacts
                    or missing_paths or mismatched_paths or malformed_workflows):
                return {
                    "verdict": FAIL,
                    "ado_pipelines": ado_count,
                    "gh_workflows": gh_count,
                    "expected": ado_count,
                    "actual": gh_count,
                    "missing": max(0, ado_count - gh_count),
                    "missing_migration_attempts": missing_attempts,
                    "missing_migration_pipeline_ids": missing_attempt_ids,
                    "incomplete_migrations": incomplete,
                    "missing_artifacts": missing_artifacts,
                    "expected_paths": expected_paths,
                    "actual_paths": sorted(actual_paths),
                    "missing_paths": missing_paths,
                    "mismatched_paths": mismatched_paths,
                    "malformed_workflow_indexes": malformed_workflows,
                    "comparison": "pipeline_identity_and_workflow_path",
                    "detail": (
                        "Pipeline workflow validation failed: "
                        f"{missing_attempts} missing attempt(s), "
                        f"{len(incomplete)} incomplete conversion(s), "
                        f"{len(missing_artifacts)} missing artifact(s), "
                        f"{len(missing_paths)} workflow path(s) absent, "
                        f"{len(mismatched_paths)} workflow content mismatch(es)"
                    ),
                }

        if gh_count >= ado_count:
            return {"verdict": PASS, "ado_pipelines": ado_count,
                    "gh_workflows": gh_count,
                    "detail": "All pipelines have corresponding workflows",
                    "expected": ado_count, "actual": gh_count, "missing": 0,
                    "expected_paths": expected_paths if migration_rows is not None else [],
                    "comparison": (
                        "pipeline_identity_and_workflow_path"
                        if migration_rows is not None else "inventory_count"
                    )}
        missing = ado_count - gh_count
        return {"verdict": FAIL, "ado_pipelines": ado_count,
                "gh_workflows": gh_count,
                "expected": ado_count, "actual": gh_count, "missing": missing,
                "detail": f"{missing} required workflow(s) missing"}

    def _verify_action_dependencies(
        self,
        repo: RepoConfig,
        action_uses: set[str],
        immutable_ref: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        """Resolve the complete immutable action/workflow dependency graph.

        Pinning a wrapper repository is insufficient when its descriptor calls
        a mutable nested action or container.  This verifier reads every exact
        descriptor, follows composite/reusable-workflow ``uses`` edges, checks
        Node/Docker entrypoints, and binds all bytes to Git blob IDs.
        """
        receipts: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        verified: set[tuple[str, str, str, str]] = set()
        visiting: set[tuple[str, str, str, str]] = set()
        repository_cache: dict[tuple[str, str, str], dict[str, str]] = {}
        max_nodes = 512
        max_depth = 20

        content_reader = getattr(self.gh, "get_file_content", None)
        if not callable(content_reader):
            return [], [{
                "uses": use,
                "reason": "GitHub client cannot read action dependency content",
            } for use in sorted(action_uses, key=str.casefold)]

        def safe_path(value: str, *, base: str = "") -> str:
            if not isinstance(value, str) or not value.strip() \
                    or "\\" in value or any(ord(char) < 32 for char in value):
                raise ValueError("action dependency path is unsafe")
            normalized = value.strip()
            if normalized.startswith("./"):
                normalized = normalized[2:]
            raw = PurePosixPath(normalized)
            if raw.is_absolute() or any(part in {"", ".", ".."} for part in raw.parts):
                raise ValueError("action dependency path escapes its repository")
            combined = PurePosixPath(base) / raw if base else raw
            if any(part == ".." for part in combined.parts):
                raise ValueError("action dependency path escapes its repository")
            return combined.as_posix()

        def read_bound_file(
            owner: str, name: str, path: str, ref: str
        ) -> tuple[bytes, str]:
            sha = self.gh.get_file_sha(owner, name, path, ref)
            if not re.fullmatch(r"[0-9a-fA-F]{40}", str(sha or "")):
                raise RuntimeError(f"dependency file {path!r} is missing")
            content = content_reader(owner, name, path, ref)
            if not isinstance(content, bytes):
                raise TypeError("GitHub dependency content must be bytes")
            observed = hashlib.sha1(
                f"blob {len(content)}\0".encode("ascii") + content
            ).hexdigest()
            if observed.lower() != str(sha).lower():
                raise RuntimeError(
                    f"dependency file {path!r} does not match its Git blob ID"
                )
            return content, observed.lower()

        def repository_context(owner: str, name: str, ref: str) -> dict[str, str]:
            cache_key = (owner.casefold(), name.casefold(), ref.lower())
            cached = repository_cache.get(cache_key)
            if cached is not None:
                return cached
            metadata = self.gh.get_repo(owner, name)
            if not isinstance(metadata, Mapping):
                raise TypeError("action repository response is malformed")
            repo_id = str(metadata.get("node_id") or metadata.get("id") or "")
            if not repo_id:
                raise RuntimeError("action repository has no immutable ID")
            resolver = getattr(self.gh, "get_commit_sha", None)
            if not callable(resolver) or str(
                resolver(owner, name, ref)
            ).lower() != ref.lower():
                raise RuntimeError("pinned action commit does not resolve")
            visibility = str(metadata.get("visibility") or "")
            access_level = "public"
            if visibility != "public":
                access_reader = getattr(self.gh, "get_actions_access", None)
                if not callable(access_reader):
                    raise RuntimeError("private action sharing policy is unreadable")
                access = access_reader(owner, name)
                if not isinstance(access, Mapping):
                    raise TypeError("private action sharing policy is malformed")
                access_level = str(access.get("access_level") or "")
                if owner.casefold() != repo.gh_org.casefold() or access_level \
                        not in {"organization", "enterprise"}:
                    raise RuntimeError(
                        "private action is not shared with the target repository"
                    )
            result = {
                "repository_id": repo_id,
                "visibility": visibility,
                "actions_access_level": access_level,
            }
            repository_cache[cache_key] = result
            return result

        def parse_yaml(content: bytes, path: str) -> Mapping[str, Any]:
            try:
                parsed = yaml.safe_load(content.decode("utf-8"))
            except (UnicodeDecodeError, yaml.YAMLError) as exc:
                raise ValueError(
                    f"dependency descriptor {path!r} is not valid UTF-8 YAML"
                ) from exc
            if not isinstance(parsed, Mapping):
                raise TypeError(f"dependency descriptor {path!r} is not an object")
            return parsed

        def collect_uses(value: Any) -> list[str]:
            found: list[str] = []
            if isinstance(value, Mapping):
                for key, child in value.items():
                    if key == "uses" and isinstance(child, str):
                        found.append(child.strip())
                    else:
                        found.extend(collect_uses(child))
            elif isinstance(value, list):
                for child in value:
                    found.extend(collect_uses(child))
            return found

        def verify_container(use: str) -> None:
            image = use[len("docker://"):]
            if not re.fullmatch(r"[^\s@]+@sha256:[0-9a-fA-F]{64}", image):
                raise RuntimeError("container action is not digest pinned")
            key = ("docker", "", image.casefold(), "")
            if key not in verified:
                verified.add(key)
                receipts.append({
                    "uses": use,
                    "kind": "container_image",
                    "digest": image.rsplit("@", 1)[1].lower(),
                })

        def verify_dockerfile(
            owner: str,
            name: str,
            ref: str,
            descriptor_dir: str,
            image: str,
        ) -> dict[str, str]:
            dockerfile_path = safe_path(image, base=descriptor_dir)
            content, blob_sha = read_bound_file(
                owner, name, dockerfile_path, ref
            )
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("action Dockerfile is not valid UTF-8") from exc
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                from_match = re.match(
                    r"^FROM(?:\s+--platform=\S+)?\s+(\S+)",
                    stripped,
                    re.IGNORECASE,
                )
                if from_match:
                    base_image = from_match.group(1)
                    if base_image.casefold() != "scratch" and not re.fullmatch(
                        r"[^\s@]+@sha256:[0-9a-fA-F]{64}", base_image
                    ):
                        raise RuntimeError(
                            "action Dockerfile base image is not digest pinned"
                        )
                if re.match(r"^(?:ADD|COPY)\s+https?://", stripped, re.IGNORECASE):
                    raise RuntimeError(
                        "action Dockerfile downloads an unbound remote artifact"
                    )
            return {"path": dockerfile_path, "blob_sha": blob_sha}

        def verify_use(use: str, depth: int = 0) -> None:
            if depth > max_depth:
                raise RuntimeError("action dependency graph exceeds maximum depth")
            if len(verified) + len(visiting) >= max_nodes:
                raise RuntimeError("action dependency graph exceeds maximum size")
            if use.startswith("docker://"):
                verify_container(use)
                return
            if use.startswith("./"):
                owner, name, ref = repo.gh_org, repo.gh_repo, immutable_ref
                subpath = safe_path(use[2:])
                context = {
                    "repository_id": "target-repository",
                    "visibility": "target",
                    "actions_access_level": "local",
                }
            else:
                if "@" not in use:
                    raise RuntimeError("action dependency is missing an immutable ref")
                action_path, ref = use.rsplit("@", 1)
                parts = action_path.split("/")
                if len(parts) < 2 or not re.fullmatch(
                    r"[0-9a-fA-F]{40}", ref
                ):
                    raise RuntimeError(
                        "action dependency is not pinned to a full commit SHA"
                    )
                owner, name = parts[0], parts[1]
                subpath = safe_path("/".join(parts[2:])) \
                    if len(parts) > 2 else ""
                context = repository_context(owner, name, ref)

            if subpath.startswith(".github/workflows/"):
                kind = "reusable_workflow"
                candidates = [subpath]
            else:
                kind = "action"
                prefix = f"{subpath}/" if subpath else ""
                candidates = [prefix + "action.yml", prefix + "action.yaml"]
            entrypoints: list[tuple[str, bytes, str]] = []
            for candidate in candidates:
                sha = self.gh.get_file_sha(owner, name, candidate, ref)
                if not sha:
                    continue
                content, blob_sha = read_bound_file(
                    owner, name, candidate, ref
                )
                entrypoints.append((candidate, content, blob_sha))
            if len(entrypoints) != 1:
                raise RuntimeError(
                    "pinned action has no unique runnable entrypoint"
                )
            entrypoint, content, blob_sha = entrypoints[0]
            graph_key = (
                owner.casefold(), name.casefold(), ref.lower(), entrypoint
            )
            if graph_key in verified:
                return
            if graph_key in visiting:
                raise RuntimeError("action dependency graph contains a cycle")
            visiting.add(graph_key)
            descriptor = parse_yaml(content, entrypoint)
            dependency_files: list[dict[str, str]] = []
            nested_uses: list[str] = []
            if kind == "reusable_workflow":
                nested_uses = collect_uses(descriptor)
            else:
                runs = descriptor.get("runs")
                if not isinstance(runs, Mapping):
                    raise RuntimeError("action descriptor has no valid runs block")
                using = str(runs.get("using") or "").casefold()
                descriptor_dir = PurePosixPath(entrypoint).parent.as_posix()
                if descriptor_dir == ".":
                    descriptor_dir = ""
                if using == "composite":
                    steps = runs.get("steps")
                    if not isinstance(steps, list):
                        raise RuntimeError("composite action has no steps")
                    nested_uses = collect_uses(steps)
                elif re.fullmatch(r"node(?:12|16|20|24)", using):
                    main = runs.get("main")
                    if not isinstance(main, str) or not main.strip():
                        raise RuntimeError("Node action has no main entrypoint")
                    for field in ("main", "pre", "post"):
                        value = runs.get(field)
                        if value is None:
                            continue
                        if not isinstance(value, str) or not value.strip():
                            raise RuntimeError(
                                f"Node action {field} entrypoint is invalid"
                            )
                        path = safe_path(value, base=descriptor_dir)
                        _data, file_sha = read_bound_file(
                            owner, name, path, ref
                        )
                        dependency_files.append({
                            "role": field,
                            "path": path,
                            "blob_sha": file_sha,
                        })
                elif using == "docker":
                    image = runs.get("image")
                    if not isinstance(image, str) or not image.strip():
                        raise RuntimeError("Docker action has no image")
                    if image.startswith("docker://"):
                        verify_container(image)
                    else:
                        dependency_files.append({
                            "role": "dockerfile",
                            **verify_dockerfile(
                                owner, name, ref, descriptor_dir, image
                            ),
                        })
                else:
                    raise RuntimeError(
                        f"action runtime {using!r} is not supported"
                    )

            for dependency in sorted(set(nested_uses), key=str.casefold):
                verify_use(dependency, depth + 1)
            visiting.remove(graph_key)
            verified.add(graph_key)
            receipts.append({
                "uses": use,
                "kind": kind,
                **context,
                "commit_sha": ref.lower(),
                "entrypoint": {
                    "path": entrypoint,
                    "blob_sha": blob_sha,
                },
                "dependency_files": dependency_files,
                "nested_uses": sorted(set(nested_uses), key=str.casefold),
            })

        for use in sorted(action_uses, key=str.casefold):
            try:
                verify_use(use)
            except Exception as exc:
                failures.append({
                    "uses": use,
                    "reason": self._safe_error(exc),
                })
        receipts.sort(
            key=lambda item: (
                str(item.get("uses", "")).casefold(),
                str(item.get("kind", "")),
            )
        )
        return receipts, failures

    def _check_approved_workflows(
        self, repo: RepoConfig, immutable_ref: str = ""
    ) -> dict:
        """Validate only the exact pipeline set authorized by a PEV plan.

        This path deliberately does not read ``pipeline_inventory`` or the
        latest inventory-run receipt.  Both are mutable operational state and
        therefore cannot change the meaning of an already approved plan.
        """
        source_key = f"{repo.ado_project}/{repo.ado_repo}"
        if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", immutable_ref):
            return {
                "verdict": FAIL,
                "ado_pipelines": None,
                "gh_workflows": None,
                "comparison": "approved_pipeline_receipts",
                "detail": "Immutable target commit is required for workflow validation",
            }
        snapshot = self.approved_pipeline_snapshots.get(source_key)
        if not isinstance(snapshot, Mapping):
            return {
                "verdict": FAIL,
                "ado_pipelines": None,
                "gh_workflows": None,
                "comparison": "approved_pipeline_receipts",
                "detail": f"Approved pipeline snapshot is missing for {source_key}",
            }

        receipts = snapshot.get("pipelines")
        approved_count = snapshot.get("pipeline_count")
        approved_digest = snapshot.get("inventory_digest")
        if (
            not isinstance(receipts, list)
            or isinstance(approved_count, bool)
            or not isinstance(approved_count, int)
            or approved_count != len(receipts)
            or not isinstance(approved_digest, str)
            or approved_digest != content_digest(receipts)
        ):
            return {
                "verdict": FAIL,
                "ado_pipelines": None,
                "gh_workflows": None,
                "comparison": "approved_pipeline_receipts",
                "detail": f"Approved pipeline snapshot is invalid for {source_key}",
            }

        try:
            pattern = re.compile(repo.pipeline_filter) if repo.pipeline_filter else None
            approved_by_identity: dict[tuple[int, str], Mapping[str, Any]] = {}
            for index, receipt in enumerate(receipts):
                if not isinstance(receipt, Mapping):
                    raise TypeError(f"pipeline receipt {index} must be an object")
                pipeline_id = receipt.get("pipeline_id")
                pipeline_type = receipt.get("pipeline_type")
                pipeline_name = receipt.get("pipeline_name")
                if isinstance(pipeline_id, bool) or not isinstance(pipeline_id, int):
                    raise TypeError(
                        f"pipeline receipt {index} has an invalid pipeline_id"
                    )
                if not isinstance(pipeline_type, str) or not pipeline_type.strip():
                    raise TypeError(
                        f"pipeline receipt {index} has an invalid pipeline_type"
                    )
                if not isinstance(pipeline_name, str) or not pipeline_name.strip():
                    raise TypeError(
                        f"pipeline receipt {index} has an invalid pipeline_name"
                    )
                receipt_repo = receipt.get("repo_name")
                if receipt_repo != repo.ado_repo:
                    raise ValueError(
                        f"pipeline receipt {index} belongs to repository "
                        f"{receipt_repo!r}, not {repo.ado_repo!r}"
                    )
                identity = (pipeline_id, pipeline_type.strip())
                if identity in approved_by_identity:
                    raise ValueError(
                        f"duplicate approved pipeline identity {identity!r}"
                    )
                if pattern is None or pattern.search(pipeline_name):
                    approved_by_identity[identity] = receipt
        except Exception as exc:
            return self._api_failure(
                "Cannot interpret approved pipeline receipts",
                exc,
                ado_pipelines=None,
                gh_workflows=None,
                comparison="approved_pipeline_receipts",
            )

        expected = set(approved_by_identity)
        ado_count = len(expected)
        live_pipeline_check = self._check_live_pipeline_inventory(
            repo, approved_by_identity
        )
        if live_pipeline_check.get("verdict") != PASS:
            return live_pipeline_check
        try:
            migration_rows = self._approved_pipeline_migration_rows(repo)
        except Exception as exc:
            return self._api_failure(
                "Cannot read approved-wave pipeline migration evidence",
                exc,
                ado_pipelines=ado_count,
                gh_workflows=None,
                approved_wave_id=self.approved_wave_id,
                comparison="approved_pipeline_receipts",
            )

        rows_by_identity: dict[tuple[int, str], dict[str, Any]] = {}
        malformed_rows: list[int] = []
        for index, row in enumerate(migration_rows):
            try:
                pipeline_id = row.get("pipeline_id")
                pipeline_type = row.get("pipeline_type")
                if isinstance(pipeline_id, bool) or not isinstance(pipeline_id, int):
                    raise TypeError("pipeline_id must be an integer")
                if not isinstance(pipeline_type, str) or not pipeline_type.strip():
                    raise TypeError("pipeline_type must be a non-empty string")
                identity = (pipeline_id, pipeline_type.strip())
                if identity in rows_by_identity:
                    raise ValueError("duplicate identity")
                rows_by_identity[identity] = row
            except (TypeError, ValueError):
                malformed_rows.append(index)

        observed = set(rows_by_identity)
        missing_identities = sorted(expected - observed)
        unexpected_identities = sorted(observed - expected)
        incomplete: list[dict[str, Any]] = []
        missing_artifacts: list[dict[str, Any]] = []
        missing_content_receipts: list[dict[str, Any]] = []
        expected_paths: list[str] = []
        expected_evidence_paths: list[str] = []
        expected_blob_shas: dict[str, str] = {}
        artifact_kinds: dict[str, str] = {}
        external_configuration_receipts: list[dict[str, Any]] = []

        for identity in sorted(expected & observed):
            row = rows_by_identity[identity]
            receipt = approved_by_identity[identity]
            if row.get("status") != "completed":
                incomplete.append({
                    "pipeline_id": identity[0],
                    "pipeline_type": identity[1],
                    "pipeline_name": receipt.get("pipeline_name", ""),
                    "status": row.get("status"),
                })
                continue
            workflow_file = str(row.get("workflow_file") or "").strip()
            if not workflow_file:
                missing_artifacts.append({
                    "pipeline_id": identity[0],
                    "pipeline_type": identity[1],
                    "pipeline_name": receipt.get("pipeline_name", ""),
                })
                continue
            path = f".github/workflows/{Path(workflow_file).name}"
            raw_stats = row.get("transform_stats") or "{}"
            try:
                stats = raw_stats if isinstance(raw_stats, dict) else json.loads(raw_stats)
            except (TypeError, ValueError):
                stats = {}
            if not isinstance(stats, dict):
                stats = {}
            blob_sha = stats.get("workflow_blob_sha")
            evidence_blob_sha = stats.get("evidence_blob_sha")
            evidence_file = str(stats.get("evidence_file") or "").strip()
            evidence_path = (
                f".ado2gh/pipeline-evidence/{Path(evidence_file).name}"
                if evidence_file else ""
            )
            recorded_evidence_path = str(
                stats.get("evidence_remote_path") or ""
            )
            production_ready = stats.get("production_ready") is True
            external_configuration = stats.get(
                "external_configuration_evidence"
            )
            expected_receipt_digest = content_digest(dict(receipt))
            if (
                not production_ready
                or not isinstance(external_configuration, dict)
                or external_configuration.get("verified") is not True
                or not isinstance(blob_sha, str)
                or not re.fullmatch(r"[0-9a-fA-F]{40}", blob_sha)
                or not isinstance(evidence_blob_sha, str)
                or not re.fullmatch(r"[0-9a-fA-F]{40}", evidence_blob_sha)
                or not evidence_path
                or recorded_evidence_path != evidence_path
                or stats.get("approved_inventory_digest") != approved_digest
                or stats.get("approved_pipeline_receipt_digest")
                != expected_receipt_digest
                or stats.get("conversion_source_fingerprint")
                != row.get("source_fingerprint")
            ):
                missing_content_receipts.append({
                    "pipeline_id": identity[0],
                    "pipeline_type": identity[1],
                    "pipeline_name": receipt.get("pipeline_name", ""),
                })
                continue
            external_configuration_receipts.append(external_configuration)
            if path in expected_blob_shas or evidence_path in expected_blob_shas:
                missing_content_receipts.append({
                    "pipeline_id": identity[0],
                    "pipeline_type": identity[1],
                    "pipeline_name": receipt.get("pipeline_name", ""),
                    "detail": "workflow or evidence artifact path collision",
                })
                continue
            expected_paths.append(path)
            expected_evidence_paths.append(evidence_path)
            expected_blob_shas[path] = blob_sha.lower()
            expected_blob_shas[evidence_path] = evidence_blob_sha.lower()
            artifact_kinds[path] = "workflow"
            artifact_kinds[evidence_path] = "evidence"

        try:
            gh_workflows = self._list_github_workflows(repo.gh_org, repo.gh_repo)
            gh_count = len(gh_workflows)
            permissions_helper = getattr(
                self.gh, "get_actions_permissions", None
            )
            if callable(permissions_helper):
                actions_permissions = permissions_helper(
                    repo.gh_org, repo.gh_repo
                )
            else:
                get = getattr(self.gh, "_get", None)
                if not callable(get):
                    raise RuntimeError(
                        "GitHub client cannot read Actions enablement"
                    )
                actions_permissions = get(
                    f"/repos/{repo.gh_org}/{repo.gh_repo}/actions/permissions"
                )
            if not isinstance(actions_permissions, dict) or not isinstance(
                actions_permissions.get("enabled"), bool
            ):
                raise TypeError(
                    "GitHub Actions permissions response is incomplete"
                )

            required_secret_names = sorted({
                str(name)
                for receipt in external_configuration_receipts
                for name in receipt.get("required_secret_names", [])
            })
            environment_digests: dict[str, str] = {}
            approved_runner_labels: set[str] = set()
            approved_checkouts: list[dict[str, str]] = []
            for receipt in external_configuration_receipts:
                runner_values = receipt.get("runner_labels", [])
                checkout_values = receipt.get("repository_checkouts", [])
                if not isinstance(runner_values, list) or not isinstance(
                    checkout_values, list
                ):
                    raise TypeError(
                        "Pipeline runner/checkout receipt is malformed"
                    )
                approved_runner_labels.update(str(item) for item in runner_values)
                for item in checkout_values:
                    if not isinstance(item, dict):
                        raise TypeError(
                            "Pipeline checkout receipt entry is malformed"
                        )
                    approved_checkouts.append({
                        "repository": str(item.get("repository", "")),
                        "target_repo_id": str(item.get("target_repo_id", "")),
                        "ref": str(item.get("ref", "")),
                        "resolved_sha": str(item.get("resolved_sha", "")),
                    })
                values = receipt.get(
                    "environment_configuration_digests", {}
                )
                if not isinstance(values, dict):
                    raise TypeError(
                        "Pipeline environment configuration receipt is malformed"
                    )
                for name, digest in values.items():
                    prior = environment_digests.get(str(name))
                    if prior is not None and prior != digest:
                        raise RuntimeError(
                            "Pipeline receipts disagree on environment configuration"
                        )
                    environment_digests[str(name)] = str(digest)
            if required_secret_names:
                secret_reader = getattr(
                    self.gh, "list_actions_secret_names", None
                )
                if not callable(secret_reader):
                    raise RuntimeError(
                        "GitHub client cannot read Actions secret names"
                    )
                live_secret_names = secret_reader(
                    repo.gh_org, repo.gh_repo
                )
                missing_external_secrets = sorted(
                    set(required_secret_names) - set(live_secret_names)
                )
            else:
                missing_external_secrets = []
            from ado2gh.pipelines.approvals import (
                github_environment_configuration_digest,
            )
            environment_reader = getattr(self.gh, "get_environment", None)
            drifted_external_environments: list[str] = []
            for name, digest in environment_digests.items():
                if not callable(environment_reader):
                    raise RuntimeError(
                        "GitHub client cannot read environment protection"
                    )
                environment = environment_reader(
                    repo.gh_org, repo.gh_repo, name
                )
                if not isinstance(environment, Mapping) or (
                    github_environment_configuration_digest(environment)
                    != digest
                ):
                    drifted_external_environments.append(name)
            missing_runner_labels: list[str] = []
            if approved_runner_labels:
                runner_reader = getattr(self.gh, "list_actions_runners", None)
                if not callable(runner_reader):
                    raise RuntimeError(
                        "GitHub client cannot read Actions runner labels"
                    )
                runners = runner_reader(repo.gh_org, repo.gh_repo)
                live_labels = {
                    str(label.get("name"))
                    for runner in runners if runner.get("status") == "online"
                    for label in runner.get("labels", [])
                    if isinstance(label, Mapping) and label.get("name")
                }
                missing_runner_labels = sorted(
                    approved_runner_labels - live_labels
                )
            drifted_checkouts: list[str] = []
            resolve_commit = getattr(self.gh, "get_commit_sha", None)
            for checkout in approved_checkouts:
                slug = checkout["repository"]
                if slug.count("/") != 1:
                    drifted_checkouts.append(slug)
                    continue
                owner, name = slug.split("/", 1)
                target = self.gh.get_repo(owner, name)
                live_id = str(
                    target.get("node_id") or target.get("id") or ""
                ) if isinstance(target, Mapping) else ""
                if live_id != checkout["target_repo_id"]:
                    drifted_checkouts.append(slug)
                    continue
                if checkout["ref"]:
                    if not callable(resolve_commit) or resolve_commit(
                        owner, name, checkout["ref"]
                    ).lower() != checkout["resolved_sha"].lower():
                        drifted_checkouts.append(
                            f"{slug}@{checkout['ref']}"
                        )
        except Exception as exc:
            return self._api_failure(
                "Cannot read GitHub workflows",
                exc,
                ado_pipelines=ado_count,
                gh_workflows=None,
                approved_wave_id=self.approved_wave_id,
                comparison="approved_pipeline_receipts",
            )

        actual_paths: set[str] = set()
        malformed_workflows: list[int] = []
        inactive_workflow_paths: list[str] = []
        for index, workflow in enumerate(gh_workflows):
            if not isinstance(workflow, dict) or not isinstance(
                workflow.get("path"), str
            ) or not workflow["path"].strip():
                malformed_workflows.append(index)
                continue
            workflow_path = workflow["path"].lstrip("/")
            actual_paths.add(workflow_path)
            if workflow_path in expected_paths and workflow.get("state") != "active":
                inactive_workflow_paths.append(workflow_path)
        missing_paths = sorted(set(expected_paths) - actual_paths)
        mismatched_paths: list[dict[str, str]] = []
        workflow_action_uses: set[str] = set()
        if expected_blob_shas:
            try:
                content_reader = getattr(self.gh, "get_file_content", None)
                for path, expected_sha in expected_blob_shas.items():
                    observed_sha = self.gh.get_file_sha(
                        repo.gh_org, repo.gh_repo, path, immutable_ref
                    )
                    if observed_sha != expected_sha:
                        mismatched_paths.append({
                            "path": path,
                            "expected_blob_sha": expected_sha,
                            "actual_blob_sha": observed_sha,
                        })
                    if artifact_kinds.get(path) == "workflow":
                        if not callable(content_reader):
                            raise RuntimeError(
                                "GitHub client cannot read immutable workflow content"
                            )
                        parsed = yaml.safe_load(
                            content_reader(
                                repo.gh_org, repo.gh_repo, path, immutable_ref
                            ).decode("utf-8")
                        )

                        def collect_uses(value: Any) -> None:
                            if isinstance(value, Mapping):
                                for key, child in value.items():
                                    if key == "uses" and isinstance(child, str):
                                        workflow_action_uses.add(child.strip())
                                    else:
                                        collect_uses(child)
                            elif isinstance(value, list):
                                for child in value:
                                    collect_uses(child)

                        collect_uses(parsed)
            except Exception as exc:
                return self._api_failure(
                    "Cannot verify workflow and evidence content digests",
                    exc,
                    ado_pipelines=ado_count,
                    gh_workflows=gh_count,
                    approved_wave_id=self.approved_wave_id,
                    comparison="approved_pipeline_receipts",
                )

        allowed_actions = actions_permissions.get("allowed_actions")
        selected_actions_policy: dict[str, Any] = {}
        blocked_action_uses: list[str] = []
        action_dependency_receipts, invalid_action_dependencies = \
            self._verify_action_dependencies(
                repo, workflow_action_uses, immutable_ref
            )
        resolved_action_uses = set(workflow_action_uses) | {
            str(item.get("uses", "")).strip()
            for item in action_dependency_receipts
            if str(item.get("uses", "")).strip()
        }
        if allowed_actions not in {"all", "local_only", "selected"}:
            blocked_action_uses = sorted(resolved_action_uses) or [
                "<actions-policy-unavailable>"
            ]
        elif allowed_actions == "local_only":
            blocked_action_uses = sorted(
                use for use in resolved_action_uses
                if not use.startswith("./")
            )
        elif allowed_actions == "selected":
            try:
                selected_reader = getattr(
                    self.gh, "get_actions_selected_policy", None
                )
                if not callable(selected_reader):
                    raise RuntimeError(
                        "GitHub client cannot read selected-actions policy"
                    )
                selected_actions_policy = selected_reader(
                    repo.gh_org, repo.gh_repo
                )
                patterns = selected_actions_policy["patterns_allowed"]
                github_owned = selected_actions_policy[
                    "github_owned_allowed"
                ]

                def action_allowed(use: str) -> bool:
                    if use.startswith("./"):
                        return True
                    slug = use.split("@", 1)[0]
                    if github_owned and slug.casefold().startswith("actions/"):
                        return True
                    return any(
                        fnmatchcase(use.casefold(), pattern.casefold())
                        or fnmatchcase(slug.casefold(), pattern.casefold())
                        for pattern in patterns
                    )

                # `verified_allowed` is intentionally not treated as proof:
                # the REST response does not attest which referenced creator
                # currently has Marketplace verified status.
                blocked_action_uses = sorted(
                    use for use in resolved_action_uses
                    if not action_allowed(use)
                )
            except Exception as exc:
                return self._api_failure(
                    "Cannot verify GitHub selected-actions policy",
                    exc,
                    ado_pipelines=ado_count,
                    gh_workflows=gh_count,
                    approved_wave_id=self.approved_wave_id,
                    comparison="approved_pipeline_receipts",
                )

        failed = any((
            malformed_rows,
            missing_identities,
            unexpected_identities,
            incomplete,
            missing_artifacts,
            missing_content_receipts,
            missing_paths,
            mismatched_paths,
            malformed_workflows,
            inactive_workflow_paths,
            not actions_permissions["enabled"],
            blocked_action_uses,
            invalid_action_dependencies,
            missing_external_secrets,
            drifted_external_environments,
            missing_runner_labels,
            drifted_checkouts,
        ))
        evidence = {
            "verdict": FAIL if failed else PASS,
            "ado_pipelines": ado_count,
            "gh_workflows": gh_count,
            "expected": ado_count,
            "actual": gh_count,
            "approved_inventory_digest": approved_digest,
            "approved_wave_id": self.approved_wave_id,
            "pipeline_filter": repo.pipeline_filter,
            "missing_pipeline_identities": [list(item) for item in missing_identities],
            "unexpected_pipeline_identities": [
                list(item) for item in unexpected_identities
            ],
            "malformed_migration_row_indexes": malformed_rows,
            "incomplete_migrations": incomplete,
            "missing_artifacts": missing_artifacts,
            "missing_content_receipts": missing_content_receipts,
            "expected_paths": sorted(expected_paths),
            "expected_evidence_paths": sorted(expected_evidence_paths),
            "actual_paths": sorted(actual_paths),
            "missing_paths": missing_paths,
            "mismatched_paths": mismatched_paths,
            "malformed_workflow_indexes": malformed_workflows,
            "inactive_workflow_paths": sorted(inactive_workflow_paths),
            "actions_enabled": actions_permissions["enabled"],
            "allowed_actions": allowed_actions,
            "selected_actions_policy": selected_actions_policy,
            "workflow_action_uses": sorted(workflow_action_uses),
            "resolved_action_uses": sorted(resolved_action_uses),
            "blocked_action_uses": blocked_action_uses,
            "action_dependency_receipts": action_dependency_receipts,
            "invalid_action_dependencies": invalid_action_dependencies,
            "missing_external_secret_names": missing_external_secrets,
            "drifted_external_environments": sorted(
                drifted_external_environments
            ),
            "missing_runner_labels": missing_runner_labels,
            "drifted_repository_checkouts": drifted_checkouts,
            "comparison": "approved_pipeline_receipts",
            "live_pipeline_source_check": live_pipeline_check,
            "artifact_manifest": [
                {
                    "path": path,
                    "blob_sha": expected_blob_shas[path],
                    "kind": artifact_kinds[path],
                }
                for path in sorted(expected_blob_shas)
            ],
        }
        if not failed:
            evidence["detail"] = (
                "Exact approved pipeline snapshot, workflows, and evidence verified"
            )
        elif missing_identities:
            evidence["detail"] = (
                "One or more conversion receipts for the exact approved PEV plan "
                "and run are missing"
            )
        else:
            evidence["detail"] = "Approved pipeline workflow validation failed"
        return evidence

    def _check_live_pipeline_inventory(
        self,
        repo: RepoConfig,
        approved_by_identity: Mapping[tuple[int, str], Mapping[str, Any]],
    ) -> dict:
        """Re-enumerate exact ADO pipeline IDs/revisions without mutating DB."""
        try:
            list_builds = getattr(self.ado, "list_all_pipelines", None)
            get_build = getattr(self.ado, "get_build_definition_full", None)
            list_releases = getattr(
                self.ado, "list_all_release_pipelines", None
            )
            get_release = getattr(self.ado, "get_release_definition", None)
            if not all(callable(item) for item in (
                list_builds, get_build, list_releases, get_release
            )):
                raise RuntimeError(
                    "ADO client cannot perform complete live pipeline inventory"
                )
            approved_repo_ids = {
                str(receipt.get("repo_id") or "").casefold()
                for receipt in approved_by_identity.values()
                if str(receipt.get("repo_id") or "").strip()
            }
            pattern = re.compile(repo.pipeline_filter) if repo.pipeline_filter else None
            live: dict[tuple[int, str], dict[str, Any]] = {}
            build_repo_cache: dict[str, Mapping[str, Any]] = {}

            for stub in list(list_builds(repo.ado_project)):
                if not isinstance(stub, Mapping):
                    raise TypeError("ADO pipeline stub must be an object")
                pipeline_id = stub.get("id")
                if isinstance(pipeline_id, bool) or not isinstance(pipeline_id, int):
                    raise TypeError("ADO pipeline ID must be an integer")
                definition = get_build(repo.ado_project, pipeline_id)
                if not isinstance(definition, Mapping):
                    raise TypeError("ADO build definition must be an object")
                source_repo = definition.get("repository", {})
                if not isinstance(source_repo, Mapping):
                    raise TypeError("ADO build repository metadata must be an object")
                repo_id = str(source_repo.get("id") or "")
                repo_name = str(source_repo.get("name") or "")
                build_repo_cache[str(pipeline_id)] = source_repo
                if not (
                    (repo_id and repo_id.casefold() in approved_repo_ids)
                    or repo_name.casefold() == repo.ado_repo.casefold()
                ):
                    continue
                name = str(definition.get("name") or stub.get("name") or "")
                if pattern is not None and not pattern.search(name):
                    continue
                process = definition.get("process", {})
                pipeline_type = (
                    "yaml"
                    if isinstance(process, Mapping) and process.get("type") == 2
                    else "classic"
                )
                revision = definition.get("revision")
                if isinstance(revision, bool) or not isinstance(revision, int) \
                        or revision < 1:
                    raise ValueError(
                        f"ADO build pipeline {pipeline_id} has no stable revision"
                    )
                live[(pipeline_id, pipeline_type)] = {
                    "pipeline_name": name,
                    "source_revision": revision,
                }

            for stub in list(list_releases(repo.ado_project)):
                if not isinstance(stub, Mapping):
                    raise TypeError("ADO release stub must be an object")
                pipeline_id = stub.get("id")
                if isinstance(pipeline_id, bool) or not isinstance(pipeline_id, int):
                    raise TypeError("ADO release ID must be an integer")
                definition = get_release(repo.ado_project, pipeline_id)
                if not isinstance(definition, Mapping):
                    raise TypeError("ADO release definition must be an object")
                artifact_repo_ids: set[str] = set()
                artifact_repo_names: set[str] = set()
                unresolved = False
                for artifact in definition.get("artifacts", []) or []:
                    if not isinstance(artifact, Mapping) \
                            or artifact.get("type") != "Build":
                        continue
                    reference = artifact.get("definitionReference", {})
                    build_ref = (
                        reference.get("definition", {})
                        if isinstance(reference, Mapping) else {}
                    )
                    build_id = str(
                        build_ref.get("id", build_ref.get("value", ""))
                        if isinstance(build_ref, Mapping) else ""
                    )
                    if not build_id:
                        unresolved = True
                        continue
                    source_repo = build_repo_cache.get(build_id)
                    if source_repo is None:
                        source_repo = get_build(repo.ado_project, int(build_id)).get(
                            "repository", {}
                        )
                        build_repo_cache[build_id] = source_repo
                    if not isinstance(source_repo, Mapping):
                        unresolved = True
                        continue
                    repo_id = str(source_repo.get("id") or "")
                    repo_name = str(source_repo.get("name") or "")
                    if repo_id:
                        artifact_repo_ids.add(repo_id.casefold())
                    if repo_name:
                        artifact_repo_names.add(repo_name.casefold())
                belongs = not unresolved and (
                    artifact_repo_ids == approved_repo_ids
                    if approved_repo_ids else
                    artifact_repo_names == {repo.ado_repo.casefold()}
                )
                if not belongs:
                    continue
                name = str(definition.get("name") or stub.get("name") or "")
                if pattern is not None and not pattern.search(name):
                    continue
                revision = definition.get("revision")
                if isinstance(revision, bool) or not isinstance(revision, int) \
                        or revision < 1:
                    raise ValueError(
                        f"ADO release pipeline {pipeline_id} has no stable revision"
                    )
                live[(pipeline_id, "release")] = {
                    "pipeline_name": name,
                    "source_revision": revision,
                }

            expected = set(approved_by_identity)
            observed = set(live)
            changed = []
            for identity in sorted(expected & observed):
                receipt = approved_by_identity[identity]
                if (
                    live[identity]["pipeline_name"]
                    != receipt.get("pipeline_name")
                    or live[identity]["source_revision"]
                    != receipt.get("source_revision")
                ):
                    changed.append({
                        "identity": list(identity),
                        "approved_name": receipt.get("pipeline_name"),
                        "live_name": live[identity]["pipeline_name"],
                        "approved_revision": receipt.get("source_revision"),
                        "live_revision": live[identity]["source_revision"],
                    })
            missing = sorted(expected - observed)
            added = sorted(observed - expected)
            passed = not missing and not added and not changed
            return {
                "verdict": PASS if passed else FAIL,
                "approved_count": len(expected),
                "observed_count": len(observed),
                "missing": [list(item) for item in missing],
                "added": [list(item) for item in added],
                "changed": changed,
                "detail": (
                    "Live ADO pipeline identities and revisions match the plan"
                    if passed else
                    "ADO pipelines were added, removed, renamed, or revised "
                    "after plan approval"
                ),
            }
        except Exception as exc:
            return self._api_failure(
                "Cannot verify live ADO pipeline inventory",
                exc,
                comparison="approved_pipeline_receipts",
            )

    def _approved_pipeline_migration_rows(self, repo: RepoConfig) -> list[dict]:
        """Read conversion receipts from the approved plan's exact wave only."""
        getter = getattr(self.db, "get_wave_pipeline_migrations", None)
        if not callable(getter):
            raise RuntimeError(
                "State database cannot read exact-wave pipeline migrations"
            )
        try:
            rows = getter(
                self.approved_wave_id,
                pev_plan_id=self.approved_plan_id,
                pev_run_id=self.approved_run_id,
            )
        except TypeError:
            # Compatibility for narrowly scoped test/integration adapters. The
            # exact plan/run filter below remains mandatory.
            rows = getter(self.approved_wave_id)
        if not isinstance(rows, list):
            raise TypeError("Exact-wave pipeline migrations response must be a list")
        selected: list[dict] = []
        for row in rows:
            if not isinstance(row, dict):
                raise TypeError("Pipeline migration receipt must be an object")
            if row.get("wave_id") != self.approved_wave_id:
                raise ValueError("Pipeline migration receipt came from another wave")
            if (
                row.get("project") == repo.ado_project
                and row.get("repo_name") == repo.ado_repo
                and row.get("gh_org") == repo.gh_org
                and row.get("gh_repo") == repo.gh_repo
            ):
                if (
                    row.get("pev_plan_id") != self.approved_plan_id
                    or row.get("pev_run_id") != self.approved_run_id
                ):
                    # Other runs are legitimate immutable history, but they
                    # can never satisfy this validation attempt.
                    continue
                if not re.fullmatch(
                        r"sha256:[0-9a-f]{64}",
                        str(row.get("source_fingerprint") or ""),
                    ):
                    raise ValueError(
                        "Pipeline migration receipt is not bound to the exact "
                        "approved PEV plan and run"
                    )
                selected.append(row)
        return selected

    def _pipeline_migration_rows(self, repo: RepoConfig) -> Optional[list[dict]]:
        connection_factory = getattr(self.db, "_conn", None)
        if not callable(connection_factory):
            return None
        with connection_factory() as conn:
            rows = conn.execute(
                "WITH ranked AS ("
                " SELECT project,pipeline_id,pipeline_type,status,workflow_file,"
                " transform_stats,id,"
                " ROW_NUMBER() OVER ("
                "  PARTITION BY project,pipeline_id,pipeline_type ORDER BY id DESC"
                " ) rn FROM pipeline_migrations"
                " WHERE project=? AND repo_name=? AND gh_org=? AND gh_repo=?"
                ") SELECT i.pipeline_id,i.pipeline_type,i.pipeline_name,"
                " r.status,r.workflow_file,r.transform_stats"
                " FROM pipeline_inventory i LEFT JOIN ranked r"
                "  ON r.project=i.project AND r.pipeline_id=i.pipeline_id"
                " AND r.pipeline_type=i.pipeline_type AND r.rn=1"
                " WHERE i.project=? AND i.repo_name=?"
                " ORDER BY i.pipeline_id,i.pipeline_type",
                (
                    repo.ado_project, repo.ado_repo, repo.gh_org, repo.gh_repo,
                    repo.ado_project, repo.ado_repo,
                ),
            ).fetchall()
        return [dict(row) for row in rows]

    def _list_github_workflows(self, org: str, repo: str) -> list[dict]:
        """Read the complete, identity-checked workflow inventory."""
        workflows = self.gh.list_workflows(org, repo)
        if not isinstance(workflows, list):
            raise TypeError("GitHub workflows response must be a list")
        # GHClient.list_workflows owns pagination and verifies stable
        # total_count plus duplicate identities.  Re-paginating here would
        # duplicate the second page when exactly 100 workflows exist.
        return workflows

    def _check_branch_protection(self, repo: RepoConfig,
                                 snapshot: dict = None) -> dict:
        source_key = f"{repo.ado_project}/{repo.ado_repo}"
        approved_scopes = self.approved_scope_snapshots.get(source_key)
        if approved_scopes is not None:
            approved = approved_scopes.get("branch_policies")
            try:
                evidence = validate_scope_snapshot("branch_policies", approved)
            except Exception as exc:
                return self._api_failure(
                    "Approved branch-policy snapshot is invalid", exc
                )
            count = evidence["item_count"]
            if count == 0:
                return {
                    "verdict": PASS,
                    "not_applicable": True,
                    "approved_policy_count": 0,
                    "approved_snapshot_digest": evidence["digest"],
                    "detail": "Approved source snapshot contains no branch policies",
                }
            return {
                "verdict": WARN,
                "approved_policy_count": count,
                "approved_counts": evidence["counts"],
                "approved_snapshot_digest": evidence["digest"],
                "detail": (
                    "Source branch policies exist; exact type/branch semantic "
                    "parity requires the approved execution review receipt"
                ),
            }
        try:
            gh_repo = self._snapshot_value(
                snapshot, "gh_repo",
                lambda: self.gh.get_repo(repo.gh_org, repo.gh_repo),
            )
            default_branch = self._required_field(
                gh_repo, "default_branch", "GitHub repository"
            )
            # Try to read branch protection
            protection = self.gh._get(
                f"/repos/{repo.gh_org}/{repo.gh_repo}/branches/"
                f"{quote(default_branch, safe='')}/protection"
            )
            if not isinstance(protection, dict) or not protection:
                raise ValueError("GitHub protection response is empty or malformed")
            return {"verdict": PASS, "detail": "Branch protection configured"}
        except Exception as exc:
            return self._api_failure(
                "Required branch protection is absent or could not be verified", exc
            )

    def _write_report(self, results: list[dict], output_path: str):
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        # CSV summary
        csv_path = str(out)
        if csv_path.endswith(".json"):
            csv_path = csv_path.replace(".json", ".csv")
        elif not csv_path.endswith(".csv"):
            csv_path += ".csv"

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "ado_project", "ado_repo", "gh_target", "overall",
                "repo_exists", "default_branch", "head_commit",
                "branches", "tags", "workflows", "branch_protection", "detail",
            ])
            for r in results:
                checks = r.get("checks", {})
                writer.writerow([
                    r.get("ado_project", ""),
                    r.get("ado_repo", ""),
                    r.get("gh_target", ""),
                    r.get("overall", ""),
                    checks.get("repo_exists", {}).get("verdict", ""),
                    checks.get("default_branch", {}).get("verdict", ""),
                    checks.get("head_commit", {}).get("verdict", ""),
                    checks.get("branches", {}).get("verdict", ""),
                    checks.get("tags", {}).get("verdict", ""),
                    checks.get("workflows", {}).get("verdict", ""),
                    checks.get("branch_protection", {}).get("verdict", ""),
                    checks.get("head_commit", {}).get("detail", ""),
                ])

        # JSON detail
        json_path = csv_path.replace(".csv", ".json")
        Path(json_path).write_text(
            json.dumps(results, indent=2, default=str), encoding="utf-8"
        )

        log.info("Validation report: %s + %s", csv_path, json_path)

    def print_summary(self, results: list[dict]):
        from rich.table import Table
        from rich import box

        t = Table(title="Post-Migration Validation", box=box.ROUNDED)
        t.add_column("Repo", style="cyan", max_width=35)
        t.add_column("GH Target", style="green", max_width=30)
        t.add_column("Overall", width=8)
        t.add_column("Commit", width=10)
        t.add_column("Branches", width=10)
        t.add_column("Detail", overflow="fold", max_width=40)

        for r in results:
            checks = r.get("checks", {})
            overall = r.get("overall", "")
            color = {"PASS": "green", "WARN": "yellow", "FAIL": "red"}.get(overall, "white")

            commit_check = checks.get("head_commit", {})
            branch_check = checks.get("branches", {})

            t.add_row(
                r.get("ado_repo", ""),
                r.get("gh_target", ""),
                f"[{color}]{overall}[/{color}]",
                commit_check.get("verdict", "-"),
                branch_check.get("verdict", "-"),
                commit_check.get("detail", r.get("error", "")),
            )

        console.print(t)
        passed = sum(1 for r in results if r.get("overall") == PASS)
        warned = sum(1 for r in results if r.get("overall") == WARN)
        failed = sum(1 for r in results if r.get("overall") == FAIL)
        total = len(results)
        parts = [f"[bold green]{passed} pass[/bold green]"]
        if warned:
            parts.append(f"[bold yellow]{warned} warn[/bold yellow]")
        if failed:
            parts.append(f"[bold red]{failed} fail[/bold red]")
        console.print(f"\n[bold]Validation result:[/bold] " + ", ".join(parts) + f" out of {total}")
