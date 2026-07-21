from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ado2gh.clients.ado_client import ADOClient
from ado2gh.core.config_loader import ConfigLoader
from ado2gh.models import MigrationScope, RepoConfig
from ado2gh.pev.contracts import (
    MigrationPlan,
    PlanIntegrityError,
    PlanTask,
    PlannedRepository,
    access_disposition_digest,
    compute_source_refs_digest,
    content_digest,
    github_deploy_key_identity_key,
    github_user_identity_key,
    target_access_policy_digest,
)
from ado2gh.pev.source_integrity import fetch_project_access_snapshots
from ado2gh.reporting.post_migration_validator import PostMigrationValidator


SOURCE_SHA = "a" * 40
GROUP_DESCRIPTOR = (
    "Microsoft.TeamFoundation.Identity;"
    "S-1-9-1551374245-2176056848-3280077385-2384430082-1-1"
)
ROOT_DESCRIPTOR = (
    "Microsoft.TeamFoundation.Identity;"
    "S-1-9-1551374245-2176056848-3280077385-2384430082-0-0"
)
USER_DESCRIPTOR = (
    "Microsoft.IdentityModel.Claims.ClaimsIdentity;tenant\\alice@example.test"
)


class AccessADO:
    def __init__(self, *, member: str = "member-one"):
        self.member = member
        self.acl_calls = 0
        self.root_acl_calls = 0
        self.identity_calls = 0

    def get_repo(self, _project, repo_id):
        return {"id": repo_id, "project": {"id": "project-guid"}}

    def list_project_git_acls(self, project_id):
        assert project_id == "project-guid"
        self.acl_calls += 1
        return [
            {
                "token": "repoV2/project-guid",
                "inheritPermissions": True,
                "acesDictionary": {
                    ROOT_DESCRIPTOR: {
                        "descriptor": ROOT_DESCRIPTOR,
                        "allow": 2,
                        "deny": 0,
                        "extendedInfo": {"effectiveAllow": 2},
                    }
                },
            },
            {
                "token": "repoV2/project-guid/repo-one",
                "inheritPermissions": True,
                "acesDictionary": {
                    USER_DESCRIPTOR: {
                        "descriptor": USER_DESCRIPTOR,
                        "allow": 4,
                        "deny": 0,
                        "extendedInfo": {"effectiveAllow": 4},
                    }
                },
            },
            {
                "token": "repoV2/project-guid/repo-two/refs/heads/main",
                "inheritPermissions": False,
                "acesDictionary": {},
            },
            # An unplanned repository must not expand the identity query.
            {
                "token": "repoV2/project-guid/not-planned",
                "inheritPermissions": True,
                "acesDictionary": {
                    "Microsoft.TeamFoundation.Identity;unplanned": {
                        "descriptor": "Microsoft.TeamFoundation.Identity;unplanned",
                        "allow": 2,
                        "deny": 0,
                    }
                },
            },
        ]

    def list_git_root_acls(self):
        self.root_acl_calls += 1
        return [{
            "token": "repoV2",
            "inheritPermissions": True,
            "acesDictionary": {
                GROUP_DESCRIPTOR: {
                    "descriptor": GROUP_DESCRIPTOR,
                    "allow": 1,
                    "deny": 0,
                    "extendedInfo": {"effectiveAllow": 1},
                }
            },
        }]

    def resolve_acl_identities(self, descriptors):
        self.identity_calls += 1
        assert set(descriptors) == {
            GROUP_DESCRIPTOR, ROOT_DESCRIPTOR, USER_DESCRIPTOR,
        }
        return {
            GROUP_DESCRIPTOR: {
                "id": "group-storage-id",
                "descriptor": GROUP_DESCRIPTOR,
                "subjectDescriptor": "vssgp.group",
                "isActive": True,
                "isContainer": True,
                "members": [{
                    "identityType": "Claims",
                    "identifier": self.member,
                }],
                "memberIds": [f"storage-{self.member}"],
            },
            USER_DESCRIPTOR: {
                "id": "user-storage-id",
                "descriptor": USER_DESCRIPTOR,
                "subjectDescriptor": "aad.user",
                "isActive": True,
                "isContainer": False,
                "members": [],
                "memberIds": [],
            },
            ROOT_DESCRIPTOR: {
                "id": "root-group-storage-id",
                "descriptor": ROOT_DESCRIPTOR,
                "subjectDescriptor": "vssgp.root-group",
                "isActive": True,
                "isContainer": True,
                "members": [],
                "memberIds": [],
            },
        }


