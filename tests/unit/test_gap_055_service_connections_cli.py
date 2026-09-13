"""GAP-055 — ``ado2gh service-connections`` produced an empty manifest.

``ServiceConnectionManifest.generate(projects, output_path)`` scans ADO
*projects*: it passes each element straight to
``ADOClient.list_service_connections(project)``, which encodes it into the REST
URL. ``ado2gh/cli/misc.py`` handed it the ``list[RepoConfig]`` returned by
``load_repos`` instead, so every iteration failed inside ``generate``'s
``except Exception`` guard and the command wrote a manifest with zero
connections — the documented step 4 of the execution workflow produced nothing
for the ops team, and said so only in a warning log line.

``d1427fd`` (T077) fixed it while making mypy a CI gate, deduplicating the
repos' ``ado_project`` values into a sorted ``list[str]``. This test covers that
fix, which landed with no test of its own.
"""
from __future__ import annotations

import json

from click.testing import CliRunner

from ado2gh.cli import misc as misc_cli
from ado2gh.cli.main import cli

CONFIG = """\
global:
  gh_org: fake-org
  ado_org_url: https://dev.azure.com/fake-org
waves:
  - wave_id: 1
    name: wave-1
    repos:
      - ado_project: Contoso
        ado_repo: payments
      - ado_project: Contoso
        ado_repo: billing
      - ado_project: Fabrikam
        ado_repo: shipping
"""

CONNECTIONS = {
    "Contoso": [
        {"name": "contoso-azure", "type": "azurerm", "url": "https://example.invalid"},
        {"name": "contoso-docker", "type": "dockerregistry", "url": "https://example.invalid"},
    ],
    "Fabrikam": [
        {"name": "fabrikam-aws", "type": "aws", "url": "https://example.invalid"},
    ],
}


class _FakeADO:
    """ADO client stand-in recording exactly what the manifest asked it to scan."""

    def __init__(self):
        self.asked = []

    def list_service_connections(self, project):
        self.asked.append(project)
        if not isinstance(project, str):
            raise TypeError(f"project must be a name, got {type(project).__name__}")
        return CONNECTIONS.get(project, [])


def _run(tmp_path, monkeypatch):
    """Invoke ``service-connections`` against the fake ADO client."""
    config_path = tmp_path / "migration.yaml"
    config_path.write_text(CONFIG, encoding="utf-8")
    output_path = tmp_path / "service_connection_manifest.json"
    ado = _FakeADO()
    monkeypatch.setattr(misc_cli, "load_clients", lambda cfg: (ado, None))

    result = CliRunner().invoke(
        cli, ["service-connections", "-c", str(config_path), "-o", str(output_path)],
    )
    assert result.exit_code == 0, (
        f"service-connections exited {result.exit_code}: "
        f"{result.exception!r}\n{result.output}"
    )
    return ado, json.loads(output_path.read_text(encoding="utf-8"))


def test_manifest_lists_the_connections_of_every_project(tmp_path, monkeypatch):
    """Every wave repo's project is scanned and its connections reach the manifest."""
    ado, manifest = _run(tmp_path, monkeypatch)

    assert sorted(manifest["by_project"]) == ["Contoso", "Fabrikam"]
    assert [c["name"] for c in manifest["by_project"]["Contoso"]] == [
        "contoso-azure", "contoso-docker",
    ]
    assert [c["name"] for c in manifest["by_project"]["Fabrikam"]] == ["fabrikam-aws"]
    assert manifest["summary"]["total_connections"] == 3
    assert "AZURE_CLIENT_ID" in manifest["by_project"]["Contoso"][0]["gh_secret_names"]
    assert ado.asked, "the manifest never scanned anything"


def test_each_project_is_scanned_once_by_name(tmp_path, monkeypatch):
    """The two Contoso repos collapse to one project scan, keyed by the name."""
    ado, _ = _run(tmp_path, monkeypatch)

    assert ado.asked == ["Contoso", "Fabrikam"]
