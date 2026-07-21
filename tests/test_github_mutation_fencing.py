from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest

from ado2gh.core.migration_engine import MigrationEngine
from ado2gh.core.workflow_publisher import WorkflowPublisher
from ado2gh.models import MigrationScope, RepoConfig
from ado2gh.pev.source_integrity import create_scope_snapshot


def _repo() -> RepoConfig:
    return RepoConfig(
        ado_project="Payments",
        ado_repo="api",
        gh_org="octo",
        gh_repo="payments-api",
        scopes=["pipelines"],
    )


class _LeaseDB:
    def __init__(self, events: list[str]):
        self.events = events
        self.lease_valid = True
        self.finished = 0

    def assert_pev_target_lease(self, *_args, **_kwargs):
        self.events.append("assert_lease")
        if not self.lease_valid:
            raise RuntimeError("lease expired")

    def renew_pev_target_leases(self, *_args, **_kwargs):
        self.events.append("renew_lease")
        return self.lease_valid

    def begin_pev_remote_operation(self, *_args, **_kwargs):
        self.events.append("begin_operation")
        return "remote-op-1"

    def finish_pev_remote_operation(self, *_args, **_kwargs):
        self.events.append("finish_operation")
        self.finished += 1
        return True

    def record_repository_target_use(self, *_args, **_kwargs):
        self.events.append("record_target_use")


class _TargetGH:
    def repo_exists(self, *_args):
        return True

    def get_repo(self, *_args):
        return {
            "node_id": "R_target_1",
            "size": 0,
            "visibility": "private",
            "default_branch": "",
        }

    def list_git_refs(self, _org, _repo, _namespace):
        return []


def _fenced_engine(events: list[str]) -> tuple[MigrationEngine, _LeaseDB]:
    repo = _repo()
    key = MigrationEngine._target_lease_key(repo)
    refs_digest = MigrationEngine._source_ref_digest({}, {})
    db = _LeaseDB(events)
    engine = MigrationEngine(
        {
            "pev_plan_id": "plan-1",
            "pev_run_id": "run-1",
            "pev_target_fencing_required": True,
            "pev_target_lease_owner": "worker-1",
            "pev_target_fencing_tokens": {key: 7},
            "pev_target_snapshots": {
                "Payments/api": {
                    "target_exists": True,
                    "target_repo_id": "R_target_1",
                    "target_size": 0,
                    "target_visibility": "private",
                    "target_default_branch": "",
                    "target_branch_refs": {},
                    "target_tag_refs": {},
                    "target_refs_digest": refs_digest,
                }
            },
        },
        object(),
        _TargetGH(),
        db,
    )
    return engine, db


def test_github_mutation_is_journaled_before_dispatch_and_resolved_after_fence():
    events: list[str] = []
    engine, db = _fenced_engine(events)

    result = engine._dispatch_github_mutation(
        _repo(),
        "github_test_write",
        {"resource": "safe"},
        lambda: events.append("remote_write") or {"ok": True},
    )

    assert result == {"ok": True}
    assert events.index("begin_operation") < events.index("remote_write")
    assert events.index("remote_write") < events.index("finish_operation")
    assert db.finished == 1


def test_late_success_after_lease_loss_leaves_barrier_and_stops_worker():
    events: list[str] = []
    engine, db = _fenced_engine(events)
    writes = 0

    def _late_write():
        nonlocal writes
        writes += 1
        db.lease_valid = False
        return {"ok": True}

    with pytest.raises(RuntimeError, match="lease expired"):
        engine._dispatch_github_mutation(
            _repo(), "github_late_write", {}, _late_write
        )

    assert writes == 1
    assert db.finished == 0
    with pytest.raises(RuntimeError, match="unreconciled remote mutation"):
        engine._dispatch_github_mutation(
            _repo(),
            "github_second_write",
            {},
            lambda: pytest.fail("stale worker dispatched a second write"),
        )