def _repositories():
    return [
        SimpleNamespace(
            ado_project="Payments",
            source_key="Payments/one",
            source_repo_id="repo-one",
        ),
        SimpleNamespace(
            ado_project="Payments",
            source_key="Payments/two",
            source_repo_id="repo-two",
        ),
    ]


def _evidence(snapshot, mapping, excluded):
    team_slugs = sorted({target.casefold() for _, target, _ in mapping})
    mapped_members = sorted({
        member
        for principal, _, _ in mapping
        for member in snapshot["principal_memberships"][principal]
    })
    identity_mapping = {
        member: github_user_identity_key(f"target-user-{index}")
        for index, member in enumerate(mapped_members)
    }
    target_members = {slug: [] for slug in team_slugs}
    for principal, target, _ in mapping:
        target_members[target.casefold()].extend(
            identity_mapping[member]
            for member in snapshot["principal_memberships"][principal]
        )
    target_members = {
        slug: sorted(set(members)) for slug, members in target_members.items()
    }
    target_parents = {slug: "" for slug in team_slugs}
    return {
        "approver": "security@example.test",
        "ticket": "SEC-1234",
        "approved_at": "2026-07-21T12:00:00+00:00",
        "source_acl_digest": snapshot["digest"],
        "review_evidence_digest": "sha256:" + "e" * 64,
        "excluded_principals": dict(excluded),
        "disposition_digest": access_disposition_digest(
            snapshot["digest"], mapping, excluded, identity_mapping
        ),
        "identity_mapping": identity_mapping,
        "target_base_repository_permission": "none",
        "target_team_members": target_members,
        "target_team_parents": target_parents,
        "target_deploy_keys": {},
        "target_github_apps": {},
        "target_access_digest": target_access_policy_digest(
            "none", mapping, target_members, target_parents, {}, {}
        ),
    }


def _plan(snapshot, mapping, excluded, *, evidence_override=None):
    source, target = "Payments/one", "octo/one"
    branches = (("refs/heads/main", SOURCE_SHA),)
    repo = PlannedRepository(
        ado_project="Payments",
        ado_repo="one",
        gh_org="octo",
        gh_repo="one",
        scopes=(MigrationScope.REPO.value,),
        source_repo_id="repo-one",
        default_branch="main",
        source_head_sha=SOURCE_SHA,
        source_branch_refs=branches,
        source_refs_digest=compute_source_refs_digest(branches, ()),
        team_mapping=tuple(mapping),
        access_policy_approved=True,
        access_policy_evidence=(
            evidence_override
            if evidence_override is not None
            else _evidence(snapshot, mapping, excluded)
        ),
        source_access_snapshot=dict(snapshot),
    )
    preflight = PlanTask(
        "preflight", "preflight", source, target,
        metadata={
            "target_exists": False,
            "target_repo_id": "",
            "target_size": 0,
            "target_default_branch": "",
            "target_branch_refs": {},
            "target_tag_refs": {},
            "target_refs_digest": compute_source_refs_digest((), ()),
        },
    )
    execute = PlanTask(
        "execute", "execute", source, target,
        scope=MigrationScope.REPO.value,
        dependencies=(preflight.task_id,),
    )
    validate = PlanTask(
        "validate", "validate", source, target,
        dependencies=(execute.task_id,),
    )
    return MigrationPlan.create(
        source_org_url="https://dev.azure.com/example",
        target_org="octo",
        repositories=(repo,),
        tasks=(preflight, execute, validate),
        policy={"mapping": {"existing_target_policy": "fail"}},
        config_digest=content_digest({"config": "approved"}),
    )


