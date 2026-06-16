#!/usr/bin/env bash
# Lightweight local agent stack (accelerator + agent only).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export ADO2GH_CONFIG="${ADO2GH_CONFIG:-$ROOT/migration.yaml}"
export ADO2GH_STORAGE_BACKEND=sqlite
export ADO2GH_SQLITE_PATH="$ROOT/migration_state.db"
export ADO2GH_DATA_DIR="$ROOT/data"
export ADO2GH_LIGHTWEIGHT_MODE=true
export LLM_PROVIDER=stub
export ADO2GH_AUTH_ENABLED=false
export ADO2GH_LOCAL_PROFILE=lightweight

echo "Starting accelerator on :8080 and agent on :8090 ..."
python -m uvicorn services.accelerator_api.main:app --host 0.0.0.0 --port 8080 &
sleep 2
export ACCELERATOR_URL=http://localhost:8080
python -m uvicorn services.agent.main:app --host 0.0.0.0 --port 8090
