from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from ado2gh.core.config_loader import ConfigLoader
from ado2gh.core.migration_engine import MigrationEngine
from ado2gh.models import MigrationScope, RepoConfig


class _TokenManager:
    def __init__(self, token: str = "github-secret-token"):
        self.token = token
        self.reads = 0

    def get_token(self):
        self.reads += 1
        return self.token


def _repo() -> RepoConfig:
    return RepoConfig(
        ado_project="Payments",
        ado_repo="api",
        gh_org="octo",
        gh_repo="payments-api",
        scopes=[MigrationScope.REPO.value],
    )


def _engine(tmp_path: Path, *, source: str, target: str) -> MigrationEngine:
    executable = tmp_path / ("approved-ado2gh.exe" if os.name == "nt" else "approved-ado2gh")
    executable.write_bytes(b"approved standalone ADO2GH binary")
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    ado = SimpleNamespace(org_url=source, pat="ado-secret-token")
    gh = SimpleNamespace(BASE=target, token_manager=_TokenManager())
    engine = MigrationEngine(
        {
            "migration_strategy": "gei",
            "gei": {
                "ado2gh_executable_path": str(executable.resolve()),
                "ado2gh_executable_sha256": digest,
            },
        },
        ado,
        gh,
        object(),
    )
    engine._approved_source_ref_snapshot = lambda _repo: {
        "source_refs_digest": "sha256:approved"
    }
    engine._verify_ado_source_snapshot = lambda _repo, _snapshot: None
    engine._extend_target_fence = lambda _repo, _ttl: None
    engine._begin_remote_operation = lambda _repo, _kind, _payload: ""
    engine._finish_remote_operation = lambda *_args, **_kwargs: None
    engine._quarantine_target_fence = lambda *_args, **_kwargs: None
    return engine


@pytest.mark.parametrize(
    ("url", "org"),
    [
        ("https://dev.azure.com/Contoso", "Contoso"),
        ("https://contoso.visualstudio.com", "contoso"),
    ],
)
def test_gei_accepts_only_exact_azure_devops_services_authorities(url, org):
    assert MigrationEngine._gei_ado_services_org(url) == org


@pytest.mark.parametrize(
    "url",
    [
        "https://ado.internal.example/contoso",
        "https://dev.azure.com/contoso/extra",
        "https://dev.azure.com//contoso",
        "https://dev.azure.com/contoso%2Fother",
        "https://sub.contoso.visualstudio.com",
        "http://dev.azure.com/contoso",
        "https://user@dev.azure.com/contoso",
    ],
)
def test_gei_rejects_custom_or_ambiguous_ado_authorities(url):
    with pytest.raises(RuntimeError, match="Azure DevOps Services|ADO Services"):
        MigrationEngine._gei_ado_services_org(url)


@pytest.mark.parametrize(
    ("url", "canonical"),
    [
        ("https://api.github.com", "https://api.github.com"),
        ("https://api.octocorp.ghe.com", "https://api.octocorp.ghe.com"),
    ],
)
def test_gei_accepts_only_enterprise_cloud_target_authorities(url, canonical):
    assert MigrationEngine._gei_target_api_url(url) == canonical


@pytest.mark.parametrize(
    "url",
    [
        "https://github.example/api/v3",
        "https://api.github.com/extra",
        "https://evil.example",
        "http://api.github.com",
        "https://user@api.github.com",
    ],
)
def test_gei_rejects_ghes_and_unapproved_target_authorities(url):
    with pytest.raises(RuntimeError, match="Enterprise Cloud"):
        MigrationEngine._gei_target_api_url(url)


