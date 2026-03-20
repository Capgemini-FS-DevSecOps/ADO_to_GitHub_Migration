# Pipeline Transformation Guide

How ado2gh converts Azure DevOps pipelines to GitHub Actions workflows.

---

## Pipeline Types

| ADO Type | GH Equivalent | Conversion |
|---|---|---|
| YAML pipeline | GitHub Actions workflow | **Auto** — syntax transform + task mapping |
| Classic build pipeline | GitHub Actions workflow | **Assisted** — no source YAML, best-effort |
| Classic release pipeline | GH Actions + Environments | **Manual** — stages map to deployment jobs |

---

## Supported ADO Task Mappings

### Build & Setup Tasks

| ADO Task | GitHub Action | Notes |
|---|---|---|
| `NodeTool@0` | `actions/setup-node@v4` | `versionSpec` → `node-version` |
| `UsePythonVersion@0` | `actions/setup-python@v5` | `versionSpec` → `python-version` |
| `DotNetCoreCLI@2` | `actions/setup-dotnet@v4` | `version` → `dotnet-version` |
| `JavaToolInstaller@0` | `actions/setup-java@v4` | `versionSpec` → `java-version` |
| `GoTool@0` | `actions/setup-go@v5` | `version` → `go-version` |

### Docker & Container

| ADO Task | GitHub Action |
|---|---|
| `Docker@2` | `docker/build-push-action@v5` |

### Azure Services

| ADO Task | GitHub Action |
|---|---|
| `AzureCLI@2` | `azure/CLI@v2` |
| `AzureWebApp@1` | `azure/webapps-deploy@v3` |
| `AzureFunctionApp@2` | `azure/functions-action@v1` |

### Artifacts

| ADO Task | GitHub Action |
|---|---|
| `PublishBuildArtifacts@1` | `actions/upload-artifact@v4` |
| `DownloadBuildArtifacts@0` | `actions/download-artifact@v4` |
| `PublishTestResults@2` | `dorny/test-reporter@v1` |

### Build Tools

| ADO Task | GitHub Action | Notes |
|---|---|---|
| `NuGetCommand@2` | `actions/setup-dotnet@v4` | Use with `dotnet restore/pack/push` |
| `Maven@4` | `actions/setup-java@v4` | Run Maven via shell after setup |
| `Gradle@3` | `gradle/actions/setup-gradle@v3` | |
| `Terraform@0` | `hashicorp/setup-terraform@v3` | |
| `HelmDeploy@0` | `azure/k8s-deploy@v5` | Review Helm inputs |
| `Kubernetes@1` | `azure/k8s-deploy@v5` | |

### Script Tasks (Direct Mapping)

| ADO Task | GitHub Actions `run:` |
|---|---|
| `CmdLine@2` | `run:` with `shell: bash` |
| `Bash@3` | `run:` with `shell: bash` |
| `PowerShell@2` | `run:` with `shell: pwsh` |
| `Npm@1` | `run: npm <command>` |

---

## What Gets Preserved

- **Triggers** — CI branches, PR branches, schedules (cron conversion)
- **Stages** → jobs with `needs:` dependencies
- **Environments** → GitHub Environments (created automatically)
- **Conditions** → `if:` expressions (common mappings)
- **Variables** (non-secret) → `env:` block
- **Agent pools** → runner labels (`ubuntu-latest`, `windows-latest`, etc.)

## What Needs Manual Setup

- **Secret variables** — values cannot be read from ADO API. Use `service-connections` manifest.
- **Variable groups** — names are preserved as warnings; set values via `gh secret set`.
- **Service connections** — see [service connection manifest](#service-connection-manifest).
- **Self-hosted agent pools** — flagged in migration notes; register equivalent self-hosted runners.
- **Approval gates** — map to GitHub Environment required reviewers.
- **Unsupported tasks** — marked as `[MANUAL]` steps in generated workflow.

---

## Generated Output Per Pipeline

For each pipeline, the tool generates:

1. **`{pipeline_name}.yml`** — GitHub Actions workflow YAML with:
   - Header comment with source pipeline info
   - Mapped triggers, jobs, steps
   - `[MANUAL]` placeholders for unsupported tasks
   - `workflow_dispatch` trigger for manual testing

2. **`_{pipeline_name}_migration_notes.md`** — Markdown migration guide with:
   - Variable groups to map as GitHub Secrets
   - Environments to create with required reviewers
   - Service connections requiring manual setup
   - Unsupported ADO tasks needing manual conversion
   - Pre-migration checklist

---

## Pipeline Readiness Assessment

Before migration, run:

```bash
ado2gh pipeline-readiness --config migration.yaml --output readiness.csv
```

### Conversion Levels

| Level | Meaning | Typical Effort |
|---|---|---|
| **auto** | YAML pipeline, simple, no blockers | 0.5 hours |
| **assisted** | Has warnings (var groups, environments), needs review | 2–8 hours |
| **manual** | Has blockers (unsupported tasks, self-hosted pools) | 8–24 hours |

### Effort Estimation Matrix

| Type × Complexity | Simple | Medium | Complex |
|---|---|---|---|
| YAML | 0.5h | 2h | 8h |
| Classic Build | 2h | 6h | 16h |
| Classic Release | 4h | 12h | 24h |

Additional effort modifiers:
- +4h per unsupported blocker task
- +2h per self-hosted pool
- +0.5h per variable group
- +1h per service connection
- +0.5h per environment with approvers

---

## ADO Condition Mapping

| ADO Condition | GitHub Actions `if:` |
|---|---|
| `succeeded()` | `success()` |
| `failed()` | `failure()` |
| `always()` | `always()` |
| `succeededOrFailed()` | `success() \|\| failure()` |
| `eq(variables['Build.SourceBranchName'], 'main')` | `github.ref == 'refs/heads/main'` |
| `eq(variables['Build.Reason'], 'PullRequest')` | `github.event_name == 'pull_request'` |

Unmapped conditions are flagged in migration notes for manual review.

---

## Agent Pool Mapping

| ADO Pool | GitHub Runner |
|---|---|
| `ubuntu-latest` | `ubuntu-latest` |
| `windows-latest` | `windows-latest` |
| `macos-latest` | `macos-latest` |
| `vs2019` | `windows-2019` |
| `vs2022` | `windows-2022` |
| `ubuntu-22.04` | `ubuntu-22.04` |
| `ubuntu-20.04` | `ubuntu-20.04` |
| `macos-13` | `macos-13` |
| Custom pool name | `self-hosted` (flagged in notes) |
