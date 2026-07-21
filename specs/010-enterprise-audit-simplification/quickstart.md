# Quickstart: Enterprise Audit & Framework Simplification

**Feature**: 010-enterprise-audit-simplification | **Date**: 2026-06-24

## Prerequisites

- Python 3.9+ with `pip install -e ".[dev]"` completed
- Node.js 18+ with `apps/migration-ui/` dependencies installed (`npm install` in that directory)
- Docker Compose v2+ (for Docker validation scenarios)
- `git` on PATH

## Validation Scenarios

### VS-1: Dead Code Removal Verification

**Prerequisites**: Repository in pre-audit state

**Steps**:
1. Run `python -c "import ast, os; ..."` to verify no orphaned modules remain (or manually verify the import graph)
2. Confirm `ado2gh/__pycache__/ib-ai-agent/` directory does not exist on disk
3. Confirm `cloud_credentials.json`, `llm_models.json`, `ui_settings.json` are in `.gitignore`
4. Run `git ls-files cloud_credentials.json llm_models.json ui_settings.json` — should return empty

**Expected**: No orphaned Python modules, no stray venv, root-level artifacts gitignored.

### VS-2: State Layer Consolidation

**Prerequisites**: Consolidated state layer deployed

**Steps**:
1. Run `pytest tests/ -k state -x` — all state-related tests pass
2. Verify `ado2gh/state/dynamodb_db.py` does not exist
3. Run `python -c "from ado2gh.state.factory import create_state_db; db = create_state_db(); print(type(db))"` with `ADO2GH_STORAGE_BACKEND=sqlite` — should create SQLite instance
4. Repeat with `ADO2GH_STORAGE_BACKEND=postgres` (requires PostgreSQL running) — should create PostgreSQL instance
5. Count lines: `wc -l ado2gh/state/*.py` — total should be ≤60% of original combined total

**Expected**: All tests pass, DynamoDB removed, both backends work, line count reduced ≥40%.

### VS-3: Monolithic File Decomposition

**Prerequisites**: All files decomposed

**Steps**:
1. Run `find ado2gh/ services/ -name "*.py" -not -path "*/__pycache__/*" -not -name "__init__.py" -not -path "*/test*" | xargs wc -l | sort -rn | head -20` — no file should exceed 800 lines (excluding tests, `__init__.py`)
2. Run `pytest tests/ -x` — all tests pass
3. Verify `from ado2gh.agents.session_orchestrator import *` still works (re-export check)
4. Verify `from ado2gh.api.pipeline_runner import *` still works (re-export check)

**Expected**: No file >800 lines, all tests pass, public API preserved.

### VS-4: Docker Compose Consolidation

**Prerequisites**: Consolidated compose files

**Steps**:
1. Verify only `docker-compose.yml` and `docker-compose.prod.yml` exist in repo root
2. Run `docker compose up --build` — accelerator and agent start (lightweight mode)
3. Run `docker compose --profile default up --build` — full stack starts (accelerator, agent, redis, worker, web)
4. Run `docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile default up --build` — PostgreSQL backend used with auth enabled

**Expected**: 2 compose files, profiles work correctly, prod override applies PostgreSQL.

### VS-5: Enterprise Readiness Tooling

**Prerequisites**: ruff, mypy, pre-commit configured

**Steps**:
1. Run `ruff check ado2gh/` — executes without configuration errors (may report baseline violations)
2. Run `mypy ado2gh/` — executes without configuration errors (may report baseline type errors)
3. Run `pytest --cov=ado2gh --cov-fail-under=85` — coverage gate is enforced
4. Run `pre-commit run --all-files` — hooks execute (ruff, formatting, large-file check)
5. Check `.env.example` contains every env var referenced in codebase: `grep -r "os.environ\|os.getenv" ado2gh/ services/ | sed 's/.*getenv\(["\x27]\([^"\x27]*\).*/\1/' | sort -u | while read v; do grep -q "^$v=" .env.example || echo "MISSING: $v"; done`

**Expected**: All tools execute, coverage gate enforced, no missing env vars.

### VS-6: Spec Lifecycle

**Prerequisites**: Specs consolidated

**Steps**:
1. Verify `specs/archive/` directory exists with specs 002 and 005
2. Verify `specs/README.md` exists and lists all specs with status
3. Verify no FR from archived specs is lost — cross-reference archived spec FRs with implementation files or active specs

**Expected**: Implemented specs archived, README index complete, no lost requirements.

### VS-7: Module Structure

**Prerequisites**: Module restructuring complete

**Steps**:
1. Verify `ado2gh/tools/` directory does not exist
2. Verify `ado2gh/cli.py` does not exist (wrapper removed)
3. Verify `ado2gh/infra/` directory does not exist
4. Verify `ado2gh/api/llm/` subpackage exists with `__init__.py`
5. Verify `ado2gh/api/credentials/` subpackage exists with `__init__.py`
6. Run `pytest tests/ -x` — all tests pass
7. Run `ado2gh --help` — CLI works

**Expected**: No single-file directories, no wrapper files, subpackages created, all tests pass, CLI works.

### VS-8: Scripts Cleanup

**Prerequisites**: Scripts reduced

**Steps**:
1. Verify only `scripts/dev/` directory exists (2-3 files)
2. Verify no scripts that duplicate CLI commands remain
3. Run remaining scripts to confirm they work
4. Check `CLAUDE.md` and `README.md` references match actual file structure

**Expected**: Minimal scripts/dev/, no CLI duplicates, docs accurate.

### VS-9: Structural Change Log

**Prerequisites**: All structural changes complete

**Steps**:
1. Verify `docs/STRUCTURAL_CHANGELOG.md` exists
2. Verify every structural change (file move, rename, split, merge, deletion, gitignore) has an entry
3. Verify every deletion entry has `Verified = yes` and `Test Status = pass`
4. Verify every entry has a `Timestamp` field with a valid ISO 8601 timestamp

**Expected**: Complete change log, all deletions verified.

### VS-10: Full Regression

**Prerequisites**: All simplification work complete

**Steps**:
1. Run `pytest tests/ -x --cov=ado2gh --cov-fail-under=85` — all tests pass, coverage ≥85%
2. Run `docker compose --profile default up --build` — full stack starts and health checks pass
3. Run `ado2gh discover --config migration.yaml --dry-run` — CLI discovery works
4. Open `http://localhost:3000` — UI loads with consolidated pages

**Expected**: Zero regressions, full functionality preserved.
