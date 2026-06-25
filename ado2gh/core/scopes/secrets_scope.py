"""Secrets mapping manifest scope.

@deprecated SecretsScopeHandler is deprecated as of v1.0.0.
The map_secrets functionality has been merged into the analyze_deps step
(see FR-013 in Spec 009). This handler will be removed in v2.0.0.
Use the analyze_deps step for dependency analysis including service connections
and variable groups.
"""
from __future__ import annotations

import json
import warnings
from typing import Any

from ado2gh.core.scopes.base import ScopeContext, ScopeResult
from ado2gh.models import MigrationScope, RepoConfig
from ado2gh.output_dirs import output_base


class SecretsScopeHandler:
    """@deprecated Use analyze_deps step instead. Removal in v2.0.0."""
    scope = MigrationScope.SECRETS.value

    def __init__(self) -> None:
        warnings.warn(
            "SecretsScopeHandler is deprecated. Use analyze_deps step instead. "
            "This will be removed in v2.0.0.",
            DeprecationWarning,
            stacklevel=2,
        )

    def migrate(self, repo: RepoConfig, ctx: ScopeContext, **kwargs: Any) -> ScopeResult:
        var_groups = ctx.ado.list_variable_groups(repo.ado_project)
        svc_conns = ctx.ado.list_service_connections(repo.ado_project)
        stats = {
            "variable_groups": len(var_groups),
            "service_connections": len(svc_conns),
        }
        if ctx.dry_run:
            stats["dry_run"] = True
            return ScopeResult(stats=stats)

        out = output_base() / "secrets" / repo.gh_org / repo.gh_repo
        out.mkdir(parents=True, exist_ok=True)
        mapping = {
            "instructions": (
                f"Secret VALUES cannot be read from ADO API. "
                f"Use: gh secret set SECRET_NAME --body VALUE "
                f"--repo {repo.gh_org}/{repo.gh_repo}"
            ),
            "variable_groups": [
                {
                    "name": vg.get("name"),
                    "type": vg.get("type"),
                    "variables": [
                        {"name": k, "is_secret": v.get("isSecret", False)}
                        for k, v in vg.get("variables", {}).items()
                    ],
                }
                for vg in var_groups
            ],
            "service_connections": [
                {"name": sc.get("name"), "type": sc.get("type"),
                 "suggestion": self._suggest(sc)}
                for sc in svc_conns
            ],
        }
        path = out / "secrets_mapping.json"
        path.write_text(json.dumps(mapping, indent=2))
        stats["manifest_path"] = str(path)
        return ScopeResult(stats=stats)

    @staticmethod
    def _suggest(sc: dict) -> str:
        t = sc.get("type", "").lower()
        if "azure" in t:
            return "AZURE_CREDENTIALS or use OIDC (azure/login@v2)"
        if "docker" in t:
            return "DOCKERHUB_USERNAME + DOCKERHUB_TOKEN"
        if "github" in t:
            return "GH_TOKEN (already available)"
        if "aws" in t:
            return "AWS credentials or OIDC"
        return f"Review manually — type: {sc.get('type', 'unknown')}"