class _ControlGH:
    def __init__(self):
        self.environments = {}
        self.custom_environment_rules = {}
        self.protections = {}
        self.environment_puts = 0
        self.protection_puts = 0
        self.team_permissions = {}
        self.team_grants = []

    def get_environment(self, _org, _repo, name):
        return self.environments.get(name)

    def create_environment(self, _org, _repo, name):
        self.environment_puts += 1
        self.environments[name] = {"name": name, "protection_rules": []}
        return True

    def list_environment_deployment_protection_rules(
        self, _org, _repo, name
    ):
        return self.custom_environment_rules.get(name, [])

    def get_branch_protection(self, _org, _repo, branch):
        return self.protections.get(branch)

    def set_branch_protection(
        self, _org, _repo, branch, required_reviewers=1
    ):
        self.protection_puts += 1
        value = {
            "required_pull_request_reviews": {
                "required_approving_review_count": required_reviewers,
                "dismiss_stale_reviews": True,
            }
        }
        self.protections[branch] = value
        return value

    def add_team_to_repo(self, _org, team, _repo, permission="push"):
        self.team_grants.append((team, permission))
        self.team_permissions[team] = permission

    def get_team_repo_permission(self, _org, team, _repo):
        return self.team_permissions[team]


def test_existing_environment_controls_are_never_overwritten():
    gh = _ControlGH()
    existing = {
        "name": "production",
        "protection_rules": [{"type": "required_reviewers"}],
    }
    gh.environments["production"] = existing
    engine = MigrationEngine({}, object(), gh, object())

    assert engine._ensure_github_environment(_repo(), "production") == "preserved"
    assert gh.environment_puts == 0
    assert gh.environments["production"] is existing


def test_protected_source_environment_must_be_configured_before_delivery():
    gh = _ControlGH()
    engine = MigrationEngine({}, object(), gh, object())

    with pytest.raises(RuntimeError, match="approvals or deployment checks"):
        engine._ensure_github_environment(
            _repo(), "production", require_existing=True
        )

    assert gh.environment_puts == 0


def test_protected_environment_readback_must_meet_source_counts():
    gh = _ControlGH()
    gh.environments["production"] = {
        "name": "production",
        "protection_rules": [{
            "type": "required_reviewers",
            "reviewers": [{"type": "User"}],
        }],
    }
    gh.custom_environment_rules["production"] = []
    engine = MigrationEngine({}, object(), gh, object())

    with pytest.raises(RuntimeError, match="source requires at least 2"):
        engine._ensure_github_environment(
            _repo(),
            "production",
            required_approver_count=2,
            required_check_count=1,
        )

    assert gh.environment_puts == 0


def test_protected_environment_with_sufficient_readback_is_preserved():
    gh = _ControlGH()
    gh.environments["production"] = {
        "name": "production",
        "protection_rules": [{
            "type": "required_reviewers",
            "reviewers": [{"type": "User"}, {"type": "Team"}],
        }],
    }
    gh.custom_environment_rules["production"] = [
        {"id": 7, "enabled": True}
    ]
    engine = MigrationEngine({}, object(), gh, object())

    assert engine._ensure_github_environment(
        _repo(),
        "production",
        required_approver_count=2,
        required_check_count=1,
    ) == "preserved"
    assert gh.environment_puts == 0


def test_existing_branch_protection_is_never_replaced():
    gh = _ControlGH()
    existing = {
        "required_pull_request_reviews": {
            "required_approving_review_count": 6,
            "dismiss_stale_reviews": True,
        },
        "enforce_admins": {"enabled": True},
    }
    gh.protections["main"] = existing
    engine = MigrationEngine({}, object(), gh, object())

    assert engine._ensure_github_branch_protection(
        _repo(), "main", 2
    ) == "preserved"
    assert gh.protection_puts == 0
    assert gh.protections["main"] is existing


def test_team_access_uses_and_verifies_exact_approved_permission():
    gh = _ControlGH()
    engine = MigrationEngine({}, object(), gh, object())

    assert engine._apply_github_team_access(
        _repo(), "payments-readers", "triage"
    ) == "triage"
    assert gh.team_grants == [("payments-readers", "triage")]


