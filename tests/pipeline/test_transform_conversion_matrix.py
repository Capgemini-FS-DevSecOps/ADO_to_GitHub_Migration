"""Table-driven ADO task -> GitHub Actions step conversions (COV-DRIFT-006).

`ado2gh/pipelines/transform/transformer.py` sat at 47 % with 141 statements
unexercised while this feature rewrote 104 lines of it. The drift report calls
this "where a silent defect is most expensive: a wrongly converted task emits a
workflow that runs and does the wrong thing, rather than failing loudly".

The matrix below is the conversion contract: one row per task shape, asserting
the whole emitted step rather than one field of it, so a changed action version,
a dropped ``with:`` key or a swapped input name fails a test.

`_map_step` is exercised directly. It is a private method, but it is the single
choke point every workflow step goes through and the only place a per-task
conversion can be asserted without also asserting the job graph around it.
"""
from __future__ import annotations

import pytest

from ado2gh.pipelines.transform.task_registry import (
    ADO_TASK_MAP,
    RUN_BASED_TASKS,
    is_run_based,
    lookup_task,
    register_task,
)
from ado2gh.pipelines.transform.transformer import PipelineTransformer


@pytest.fixture
def convert():
    """Convert one ADO step and hand back the step plus the lists it appended to."""
    transformer = PipelineTransformer()

    def _convert(step: dict) -> tuple[dict | None, list[str], list[str]]:
        warnings: list[str] = []
        unsupported: list[str] = []
        return transformer._map_step(step, warnings, unsupported), warnings, unsupported

    return _convert


# --------------------------------------------------------------------------
# Tasks that map to a marketplace action, with their whole `with:` block
# --------------------------------------------------------------------------

ACTION_MATRIX = [
    (
        "NodeTool@0", {"versionSpec": "20.x"},
        {"uses": "actions/setup-node@v4", "with": {"node-version": "20.x"}},
    ),
    (
        "NodeTool@0", {"version": "18.x"},
        {"uses": "actions/setup-node@v4", "with": {"node-version": "18.x"}},
    ),
    (
        "NodeTool@0", {},
        {"uses": "actions/setup-node@v4"},
    ),
    (
        "UsePythonVersion@0", {"versionSpec": "3.12"},
        {"uses": "actions/setup-python@v5", "with": {"python-version": "3.12"}},
    ),
    (
        "DotNetCoreCLI@2", {"version": "8.0.x"},
        {"uses": "actions/setup-dotnet@v4", "with": {"dotnet-version": "8.0.x"}},
    ),
    (
        "JavaToolInstaller@0", {"versionSpec": "17"},
        {
            "uses": "actions/setup-java@v4",
            "with": {"java-version": "17", "distribution": "temurin"},
        },
    ),
    (
        "JavaToolInstaller@0", {"versionSpec": "21", "jdkArchitectureOption": "zulu"},
        {
            "uses": "actions/setup-java@v4",
            "with": {"java-version": "21", "distribution": "zulu"},
        },
    ),
    (
        "GoTool@0", {"version": "1.22"},
        {"uses": "actions/setup-go@v5", "with": {"go-version": "1.22"}},
    ),
    (
        "Docker@2", {},
        {
            "uses": "docker/build-push-action@v5",
            "with": {"context": ".", "push": "false"},
        },
    ),
    (
        "Docker@2",
        {
            "buildContext": "src", "Dockerfile": "src/Dockerfile",
            "push": "true", "tags": "latest",
        },
        {
            "uses": "docker/build-push-action@v5",
            "with": {
                "context": "src", "file": "src/Dockerfile",
                "push": "true", "tags": "latest",
            },
        },
    ),
    (
        "AzureCLI@2", {"inlineScript": "az group list"},
        {"uses": "azure/CLI@v2", "with": {"inlineScript": "az group list"}},
    ),
    (
        "AzureWebApp@1", {"appName": "fake-app", "package": "drop.zip"},
        {
            "uses": "azure/webapps-deploy@v3",
            "with": {"app-name": "fake-app", "package": "drop.zip"},
        },
    ),
    (
        "AzureFunctionApp@2", {"appName": "fake-fn"},
        {"uses": "azure/functions-action@v1", "with": {"app-name": "fake-fn"}},
    ),
    (
        "PublishBuildArtifacts@1", {},
        {
            "uses": "actions/upload-artifact@v4",
            "with": {"name": "drop", "path": "."},
        },
    ),
    (
        "PublishBuildArtifacts@1",
        {"PathtoPublish": "$(Build.ArtifactStagingDirectory)", "ArtifactName": "bin"},
        {
            "uses": "actions/upload-artifact@v4",
            "with": {"name": "bin", "path": "$(Build.ArtifactStagingDirectory)"},
        },
    ),
    (
        "DownloadBuildArtifacts@0", {"artifactName": "bin"},
        {"uses": "actions/download-artifact@v4", "with": {"name": "bin"}},
    ),
    (
        "DownloadBuildArtifacts@0", {},
        {"uses": "actions/download-artifact@v4", "with": {"name": "drop"}},
    ),
    (
        "PublishTestResults@2", {"testResultsFormat": "JUnit", "testResultsFiles": "**/*.xml"},
        {
            "uses": "dorny/test-reporter@v1",
            "with": {"reporter": "junit", "path": "**/*.xml"},
        },
    ),
    (
        "AzureResourceManagerTemplateDeployment@3", {},
        {"uses": "azure/arm-deploy@v2"},
    ),
    (
        "AzureResourceGroupDeployment@2", {},
        {"uses": "azure/arm-deploy@v2"},
    ),
]