def test_project_snapshot_batches_principals_and_persists_only_hashes():
    ado = AccessADO()
    repositories = _repositories() + [
        SimpleNamespace(
            ado_project="Payments",
            source_key=f"Payments/repo-{index}",
            source_repo_id=f"repo-{index}",
        )
        for index in range(2, 5_000)
    ]

    snapshots = fetch_project_access_snapshots(ado, repositories)

    assert ado.acl_calls == 1
    assert ado.root_acl_calls == 1
    assert ado.identity_calls == 1
    assert len(snapshots) == 5_000
    first = snapshots["Payments/one"]
    second = snapshots["Payments/two"]
    assert first["schema_version"] == 2
    assert first["principal_count"] == 3
    assert first["group_count"] == 2
    assert first["membership_edge_count"] == 1
    assert second["principal_count"] == 2
    persisted = json.dumps(snapshots, sort_keys=True)
    assert GROUP_DESCRIPTOR not in persisted
    assert ROOT_DESCRIPTOR not in persisted
    assert USER_DESCRIPTOR not in persisted
    assert "member-one" not in persisted
    assert all(key.startswith("sha256:") for key in first["principal_keys"])


def test_expanded_group_membership_drift_changes_live_snapshot():
    approved = fetch_project_access_snapshots(
        AccessADO(member="member-one"), _repositories()
    )
    live = fetch_project_access_snapshots(
        AccessADO(member="member-two"), _repositories()
    )

    assert approved["Payments/one"]["principal_keys"] \
        == live["Payments/one"]["principal_keys"]
    assert approved["Payments/one"]["acl_digest"] \
        == live["Payments/one"]["acl_digest"]
    assert approved["Payments/one"]["membership_digest"] \
        != live["Payments/one"]["membership_digest"]
    assert approved["Payments/one"] != live["Payments/one"]

    checker = object.__new__(PostMigrationValidator)
    checker.approved_access_snapshots = approved
    checker.live_access_snapshots = live
    verdict = checker._check_source_access_integrity(RepoConfig(
        ado_project="Payments",
        ado_repo="one",
        gh_org="octo",
        gh_repo="one",
        access_policy_approved=True,
    ))
    assert verdict["verdict"] == "FAIL"
    assert "memberships changed" in verdict["detail"]


def test_stability_read_fails_when_membership_changes_mid_snapshot():
    ado = AccessADO()
    original = ado.resolve_acl_identities

    def changing(descriptors):
        result = original(descriptors)
        ado.member = "changed-during-read"
        return result

    ado.resolve_acl_identities = changing

    with pytest.raises(RuntimeError, match="membership.*changed"):
        fetch_project_access_snapshots(ado, _repositories(), require_stable=True)


def test_access_approval_requires_exhaustive_mapped_or_excluded_principals():
    snapshot = fetch_project_access_snapshots(
        AccessADO(), _repositories()
    )["Payments/one"]
    first, *remaining = snapshot["principal_keys"]
    mapping = ((first, "developers", "push"),)
    exclusions = {key: "Service identity is retired" for key in remaining}

    plan = _plan(snapshot, mapping, exclusions)
    assert plan.repositories[0].access_policy_approved is True
    assert plan.schema_version == 8
    loaded = MigrationPlan.from_dict(plan.to_dict())
    assert loaded.plan_id == plan.plan_id
    assert loaded.repositories[0].access_policy_evidence \
        == plan.repositories[0].access_policy_evidence

    with pytest.raises(PlanIntegrityError, match="not exhaustive"):
        _plan(snapshot, (), {})
    with pytest.raises(PlanIntegrityError, match="not exhaustive"):
        _plan(snapshot, mapping, {})