def test_executor_work_item_payload_is_used_without_project_refetch():
    payload = [{
        "id": 17,
        "fields": {
            "System.Title": "Bound item",
            "System.WorkItemType": "Task",
            "System.State": "Active",
            "System.Description": "",
        },
        "relations": [],
    }]

    class _NoWorkItemFetch:
        def list_work_items(self, *_args, **_kwargs):
            pytest.fail("engine re-fetched project work items")

    scope = MigrationScope.WORK_ITEMS.value
    engine = MigrationEngine(
        {
            "pev_plan_id": "plan-1",
            "pev_run_id": "run-1",
            "pev_source_ref_snapshots": {
                "Payments/api": {"source_repo_id": "repo-id"}
            },
            "pev_non_git_source_snapshots": {
                "Payments/api": {
                    scope: create_scope_snapshot(scope, payload)
                }
            },
            "pev_work_item_payloads": {"Payments/api": payload},
        },
        _NoWorkItemFetch(),
        object(),
        object(),
    )

    observed, evidence = engine._fetch_verified_scope_payload(_repo(), scope)

    assert observed == payload
    assert evidence == create_scope_snapshot(scope, payload)


class _PublisherGH:
    def __init__(self):
        self.refs = {"main": "a" * 40}
        self.files = {}
        self.pull = {}
        self.blobs = {}
        self.trees = {"b" * 40: {}}
        self.commits = {
            "a" * 40: {
                "sha": "a" * 40,
                "message": "base",
                "tree": {"sha": "b" * 40},
                "parents": [],
            }
        }

    def get_default_branch(self, *_args):
        return "main"

    def get_branch_sha(self, _org, _repo, branch):
        return self.refs[branch]

    def create_branch(self, _org, _repo, branch, sha):
        self.refs[branch] = sha
        tree_sha = self.commits[sha]["tree"]["sha"]
        for path, (_mode, kind, blob_sha) in self.trees[tree_sha].items():
            if kind == "blob":
                self.files[(branch, path)] = blob_sha
        return {"ref": f"refs/heads/{branch}"}

    def get_git_commit(self, _org, _repo, sha):
        return self.commits[sha]

    def get_git_tree(self, _org, _repo, sha, recursive=False):
        return {
            "sha": sha,
            "truncated": False,
            "tree": [
                {"path": path, "mode": mode, "type": kind, "sha": item_sha}
                for path, (mode, kind, item_sha) in sorted(self.trees[sha].items())
            ],
        }

    def create_git_blob(self, _org, _repo, content):
        sha = hashlib.sha1(
            f"blob {len(content)}\0".encode("ascii") + content
        ).hexdigest()
        self.blobs[sha] = content
        return {"sha": sha}

    def create_git_tree(self, _org, _repo, *, base_tree_sha, entries):
        leaves = dict(self.trees[base_tree_sha])
        for entry in entries:
            leaves[entry["path"]] = (
                entry["mode"], entry["type"], entry["sha"]
            )
        sha = hashlib.sha1(repr(sorted(leaves.items())).encode()).hexdigest()
        self.trees[sha] = leaves
        return {"sha": sha}

    def create_git_commit(self, _org, _repo, *, message, tree_sha, parents):
        sha = hashlib.sha1(
            (message + tree_sha + "".join(parents)).encode()
        ).hexdigest()
        self.commits[sha] = {
            "sha": sha,
            "message": message,
            "tree": {"sha": tree_sha},
            "parents": [{"sha": parent} for parent in parents],
        }
        return {"sha": sha}

    def get_file_sha(self, _org, _repo, path, ref):
        return self.files.get((ref, path), "")

    def put_file(self, _org, _repo, path, content_b64, branch, *_args, **_kwargs):
        content = base64.b64decode(content_b64)
        sha = hashlib.sha1(
            f"blob {len(content)}\0".encode("ascii") + content
        ).hexdigest()
        self.files[(branch, path)] = sha
        return {"content": {"sha": sha}}

    def find_open_pull_request(self, *_args):
        return self.pull

    def create_pull_request(self, *_args, **_kwargs):
        self.pull = {"number": 3, "html_url": "https://example.invalid/3"}
        return self.pull


