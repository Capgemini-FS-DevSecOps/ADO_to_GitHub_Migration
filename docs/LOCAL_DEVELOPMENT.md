# Local development with SQLite

Run the full migration platform on your laptop **without PostgreSQL**. SQLite is the default state backend for local development.

| Mode | Compose / command | UI | Agent | Postgres |
|------|-------------------|----|-------|----------|
| **Full stack (recommended)** | `docker compose up --build` | ✓ | ✓ | No |
| **Native processes** | `scripts/dev/run-local-agent.ps1` + `scripts/dev/run-ui.ps1` | ✓ | ✓ | No |
| **CLI only** | `pip install -e .` + `ado2gh` | — | — | No |
| **Agent IDE (minimal)** | `scripts/dev/run-local-agent.ps1` | — | ✓ | No |
| **Production-like** | `docker compose -f docker-compose.yml -f docker-compose.prod.yml up` | ✓ | ✓ | Yes |

---

## Prerequisites

- **Docker Desktop** (for compose workflows) or **Python 3.11+** and **Node.js 20+** (for native)
- **Git** on `PATH` (required for repo mirroring)
- Optional: **GitHub CLI** + `gh-ado2gh` if using the GEI migration strategy

---

## 1. Configure environment

Copy the example env file and set credentials:

```bash
cp .env.example .env
```

Edit `.env`:

```bash
ADO2GH_STORAGE_BACKEND=sqlite
ADO2GH_SQLITE_PATH=./migration_state.db
ADO2GH_DATA_DIR=./data

ADO_ORG_URL=https://dev.azure.com/YOUR_ORG
ADO_PAT=your-ado-pat
GH_TOKEN=your-github-token
```

Docker Compose reads `ADO_ORG_URL`, `ADO_PAT`, and `GH_TOKEN` from the shell environment or a `.env` file at the repo root.

Edit `migration.yaml` with your ADO org and GitHub destination org (or rely on env vars for ADO URL).

---

## 2. Full stack with Docker (SQLite)

From the repository root:

```bash
docker compose up --build
```

This starts:

| Service | Port | Role |
|---------|------|------|
| **web** | 3000 | Next.js migration console |
| **accelerator** | 8080 | FastAPI API (profiles, discovery, pipeline runs) |
| **agent** | 8090 | PEV migration agent + MCP |
| **worker** | — | Background pipeline worker |
| **redis** | 6379 | Job queue |

State is stored in the Docker volume `ado2gh-data` at `/app/data/migration_state.db` inside containers.

### First visit

1. Open **http://localhost:3000**
2. Auth is **disabled** in the default local compose (`NEXT_PUBLIC_REQUIRE_AUTH=false`)
3. Configure a migration profile under **Settings → Profiles** (ADO PAT, GitHub org)
4. Run **Discovery** scan, then use **Agent** or **Migrate** tabs

### Rebuild after code changes

Rebuild only what changed to avoid dangling `<none>` images:

```bash
docker compose build agent web accelerator
docker compose up -d
```

Clean up old image layers:

```bash
docker image prune -f
```

### Stop

```bash
docker compose down
```

Data persists in the `ado2gh-data` volume until you remove it with `docker volume rm ado2gh-data`.

---

## 3. Native full stack (no Docker)

**Windows (PowerShell):**

```powershell
.\scripts\dev\run-local-agent.ps1   # accelerator (:8080) + agent (:8090)
.\scripts\dev\run-ui.ps1            # Next.js UI (:3000)
```

**What it does:** installs Python deps (`requirements.txt` + `pip install -e ".[api]"`), npm deps on first run, then starts the services in separate terminal windows. SQLite at `./migration_state.db` and `./data`.

**Recommended:** use a venv so Windows does not pick the Store `python` stub:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
.\scripts\dev\run-local-agent.ps1
```

### `[WinError 2] The system cannot find the file specified`

| When | Fix |
|------|-----|
| Script fails on `python` | Turn off **App execution aliases** for `python.exe` / `python3.exe`, or use `.venv` as above |
| A spawned window closes immediately | In repo root: `python -m uvicorn services.accelerator_api.main:app --port 8080` and read the error |
| `npm run dev` fails | Install [Node.js LTS](https://nodejs.org/); `cd apps\migration-ui` → `npm install` |
| Migration / git step fails | Install **Git for Windows**; ensure `git` is on `PATH` |

Set credentials in the same shell before running:

```powershell
$env:ADO_ORG_URL = "https://dev.azure.com/YOUR_ORG"
$env:ADO_PAT = "..."
$env:GH_TOKEN = "..."
```

**UI only** (API already running):

```powershell
.\scripts\dev\run-ui.ps1
```

---

## 4. CLI only (SQLite)

For headless migration workflows without the web console:

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[api,agent,dev]"

export ADO2GH_STORAGE_BACKEND=sqlite
export ADO2GH_SQLITE_PATH=./migration_state.db
export ADO_PAT=... ADO_ORG_URL=... GH_TOKEN=...

ado2gh discover --config migration.yaml
ado2gh phase assign --config migration.yaml --input in/repo_map.txt
ado2gh phase run --phase poc --config migration_phase.yaml --dry-run
```

