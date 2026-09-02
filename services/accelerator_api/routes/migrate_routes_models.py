"""migrate_routes_models.py module -- Pydantic request models for migration feature routes."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class GitMirrorRequest(BaseModel):
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
    github_org: str = Field(description="GitHub organization")
    github_repo: str = Field(description="GitHub repository name")
    secret_name: str = Field(description="Name of the secret to provision (e.g. AZURE_DEVOPS_PAT)")
    secret_value: str = Field(default="", description="Secret value (only used in live mode; omitted from logs)")
    dry_run: bool = Field(default=True, description="Dry-run mode — validates without creating")


class ServiceConnectionMigrateRequest(BaseModel):
    project: str = Field(description="ADO project name")
    connection_name: str = Field(description="ADO service connection name to migrate")
    github_org: str = Field(description="GitHub organization")
    github_repo: str = Field(description="GitHub repository name")
    dry_run: bool = Field(default=True, description="Dry-run mode")


class BoardsMigrateRequest(BaseModel):
    project: str = Field(description="ADO project name")
    github_org: str = Field(description="GitHub organization")
    github_repo: str = Field(description="GitHub repository name")
    work_item_types: list[str] = Field(default=[], description="Filter to specific work item types (empty = all)")
    dry_run: bool = Field(default=True, description="Dry-run mode")


class TestPlansMigrateRequest(BaseModel):
    project: str = Field(description="ADO project name")
    github_org: str = Field(description="GitHub organization")
    github_repo: str = Field(description="GitHub repository name")
    dry_run: bool = Field(default=True, description="Dry-run mode")


class ArtifactsPublishRequest(BaseModel):
    project: str = Field(description="ADO project name")
    feed_name: str = Field(description="ADO artifact feed name")
    github_org: str = Field(description="GitHub organization")
    package_type: str = Field(description="Package type: npm, NuGet, Docker, Maven, PyPI")
    dry_run: bool = Field(default=True, description="Dry-run mode")


class WikiMigrateRequest(BaseModel):
    project: str = Field(description="ADO project name")
    wiki_name: str = Field(description="ADO wiki name or ID")
    github_org: str = Field(description="GitHub organization")
    github_repo: str = Field(description="GitHub repository name")
    dry_run: bool = Field(default=True, description="Dry-run mode")


class BranchPoliciesMigrateRequest(BaseModel):
    project: str = Field(description="ADO project name")
    repo_name: str = Field(description="ADO repository name")
    github_org: str = Field(description="GitHub organization")
    github_repo: str = Field(description="GitHub repository name")
    dry_run: bool = Field(default=True, description="Dry-run mode")
