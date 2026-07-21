from __future__ import annotations

import textwrap

import pytest

from ado2gh.core.config_loader import ConfigLoader, resolve_repository_mapping
from ado2gh.clients.ado_client import ADOClient
from ado2gh.clients.gh_client import GHClient
from ado2gh.clients.token_manager import TokenManager
from ado2gh.pipelines.llm import create_pipeline_llm_client_from_env


def _write(tmp_path, name: str, content: str):
    path = tmp_path / name
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


def test_yaml_rejects_duplicate_keys(tmp_path):
    config = _write(
        tmp_path,
        "duplicate.yaml",
        """
        global:
          gh_org: target-org
          gh_org: wrong-org
        waves: []
        """,
    )

    with pytest.raises(Exception, match="duplicate key"):
        ConfigLoader.load(str(config))


def test_mapping_rules_preserve_relative_repo_and_apply_exact_override(tmp_path):
    config = _write(
        tmp_path,
        "mapping.yaml",
        """
        global:
          gh_org: default-org
          mapping:
            project_to_org:
              Payments: payments-org
            repositories:
              Payments/Legacy.API: platform-org/modern-api
        waves:
          - wave_id: 1
            repos:
              - ado_project: Payments
                ado_repo: checkout
              - ado_project: Payments
                ado_repo: Legacy.API
        """,
    )

    global_cfg, waves = ConfigLoader.load(str(config))

    assert global_cfg["mapping"]["project_to_org"]["Payments"] == "payments-org"
    assert [(repo.gh_org, repo.gh_repo) for repo in waves[0].repos] == [
        ("payments-org", "checkout"),
        ("platform-org", "modern-api"),
    ]
    assert resolve_repository_mapping(
        "PAYMENTS", "legacy.api", "default-org", global_cfg["mapping"],
        explicit_gh_org="override-org",
    ) == ("override-org", "modern-api")


def test_pev_mapping_policy_is_validated_and_preserved(tmp_path):
    config = _write(
        tmp_path,
        "pev-mapping.yaml",
        """
        global:
          gh_org: fallback-org
          mapping:
            target_org: enterprise-org
            strategy: project-prefix
            separator: '-'
            lowercase: true
            apply_to_explicit: true
            existing_target_policy: reuse
            allow_nonempty_target: false
            preflight_targets: true
            projects:
              Payments:
                prefix: Pay
                gh_org: payments-org
            repositories:
              Payments/Special: special-repo
        waves:
          - wave_id: 1
            repos:
              - ado_project: Payments
                ado_repo: Checkout
              - ado_project: Payments
                ado_repo: Special
        """,
    )

    global_cfg, waves = ConfigLoader.load(str(config))

    assert global_cfg["mapping"]["existing_target_policy"] == "reuse"
    assert [(repo.gh_org, repo.gh_repo) for repo in waves[0].repos] == [
        ("payments-org", "pay-checkout"),
        ("payments-org", "special-repo"),
    ]


def test_case_insensitive_target_collision_fails_closed(tmp_path):
    repos = _write(
        tmp_path,
        "repos.txt",
        """
        One/source::Target-Org/Service
        Two/other::target-org/service
        """,
    )

    with pytest.raises(ValueError, match="target collision"):
        ConfigLoader.load_text_input(str(repos), "default-org")


def test_duplicate_source_across_waves_is_rejected(tmp_path):
    config = _write(
        tmp_path,
        "duplicate-source.yaml",
        """
        global:
          gh_org: target-org
        waves:
          - wave_id: 1
            repos:
              - ado_project: Project
                ado_repo: Repo
          - wave_id: 2
            repos:
              - ado_project: project
                ado_repo: repo
                gh_repo: repo-two
        """,
    )

    with pytest.raises(ValueError, match="duplicate source mapping"):
        ConfigLoader.load(str(config))


@pytest.mark.parametrize(
    "content,match",
    [
        ("Project/Repo::target-org/", "target repository"),
        ("Project/Repo::target_org/repo", "organization slug"),
        ("Project/Repo::target-org/repo.git", "unsafe or ambiguous"),
    ],
)
def test_invalid_targets_are_skipped_or_strictly_rejected(tmp_path, content, match):
    repos = _write(tmp_path, "bad.txt", content)

    assert ConfigLoader.load_text_input(str(repos), "default-org") == []
    with pytest.raises(ValueError, match=match):
        ConfigLoader.load_text_input(str(repos), "default-org", strict=True)


def test_invalid_scope_and_pipeline_regex_are_rejected(tmp_path):
    config = _write(
        tmp_path,
        "invalid.yaml",
        """
        global:
          gh_org: target-org
        waves:
          - wave_id: 1
            repos:
              - ado_project: Project
                ado_repo: Repo
                scopes: [repo, destroy]
                pipeline_filter: "["
        """,
    )

    with pytest.raises(ValueError, match="unknown migration scope"):
        ConfigLoader.load(str(config))


def test_csv_requires_headers_and_supports_mapping_rules(tmp_path):
    bad = _write(tmp_path, "bad.csv", "source,target\nA,B\n")
    with pytest.raises(ValueError, match="missing required column"):
        ConfigLoader.load_csv_input(str(bad), "target-org")

    good = _write(
        tmp_path,
        "good.CSV",
        "ado_project,ado_repo,gh_org,gh_repo,scopes\n"
        "Project,Repo,,,repo|pipelines\n",
    )
    repos = ConfigLoader.load_input(
        str(good),
        "target-org",
        mapping={"project_to_org": {"Project": "mapped-org"}},
        strict=True,
    )
    assert len(repos) == 1
    assert (repos[0].gh_org, repos[0].gh_repo) == ("mapped-org", "Repo")
    assert repos[0].scopes == ["repo", "pipelines"]