@pytest.mark.parametrize(
    "task,inputs,expected", ACTION_MATRIX,
    ids=[f"{t}-{i}" for i, (t, _, _) in enumerate(ACTION_MATRIX)],
)
def test_an_action_backed_task_converts_to_exactly_this_step(
    convert, task, inputs, expected,
):
    step, warnings, unsupported = convert({"task": task, "inputs": inputs})
    assert step == expected
    assert unsupported == []
    assert warnings == []


# --------------------------------------------------------------------------
# Tasks that become a shell step, with their whole command line
# --------------------------------------------------------------------------

RUN_MATRIX = [
    ("CmdLine@2", {"script": "make build"}, "make build"),
    ("Bash@3", {"script": "./build.sh"}, "./build.sh"),
    ("Npm@1", {}, "npm install"),
    ("Npm@1", {"command": "ci"}, "npm ci"),
    ("Npm@1", {"command": "ci", "workingDir": "web"}, "cd web && npm ci"),
    ("NuGetCommand@2", {}, "dotnet restore"),
    ("NuGetCommand@2", {"command": "restore", "restoreSolution": "App.sln"},
     "dotnet restore App.sln"),
    ("NuGetCommand@2", {"command": "push", "solution": "App.sln"}, "dotnet push App.sln"),
    ("Maven@4", {}, "mvn package -f pom.xml"),
    ("Maven@4", {"goals": "verify", "mavenPomFile": "svc/pom.xml"},
     "mvn verify -f svc/pom.xml"),
    ("Gradle@3", {}, "./gradlew build"),
    ("Gradle@3", {"tasks": "check"}, "./gradlew check"),
    ("Terraform@0", {}, "terraform init"),
    ("Terraform@0", {"command": "apply"}, "terraform apply"),
    ("HelmDeploy@0", {}, "helm install"),
    ("HelmDeploy@0", {"command": "upgrade", "chartPath": "./chart"}, "helm upgrade ./chart"),
    ("HelmDeploy@0", {"chartName": "web"}, "helm install web"),
    ("Kubernetes@1", {}, "kubectl apply"),
    ("Kubernetes@1", {"command": "delete", "arguments": "-f k8s/"}, "kubectl delete -f k8s/"),
    ("PipAuthenticate@1", {},
     'echo "TODO: configure authentication (was PipAuthenticate@1)"'),
    # NpmAuthenticate@0 is matched by the `Npm` prefix branch before the
    # "Authenticate" branch is ever reached, so it converts to an install rather
    # than to the authentication TODO. Pinned as observed behaviour; the
    # divergence is carried as a follow-up rather than fixed here.
    ("NpmAuthenticate@0", {}, "npm install"),
]


@pytest.mark.parametrize(
    "task,inputs,expected", RUN_MATRIX,
    ids=[f"{t}-{i}" for i, (t, _, _) in enumerate(RUN_MATRIX)],
)
def test_a_run_backed_task_converts_to_exactly_this_command(
    convert, task, inputs, expected,
):
    step, _, unsupported = convert({"task": task, "inputs": inputs})
    assert step["run"] == expected
    assert "uses" not in step, f"{task} emitted an action instead of a shell step"
    assert unsupported == []


def test_an_inline_script_input_wins_over_the_task_specific_command(convert):
    step, _, _ = convert(
        {"task": "Maven@4", "inputs": {"script": "mvn -v", "goals": "package"}},
    )
    assert step["run"] == "mvn -v"


def test_powershell_steps_declare_the_pwsh_shell(convert):
    step, _, _ = convert({"task": "PowerShell@2", "inputs": {"script": "Write-Host hi"}})
    assert step["run"] == "Write-Host hi"
    assert step["shell"] == "pwsh"


def test_a_run_backed_task_that_is_not_powershell_declares_no_shell(convert):
    step, _, _ = convert({"task": "Bash@3", "inputs": {"script": "echo hi"}})
    assert "shell" not in step


# --------------------------------------------------------------------------
# Inline script steps (no task name at all)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "step,expected_run,expected_shell",
    [
        ({"script": "make test"}, "make test", "bash"),
        ({"bash": "./run.sh"}, "./run.sh", "bash"),
        ({"powershell": "Get-Date"}, "Get-Date", "pwsh"),
    ],
)
def test_an_inline_script_step_becomes_a_run_step(
    convert, step, expected_run, expected_shell,
):
    out, _, unsupported = convert(step)
    assert out["run"] == expected_run
    assert out["shell"] == expected_shell
    assert unsupported == []


# --------------------------------------------------------------------------
# Steps that are dropped
# --------------------------------------------------------------------------


