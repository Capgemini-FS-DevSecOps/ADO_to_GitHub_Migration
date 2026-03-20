"""Service connection migration manifest — maps ADO service connections to GitHub equivalents."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ado2gh.clients.ado_client import ADOClient
from ado2gh.logging_config import console, log
from ado2gh.models import RepoConfig


class ServiceConnectionManifest:
    """Generate a detailed manifest mapping ADO service connections to GitHub secrets/OIDC.

    ADO service connections CANNOT be programmatically migrated (values are not
    readable via API). This manifest provides ops teams with:
    - What connections exist per project
    - Suggested GitHub secret names
    - OIDC setup instructions for Azure/AWS
    - Which pipelines depend on each connection
    """

    # Map ADO service connection types to GitHub setup instructions
    MIGRATION_GUIDES = {
        "azurerm": {
            "gh_secret_names": ["AZURE_CLIENT_ID", "AZURE_TENANT_ID", "AZURE_SUBSCRIPTION_ID"],
            "recommendation": "Use OIDC with azure/login@v2 and federated credentials (no secrets needed)",
            "docs": "https://github.com/azure/login#login-with-openid-connect-oidc-based-authentication",
        },
        "azure": {
            "gh_secret_names": ["AZURE_CREDENTIALS"],
            "recommendation": "Use OIDC with azure/login@v2 for keyless auth, or AZURE_CREDENTIALS JSON blob",
            "docs": "https://github.com/azure/login",
        },
        "dockerregistry": {
            "gh_secret_names": ["DOCKER_USERNAME", "DOCKER_PASSWORD"],
            "recommendation": "Set as repository secrets; use docker/login-action@v3",
            "docs": "https://github.com/docker/login-action",
        },
        "aws": {
            "gh_secret_names": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"],
            "recommendation": "Use OIDC with aws-actions/configure-aws-credentials@v4 (preferred)",
            "docs": "https://github.com/aws-actions/configure-aws-credentials",
        },
        "kubernetes": {
            "gh_secret_names": ["KUBE_CONFIG"],
            "recommendation": "Base64-encode kubeconfig as repository secret; use azure/k8s-set-context@v4",
            "docs": "https://github.com/azure/k8s-set-context",
        },
        "github": {
            "gh_secret_names": [],
            "recommendation": "Use built-in GITHUB_TOKEN (automatic) or create a PAT for cross-repo access",
            "docs": "https://docs.github.com/en/actions/security-guides/automatic-token-authentication",
        },
        "npm": {
            "gh_secret_names": ["NPM_TOKEN"],
            "recommendation": "Set NPM_TOKEN as repository secret; use in .npmrc or setup-node",
            "docs": "https://docs.github.com/en/actions/publishing-packages/publishing-nodejs-packages",
        },
        "nuget": {
            "gh_secret_names": ["NUGET_API_KEY"],
            "recommendation": "Set NUGET_API_KEY as repository secret; use setup-dotnet + dotnet nuget push",
            "docs": "https://docs.github.com/en/actions/publishing-packages/publishing-dotnet-packages",
        },
        "sonarqube": {
            "gh_secret_names": ["SONAR_TOKEN", "SONAR_HOST_URL"],
            "recommendation": "Set as repository secrets; use SonarSource/sonarqube-scan-action@master",
            "docs": "https://github.com/SonarSource/sonarqube-scan-action",
        },
        "ssh": {
            "gh_secret_names": ["SSH_PRIVATE_KEY", "SSH_HOST", "SSH_USERNAME"],
            "recommendation": "Set SSH key as secret; use webfactory/ssh-agent@v0.9.0",
            "docs": "https://github.com/webfactory/ssh-agent",
        },
    }

    def __init__(self, ado: ADOClient):
        self.ado = ado

    def generate(self, projects: list[str],
                 output_path: str = "output/service_connection_manifest.json") -> dict:
        """Scan all projects for service connections and generate migration manifest."""
        all_connections: list[dict] = []
        by_project: dict[str, list] = {}

        for project in projects:
            try:
                connections = self.ado.list_service_connections(project)
                mapped = [self._map_connection(project, sc) for sc in connections]
                by_project[project] = mapped
                all_connections.extend(mapped)
            except Exception as exc:
                log.warning("Failed to scan service connections for %s: %s", project, exc)

        summary = self._build_summary(all_connections)

        manifest = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
            "by_project": by_project,
            "connections": all_connections,
        }

        # Write JSON manifest
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(manifest, indent=2, default=str))

        # Write CSV for ops teams
        csv_path = output_path.replace(".json", ".csv")
        self._write_csv(all_connections, csv_path)

        log.info("Service connection manifest: %s (%d connections across %d projects)",
                 output_path, len(all_connections), len(projects))

        return summary

    def _map_connection(self, project: str, sc: dict) -> dict:
        """Map a single ADO service connection to GitHub migration guidance."""
        sc_type = sc.get("type", "").lower()
        sc_name = sc.get("name", "")
        sc_url = sc.get("url", "")

        # Find matching guide
        guide = None
        for key, g in self.MIGRATION_GUIDES.items():
            if key in sc_type:
                guide = g
                break

        if guide is None:
            guide = {
                "gh_secret_names": [f"{sc_name.upper().replace('-', '_')}_TOKEN"],
                "recommendation": f"Review manually — ADO type: {sc.get('type', 'unknown')}",
                "docs": "",
            }

        return {
            "project": project,
            "name": sc_name,
            "type": sc.get("type", ""),
            "url": sc_url,
            "is_shared": sc.get("isShared", False),
            "created_by": sc.get("createdBy", {}).get("displayName", ""),
            "gh_secret_names": guide["gh_secret_names"],
            "recommendation": guide["recommendation"],
            "docs_url": guide.get("docs", ""),
            "action_required": "ops_team_setup",
        }

    def _build_summary(self, connections: list[dict]) -> dict:
        by_type: dict[str, int] = {}
        for c in connections:
            t = c["type"]
            by_type[t] = by_type.get(t, 0) + 1

        oidc_eligible = sum(1 for c in connections
                            if "oidc" in c.get("recommendation", "").lower())

        return {
            "total_connections": len(connections),
            "by_type": by_type,
            "oidc_eligible": oidc_eligible,
            "manual_setup_required": len(connections),
            "unique_secret_names": len({
                name for c in connections
                for name in c.get("gh_secret_names", [])
            }),
        }

    def _write_csv(self, connections: list[dict], output_path: str):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "project", "name", "type", "url", "gh_secret_names",
                "recommendation", "docs_url", "action_required",
            ])
            writer.writeheader()
            for c in connections:
                row = {**c}
                row["gh_secret_names"] = ", ".join(row.get("gh_secret_names", []))
                writer.writerow({k: row.get(k, "") for k in writer.fieldnames})

    def print_summary(self, summary: dict):
        from rich.table import Table
        from rich import box

        t = Table(title="Service Connection Migration Manifest", box=box.ROUNDED)
        t.add_column("Metric", style="bold")
        t.add_column("Value", justify="right")

        t.add_row("Total connections", str(summary["total_connections"]))
        t.add_row("[green]OIDC-eligible (keyless)[/green]",
                  str(summary["oidc_eligible"]))
        t.add_row("Unique GH secrets needed", str(summary["unique_secret_names"]))
        t.add_row("[yellow]Manual setup required[/yellow]",
                  str(summary["manual_setup_required"]))
        t.add_row("", "")
        for sc_type, count in summary.get("by_type", {}).items():
            t.add_row(f"  {sc_type}", str(count))

        console.print(t)
