"""Request bodies for the ``/v1/migrate/*`` resource endpoints.

One model per route. Every one carries ``dry_run``, defaulting to ``True``,
so a caller that forgets it gets a preview rather than an irreversible
migration (CA-001).
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class GitMirrorRequest(BaseModel):
    """Body of ``POST /v1/migrate/git-mirror``.

    Names the ADO repository to copy and, optionally, where it should land;
    both GitHub fields fall back to the active profile and the ADO name.
    """
    project: str = Field(description="ADO project name")
    repo_name: str = Field(description="ADO repository name")
    github_org: Optional[str] = Field(
        default=None,
        description="Target GitHub organization (defaults to active profile gh_org)",
    )
    github_repo: Optional[str] = Field(
        default=None,
        description="Target GitHub repository (defaults to ADO repo name)",
    )
    dry_run: bool = Field(default=True, description="Dry-run mode — analyze without mutating GitHub")


class PipelineConvertRequest(BaseModel):
    """Body of ``POST /v1/migrate/pipeline-convert``.

    Names the ADO repository whose pipelines become GitHub Actions workflows,
    as a single ``project/repo_name`` key.
    """
    repo: str = Field(description="ADO repo key as project/repo_name")
    dry_run: bool = Field(default=True, description="Dry-run mode")
    github_org: Optional[str] = Field(
        default=None,
        description="Target GitHub organization (defaults to active profile gh_org)",
    )
    github_repo: Optional[str] = Field(
        default=None,
        description="Target GitHub repository (defaults to ADO repo name)",
    )


class SecretProvisionRequest(BaseModel):
    """Body of ``POST /v1/migrate/secret-provision``.

    Names the GitHub repository and the Actions secret to create. The value is
    read only on a live run and is masked everywhere it could be recorded — it
    never appears in a response, a log line or an audit row (CA-003).
    """
    github_org: str = Field(description="GitHub organization")
    github_repo: str = Field(description="GitHub repository name")
    secret_name: str = Field(description="Name of the secret to provision (e.g. AZURE_DEVOPS_PAT)")
    secret_value: str = Field(default="", description="Secret value (only used in live mode; omitted from logs)")
    dry_run: bool = Field(default=True, description="Dry-run mode — validates without creating")


class ServiceConnectionMigrateRequest(BaseModel):
    """Body of ``POST /v1/migrate/service-connection``.

    Names the ADO service connection to translate and the GitHub repository
    that receives the resulting secret or environment.
    """
    project: str = Field(description="ADO project name")
    connection_name: str = Field(description="ADO service connection name to migrate")
    github_org: str = Field(description="GitHub organization")
    github_repo: str = Field(description="GitHub repository name")
    dry_run: bool = Field(default=True, description="Dry-run mode")


class BoardsMigrateRequest(BaseModel):
    """Body of ``POST /v1/migrate/boards``.

    Names the ADO project whose work items become GitHub Issues. Leave
    ``work_item_types`` empty to migrate every type.
    """
    project: str = Field(description="ADO project name")
    github_org: str = Field(description="GitHub organization")
    github_repo: str = Field(description="GitHub repository name")
    work_item_types: list[str] = Field(default=[], description="Filter to specific work item types (empty = all)")
    dry_run: bool = Field(default=True, description="Dry-run mode")


class TestPlansMigrateRequest(BaseModel):
    """Body of ``POST /v1/migrate/test-plans``.

    Names the ADO project whose test plans, suites and cases become GitHub
    milestones and issues.
    """
    project: str = Field(description="ADO project name")
    github_org: str = Field(description="GitHub organization")
    github_repo: str = Field(description="GitHub repository name")
    dry_run: bool = Field(default=True, description="Dry-run mode")


class ArtifactsPublishRequest(BaseModel):
    """Body of ``POST /v1/migrate/artifacts``.

    Names the ADO artifact feed to register against GitHub Packages and the
    package ecosystem it holds.
    """
    project: str = Field(description="ADO project name")
    feed_name: str = Field(description="ADO artifact feed name")
    github_org: str = Field(description="GitHub organization")
    package_type: str = Field(description="Package type: npm, NuGet, Docker, Maven, PyPI")
    dry_run: bool = Field(default=True, description="Dry-run mode")


class WikiMigrateRequest(BaseModel):
    """Body of ``POST /v1/migrate/wiki``.

    Names the ADO wiki, by name or id, to extract for transfer into the target
    repository's GitHub wiki.
    """
    project: str = Field(description="ADO project name")
    wiki_name: str = Field(description="ADO wiki name or ID")
    github_org: str = Field(description="GitHub organization")
    github_repo: str = Field(description="GitHub repository name")
    dry_run: bool = Field(default=True, description="Dry-run mode")


class BranchPoliciesMigrateRequest(BaseModel):
    """Body of ``POST /v1/migrate/branch-policies``.

    Names the ADO repository whose branch policies become GitHub branch
    protection rules on the target repository.
    """
    project: str = Field(description="ADO project name")
    repo_name: str = Field(description="ADO repository name")
    github_org: str = Field(description="GitHub organization")
    github_repo: str = Field(description="GitHub repository name")
    dry_run: bool = Field(default=True, description="Dry-run mode")