def test_a_disabled_step_is_dropped(convert):
    out, warnings, unsupported = convert(
        {"task": "Bash@3", "inputs": {"script": "echo hi"}, "enabled": False},
    )
    assert out is None
    assert unsupported == []
    assert warnings == []


def test_a_checkout_step_is_dropped(convert):
    """GitHub Actions checks out the repository itself, so the step is redundant."""
    assert convert({"checkout": "self"})[0] is None


def test_a_step_with_nothing_to_convert_is_dropped(convert):
    assert convert({})[0] is None


def test_an_unresolved_template_reference_is_dropped_with_a_warning(convert):
    out, warnings, unsupported = convert({"template": "templates/build.yml"})
    assert out is None
    assert unsupported == []
    assert len(warnings) == 1
    assert "templates/build.yml" in warnings[0]
    assert "re-run pipeline inventory" in warnings[0]


# --------------------------------------------------------------------------
# Unsupported tasks become a visible TODO rather than disappearing
# --------------------------------------------------------------------------


def test_an_unknown_task_becomes_a_todo_step_and_is_reported(convert):
    out, warnings, unsupported = convert(
        {"task": "SomeVendorTask@7", "inputs": {"x": "y"}},
    )
    assert out == {
        "name": "TODO: migrate 'SomeVendorTask@7'",
        "run": 'echo "ADO task SomeVendorTask@7 needs manual migration"',
    }
    assert unsupported == ["SomeVendorTask@7"]
    assert len(warnings) == 1
    assert "no known GHA equivalent" in warnings[0]


def test_the_todo_step_overrides_the_display_name_so_it_cannot_be_missed(convert):
    out, _, _ = convert({"task": "SomeVendorTask@7", "displayName": "Looks fine"})
    assert out["name"] == "TODO: migrate 'SomeVendorTask@7'"


# --------------------------------------------------------------------------
# Fields carried across from the ADO step
# --------------------------------------------------------------------------


def test_the_display_name_becomes_the_step_name(convert):
    out, _, _ = convert(
        {"task": "Bash@3", "displayName": "Run tests", "inputs": {"script": "t"}},
    )
    assert out["name"] == "Run tests"


def test_the_legacy_name_field_is_used_when_there_is_no_display_name(convert):
    out, _, _ = convert({"task": "Bash@3", "name": "legacy", "inputs": {"script": "t"}})
    assert out["name"] == "legacy"


def test_the_legacy_task_name_field_is_honoured(convert):
    out, _, _ = convert({"taskName": "NodeTool@0", "inputs": {"versionSpec": "20.x"}})
    assert out["uses"] == "actions/setup-node@v4"


def test_an_env_block_is_carried_over_as_a_copy(convert):
    env = {"BUILD_ID": "1"}
    out, _, _ = convert({"task": "Bash@3", "env": env, "inputs": {"script": "t"}})
    assert out["env"] == {"BUILD_ID": "1"}
    assert out["env"] is not env, "the ADO step's env dict was aliased into the workflow"


def test_a_condition_is_translated_into_an_if_expression(convert):
    out, _, _ = convert(
        {"task": "Bash@3", "condition": "succeeded()", "inputs": {"script": "t"}},
    )
    assert "if" in out
    assert out["if"], "the condition was translated to an empty expression"


def test_a_step_with_no_condition_emits_no_if_key(convert):
    out, _, _ = convert({"task": "Bash@3", "inputs": {"script": "t"}})
    assert "if" not in out


# --------------------------------------------------------------------------
# The registry itself
# --------------------------------------------------------------------------


def test_every_mapped_task_resolves_to_either_an_action_or_a_run_step():
    for task, action in ADO_TASK_MAP.items():
        assert lookup_task(task) == action
        assert is_run_based(task) is (action == "run")


def test_the_run_based_set_matches_the_table_it_is_derived_from():
    assert RUN_BASED_TASKS == {t for t, a in ADO_TASK_MAP.items() if a == "run"}


def test_an_unmapped_task_resolves_to_none():
    assert lookup_task("NoSuchTask@1") is None
    assert is_run_based("NoSuchTask@1") is False


def test_a_runtime_registration_wins_over_the_built_in_table(convert):
    original = lookup_task("NodeTool@0")
    register_task("NodeTool@0", "acme/setup-node@v1")
    try:
        assert lookup_task("NodeTool@0") == "acme/setup-node@v1"
        out, _, unsupported = convert({"task": "NodeTool@0", "inputs": {}})
        assert out["uses"] == "acme/setup-node@v1"
        assert unsupported == []
    finally:
        register_task("NodeTool@0", original)
    assert lookup_task("NodeTool@0") == original


def test_a_runtime_registration_can_add_a_task_the_table_does_not_know(convert):
    register_task("AcmeDeploy@1", "run")
    try:
        assert is_run_based("AcmeDeploy@1") is True
        out, _, unsupported = convert({"task": "AcmeDeploy@1", "inputs": {"script": "x"}})
        assert out["run"] == "x"
        assert unsupported == [], "a registered task was still reported as unsupported"
    finally:
        from ado2gh.pipelines.transform import task_registry

        task_registry._extra.pop("AcmeDeploy@1", None)