See [EXECUTION_MANUAL.md](EXECUTION_MANUAL.md) and [COMMAND_REFERENCE.md](COMMAND_REFERENCE.md) for the full CLI workflow.

---

## 5. Agent-only stack (native)

For agent development without UI, Redis, or worker:

```powershell
.\scripts\dev\run-local-agent.ps1
```

---

## 6. SQLite state location

| Run mode | Database path |
|----------|---------------|
| Docker Compose | Volume `ado2gh-data` → `/app/data/migration_state.db` |
| Native / CLI | `./migration_state.db` (or `ADO2GH_SQLITE_PATH`) |
| `scripts/dev/run-local-agent.ps1` | `./migration_state.db` + `./data/` |

The SQLite file uses WAL mode. Safe to back up while stopped; for live backup copy `.db`, `.db-wal`, and `.db-shm` together.

Reset local state:

```bash
rm -f migration_state.db migration_state.db-wal migration_state.db-shm
docker volume rm ado2gh-data   # if using Docker
```

---

## 7. LLM models (agent chat)

The agent requires at least one enabled LLM under **Settings → LLM models**, or it runs in **degraded/stub** mode.

For local testing without cloud APIs:

- Use **Ollama** (configure in Settings → LLM models), or
- Leave stub mode for deterministic tool-chain tests only

---

## 8. Production vs local

| | Local (SQLite) | Production |
|--|----------------|------------|
| Compose | `docker compose up` | `docker compose -f docker-compose.yml -f docker-compose.prod.yml up` |
| Backend | `ADO2GH_STORAGE_BACKEND=sqlite` | `postgres` |
| Auth | Off by default | `ADO2GH_AUTH_ENABLED=true` |
| Internal token | Not needed while auth is off | `ADO2GH_INTERNAL_TOKEN=<random secret>`, the same value on the accelerator and the agent |
| TLS at the edge | Not applicable — plain HTTP | `ADO2GH_TRUSTED_PROXY=true` when a reverse proxy terminates TLS, so the session cookie takes `Secure` from that proxy's `X-Forwarded-Proto` (last hop); left unset, forwarding headers are ignored |
| Bootstrap | Optional | `/login?bootstrap=1` for first admin |

`ADO2GH_INTERNAL_TOKEN` is required whenever `ADO2GH_AUTH_ENABLED=true`. It is the shared
secret the accelerator presents on the agent's `/v1/internal/` routes, which is how an
approved live migration is resumed. If it is unset, or differs between the two services,
the agent answers 401 and approved sessions never resume. `docker-compose.prod.yml`
refuses to start without it.

See [SETUP_GUIDE.md](SETUP_GUIDE.md) for PAT scopes and [ARCHITECTURE.md](ARCHITECTURE.md) for deployment modes.

---

## 9. Tests and quality gates

Run the suite from the repository root. On Windows the virtual environment's interpreter is the reliable invocation; plain `pytest` works too once the venv is activated:

```powershell
.\.venv\Scripts\python.exe -m pytest > test-output.txt 2>&1
```

Redirect the output to a file rather than piping it, then read the file. The suite is roughly 1,000 tests and takes about 100 seconds. `pytest-cov`, `ruff` and `vulture` are installed by `pip install -e ".[api,agent,dev]"`.

Lint with the same configuration CI uses:

```bash
ruff check ado2gh/ services/
```

On top of `E`, `F`, `W` and `I`, the rule set requires Google-style docstrings (`D1`) and type annotations (`ANN`), and rejects boolean flag parameters (`FBT001`/`FBT002`), more than five parameters (`PLR0913`, `max-args = 5`), mutable default arguments (`B006`), unused arguments (`ARG`) and inconsistent returns (`RET501`-`RET503`). That second group is not enforced under `tests/`, where undocumented helpers and unused fixture arguments are the convention. Genuine exceptions carry a `# noqa` and a matching row in the exception register.

CI also runs a coverage ratchet:

```bash
pytest --cov=ado2gh --cov-fail-under=62
```

The threshold in `.github/workflows/ci.yml` is raised after each increment and never lowered, so relaxing it to make a build pass is not an option. The 85 % target is still outstanding.

Three guard tests protect the structure of the tree:

| Guard | What it enforces |
|-------|------------------|
| `tests/contract/test_public_surface_snapshot.py` | CLI commands, database tables, environment variables and HTTP routes are frozen. Update the stored snapshot deliberately when a public surface really changes |
| `tests/unit/test_file_size_limit.py` | No Python file under `ado2gh/` or `services/` exceeds 800 lines |
| `tests/unit/test_no_orphaned_modules.py` | Every module is reachable from an import; dynamically imported ones need an allowlist entry in that test |

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Port already in use | Stop other stacks or change ports in `docker-compose.yml` |
| UI can't reach API | Confirm `NEXT_PUBLIC_ACCELERATOR_URL=http://localhost:8080` |
| Empty discovery | Complete profile setup; run scan from Discovery tab |
| Many `<none>` Docker images | `docker image prune -f` after repeated `--build` runs |
| GEI fails in container | Images set `DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1` — rebuild accelerator/worker |

More: [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
