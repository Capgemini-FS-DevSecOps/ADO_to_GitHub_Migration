from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from ado2gh.core.migration_engine import MigrationEngine
from ado2gh.models import RepoConfig


TARGET = "https://github.com/octo/payments-api.git"
TARGET_LFS = TARGET + "/info/lfs"
MAIN_SHA = "1" * 40
OLD_SHA = "2" * 40
TAG_SHA = "3" * 40
HIDDEN_SHA = "4" * 40


def _engine() -> MigrationEngine:
    return MigrationEngine(
        {"ado_org_url": "https://dev.azure.com/example"},
        SimpleNamespace(org_url="https://dev.azure.com/example"),
        object(),
        object(),
    )


def _repo() -> RepoConfig:
    return RepoConfig(
        ado_project="Payments",
        ado_repo="api",
        gh_org="octo",
        gh_repo="payments-api",
    )


def _auth_env(tmp_path: Path) -> dict[str, str]:
    return {
        **MigrationEngine._isolated_git_env(
            str(tmp_path), "lfs-test", lfs_url=TARGET_LFS
        ),
        "ADO2GH_GIT_PASSWORD": "fenced-secret",
    }


def test_empty_approved_repository_is_a_complete_zero_object_migration(
    tmp_path, monkeypatch
):
    def unexpected_run(*_args, **_kwargs):
        raise AssertionError("empty repositories must not select HEAD or another ref")

    monkeypatch.setattr(
        "ado2gh.core.migration_engine.subprocess.run", unexpected_run
    )
    result = _engine()._push_lfs_objects(
        str(tmp_path / "empty.git"),
        TARGET,
        _auth_env(tmp_path),
        repo=_repo(),
        lfs_url=TARGET_LFS,
        approved_refs={},
    )

    assert result == {
        "lfs_objects": 0,
        "lfs_scan": "approved_heads_tags_history",
        "lfs_verified": True,
    }