def test_access_approval_rejects_overlap_and_unbound_disposition_digest():
    snapshot = fetch_project_access_snapshots(
        AccessADO(), _repositories()
    )["Payments/one"]
    first, *remaining = snapshot["principal_keys"]
    mapping = ((first, "developers", "push"),)

    with pytest.raises(PlanIntegrityError, match="both mapped and excluded"):
        _plan(
            snapshot,
            mapping,
            {first: "duplicate", **{key: "retired" for key in remaining}},
        )

    exclusions = {key: "retired" for key in remaining}
    evidence = _evidence(snapshot, mapping, exclusions)
    evidence["disposition_digest"] = "sha256:" + "0" * 64
    with pytest.raises(PlanIntegrityError, match="does not bind"):
        _plan(
            snapshot,
            mapping,
            exclusions,
            evidence_override=evidence,
        )


def test_mapped_group_requires_one_to_one_source_to_target_member_coverage():
    snapshot = fetch_project_access_snapshots(
        AccessADO(), _repositories()
    )["Payments/one"]
    principal = next(
        key for key, members in snapshot["principal_memberships"].items()
        if members
    )
    excluded = {
        key: "reviewed exclusion"
        for key in snapshot["principal_keys"] if key != principal
    }
    mapping = ((principal, "developers", "push"),)
    evidence = _evidence(snapshot, mapping, excluded)
    evidence["identity_mapping"] = {}
    evidence["target_team_members"] = {"developers": []}
    evidence["disposition_digest"] = access_disposition_digest(
        snapshot["digest"], mapping, excluded, {}
    )
    evidence["target_access_digest"] = target_access_policy_digest(
        "none", mapping, {"developers": []}, {"developers": ""}, {}, {}
    )

    with pytest.raises(PlanIntegrityError, match="every effective member"):
        _plan(
            snapshot,
            mapping,
            excluded,
            evidence_override=evidence,
        )


@pytest.mark.parametrize(
    "base,teams,members,direct,expected",
    [
        ("none", [{"slug": "developers", "permission": "push"}], [], [], "PASS"),
        ("read", [{"slug": "developers", "permission": "push"}], [], [], "FAIL"),
        (
            "none",
            [
                {"slug": "developers", "permission": "push"},
                {"slug": "unreviewed", "permission": "pull"},
            ],
            [],
            [],
            "FAIL",
        ),
        (
            "none",
            [{"slug": "developers", "permission": "push"}],
            [],
            [{"node_id": "user-node-id"}],
            "FAIL",
        ),
        (
            "none",
            [{"slug": "developers", "permission": "push"}],
            [{"node_id": "unapproved-team-member"}],
            [],
            "FAIL",
        ),
    ],
)
def test_target_access_policy_is_exhaustive(
    base, teams, members, direct, expected
):
    snapshot = fetch_project_access_snapshots(
        AccessADO(), _repositories()
    )["Payments/one"]
    first, *remaining = snapshot["principal_keys"]
    plan = _plan(
        snapshot,
        ((first, "developers", "push"),),
        {key: "retired" for key in remaining},
    )
    approved_member_count = len(
        plan.repositories[0].access_policy_evidence[
            "target_team_members"
        ]["developers"]
    )
    approved_member_rows = [
        {"node_id": f"target-user-{index}"}
        for index in range(approved_member_count)
    ]

    class TargetGH:
        def get_org(self, _org):
            return {"default_repository_permission": base}

        def list_repo_teams(self, _org, _repo):
            return teams

        def list_repo_direct_collaborators(self, _org, _repo):
            return direct

        def list_team_members(self, _org, _team):
            return approved_member_rows + members

        def list_repo_invitations(self, _org, _repo):
            return []

        def list_deploy_keys(self, _org, _repo):
            return []

        def list_repo_app_installations(self, _org, _repo):
            return []

    checker = object.__new__(PostMigrationValidator)
    checker.gh = TargetGH()
    checker.approved_target_snapshots = {
        "Payments/one": {"target_exists": False}
    }
    result = checker._check_target_access_policy(
        plan.repositories[0].to_repo_config()
    )

    assert result["verdict"] == expected