def test_workflow_publisher_routes_every_write_through_mutation_runner(tmp_path):
    workflow = tmp_path / "build.yml"
    workflow.write_text(
        "name: build\non: workflow_dispatch\njobs:\n  build:\n"
        "    if: github.ref != 'refs/heads/ado2gh/migrated-workflows' && "
        "github.head_ref != 'ado2gh/migrated-workflows'\n"
        "    runs-on: ubuntu-latest\n    steps: []\n",
        encoding="utf-8",
    )
    gh = _PublisherGH()
    operations: list[str] = []

    def _run(_repo, kind, _payload, operation):
        operations.append(kind)
        return operation()

    result = WorkflowPublisher(
        gh, mutation_runner=_run
    ).publish(_repo(), [Path(workflow)])

    assert result["state"] == "review_pr"
    assert operations == [
        "github_publish_workflow_artifact_set",
        "github_create_workflow_review_pull_request",
    ]


def test_workflow_publisher_uses_validator_bound_bytes_not_mutable_path(tmp_path):
    workflow = tmp_path / "build.yml"
    approved = (
        b"name: approved\non: workflow_dispatch\njobs:\n  build:\n"
        b"    if: github.ref != 'refs/heads/ado2gh/migrated-workflows' && "
        b"github.head_ref != 'ado2gh/migrated-workflows'\n"
        b"    runs-on: ubuntu-latest\n    steps: []\n"
    )
    workflow.write_bytes(approved)
    approved_contents = {str(workflow.resolve()): approved}
    workflow.write_text(
        "name: attacker-controlled\non: workflow_dispatch\njobs: {}\n",
        encoding="utf-8",
    )
    gh = _PublisherGH()

    def _run(_repo, _kind, _payload, operation):
        return operation()

    WorkflowPublisher(gh, mutation_runner=_run).publish(
        _repo(),
        [workflow],
        approved_contents=approved_contents,
    )

    expected_sha = hashlib.sha1(
        f"blob {len(approved)}\0".encode("ascii") + approved
    ).hexdigest()
    assert gh.files[
        ("ado2gh/migrated-workflows", ".github/workflows/build.yml")
    ] == expected_sha


def test_workflow_publisher_rejects_push_trigger_on_review_branch(tmp_path):
    workflow = tmp_path / "unsafe.yml"
    content = (
        b"name: unsafe\non:\n  push:\n    branches: ['**']\njobs:\n  build:\n"
        b"    if: github.ref != 'refs/heads/ado2gh/migrated-workflows' && "
        b"github.head_ref != 'ado2gh/migrated-workflows'\n"
        b"    runs-on: ubuntu-latest\n    steps: []\n"
    )
    workflow.write_bytes(content)
    operations: list[str] = []

    def _run(_repo, kind, _payload, operation):
        operations.append(kind)
        return operation()

    with pytest.raises(PermissionError, match="unreviewed staging branch"):
        WorkflowPublisher(
            _PublisherGH(), mutation_runner=_run
        ).publish(
            _repo(),
            [workflow],
            approved_contents={str(workflow.resolve()): content},
        )

    assert operations == []


@pytest.mark.parametrize("trigger", ["pull_request", "workflow_dispatch"])
def test_workflow_publisher_rejects_unguarded_non_push_execution(
    tmp_path, trigger
):
    workflow = tmp_path / f"unsafe-{trigger}.yml"
    content = (
        f"name: unsafe\non: {trigger}\njobs:\n  build:\n"
        "    runs-on: ubuntu-latest\n    steps: []\n"
    ).encode()
    workflow.write_bytes(content)
    operations: list[str] = []

    def _run(_repo, kind, _payload, operation):
        operations.append(kind)
        return operation()

    with pytest.raises(PermissionError, match="review staging guard"):
        WorkflowPublisher(_PublisherGH(), mutation_runner=_run).publish(
            _repo(),
            [workflow],
            approved_contents={str(workflow.resolve()): content},
        )

    assert operations == []


def test_workflow_publisher_rejects_guard_prefix_with_bypass_disjunction(tmp_path):
    workflow = tmp_path / "bypass.yml"
    content = (
        b"name: bypass\non: pull_request\njobs:\n  build:\n"
        b"    if: github.ref != 'refs/heads/ado2gh/migrated-workflows' && "
        b"github.head_ref != 'ado2gh/migrated-workflows' && (false) || (true)\n"
        b"    runs-on: ubuntu-latest\n    steps: []\n"
    )
    workflow.write_bytes(content)

    with pytest.raises(PermissionError, match="unreviewed staging branch"):
        WorkflowPublisher(
            _PublisherGH(),
            mutation_runner=lambda _r, _k, _p, operation: operation(),
        ).publish(
            _repo(), [workflow],
            approved_contents={str(workflow.resolve()): content},
        )