def test_approved_reachable_history_and_source_fetch_exclude_hidden_refs(
    tmp_path, monkeypatch
):
    calls: list[tuple[list[str], str]] = []

    def fake_run(command, **kwargs):
        input_value = kwargs.get("input", "")
        calls.append((list(command), input_value))
        if command[:2] == ["git", "rev-list"]:
            assert input_value == "refs/heads/main\nrefs/tags/v1\n"
            return SimpleNamespace(
                returncode=0,
                stdout=f"{OLD_SHA}\n{MAIN_SHA}\n",
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("ado2gh.core.migration_engine.subprocess.run", fake_run)
    revisions = MigrationEngine._approved_lfs_revisions(
        str(tmp_path),
        {"refs/tags/v1": TAG_SHA, "refs/heads/main": MAIN_SHA},
        {},
    )
    assert revisions == [
        "refs/heads/main",
        "refs/tags/v1",
        OLD_SHA,
        MAIN_SHA,
    ]

    MigrationEngine._fetch_all_lfs_for_approved_refs(
        str(tmp_path),
        "origin",
        revisions[:2],
        {},
        purpose="unit source history",
    )

    rendered = "\n".join(
        " ".join(command) + "\n" + str(input_value)
        for command, input_value in calls
    )
    assert "refs/pull/17/head" not in rendered
    assert HIDDEN_SHA not in rendered
    fetch_command = calls[-1][0]
    assert fetch_command == [
        "git", "lfs", "fetch", "--all", "origin",
        "refs/heads/main", "refs/tags/v1",
    ]


def test_reachable_object_scan_uses_only_explicit_approved_roots(
    tmp_path, monkeypatch
):
    payload = b"historical-lfs-payload"
    lfs_oid = hashlib.sha256(payload).hexdigest()
    pointer = (
        "version https://git-lfs.github.com/spec/v1\n"
        f"oid sha256:{lfs_oid}\n"
        f"size {len(payload)}\n"
    ).encode("ascii")
    blob_id = "a" * 40
    calls: list[tuple[list[str], object]] = []

    def fake_run(command, **kwargs):
        calls.append((list(command), kwargs.get("input", "")))
        if command[:3] == ["git", "rev-list", "--objects"]:
            return SimpleNamespace(returncode=0, stdout=f"{blob_id}\n", stderr="")
        if command[:3] == ["git", "cat-file", "--batch"]:
            output = (
                f"{blob_id} blob {len(pointer)}\n".encode("ascii")
                + pointer
                + b"\n"
            )
            return SimpleNamespace(returncode=0, stdout=output, stderr=b"")
        if command[:2] == ["git", "cat-file"]:
            return SimpleNamespace(
                returncode=0,
                stdout=f"{blob_id} blob {len(pointer)}\n",
                stderr="",
            )
        raise AssertionError(command)

    monkeypatch.setattr("ado2gh.core.migration_engine.subprocess.run", fake_run)
    revisions = ["refs/heads/main", "refs/tags/v1", OLD_SHA, MAIN_SHA]
    assert MigrationEngine._inventory_lfs_oids(
        str(tmp_path), revisions, {}
    ) == {lfs_oid}

    rendered = "\n".join(
        " ".join(command)
        + "\n"
        + (value.decode() if isinstance(value, bytes) else str(value))
        for command, value in calls
    )
    assert "--all" not in rendered
    assert "refs/pull/17/head" not in rendered
    assert HIDDEN_SHA not in rendered
    assert "refs/heads/main" in rendered
    assert "refs/tags/v1" in rendered
    assert OLD_SHA in rendered


def test_real_git_graph_includes_deleted_history_but_excludes_hidden_pr_ref(
    tmp_path,
):
    repository = tmp_path / "graph"

    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=repository,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    repository.mkdir()
    git("init", "--initial-branch=main")
    git("config", "user.name", "ADO2GH Test")
    git("config", "user.email", "ado2gh@example.invalid")

    approved_payload = b"approved historical payload"
    approved_oid = hashlib.sha256(approved_payload).hexdigest()
    approved_pointer = repository / "approved.bin"
    approved_pointer.write_text(
        "version https://git-lfs.github.com/spec/v1\n"
        f"oid sha256:{approved_oid}\n"
        f"size {len(approved_payload)}\n",
        encoding="ascii",
    )
    git("add", "approved.bin")
    git("commit", "-m", "historical approved pointer")
    git("rm", "approved.bin")
    git("commit", "-m", "delete approved pointer from tip")

    main_sha = git("rev-parse", "refs/heads/main")
    git("checkout", "-b", "hidden-pr")
    hidden_payload = b"hidden pull request payload"
    hidden_oid = hashlib.sha256(hidden_payload).hexdigest()
    (repository / "hidden.bin").write_text(
        "version https://git-lfs.github.com/spec/v1\n"
        f"oid sha256:{hidden_oid}\n"
        f"size {len(hidden_payload)}\n",
        encoding="ascii",
    )
    git("add", "hidden.bin")
    git("commit", "-m", "unapproved hidden pointer")
    hidden_sha = git("rev-parse", "HEAD")
    git("checkout", "main")
    git("update-ref", "refs/pull/17/head", hidden_sha)
    git("branch", "-D", "hidden-pr")

    revisions = MigrationEngine._approved_lfs_revisions(
        str(repository), {"refs/heads/main": main_sha}, {}
    )
    inventory = MigrationEngine._inventory_lfs_oids(
        str(repository), revisions, {}
    )

    assert approved_oid in inventory
    assert hidden_oid not in inventory


def test_target_push_uses_exact_oids_and_verifies_only_approved_refs(
    tmp_path, monkeypatch
):
    payload = b"approved-lfs-object"
    lfs_oid = hashlib.sha256(payload).hexdigest()
    mirror_path = tmp_path / "mirror.git"
    mirror_path.mkdir()
    verification_path = tmp_path / "target_lfs_verification.git"
    refs = {"refs/heads/main": MAIN_SHA, "refs/tags/v1": TAG_SHA}
    revisions = ["refs/heads/main", "refs/tags/v1", OLD_SHA, MAIN_SHA]
    calls: list[tuple[list[str], object]] = []

    def fake_run(command, **kwargs):
        calls.append((list(command), kwargs.get("input", "")))
        if command[:2] == ["git", "rev-list"]:
            assert kwargs["input"] == "refs/heads/main\nrefs/tags/v1\n"
            return SimpleNamespace(
                returncode=0,
                stdout=f"{OLD_SHA}\n{MAIN_SHA}\n",
                stderr="",
            )
        if command[:3] == ["git", "init", "--bare"]:
            Path(command[3]).mkdir(parents=True, exist_ok=True)
        if command[:4] == ["git", "lfs", "fetch", "--all"]:
            if Path(kwargs.get("cwd", "")) == verification_path:
                object_path = (
                    verification_path
                    / "lfs"
                    / "objects"
                    / lfs_oid[:2]
                    / lfs_oid[2:4]
                    / lfs_oid
                )
                object_path.parent.mkdir(parents=True, exist_ok=True)
                object_path.write_bytes(payload)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("ado2gh.core.migration_engine.subprocess.run", fake_run)
    result = _engine()._push_lfs_objects(
        str(mirror_path),
        TARGET,
        _auth_env(tmp_path),
        lfs_url=TARGET_LFS,
        approved_refs=refs,
        approved_revisions=revisions,
        approved_lfs_oids={lfs_oid},
    )

    assert result["lfs_verified"] is True
    assert result["lfs_objects"] == 1
    push_calls = [
        (command, input_value)
        for command, input_value in calls
        if command[:3] == ["git", "lfs", "push"]
    ]
    assert push_calls == [
        (
            [
                "git", "lfs", "push", "--all", "origin",
                "refs/heads/main", "refs/tags/v1",
            ],
            "",
        )
    ]
    git_fetches = [
        command for command, _ in calls
        if command[:2] == ["git", "fetch"]
    ]
    assert git_fetches == [
        [
            "git", "fetch", "--force", "--no-tags", "origin",
            "+refs/heads/main:refs/heads/main",
        ],
        [
            "git", "fetch", "--force", "--no-tags", "origin",
            "+refs/tags/v1:refs/tags/v1",
        ],
    ]

    rendered = "\n".join(
        " ".join(command)
        + "\n"
        + (value.decode() if isinstance(value, bytes) else str(value))
        for command, value in calls
    )
    assert "refs/pull/17/head" not in rendered
    assert HIDDEN_SHA not in rendered
    scoped_all_calls = [
        command for command, _ in calls if "--all" in command
    ]
    assert scoped_all_calls
    for command in scoped_all_calls:
        origin_index = command.index("origin")
        assert command[origin_index + 1:] == [
            "refs/heads/main", "refs/tags/v1"
        ]


def test_hidden_ref_cannot_be_smuggled_into_approved_lfs_inventory(
    tmp_path, monkeypatch
):
    def unexpected_run(*_args, **_kwargs):
        raise AssertionError("malformed ref inventory must fail before Git executes")

    monkeypatch.setattr(
        "ado2gh.core.migration_engine.subprocess.run", unexpected_run
    )
    with pytest.raises(RuntimeError, match="inventory is malformed"):
        _engine()._push_lfs_objects(
            str(tmp_path / "mirror.git"),
            TARGET,
            _auth_env(tmp_path),
            lfs_url=TARGET_LFS,
            approved_refs={"refs/pull/17/head": HIDDEN_SHA},
        )