def test_write_deploy_key_must_be_explicitly_bound_to_target_policy():
    snapshot = fetch_project_access_snapshots(
        AccessADO(), _repositories()
    )["Payments/one"]
    first, *remaining = snapshot["principal_keys"]
    mapping = ((first, "developers", "push"),)
    exclusions = {key: "retired" for key in remaining}
    evidence = _evidence(snapshot, mapping, exclusions)
    key = github_deploy_key_identity_key("42")
    evidence["target_deploy_keys"] = {key: False}
    evidence["target_access_digest"] = target_access_policy_digest(
        "none",
        mapping,
        evidence["target_team_members"],
        evidence["target_team_parents"],
        evidence["target_deploy_keys"],
        evidence["target_github_apps"],
    )
    plan = _plan(
        snapshot, mapping, exclusions, evidence_override=evidence
    )
    expected_members = [
        {"node_id": f"target-user-{index}"}
        for index in range(len(
            evidence["target_team_members"]["developers"]
        ))
    ]

    class TargetGH:
        def get_org(self, _org):
            return {"default_repository_permission": "none"}

        def list_repo_teams(self, _org, _repo):
            return [{"slug": "developers", "permission": "push"}]

        def list_team_members(self, _org, _team):
            return expected_members

        def list_repo_direct_collaborators(self, _org, _repo):
            return []

        def list_repo_invitations(self, _org, _repo):
            return []

        def list_deploy_keys(self, _org, _repo):
            return [{"id": 42, "read_only": False}]

        def list_repo_app_installations(self, _org, _repo):
            return []

    checker = object.__new__(PostMigrationValidator)
    checker.gh = TargetGH()
    checker.approved_target_snapshots = {
        "Payments/one": {"target_exists": False}
    }
    result = checker._check_target_access_policy(
        plan.repositories[0].to_repo_config()
    )
    assert result["verdict"] == "PASS"

    unapproved_plan = _plan(snapshot, mapping, exclusions)
    result = checker._check_target_access_policy(
        unapproved_plan.repositories[0].to_repo_config()
    )
    assert result["verdict"] == "FAIL"


def test_every_target_fails_when_github_app_access_is_not_enumerable():
    snapshot = fetch_project_access_snapshots(
        AccessADO(), _repositories()
    )["Payments/one"]
    first, *remaining = snapshot["principal_keys"]
    plan = _plan(
        snapshot,
        ((first, "developers", "push"),),
        {key: "retired" for key in remaining},
    )
    expected_members = [
        {"node_id": f"target-user-{index}"}
        for index in range(len(
            plan.repositories[0].access_policy_evidence[
                "target_team_members"
            ]["developers"]
        ))
    ]

    class TargetGH:
        def get_org(self, _org):
            return {"default_repository_permission": "none"}

        def list_repo_teams(self, _org, _repo):
            return [{"slug": "developers", "permission": "push"}]

        def list_team_members(self, _org, _team):
            return expected_members

        def list_repo_direct_collaborators(self, _org, _repo):
            return []

        def list_repo_invitations(self, _org, _repo):
            return []

        def list_deploy_keys(self, _org, _repo):
            return []

    checker = object.__new__(PostMigrationValidator)
    checker.gh = TargetGH()
    checker.approved_target_snapshots = {
        "Payments/one": {"target_exists": True}
    }
    result = checker._check_target_access_policy(
        plan.repositories[0].to_repo_config()
    )

    assert result["verdict"] == "FAIL"
    assert result["github_app_inventory_unavailable"] is True
    assert "App access" in result["detail"]