def test_gei_invokes_pinned_standalone_binary_with_explicit_ghe_target_and_isolated_env(
    tmp_path, monkeypatch
):
    engine = _engine(
        tmp_path,
        source="https://dev.azure.com/Contoso",
        target="https://api.octocorp.ghe.com",
    )
    monkeypatch.setenv("GH_HOST", "attacker.example")
    monkeypatch.setenv("TARGET_API_URL", "https://api.attacker.ghe.com")
    monkeypatch.setenv("GH_CONFIG_DIR", str(tmp_path / "attacker-config"))
    monkeypatch.setenv("DOTNET_STARTUP_HOOKS", str(tmp_path / "attacker.dll"))
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = list(command)
        observed["env"] = dict(kwargs["env"])
        return SimpleNamespace(
            returncode=0,
            stdout="completed without printing secrets",
            stderr="",
        )

    monkeypatch.setattr("ado2gh.core.migration_engine.subprocess.run", fake_run)
    result = engine._run_gei_migration(_repo(), {})

    command = observed["command"]
    assert Path(command[0]).name in {"gh-ado2gh", "gh-ado2gh.exe"}
    assert command[1:3] == ["migrate-repo", "--ado-org"]
    assert command[3] == "Contoso"
    assert command[-2:] == ["--target-api-url", "https://api.octocorp.ghe.com"]
    assert "gh" not in command[:1]
    env = observed["env"]
    assert env["ADO_PAT"] == "ado-secret-token"
    assert env["GH_PAT"] == "github-secret-token"
    assert env["GH_CONFIG_DIR"] != str(tmp_path / "attacker-config")
    assert "GH_HOST" not in env
    assert "TARGET_API_URL" not in env
    assert "DOTNET_STARTUP_HOOKS" not in env
    assert "ado-secret-token" not in repr(result)
    assert "github-secret-token" not in repr(result)
    assert "stdout_sha256" in result
    assert "gei_output" not in result


def test_gei_github_com_uses_extension_default_and_cannot_inherit_target_override(
    tmp_path, monkeypatch
):
    engine = _engine(
        tmp_path,
        source="https://dev.azure.com/contoso",
        target="https://api.github.com",
    )
    monkeypatch.setenv("TARGET_API_URL", "https://api.attacker.ghe.com")
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = list(command)
        observed["env"] = dict(kwargs["env"])
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("ado2gh.core.migration_engine.subprocess.run", fake_run)
    engine._run_gei_migration(_repo(), {})

    assert "--target-api-url" not in observed["command"]
    assert "TARGET_API_URL" not in observed["env"]


def test_gei_wrong_binary_digest_blocks_before_tokens_or_process(tmp_path, monkeypatch):
    engine = _engine(
        tmp_path,
        source="https://dev.azure.com/contoso",
        target="https://api.github.com",
    )
    engine.cfg["gei"]["ado2gh_executable_sha256"] = "0" * 64
    invoked = False

    def fake_run(*_args, **_kwargs):
        nonlocal invoked
        invoked = True
        raise AssertionError("unapproved executable must not run")

    monkeypatch.setattr("ado2gh.core.migration_engine.subprocess.run", fake_run)
    with pytest.raises(RuntimeError, match="SHA-256"):
        engine._run_gei_migration(_repo(), {})
    assert invoked is False
    assert engine.gh.token_manager.reads == 0


def test_gei_failure_never_includes_raw_token_bearing_output(tmp_path, monkeypatch):
    engine = _engine(
        tmp_path,
        source="https://dev.azure.com/contoso",
        target="https://api.github.com",
    )
    monkeypatch.setattr(
        "ado2gh.core.migration_engine.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=12,
            stdout="github-secret-token",
            stderr="ado-secret-token",
        ),
    )
    with pytest.raises(RuntimeError) as exc:
        engine._run_gei_migration(_repo(), {})
    assert "github-secret-token" not in str(exc.value)
    assert "ado-secret-token" not in str(exc.value)


def test_config_requires_hash_pinned_ado2gh_binary_for_gei(tmp_path):
    path = tmp_path / "migration.yaml"
    path.write_text(
        "global:\n  migration_strategy: gei\n  gh_org: octo\nwaves: []\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="global.gei"):
        ConfigLoader.load(str(path))


def test_enterprise_app_audit_requires_current_explicit_api_version(tmp_path):
    path = tmp_path / "migration.yaml"
    path.write_text(
        "global:\n  gh_enterprise_slug: octo-enterprise\n  gh_org: octo\nwaves: []\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="2026-03-10"):
        ConfigLoader.load(str(path))