@pytest.mark.parametrize("field", ["ado_org_url", "gh_api_url", "gh_web_url"])
def test_service_endpoints_require_https(field, tmp_path):
    config = _write(
        tmp_path,
        "insecure.yaml",
        f"""
        global:
          gh_org: target-org
          {field}: http://example.invalid/service
        waves: []
        """,
    )

    with pytest.raises(ValueError, match="HTTPS"):
        ConfigLoader.load(str(config))


def test_cleanup_authorization_is_strictly_typed(tmp_path):
    config = _write(
        tmp_path,
        "cleanup.yaml",
        """
        global:
          gh_org: target-org
          cleanup:
            allow_disable_pipelines: true
            allow_redirect: false
            allow_archive: false
        waves: []
        """,
    )

    global_cfg, _ = ConfigLoader.load(str(config))
    assert global_cfg["cleanup"] == {
        "allow_disable_pipelines": True,
        "allow_redirect": False,
        "allow_archive": False,
    }


def test_api_clients_reject_plain_http_even_when_constructed_directly():
    with pytest.raises(ValueError, match="HTTPS"):
        ADOClient("http://dev.azure.test/org", "pat")
    with pytest.raises(ValueError, match="HTTPS"):
        GHClient.from_single_token("token", "http://github.test/api/v3")
    with pytest.raises(ValueError, match="HTTPS"):
        TokenManager.from_single_token("token").configure_app_auth(
            "1", "2", "app.pem", "http://github.test/api/v3"
        )


def test_pipeline_llm_execution_identity_is_normalized_into_approved_config(tmp_path):
    config = _write(
        tmp_path,
        "llm.yaml",
        """
        global:
          gh_org: target-org
          pipeline_conversion:
            llm_provider: openai
            llm_model: approved-model
            llm_base_url: https://llm-gateway.example/v1
            llm_organization: org-enterprise
            llm_project: proj-migrations
            llm_api_key_env: MIGRATION_LLM_API_KEY
            llm_egress_mode: redacted-semantics
        waves: []
        """,
    )

    global_cfg, _ = ConfigLoader.load(str(config))
    conversion = global_cfg["pipeline_conversion"]
    assert conversion == {
        "llm_provider": "openai-responses",
        "llm_model": "approved-model",
        "llm_base_url": "https://llm-gateway.example/v1",
        "llm_organization": "org-enterprise",
        "llm_project": "proj-migrations",
        "llm_api_key_env": "MIGRATION_LLM_API_KEY",
        "llm_egress_mode": "redacted-semantics",
    }


def test_pipeline_llm_disabled_defaults_are_explicit_without_section(tmp_path):
    config = _write(
        tmp_path,
        "no-pipeline-options.yaml",
        """
        global:
          gh_org: target-org
        waves: []
        """,
    )

    global_cfg, _ = ConfigLoader.load(str(config))

    assert global_cfg["pipeline_conversion"] == {
        "llm_provider": "disabled",
        "llm_model": "gpt-5.6",
        "llm_base_url": "https://api.openai.com/v1",
        "llm_organization": "",
        "llm_project": "",
        "llm_api_key_env": "OPENAI_API_KEY",
        "llm_egress_mode": "metadata-only",
    }


def test_pipeline_llm_factory_rejects_unbound_or_mismatched_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("ADO2GH_LLM_PROVIDER", "openai")
    with pytest.raises(ValueError, match="does not match approved llm_provider"):
        create_pipeline_llm_client_from_env(config={})

    approved = {
        "llm_provider": "openai-responses",
        "llm_model": "approved-model",
        "llm_base_url": "https://api.openai.com/v1",
        "llm_organization": "org-approved",
        "llm_project": "proj-approved",
        "llm_egress_mode": "redacted-semantics",
    }
    monkeypatch.setenv("ADO2GH_LLM_MODEL", "unapproved-model")
    with pytest.raises(ValueError, match="ADO2GH_LLM_MODEL"):
        create_pipeline_llm_client_from_env(config=approved)


def test_pipeline_llm_factory_uses_approved_identity_and_only_key_from_env(monkeypatch):
    monkeypatch.setenv("MIGRATION_LLM_KEY", "test-secret")
    monkeypatch.setenv("ADO2GH_LLM_PROVIDER", "openai")
    monkeypatch.setenv("ADO2GH_LLM_MODEL", "approved-model")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("OPENAI_ORG_ID", "org-approved")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "proj-approved")
    client = create_pipeline_llm_client_from_env(config={
        "llm_provider": "openai-responses",
        "llm_model": "approved-model",
        "llm_base_url": "https://llm.example/v1",
        "llm_organization": "org-approved",
        "llm_project": "proj-approved",
        "llm_api_key_env": "MIGRATION_LLM_KEY",
        "llm_egress_mode": "redacted-semantics",
    })

    assert client is not None
    assert client.endpoint == "https://llm.example/v1/responses"
    assert client.organization == "org-approved"
    assert client.project == "proj-approved"
    assert client.execution_settings_digest.startswith("sha256:")


@pytest.mark.parametrize("location", ["root", "global"])
def test_unknown_configuration_keys_fail_closed(location, tmp_path):
    content = (
        "global: {}\ngloabl: {}\n"
        if location == "root"
        else "global:\n  gh_org: octo\n  paralell: 4\n"
    )
    path = _write(tmp_path, f"unknown-{location}.yaml", content)

    with pytest.raises(ValueError, match="unknown key"):
        ConfigLoader.load(str(path))