def test_config_loader_preserves_nested_exclusions_and_checks_digest(tmp_path):
    source_digest = "a" * 64
    mapped = "sha256:" + "1" * 64
    excluded = "sha256:" + "2" * 64
    mapping = ((mapped, "developers", "push"),)
    exclusions = {excluded: "Break-glass identity retained in ADO only"}
    source_identity = "sha256:" + "3" * 64
    target_identity = "sha256:" + "4" * 64
    identity_mapping = {source_identity: target_identity}
    disposition = access_disposition_digest(
        source_digest, mapping, exclusions, identity_mapping
    )
    target_access = target_access_policy_digest(
        "none", mapping, {"developers": [target_identity]},
        {"developers": ""}, {}, {}
    )
    config = tmp_path / "migration.yaml"
    config.write_text(
        "\n".join([
            "global:",
            "  gh_org: octo",
            "waves:",
            "  - wave_id: 1",
            "    repos:",
            "      - ado_project: Payments",
            "        ado_repo: one",
            "        access_policy_approved: true",
            "        team_mapping:",
            f"          '{mapped}':",
            "            github_team: developers",
            "            permission: push",
            "        access_policy_evidence:",
            "          approver: security@example.test",
            "          ticket: SEC-1234",
            "          approved_at: '2026-07-21T12:00:00+00:00'",
            f"          source_acl_digest: {source_digest}",
            f"          review_evidence_digest: 'sha256:{'e' * 64}'",
            f"          disposition_digest: '{disposition}'",
            "          target_base_repository_permission: none",
            f"          target_access_digest: '{target_access}'",
            "          target_team_members:",
            "            developers:",
            f"              - '{target_identity}'",
            "          target_team_parents:",
            "            developers: ''",
            "          target_deploy_keys: {}",
            "          target_github_apps: {}",
            "          identity_mapping:",
            f"            '{source_identity}': '{target_identity}'",
            "          excluded_principals:",
            f"            '{excluded}': Break-glass identity retained in ADO only",
        ]),
        encoding="utf-8",
    )

    _, waves = ConfigLoader.load(str(config))
    repo = waves[0].repos[0]
    assert repo.access_policy_evidence["excluded_principals"] == exclusions

    bad = config.read_text(encoding="utf-8").replace(disposition, "sha256:" + "0" * 64)
    config.write_text(bad, encoding="utf-8")
    with pytest.raises(ValueError, match="does not bind"):
        ConfigLoader.load(str(config))


def test_ado_identity_resolution_is_batched_and_uses_expanded_down():
    client = ADOClient("https://dev.azure.com/example", "pat")
    descriptors = [f"Microsoft.TeamFoundation.Identity;principal-{i}" for i in range(30)]
    calls = []

    def fake_get(url, params=None, timeout=45):
        del timeout
        calls.append((url, dict(params or {})))
        requested = params["descriptors"].split(",")
        return {
            "count": len(requested),
            "value": [{"descriptor": descriptor} for descriptor in requested],
        }

    client._get = fake_get
    resolved = client.resolve_acl_identities(descriptors)

    assert set(resolved) == set(descriptors)
    assert len(calls) == 2
    assert all(
        url == "https://vssps.dev.azure.com/example/_apis/identities"
        for url, _ in calls
    )
    assert all(params["queryMembership"] == "ExpandedDown" for _, params in calls)


def test_old_access_snapshot_schema_fails_explicitly():
    snapshot = fetch_project_access_snapshots(
        AccessADO(), _repositories()
    )["Payments/one"]
    principal = snapshot["principal_keys"][0]
    mapping = ((principal, "developers", "push"),)
    excluded = {
        key: "reviewed exclusion"
        for key in snapshot["principal_keys"] if key != principal
    }
    old = {
        "schema_version": 1,
        "mode": "ado_git_acl",
        "digest": snapshot["acl_digest"],
        "acl_count": snapshot["acl_count"],
        "principal_count": snapshot["principal_count"],
    }

    with pytest.raises(PlanIntegrityError, match="snapshot .* invalid"):
        _plan(
            old,
            mapping,
            excluded,
            evidence_override=_evidence(snapshot, mapping, excluded),
        )
