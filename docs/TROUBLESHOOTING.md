# Troubleshooting Guide

Common issues and solutions when running ADO-to-GitHub migrations.

---

## Authentication Errors

### `ADO_ORG_URL + ADO_PAT required`

```bash
export ADO_PAT="your-pat-here"
export ADO_ORG_URL="https://dev.azure.com/YOUR_ORG"
```

### `GH_TOKEN required`

```bash
export GH_TOKEN="ghp_your_token"
# OR for multi-token:
export GH_TOKEN_1="ghp_token_one"
```

### ADO PAT authentication failures

- Verify PAT hasn't expired at `https://dev.azure.com/YOUR_ORG/_usersSettings/tokens`
- Verify the PAT has the required scopes (see [Setup Guide](SETUP_GUIDE.md#required-scopes))
- For orgs with IP allowlisting, ensure your machine's IP is allowed

### GitHub 401/403 errors

- Classic token: needs `repo`, `admin:org`, `workflow` scopes
- Fine-grained token: needs Read/Write for Administration, Contents, Workflows, Environments
- Token may be expired — create a new one
- For GHEC with SSO: authorize the token for the organization

---

## Rate Limiting

### `All tokens rate-limited. Waiting Xs for reset...`

GitHub API allows 5000 requests/hour per token. Solutions:

1. **Add more tokens** — the tool auto-rotates `GH_TOKEN_1` through `GH_TOKEN_19`
2. **Check token health**: `ado2gh token-status --config migration.yaml`
3. **Reduce parallelism** — lower `parallel` in config (fewer concurrent API calls)
4. **Use GitHub App auth** — App tokens get higher rate limits in some configurations

### ADO rate limiting (HTTP 429)

The tool has built-in retry with exponential backoff. If persistent:
- Reduce `pipeline_parallel` setting
- Run during off-hours when other ADO consumers are less active

---

## Git Migration Failures

### `git clone --mirror failed`

- **Check PAT scope**: needs `Code (Read)`
- **Large repos**: increase timeout in config or check disk space
- **Network**: verify connectivity to `dev.azure.com`
- **Firewall**: some corporate networks block git protocol

### `git push --mirror failed`

- **GitHub repo already has content**: delete and recreate, or use rollback first
- **Protected branches**: the mirror push may conflict with branch protection — ensure protection isn't set before migration
- **Size limit**: GitHub has a 100MB file limit. Large files need Git LFS
- **Token scope**: needs `repo` scope on GitHub

### LFS push failed

```bash
# Verify git-lfs is installed
git lfs install

# If "git: 'lfs' is not a git command"
# Install from https://git-lfs.github.com/
```

If LFS push partially fails, the migration continues with a warning. Re-run:
```bash
ado2gh phase run --phase poc --config migration_phase.yaml
```

---

## GEI Migration Failures

### `gh gei` not found

```bash
gh extension install github/gh-gei
gh gei --version
```

### GEI blob storage errors

GEI requires blob storage configured on the ADO side:
- AWS S3 bucket with appropriate permissions, or
- Azure Blob Storage container

See: https://docs.github.com/en/migrations/using-github-enterprise-importer

### GEI timeout

GEI migrations are queued server-side. The `--wait` flag keeps the CLI waiting. For very large repos, this can timeout. Check migration status:

```bash
gh gei wait-for-migration --migration-id <ID>
```

---

## Phase & Gate Issues

### `Gate BLOCKED — Phase 'poc' gate has not passed yet`

The previous phase must pass its gate before the next phase can start.

```bash
# Check what's failing
ado2gh phase gate-check --phase poc --config migration_phase.yaml

# Fix failures and re-run (resumes automatically)
ado2gh phase run --phase poc --config migration_phase.yaml

# Or override with documented reason
ado2gh phase gate-check --phase poc --override --reason "2 repos excluded by design"
```

### Interrupted run

Just re-run the same command. The tool tracks completed batches in SQLite and resumes from the last checkpoint.

```bash
# This is safe to run multiple times
ado2gh phase run --phase wave1 --config migration_phase.yaml
```

### `No waves for phase wave1`

The `migration_phase.yaml` doesn't have repos assigned to this phase. Re-run:

```bash
ado2gh phase assign --config migration.yaml --output migration_phase.yaml
```

---

## Validation Failures

### HEAD commit SHA mismatch

This means the code didn't fully transfer. Possible causes:
- Someone pushed to ADO after migration
- Git mirror had network issues
- LFS objects not fully transferred

**Fix:** Re-run the phase. The tool will re-mirror repos that failed.

### Branch count mismatch

Some branches may have been filtered during mirror. This is usually a `WARN` not `FAIL`. Common for repos with many stale branches.

### Workflows missing

Workflow files are generated locally in `output/workflows/`. They're NOT automatically pushed to the GitHub repo. You need to commit them:

```bash
cd output/workflows/{gh_org}/{gh_repo}
git add .github/workflows/
git commit -m "Add migrated workflows from ADO"
git push
```

---

## Rollback Issues

### Want to undo only branch protection, not delete the repo

```bash
ado2gh rollback --wave 1 --scopes branch_policies --config migration_phase.yaml
```

### Rollback failed — repo doesn't exist

The repo may have already been deleted or never created. The tool logs this and continues.

### Want to retry specific repos

```bash
# See what failed
ado2gh export-failed --phase wave1 --output retry.txt

# Edit retry.txt if needed, then re-run the phase
ado2gh phase run --phase wave1 --config migration_phase.yaml
```

---

## ADO Cleanup Issues

### `ado-cleanup` failed to disable a pipeline

The PAT needs `Build (Read & Execute)` scope. Some pipelines may be locked by retention policies.

### `MIGRATION_NOTICE.md` push failed

The PAT needs `Code (Read & Write)` scope. The repo may be read-only or have policies preventing direct pushes to the default branch.

### Archive failed

ADO repo archival requires project-level admin permissions. Verify the PAT owner has the necessary role.

---

## Performance Tuning

| Setting | Default | When to Change |
|---|---|---|
| `parallel` | 4 | Increase to 6-8 if network is fast and tokens are plentiful |
| `pipeline_parallel` | 12 | CPU-bound transform; 12-16 safe on modern machines |
| `batch_size` | varies by phase | Smaller = more checkpoints, larger = fewer DB writes |

### Memory usage

The tool is lightweight — most operations are I/O bound (network + disk). The SQLite DB stays small even at 5000 repos.

### Disk space

Mirror clones are created in temp directories and cleaned up after push. A single large repo (5GB+) needs that much temp space. Ensure adequate disk on the machine running the tool.
