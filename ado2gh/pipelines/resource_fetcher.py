"""Template fetcher that understands `resources.repositories` aliases.

This provides IO (ADO REST calls) for the pure-ish template compiler.
"""

from __future__ import annotations

from dataclasses import dataclass

from ado2gh.clients.ado_client import ADOClient


@dataclass(frozen=True)
class RepoAlias:
    alias: str
    project: str
    repo_name: str
    ref: str


def parse_resource_repo_name(default_project: str, name: str) -> tuple[str, str]:
    """Parse ADO resource repository `name`.

    Defaults:
    - if `name` contains '/', treat as 'Project/Repo'
    - else treat as '<default_project>/<name>'
    """
    n = (name or "").strip()
    if "/" in n:
        p, r = n.split("/", 1)
        return p.strip(), r.strip()
    return default_project, n


class AdoTemplateFetcher:
    def __init__(self, ado: ADOClient, *, default_project: str, default_repo_id: str, default_branch: str):
        self.ado = ado
        self.default_project = default_project
        self.default_repo_id = default_repo_id
        self.default_branch = default_branch or "main"

    def fetch(self, path: str) -> str:
        """Fetch from default repo."""
        return self.ado.get_pipeline_yaml_from_git(
            self.default_project,
            self.default_repo_id,
            path,
            branch=self.default_branch,
        )

    def fetch_from_alias(self, *, alias: dict, path: str) -> str:
        """Fetch from an alias resource definition dict."""
        name = alias.get("name") or alias.get("repository") or ""
        project, repo_name = parse_resource_repo_name(self.default_project, str(name))
        ref = alias.get("ref") or self.default_branch
        branch = str(ref).replace("refs/heads/", "")
        return self.ado.get_yaml_from_repo_name(project, repo_name, path, branch=branch)

