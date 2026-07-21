from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from ado2gh.core.migration_engine import MigrationEngine
from ado2gh.models import RepoConfig


SOURCE = "https://dev.azure.com/example/Payments/_git/api"
SOURCE_LFS = SOURCE + "/info/lfs"
TARGET = "https://github.com/octo/payments-api.git"
TARGET_LFS = TARGET + "/info/lfs"


def _repo() -> RepoConfig:
    return RepoConfig(
        ado_project="Payments",
        ado_repo="api",
        gh_org="octo",
        gh_repo="payments-api",
    )


def test_askpass_releases_secret_only_for_exact_approved_repository(tmp_path):
    env = MigrationEngine._git_auth_env(
        str(tmp_path), "source", "ado2gh", "org-wide-secret", SOURCE, SOURCE_LFS
    )
    helper = tmp_path / "askpass_source.py"

    approved = subprocess.run(
        [
            sys.executable,
            str(helper),
            f"Password for 'https://ado2gh@dev.azure.com/example/Payments/_git/api':",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert approved.returncode == 0
    assert approved.stdout.strip() == "org-wide-secret"

    for hostile in (
        "Password for 'https://evil.example/steal':",
        "Password for 'https://dev.azure.com/attacker/Other/_git/trap':",
        "Password for 'https://dev.azure.com/example/Payments/_git/api/%2e%2e/trap':",
        "Password for 'https://attacker@dev.azure.com/example/Payments/_git/api':",
        "Token for 'https://dev.azure.com/example/Payments/_git/api':",
        "Password:",
    ):
        blocked = subprocess.run(
            [sys.executable, str(helper), hostile],
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )
        assert blocked.returncode != 0
        assert "org-wide-secret" not in blocked.stdout


def test_git_environment_replaces_inherited_config_and_isolates_home(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "credential.helper")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "attacker-helper")
    monkeypatch.setenv("HOME", "C:/attacker-home")
    env = MigrationEngine._git_auth_env(
        str(tmp_path), "target", "x-access-token", "secret", TARGET, TARGET_LFS
    )
    config = {
        env[f"GIT_CONFIG_KEY_{index}"]: env[f"GIT_CONFIG_VALUE_{index}"]
        for index in range(int(env["GIT_CONFIG_COUNT"]))
    }
    assert config["credential.helper"] == ""
    assert config["http.followRedirects"] == "false"
    assert config["protocol.allow"] == "never"
    assert config["protocol.https.allow"] == "always"
    assert config["lfs.basictransfersonly"] == "true"
    assert config["lfs.transfer.enablehrefrewrite"] == "false"
    assert config["lfs.url"] == TARGET_LFS
    assert env["HOME"].startswith(str(tmp_path))
    assert env["USERPROFILE"].startswith(str(tmp_path))
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"


def test_repository_lfsconfig_cannot_redirect_or_execute_transfer_agent(tmp_path):
    env = MigrationEngine._isolated_git_env(str(tmp_path), "audit")
    with pytest.raises(RuntimeError, match="outside the approved source"):
        MigrationEngine._validate_lfs_config_content(
            "[lfs]\nurl = https://evil.example/collect\n",
            SOURCE_LFS,
            "refs/heads/main",
            env,
        )
    with pytest.raises(RuntimeError, match="transfer-agent"):
        MigrationEngine._validate_lfs_config_content(
            "[lfs]\nstandalonetransferagent = exfil\n"
            "[lfs \"customtransfer.exfil\"]\npath = steal-token\n",
            SOURCE_LFS,
            "refs/tags/v1",
            env,
        )


def test_repository_lfsconfig_allows_only_exact_endpoint_and_nonnetwork_keys(
    tmp_path,
):
    env = MigrationEngine._isolated_git_env(str(tmp_path), "audit")
    MigrationEngine._validate_lfs_config_content(
        "[lfs]\n"
        f"url = {SOURCE_LFS}\n"
        f"pushurl = {SOURCE_LFS}\n"
        "locksverify = true\n"
        "fetchinclude = assets/**\n",
        SOURCE_LFS,
        "refs/heads/main",
        env,
    )


def test_clone_and_lfs_urls_reject_encoded_controls_and_mismatched_target(
    tmp_path,
):
    engine = MigrationEngine(
        {"ado_org_url": "https://dev.azure.com/example"},
        SimpleNamespace(org_url="https://dev.azure.com/example"),
        object(),
        object(),
    )
    with pytest.raises(RuntimeError, match="credential-free|control"):
        engine._validated_ado_clone_url(
            _repo(), SOURCE + "%0ahttps://evil.example/steal"
        )
    with pytest.raises(RuntimeError, match="ambiguous repository path"):
        engine._canonical_git_https_url(
            SOURCE + "/%2e%2e/trap", "hostile Git URL"
        )
    auth_env = {
        **MigrationEngine._isolated_git_env(
            str(tmp_path), "unit", lfs_url=TARGET_LFS
        ),
        "ADO2GH_GIT_PASSWORD": "secret",
    }
    with pytest.raises(RuntimeError, match="not derived"):
        engine._push_lfs_objects(
            ".",
            TARGET,
            auth_env,
            lfs_url="https://evil.example/collect/info/lfs",
        )
