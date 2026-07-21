# Setup Guide — ADO2GH Migration Tool

Complete setup instructions for running ADO-to-GitHub migrations at enterprise scale.

---

## 1. Environment Setup

### Python

```bash
# Requires Python 3.9+
python --version

# Create virtual environment
python -m venv venv
source venv/bin/activate    # Linux/macOS
# venv\Scripts\activate     # Windows

pip install -e .
```

### Git

```bash
# Requires Git 2.30+
git --version

# Install Git LFS (for repos with large binary files)
git lfs install
```

### GitHub CLI (GEI strategy only)

```bash
# Install GitHub CLI
# https://cli.github.com/

gh --version
gh auth login
gh extension install github/gh-gei
```

---

## 2. Azure DevOps PAT

Create a PAT at `https://dev.azure.com/YOUR_ORG/_usersSettings/tokens`

### Required Scopes

| Scope | Access | Used By |
|---|---|---|
| Code | Read | `discover`, `agent plan`, `agent run` (git clone), `agent validate` |
| Code | Read & Write | `ado-cleanup --add-redirect` only |
| Build | Read | `pipelines inventory`, `pipeline-readiness` |
| Build | Read & Execute | `ado-cleanup --disable-pipelines` only |
| Release | Read | `pipelines inventory` (release pipelines) |
| Work Items | Read | `agent run` (`work_items` scope) |
| Variable Groups | Read | `agent run` (`secrets` scope), `service-connections` |
| Service Connections | Read | `service-connections` |
| Wiki | Read | `agent run` (`wiki` scope) |
| Project and Team | Read | `discover` |

### Setting the PAT

```bash
export ADO_PAT="your-pat-here"
export ADO_ORG_URL="https://dev.azure.com/YOUR_ORG"
```

**Never commit PATs.** Use environment variables or a secrets vault.

---

## 3. GitHub Token(s)

### Single Token

```bash
export GH_TOKEN="ghp_your_token_here"
```

Grant only the repository and organization permissions required by the approved
scopes. Repository administration/content, Issues, Actions workflows,
environments, and branch protection may be required. Do not grant repository
deletion unless an independently approved rollback procedure explicitly needs it.

### Multi-Token (Recommended for 100+ Repos)

GitHub's API rate limit is 5000 requests/hour per token. At scale, you'll exhaust this quickly.

```bash
export GH_TOKEN_1="ghp_token_one"
export GH_TOKEN_2="ghp_token_two"
export GH_TOKEN_3="ghp_token_three"
```

The tool auto-detects `GH_TOKEN_1` through `GH_TOKEN_19` and rotates with rate-limit awareness. Check status:

```bash
ado2gh token-status --config migration.yaml
```

### GitHub App Authentication

For orgs that require App-based auth instead of PATs:

```bash
export GH_APP_ID="123456"
export GH_APP_INSTALLATION_ID="78901234"
export GH_APP_PRIVATE_KEY_PATH="/path/to/private-key.pem"

# Install additional dependencies
pip install cryptography PyJWT
```

The App must have these permissions:
- Repository: Administration (Read & Write)
- Repository: Contents (Read & Write)
- Repository: Environments (Read & Write)
- Repository: Metadata (Read)
- Repository: Workflows (Read & Write)
- Organization: Members (Read)
- Organization: Team discussions (Read & Write)

### Optional LLM Provider

Deterministic pipeline conversions do not require a provider. For optional
planner-classified ambiguity proposals, bind the non-secret provider identity
in `migration.yaml`:

```yaml
global:
  pipeline_conversion:
    llm_provider: openai-responses
    llm_model: gpt-5.6
    llm_base_url: https://api.openai.com/v1
    llm_organization: ""
    llm_project: ""
    llm_api_key_env: OPENAI_API_KEY
```

Only the key value comes from the environment:

```bash
export OPENAI_API_KEY="..."
```

Never place the provider key in `migration.yaml`. Legacy routing environment
variables may repeat approved values but cannot select or alter them. LLM
output is proposal evidence only; an exact schema-v2 content approval and a new
organization plan are required before a proposed fragment can execute.

`ADO_ORG_URL`, configured `ado_org_url`, `gh_api_url`, `gh_web_url`, and
`OPENAI_BASE_URL` must be absolute HTTPS URLs. ADO and GitHub URL fields also
reject credentials, queries, and fragments. Plain HTTP endpoints are rejected,
including for internal servers.
For GHES, configure both `gh_api_url: https://HOST/api/v3` and
`gh_web_url: https://HOST`; their authorities must agree.

---

## 4. Migration Configuration

### Edit `migration.yaml`

