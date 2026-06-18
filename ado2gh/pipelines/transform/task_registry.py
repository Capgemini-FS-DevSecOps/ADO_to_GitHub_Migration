"""Pluggable ADO task -> GitHub Actions mapping registry."""
from __future__ import annotations

from typing import Optional

ADO_TASK_MAP: dict[str, str] = {
    "NodeTool@0":              "actions/setup-node@v4",
    "UsePythonVersion@0":      "actions/setup-python@v5",
    "DotNetCoreCLI@2":         "actions/setup-dotnet@v4",
    "JavaToolInstaller@0":     "actions/setup-java@v4",
    "GoTool@0":                "actions/setup-go@v5",
    "Docker@2":                "docker/build-push-action@v5",
    "AzureCLI@2":              "azure/CLI@v2",
    "AzureWebApp@1":           "azure/webapps-deploy@v3",
    "AzureFunctionApp@2":      "azure/functions-action@v1",
    "PublishBuildArtifacts@1":  "actions/upload-artifact@v4",
    "DownloadBuildArtifacts@0": "actions/download-artifact@v4",
    "PublishTestResults@2":     "dorny/test-reporter@v1",
    "NuGetCommand@2":          "run",
    "Maven@4":                 "run",
    "Gradle@3":                "run",
    "Terraform@0":             "run",
    "HelmDeploy@0":            "run",
    "Kubernetes@1":            "run",
    "CmdLine@2":               "run",
    "Bash@3":                  "run",
    "PowerShell@2":            "run",
    "Npm@1":                   "run",
    "PipAuthenticate@1":       "run",
    "NpmAuthenticate@0":       "run",
    # ARM / Bicep deployment tasks
    "AzureResourceManagerTemplateDeployment@3": "azure/arm-deploy@v2",
    "AzureResourceManagerTemplateDeployment@2": "azure/arm-deploy@v2",
    "AzureResourceManagerTemplateDeployment@1": "azure/arm-deploy@v2",
    "AzureResourceGroupDeployment@2":           "azure/arm-deploy@v2",
    "AzureResourceGroupDeployment@1":           "azure/arm-deploy@v2",
}

RUN_BASED_TASKS: set[str] = {k for k, v in ADO_TASK_MAP.items() if v == "run"}

_POOL_RUNNER_MAP: dict[str, str] = {
    "ubuntu":       "ubuntu-latest",
    "windows":      "windows-latest",
    "macos":        "macos-latest",
    "hosted":       "ubuntu-latest",
    "default":      "ubuntu-latest",
    "azure pipelines": "ubuntu-latest",
}

_extra: dict[str, str] = {}


def register_task(ado_task: str, gha_action: str) -> None:
    """Register an additional task mapping at runtime."""
    _extra[ado_task] = gha_action


def lookup_task(ado_task: str) -> Optional[str]:
    """Resolve ADO task name to GHA action or 'run'."""
    if ado_task in _extra:
        return _extra[ado_task]
    return ADO_TASK_MAP.get(ado_task)


def is_run_based(ado_task: str) -> bool:
    action = lookup_task(ado_task)
    return action == "run"
