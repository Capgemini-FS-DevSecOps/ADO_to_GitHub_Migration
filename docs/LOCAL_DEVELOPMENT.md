# Local development with SQLite

Run the full migration platform on your laptop **without PostgreSQL**. SQLite is the default state backend for local development.

| Mode | Compose / command | UI | Agent | Postgres |
|------|-------------------|----|-------|----------|
| **Full stack (recommended)** | `docker compose up --build` | ✓ | ✓ | No |
| **Native processes** | `scripts/run-local.ps1` | ✓ | ✓ | No |
| **CLI only** | `pip install -e .` + `ado2gh` | — | — | No |
| **Agent IDE (minimal)** | `docker compose -f docker-compose.lightweight.yml up` | — | ✓ | No |
| **Production-like** | `docker compose -f docker-compose.yml -f docker-compose.prod.yml up` | ✓ | ✓ | Yes |

---

## Prerequisites

- **Docker Desktop** (for compose workflows) or **Python 3.9+** and **Node.js 20+** (for native)
- **Git** on `PATH` (required for repo mirroring)
- Optional: **GitHub CLI** + `gh-gei` if using the GEI migration strategy

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
.\scripts\run-local.ps1
```

**What it does:** installs Python deps (`requirements.txt` + `pip install -e ".[api]"`), npm deps on first run, then starts accelerator (:8080), agent (:8090), and Next.js UI (:3000) in separate terminal windows. SQLite at `./migration_state.db` and `./data`.

**Recommended:** use a venv so Windows does not pick the Store `python` stub:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
.\scripts\run-local.ps1
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
.\scripts\run-ui.ps1
```

---

## 4. CLI only (SQLite)

For headless migration workflows without the web console:

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[api,dev]"

export ADO2GH_STORAGE_BACKEND=sqlite
export ADO2GH_SQLITE_PATH=./migration_state.db
export ADO_PAT=... ADO_ORG_URL=... GH_TOKEN=...

ado2gh discover --config migration.yaml
ado2gh phase assign --config migration.yaml --input in/repo_map.txt
ado2gh phase run --phase poc --config migration_phase.yaml --dry-run
```

See [EXECUTION_MANUAL.md](EXECUTION_MANUAL.md) and [COMMAND_REFERENCE.md](COMMAND_REFERENCE.md) for the full CLI workflow.

---

## 5. Lightweight agent stack

For IDE/MCP agent development without UI, Redis, or worker:

```bash
docker compose -f docker-compose.lightweight.yml up --build
```

Or natively:

```powershell
.\scripts\run-local-agent.ps1
```

---

## 6. SQLite state location

| Run mode | Database path |
|----------|---------------|
| Docker Compose | Volume `ado2gh-data` → `/app/data/migration_state.db` |
| Native / CLI | `./migration_state.db` (or `ADO2GH_SQLITE_PATH`) |
| `run-local.ps1` | `./migration_state.db` + `./data/` |

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
| Bootstrap | Optional | `/login?bootstrap=1` for first admin |

See [SETUP_GUIDE.md](SETUP_GUIDE.md) for PAT scopes and [ARCHITECTURE.md](ARCHITECTURE.md) for deployment modes.

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
