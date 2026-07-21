"""Idempotent delivery of validated workflow artifacts through a review PR."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, TypeVar

import requests
import yaml

from ado2gh.logging_config import log
from ado2gh.models import RepoConfig


_MutationResult = TypeVar("_MutationResult")
MutationRunner = Callable[
    [RepoConfig, str, Mapping[str, Any], Callable[[], _MutationResult]],
    _MutationResult,
]


class WorkflowPublisher:
    """Stage generated workflows on a branch and open exactly one review PR.

    The publisher never merges the PR.  A pipeline migration becomes remotely
    verified only after the reviewed files are observed on the default branch.
    """

    def __init__(
        self,
        gh: Any,
        branch: str = "ado2gh/migrated-workflows",
        *,
        mutation_runner: Optional[MutationRunner] = None,
    ):
        self.gh = gh
        candidate = str(branch or "").strip()
        components = candidate.split("/")
        if (
            not candidate
            or len(candidate) > 255
            or not re.fullmatch(r"[A-Za-z0-9._/-]+", candidate)
            or any(
                not component
                or component.startswith(".")
                or component.endswith((".", ".lock"))
                for component in components
            )
            or "//" in candidate
            or ".." in candidate
        ):
            raise ValueError("Workflow staging branch must be a safe literal ref")
        self.branch = candidate
        self._mutation_runner = mutation_runner

    def verify_default_branch(
        self,
        repo: RepoConfig,
        workflow_files: Iterable[Path],
        evidence_files: Iterable[Path] = (),
        *,
        approved_contents: Optional[Mapping[str, bytes]] = None,
    ) -> dict[str, Any]:
        base = self.gh.get_default_branch(repo.gh_org, repo.gh_repo)
        missing: list[str] = []
        mismatched: list[str] = []
        unverifiable: list[str] = []
        artifacts = [
            (workflow, f".github/workflows/{workflow.name}", "workflow")
            for workflow in workflow_files
        ] + [
            (
                evidence,
                f".ado2gh/pipeline-evidence/{evidence.name}",
                "evidence",
            )
            for evidence in evidence_files
        ]
        manifest: list[dict[str, str]] = []
        for source, path, kind in artifacts:
            try:
                content = self._artifact_content(source, approved_contents)
            except (FileNotFoundError, KeyError, TypeError, ValueError):
                unverifiable.append(path)
                continue
            local_sha = hashlib.sha1(
                f"blob {len(content)}\0".encode("ascii") + content
            ).hexdigest()
            manifest.append({"path": path, "blob_sha": local_sha, "kind": kind})
            remote_sha = self.gh.get_file_sha(
                repo.gh_org, repo.gh_repo, path, base
            )
            if not remote_sha:
                missing.append(path)
                continue
            if remote_sha != local_sha:
                mismatched.append(path)
        manifest.sort(key=lambda item: item["path"])
        manifest_digest = hashlib.sha256(
            json.dumps(
                manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ).encode("utf-8")
        ).hexdigest()
        return {
            "remote_verified": not missing and not mismatched and not unverifiable,
            "base_branch": base,
            "missing": missing,
            "mismatched": mismatched,
            "unverifiable": unverifiable,
            "artifact_manifest": manifest,
            "artifact_manifest_digest": manifest_digest,
        }

    def publish(
        self,
        repo: RepoConfig,
        workflow_files: Iterable[Path],
        evidence_files: Iterable[Path] = (),
        title: str = "Review migrated GitHub Actions workflows",
        *,
        approved_contents: Optional[Mapping[str, bytes]] = None,
    ) -> dict[str, Any]:
        workflows = self._validated_paths(workflow_files, {".yml", ".yaml"})
        evidence = self._validated_paths(evidence_files, {".json", ".md"})
        if not workflows:
            return {
                "remote_verified": True,
                "state": "not_required",
                "workflow_count": 0,
            }

        for workflow in workflows:
            self._assert_staging_branch_cannot_execute(
                workflow,
                self._artifact_content(workflow, approved_contents),
            )

        observed = self.verify_default_branch(
            repo,
            workflows,
            evidence,
            approved_contents=approved_contents,
        )
        if observed["remote_verified"]:
            return {
                **observed,
                "state": "remote_verified",
                "workflow_count": len(workflows),
                "evidence_count": len(evidence),
            }

        unavailable: list[str] = []
        for path in [*workflows, *evidence]:
            try:
                self._artifact_content(path, approved_contents)
            except (FileNotFoundError, KeyError, TypeError, ValueError):
                unavailable.append(str(path))
        if unavailable:
            raise FileNotFoundError(
                "Workflow artifacts are missing locally and are not present on "
                "the target default branch: " + ", ".join(unavailable)
            )

        artifacts: dict[str, bytes] = {}
        for workflow in workflows:
            target_path = f".github/workflows/{workflow.name}"
            artifacts[target_path] = self._artifact_content(
                workflow, approved_contents
            )

        for evidence_file in evidence:
            target_path = f".ado2gh/pipeline-evidence/{evidence_file.name}"
            artifacts[target_path] = self._artifact_content(
                evidence_file, approved_contents
            )

        base = observed["base_branch"]
        base_sha = self.gh.get_branch_sha(repo.gh_org, repo.gh_repo, base)
        atomic_result = self._publish_atomic_artifact_set(
            repo, base=base, base_sha=base_sha, artifacts=artifacts
        )
        published = sorted(artifacts)

        existing = self.gh.find_open_pull_request(
            repo.gh_org, repo.gh_repo, self.branch, base
        )
        if existing:
            pull = existing
        else:
            body = (
                "Validated workflow conversions produced by the ado2gh "
                "Planner–Executor–Validator pipeline.\n\n"
                "This PR is intentionally not auto-merged. Review runner, "
                "secret, environment, permissions, trigger, and third-party "
                "action choices before approval. Validation evidence is "
                "stored under `.ado2gh/pipeline-evidence/`."
            )

            def _create_or_verify_pull_request() -> dict[str, Any]:
                try:
                    result = self.gh.create_pull_request(
                        repo.gh_org,
                        repo.gh_repo,
                        title,
                        body,
                        head=self.branch,
                        base=base,
                    )
                except requests.HTTPError as exc:
                    if exc.response is None or exc.response.status_code != 422:
                        raise
                    # A create race is safe only when the exact review PR can
                    # now be observed.  Other validation failures remain fatal.
                    result = self.gh.find_open_pull_request(
                        repo.gh_org, repo.gh_repo, self.branch, base
                    )
                    if not result:
                        raise
                if not isinstance(result, Mapping) or not result.get("number"):
                    raise RuntimeError(
                        "GitHub pull-request creation returned malformed metadata"
                    )
                return dict(result)

            pull = self._mutate(
                repo,
                "github_create_workflow_review_pull_request",
                {
                    "head": self.branch,
                    "base": base,
                    "title_sha256": hashlib.sha256(
                        title.encode("utf-8")
                    ).hexdigest(),
                },
                _create_or_verify_pull_request,
            )

        log.info(
            "Staged %d pipeline artifact(s) for %s/%s in PR %s",
            len(published), repo.gh_org, repo.gh_repo, pull.get("html_url", ""),
        )
        return {
            **observed,
            "state": "review_pr",
            "workflow_count": len(workflows),
            "evidence_count": len(evidence),
            "published": published,
            "branch": self.branch,
            "staging_commit_sha": atomic_result["commit_sha"],
            "staging_manifest_digest": atomic_result["manifest_digest"],
            "pr_number": pull.get("number"),
            "pr_url": pull.get("html_url", ""),
            "requires_review": True,
        }

    @staticmethod
    def _git_blob_sha(content: bytes) -> str:
        return hashlib.sha1(
            f"blob {len(content)}\0".encode("ascii") + content
        ).hexdigest()

    def _publish_atomic_artifact_set(
        self,
        repo: RepoConfig,
        *,
        base: str,
        base_sha: str,
        artifacts: Mapping[str, bytes],
    ) -> dict[str, str]:
        """Create one content-addressed review commit and then one ref.

        Individual Contents API writes make a workflow executable before the
        complete reviewed set exists.  Git data objects are inert until a ref
        points at the commit, so blobs/tree/commit may be prepared safely and
        the final ref creation is the sole publication point.
        """
        if not artifacts:
            raise ValueError("Atomic workflow publication requires artifacts")
        normalized: dict[str, bytes] = {}
        manifest: list[dict[str, str]] = []
        for raw_path, raw_content in sorted(artifacts.items()):
            path = str(raw_path)
            if (
                not path
                or path.startswith("/")
                or "\\" in path
                or any(part in {"", ".", ".."} for part in path.split("/"))
            ):
                raise ValueError(f"Unsafe Git artifact path: {path!r}")
            if not isinstance(raw_content, bytes):
                raise TypeError("Approved workflow artifact content must be bytes")
            if path in normalized:
                raise ValueError(f"Duplicate Git artifact path: {path!r}")
            normalized[path] = raw_content
            manifest.append({
                "path": path,
                "blob_sha": self._git_blob_sha(raw_content),
                "sha256": hashlib.sha256(raw_content).hexdigest(),
            })
        manifest_digest = hashlib.sha256(
            json.dumps(
                manifest, sort_keys=True, separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        commit_message = (
            "ado2gh workflow review staging v1\n\n"
            f"artifact-manifest-sha256:{manifest_digest}\n"
            f"repository:{repo.gh_org}/{repo.gh_repo}\n"
            f"branch:{self.branch}\n"
            f"base:{base_sha}"
        )

        base_commit = self.gh.get_git_commit(
            repo.gh_org, repo.gh_repo, base_sha
        )
        if not isinstance(base_commit, Mapping) or base_commit.get("sha") != base_sha:
            raise RuntimeError("GitHub base commit identity changed during staging")
        base_tree = base_commit.get("tree", {}) if isinstance(base_commit, Mapping) else {}
        base_tree_sha = base_tree.get("sha") if isinstance(base_tree, Mapping) else None
        if not isinstance(base_tree_sha, str) or not base_tree_sha:
            raise RuntimeError("GitHub base commit has no immutable tree")
        expected_blobs = {
            item["path"]: item["blob_sha"] for item in manifest
        }

        def _stage_all() -> dict[str, Any]:
            existing_sha = self._branch_sha_if_exists(repo)
            if existing_sha:
                self._verify_staging_ref(
                    repo,
                    commit_sha=existing_sha,
                    base_sha=base_sha,
                    base_tree_sha=base_tree_sha,
                    expected_blobs=expected_blobs,
                    expected_message=commit_message,
                )
                return {"commit_sha": existing_sha, "reused": True}

            entries: list[dict[str, str]] = []
            for path, content in normalized.items():
                blob = self.gh.create_git_blob(
                    repo.gh_org, repo.gh_repo, content
                )
                blob_sha = blob.get("sha") if isinstance(blob, Mapping) else None
                if blob_sha != expected_blobs[path]:
                    raise RuntimeError(
                        f"GitHub blob digest mismatch for approved artifact {path!r}"
                    )
                entries.append({
                    "path": path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob_sha,
                })
            tree = self.gh.create_git_tree(
                repo.gh_org,
                repo.gh_repo,
                base_tree_sha=base_tree_sha,
                entries=entries,
            )
            tree_sha = tree.get("sha") if isinstance(tree, Mapping) else None
            if not isinstance(tree_sha, str) or not tree_sha:
                raise RuntimeError("GitHub tree creation returned no digest")
            commit = self.gh.create_git_commit(
                repo.gh_org,
                repo.gh_repo,
                message=commit_message,
                tree_sha=tree_sha,
                parents=[base_sha],
            )
            commit_sha = commit.get("sha") if isinstance(commit, Mapping) else None
            if not isinstance(commit_sha, str) or not commit_sha:
                raise RuntimeError("GitHub commit creation returned no digest")
            try:
                self.gh.create_branch(
                    repo.gh_org, repo.gh_repo, self.branch, commit_sha
                )
            except requests.HTTPError as exc:
                if exc.response is None or exc.response.status_code != 422:
                    raise
                # A 422 can mean many things.  Accept only an exact concurrent
                # publication of this very commit; never adopt an arbitrary
                # pre-existing review ref.
                observed_sha = self._branch_sha_if_exists(repo)
                if observed_sha != commit_sha:
                    raise PermissionError(
                        f"Workflow staging branch {self.branch!r} already exists "
                        "without matching content-addressed ownership"
                    ) from exc
            self._verify_staging_ref(
                repo,
                commit_sha=commit_sha,
                base_sha=base_sha,
                base_tree_sha=base_tree_sha,
                expected_blobs=expected_blobs,
                expected_message=commit_message,
            )
            return {"commit_sha": commit_sha, "reused": False}

        result = self._mutate(
            repo,
            "github_publish_workflow_artifact_set",
            {
                "branch": self.branch,
                "base": base,
                "base_sha": base_sha,
                "artifact_manifest_sha256": manifest_digest,
                "artifact_count": len(normalized),
            },
            _stage_all,
        )
        commit_sha = result.get("commit_sha") if isinstance(result, Mapping) else None
        if not isinstance(commit_sha, str) or not commit_sha:
            raise RuntimeError("Atomic workflow publication returned no commit")
        return {"commit_sha": commit_sha, "manifest_digest": manifest_digest}

    def _branch_sha_if_exists(self, repo: RepoConfig) -> str:
        try:
            value = self.gh.get_branch_sha(
                repo.gh_org, repo.gh_repo, self.branch
            )
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return ""
            raise
        except KeyError:
            # Lightweight test adapters commonly model a missing ref as a
            # missing dictionary key; production clients use HTTP 404.
            return ""
        if not isinstance(value, str) or not value:
            raise RuntimeError(
                f"Workflow staging branch {self.branch!r} has no commit"
            )
        return value

    def _verify_staging_ref(
        self,
        repo: RepoConfig,
        *,
        commit_sha: str,
        base_sha: str,
        base_tree_sha: str,
        expected_blobs: Mapping[str, str],
        expected_message: str,
    ) -> None:
        observed_ref = self._branch_sha_if_exists(repo)
        if observed_ref != commit_sha:
            raise RuntimeError("Workflow staging ref changed during publication")
        commit = self.gh.get_git_commit(repo.gh_org, repo.gh_repo, commit_sha)
        if not isinstance(commit, Mapping):
            raise TypeError("GitHub staging commit response is malformed")
        if commit.get("sha") != commit_sha:
            raise RuntimeError("GitHub staging commit identity is inconsistent")
        if commit.get("message") != expected_message:
            raise PermissionError(
                f"Workflow staging branch {self.branch!r} is not owned by the "
                "approved artifact manifest"
            )
        parents = commit.get("parents")
        parent_shas = [
            item.get("sha") for item in parents
            if isinstance(item, Mapping)
        ] if isinstance(parents, list) else []
        if parent_shas != [base_sha]:
            raise PermissionError(
                "Workflow staging commit is not based on the approved default ref"
            )
        tree = commit.get("tree")
        tree_sha = tree.get("sha") if isinstance(tree, Mapping) else None
        if not isinstance(tree_sha, str) or not tree_sha:
            raise RuntimeError("GitHub staging commit has no tree")
        base_leaves = self._tree_leaves(repo, base_tree_sha)
        observed_leaves = self._tree_leaves(repo, tree_sha)
        expected_leaves = dict(base_leaves)
        for path, blob_sha in expected_blobs.items():
            expected_leaves[path] = ("100644", "blob", blob_sha)
        if observed_leaves != expected_leaves:
            changed = sorted(
                path for path in set(observed_leaves) | set(expected_leaves)
                if observed_leaves.get(path) != expected_leaves.get(path)
            )
            raise PermissionError(
                "Workflow staging tree contains unapproved or stale changes: "
                + ", ".join(changed[:20])
            )

    def _tree_leaves(
        self, repo: RepoConfig, tree_sha: str
    ) -> dict[str, tuple[str, str, str]]:
        result = self.gh.get_git_tree(
            repo.gh_org, repo.gh_repo, tree_sha, recursive=True
        )
        if not isinstance(result, Mapping) or result.get("truncated") is not False:
            raise RuntimeError("GitHub returned an incomplete recursive Git tree")
        if result.get("sha") != tree_sha:
            raise RuntimeError("GitHub Git tree identity is inconsistent")
        entries = result.get("tree")
        if not isinstance(entries, list):
            raise TypeError("GitHub Git tree response is malformed")
        leaves: dict[str, tuple[str, str, str]] = {}
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise TypeError("GitHub Git tree entry is malformed")
            entry_type = entry.get("type")
            if entry_type == "tree":
                continue
            path = entry.get("path")
            mode = entry.get("mode")
            sha = entry.get("sha")
            if not all(isinstance(item, str) and item for item in (path, mode, entry_type, sha)):
                raise TypeError("GitHub Git tree leaf is incomplete")
            if path in leaves:
                raise RuntimeError(f"GitHub Git tree repeats path {path!r}")
            leaves[path] = (mode, entry_type, sha)
        return leaves

    def _mutate(
        self,
        repo: RepoConfig,
        operation_kind: str,
        operation_payload: Mapping[str, Any],
        operation: Callable[[], _MutationResult],
    ) -> _MutationResult:
        if self._mutation_runner is None:
            raise RuntimeError(
                "Workflow publishing requires a PEV target-fenced mutation runner"
            )
        return self._mutation_runner(
            repo, operation_kind, operation_payload, operation
        )

    @staticmethod
    def _validated_paths(
        files: Iterable[Path], allowed_suffixes: set[str]
    ) -> list[Path]:
        result: list[Path] = []
        for raw in files:
            path = Path(raw).resolve()
            if path.suffix.lower() not in allowed_suffixes:
                raise ValueError(f"Unexpected artifact type: {path}")
            # Only leaf filenames are used as remote paths.  Reject control
            # characters rather than allowing a source mapping to shape paths.
            if any(ord(char) < 32 for char in path.name) or path.name in {".", ".."}:
                raise ValueError(f"Unsafe artifact filename: {path.name!r}")
            result.append(path)
        names: set[str] = set()
        for path in result:
            folded = path.name.casefold()
            if folded in names:
                raise ValueError(f"Duplicate artifact filename: {path.name}")
            names.add(folded)
        return sorted(result, key=lambda item: item.name.casefold())

    @staticmethod
    def _artifact_content(
        path: Path,
        approved_contents: Optional[Mapping[str, bytes]],
    ) -> bytes:
        """Return the immutable approved bytes for an artifact.

        A caller that supplies ``approved_contents`` has crossed the PEV
        content boundary; reopening the path in that mode would reintroduce a
        validation-to-publication race.  Keys are normalized absolute paths.
        """
        resolved = str(Path(path).resolve())
        if approved_contents is None:
            return Path(path).read_bytes()
        if resolved not in approved_contents:
            raise KeyError(f"No approved content for pipeline artifact {resolved}")
        content = approved_contents[resolved]
        if not isinstance(content, bytes):
            raise TypeError("Approved workflow artifact content must be bytes")
        return content

    def _assert_staging_branch_cannot_execute(
        self, path: Path, content: bytes
    ) -> None:
        """Prove every execution path retains the exact staging fence."""
        try:
            workflow = yaml.safe_load(content.decode("utf-8"))
        except (UnicodeDecodeError, yaml.YAMLError) as exc:
            raise ValueError(
                f"Approved workflow {path.name!r} is not valid UTF-8 YAML"
            ) from exc
        if not isinstance(workflow, Mapping):
            raise ValueError(f"Approved workflow {path.name!r} is malformed")
        jobs = workflow.get("jobs")
        if not isinstance(jobs, Mapping) or not jobs:
            raise PermissionError(
                f"Workflow {path.name!r} has no provably guarded jobs"
            )
        guard = (
            f"github.ref != 'refs/heads/{self.branch}' && "
            f"github.head_ref != '{self.branch}'"
        )
        for job_id, job in jobs.items():
            if not isinstance(job, Mapping):
                raise PermissionError(
                    f"Workflow {path.name!r} job {job_id!r} is malformed"
                )
            condition = job.get("if")
            if not isinstance(condition, str):
                raise PermissionError(
                    f"Workflow {path.name!r} job {job_id!r} has no exact "
                    "review staging guard"
                )
            expression = condition.strip()
            if expression.startswith("${{") and expression.endswith("}}"):
                expression = expression[3:-2].strip()
            composed_prefix = guard + " && "
            guarded = expression == guard
            if expression.startswith(composed_prefix):
                guarded = self._is_single_outer_parenthesized_expression(
                    expression[len(composed_prefix):]
                )
            if not guarded:
                raise PermissionError(
                    f"Workflow {path.name!r} job {job_id!r} can execute from "
                    f"unreviewed staging branch {self.branch!r}"
                )
        triggers = workflow.get("on", workflow.get(True))
        has_push = False
        push: Any = None
        if isinstance(triggers, str):
            has_push = triggers == "push"
        elif isinstance(triggers, list):
            has_push = "push" in triggers
        elif isinstance(triggers, Mapping):
            has_push = "push" in triggers
            push = triggers.get("push")
        if not has_push:
            return
        if isinstance(push, Mapping):
            branches = push.get("branches")
            if isinstance(branches, list) and branches \
                    and str(branches[-1]) == f"!{self.branch}":
                return
            ignored = push.get("branches-ignore")
            if isinstance(ignored, str):
                ignored = [ignored]
            if isinstance(ignored, list) and self.branch in {
                str(item) for item in ignored
            }:
                return
        raise PermissionError(
            f"Workflow {path.name!r} can run on unreviewed staging branch "
            f"{self.branch!r}; an exact final '!{self.branch}' push branch "
            "exclusion is required before publication"
        )

    @staticmethod
    def _is_single_outer_parenthesized_expression(value: str) -> bool:
        """Prove one pair of parentheses encloses the complete expression.

        A prefix-only check would accept ``guard && (false) || (true)`` and
        accidentally allow the final disjunct to bypass the staging fence.
        This deliberately small scanner understands only the quoting and
        parentheses needed to prove the outer conjunction; the pipeline
        validator remains responsible for the inner GitHub expression.
        """
        expression = value.strip()
        if len(expression) < 3 or expression[0] != "(" or expression[-1] != ")":
            return False
        depth = 0
        quote: Optional[str] = None
        index = 0
        while index < len(expression):
            char = expression[index]
            if quote is not None:
                if char == quote:
                    # GitHub expressions escape a quote by doubling it.
                    if index + 1 < len(expression) and expression[index + 1] == quote:
                        index += 2
                        continue
                    quote = None
            elif char in {"'", '"'}:
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth < 0 or (depth == 0 and index != len(expression) - 1):
                    return False
            index += 1
        return quote is None and depth == 0