```yaml
global:
  ado_org_url: "https://dev.azure.com/YOUR_ORG"   # or set ADO_ORG_URL env var
  gh_org: "your-github-org"
  parallel: 4
  pipeline_parallel: 12
  execution_lease_seconds: 300
  migration_strategy: mirror # "mirror" or "gei"
  default_scopes:
    - repo
    - pipelines
    - branch_policies

  mapping:
    target_org: "your-github-org"
    strategy: project-prefix
    existing_target_policy: fail
    allow_nonempty_target: false
    preflight_targets: true
    allowed_target_visibilities: [private, internal]

  pipeline_delivery:
    mode: pull_request
    branch: "ado2gh/migrated-workflows"

  validation:
    max_repair_attempts: 1

  pipeline_conversion:
    llm_provider: disabled
    llm_model: gpt-5.6
    llm_base_url: https://api.openai.com/v1
    llm_api_key_env: OPENAI_API_KEY
    require_permissions: true
    require_pinned_action_sha: true
    forbid_remote_script_execution: true
    # manual_approval_manifest: pipeline-manual-approvals.yaml
    # action_pins: {}

  cleanup:
    allow_disable_pipelines: true
    allow_redirect: false
    allow_archive: false
```

See the annotated repository-level [`migration.yaml`](../migration.yaml). Wiki
and secret scopes produce review-required exports/manifests; add them only when
that external workflow is planned.

The executor uses renewable cross-process plan and task leases. Keep the state
database on reliable local durable storage, share the same file when resuming,
and do not run one plan against copied databases. Action pin overrides and
manual approval manifests are content-addressed plan policy; adding either
requires a new plan and approval. See the
[pipeline guide](PIPELINE_TRANSFORMATION_GUIDE.md#approved-action-catalog).

Access policy is explicit per repository. `team_mapping` maps a source team to
`{github_team, permission}`, where permission is `pull`, `triage`, `push`,
`maintain`, or `admin`. The separate `access_policy_approved` flag defaults to
false because full-organization discovery cannot infer ADO ACL equivalence;
false keeps validation in review and blocks ADO cleanup.

### Strategy Selection

| Factor | Use `mirror` | Use `gei` |
|---|---|---|
| Just code + branches | Yes | Yes |
| Need PR history on GH | No | **Yes** |
| Need issue history on GH | No | **Yes** |
| Fastest execution | **Yes** | Slower (queued) |
| Works without GH CLI | **Yes** | No |
| Handles LFS | **Yes** (auto) | Partial |

---

## 5. Verify Setup

```bash
# Check CLI works
ado2gh --version

# Check token status
ado2gh token-status --config migration.yaml

# Test ADO connectivity
ado2gh discover --config migration.yaml --output test_discovery.yaml

# Create the immutable plan and exercise GitHub preflight access
ado2gh agent plan --config migration.yaml --output output/pev_plan.json

# Isolated preview; expect dry_run_passed, dry_run_needs_review, or
# dry_run_failed. It does not create resumable live state.
ado2gh agent run \
  --config migration.yaml \
  --plan output/pev_plan.json \
  --dry-run
```

Plan schema v7 binds the canonical ADO/GitHub API origins and immutable
organization identities in addition to source/target snapshots. Durable state
uses schema v13. Manual pipeline approvals use schema v2. The isolated preview
performs nested pipeline conversion and
local validation but suppresses remote writes and live receipts.

Do not use `run`, `phase run`, `pipelines retry-failed`, or `push-workflows` for
live migration. v6 accepts those compatibility commands only with `--dry-run`;
all production writes go through the approved `agent run` plan.

---

## 6. Directory Structure After Migration

```
ADO2GH/
├── migration.yaml              # Your config
├── pipeline-manual-approvals.yaml # Optional content-addressed review records
├── migration_phase.yaml        # Generated by phase assign
├── migration_state.db          # SQLite state (auto-created)
├── output/
│   ├── pev_plan.json            # Immutable reviewed organization plan
│   ├── pev_validation.csv       # Plan-bound validation report
│   ├── workflows/              # Generated GitHub Actions YAML
│   │   └── {gh_org}/{gh_repo}/.github/workflows/
│   ├── wikis/                  # Exported wiki pages
│   │   └── {gh_org}/{gh_repo}/
│   ├── secrets/                # Secrets mapping manifests
│   │   └── {gh_org}/{gh_repo}/secrets_mapping.json
│   ├── pipeline_readiness.csv  # Readiness assessment
│   └── service_connection_manifest.json
├── migration_report.html       # Final report
├── validation_report.csv       # Post-migration validation
└── failed_repos_*.txt          # Auto-generated retry lists
```