def test_workflow_publisher_rejects_preexisting_unowned_branch(tmp_path):
    workflow = tmp_path / "build.yml"
    content = (
        b"name: build\non: workflow_dispatch\njobs:\n  build:\n"
        b"    if: github.ref != 'refs/heads/ado2gh/migrated-workflows' && "
        b"github.head_ref != 'ado2gh/migrated-workflows'\n"
        b"    runs-on: ubuntu-latest\n    steps: []\n"
    )
    workflow.write_bytes(content)
    gh = _PublisherGH()
    gh.refs["ado2gh/migrated-workflows"] = "c" * 40
    gh.commits["c" * 40] = {
        "sha": "c" * 40,
        "message": "created by someone else",
        "tree": {"sha": "b" * 40},
        "parents": [{"sha": "a" * 40}],
    }

    with pytest.raises(PermissionError, match="not owned"):
        WorkflowPublisher(
            gh, mutation_runner=lambda _r, _k, _p, operation: operation()
        ).publish(
            _repo(),
            [workflow],
            approved_contents={str(workflow.resolve()): content},
        )


def test_workflow_publisher_rejects_stale_extra_workflow_on_owned_ref(tmp_path):
    workflow = tmp_path / "build.yml"
    content = (
        b"name: build\non: workflow_dispatch\njobs:\n  build:\n"
        b"    if: github.ref != 'refs/heads/ado2gh/migrated-workflows' && "
        b"github.head_ref != 'ado2gh/migrated-workflows'\n"
        b"    runs-on: ubuntu-latest\n    steps: []\n"
    )
    workflow.write_bytes(content)
    gh = _PublisherGH()
    publisher = WorkflowPublisher(
        gh, mutation_runner=lambda _r, _k, _p, operation: operation()
    )
    publisher.publish(
        _repo(), [workflow],
        approved_contents={str(workflow.resolve()): content},
    )
    commit_sha = gh.refs["ado2gh/migrated-workflows"]
    original_tree = gh.commits[commit_sha]["tree"]["sha"]
    stale = b"name: stale\non: workflow_dispatch\njobs: {}\n"
    stale_sha = hashlib.sha1(
        f"blob {len(stale)}\0".encode("ascii") + stale
    ).hexdigest()
    tampered_tree = dict(gh.trees[original_tree])
    tampered_tree[".github/workflows/stale.yml"] = (
        "100644", "blob", stale_sha
    )
    tampered_tree_sha = "d" * 40
    gh.trees[tampered_tree_sha] = tampered_tree
    gh.commits[commit_sha]["tree"] = {"sha": tampered_tree_sha}

    with pytest.raises(PermissionError, match="stale changes"):
        publisher.publish(
            _repo(), [workflow],
            approved_contents={str(workflow.resolve()): content},
        )


def test_workflow_publisher_has_no_partial_ref_when_blob_creation_fails(tmp_path):
    files = []
    approved = {}
    for name in ("one.yml", "two.yml"):
        path = tmp_path / name
        content = (
            f"name: {name}\non: workflow_dispatch\njobs:\n  build:\n"
            "    if: github.ref != 'refs/heads/ado2gh/migrated-workflows' && "
            "github.head_ref != 'ado2gh/migrated-workflows'\n"
            "    runs-on: ubuntu-latest\n    steps: []\n"
        ).encode()
        path.write_bytes(content)
        files.append(path)
        approved[str(path.resolve())] = content
    gh = _PublisherGH()
    original = gh.create_git_blob
    calls = 0

    def _fail_second(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected blob failure")
        return original(*args)

    gh.create_git_blob = _fail_second
    with pytest.raises(RuntimeError, match="injected blob failure"):
        WorkflowPublisher(
            gh, mutation_runner=lambda _r, _k, _p, operation: operation()
        ).publish(_repo(), files, approved_contents=approved)

    assert "ado2gh/migrated-workflows" not in gh.refs
    assert not any(ref == "ado2gh/migrated-workflows" for ref, _ in gh.files)
    assert len(gh.commits) == 1
