#!/usr/bin/env bash
# Migration wrapper — runs the mandatory + recommended ado2gh commands in order.
#
# Usage:
#   scripts/migrate.sh                              # uses in/repo_map.txt by default
#   scripts/migrate.sh in/my_repos.txt              # specify input file
#   scripts/migrate.sh in/my_repos.txt --no-push    # skip push-workflows step
#   scripts/migrate.sh in/my_repos.txt --dry-run    # phase run is dry-run
#
# Prerequisites:
#   1. .env file at project root with ADO_PAT, ADO_ORG_URL, GH_TOKEN exported
#   2. migration.yaml configured for your ADO org / GitHub destination
#   3. ado2gh installed: pip install -e .

set -euo pipefail

# ── Args ────────────────────────────────────────────────────────────────
# Handle -h/--help before positional parsing.
case "${1:-}" in
  -h|--help) sed -n '2,13p' "$0" | sed 's/^# \?//'; exit 0 ;;
esac

INPUT_FILE="${1:-in/repo_map.txt}"
shift || true

NO_PUSH=0
NO_VALIDATE=0
DRY_RUN=0
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --no-push)     NO_PUSH=1 ;;
    --no-validate) NO_VALIDATE=1 ;;
    --dry-run)     DRY_RUN=1 ;;
    --force)       FORCE=1 ;;
    -h|--help)
      sed -n '2,13p' "$0" | sed 's/^# \?//'
      exit 0 ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

CONFIG="migration.yaml"
PHASE_CONFIG="migration_phase.yaml"

# ── Pretty output ──────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
step() { echo -e "\n${BOLD}${CYAN}== Step $1: $2 ==${NC}"; }
ok()   { echo -e "${GREEN}OK${NC}"; }
fail() { echo -e "${RED}FAILED at step $1: $2${NC}" >&2; exit 1; }

# ── Pre-flight ─────────────────────────────────────────────────────────
[[ -f .env            ]] || fail 0 ".env not found — see README §Environment Variables"
[[ -f "$CONFIG"       ]] || fail 0 "$CONFIG not found"
[[ -f "$INPUT_FILE"   ]] || fail 0 "input file '$INPUT_FILE' not found"

# shellcheck disable=SC1091
source .env

# Force UTF-8 so log lines with arrows render on Windows cp1252 consoles.
export PYTHONIOENCODING=utf-8

REPO_COUNT=$(grep -cv -E '^\s*(#|$)' "$INPUT_FILE" || true)
echo -e "${BOLD}Migration wrapper${NC}"
echo "  input:   $INPUT_FILE  ($REPO_COUNT repo(s))"
echo "  config:  $CONFIG"
echo "  dry-run: $DRY_RUN"

# ── 1. Pipeline inventory ─────────────────────────────────────────────
step 1 "Pipeline inventory"
python -m ado2gh pipelines inventory -c "$CONFIG" --input "$INPUT_FILE" || fail 1 "inventory"
ok

# ── 2. Risk score + phase assign ──────────────────────────────────────
step 2 "Risk-score and assign to phases"
python -m ado2gh phase assign -c "$CONFIG" --input "$INPUT_FILE" || fail 2 "phase assign"
ok

# Auto-detect which phases have repos in this run, in PHASE_ORDER.
PHASES_TO_RUN=$(python - <<'PY'
import yaml
with open("migration_phase.yaml", encoding="utf-8") as f:
    data = yaml.safe_load(f) or {}
order = ["poc", "pilot", "wave1", "wave2", "wave3"]
seen = {p: False for p in order}
for w in data.get("waves", []):
    p = w.get("phase", "")
    if p in seen and w.get("repos"):
        seen[p] = True
print(" ".join(p for p in order if seen[p]))
PY
)

if [[ -z "$PHASES_TO_RUN" ]]; then
  fail 2 "no phases populated — phase assign produced no waves"
fi
echo "  phases to run: $PHASES_TO_RUN"

# ── 3. Phase run for each populated phase ─────────────────────────────
DRY_FLAG=""
[[ "$DRY_RUN" -eq 1 ]] && DRY_FLAG="--dry-run"

# Force the first phase (skip prior-phase gate) since earlier phases may be empty.
FORCE_FLAG="--force"
[[ "$FORCE" -eq 1 ]] && FORCE_FLAG="--force"

for PHASE in $PHASES_TO_RUN; do
  step 3 "phase run --phase $PHASE"
  if ! python -m ado2gh phase run -c "$PHASE_CONFIG" --phase "$PHASE" $FORCE_FLAG $DRY_FLAG; then
    echo -e "${RED}phase $PHASE failed/blocked — stopping pipeline.${NC}" >&2
    echo "Failed-repo lists are at: output/failed_repos_${PHASE}.txt + .csv"
    exit 1
  fi
  # After the first phase, subsequent phases should gate-progress naturally.
  FORCE_FLAG=""
done
ok

# Skip downstream steps in dry-run since nothing was actually pushed.
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo -e "\n${CYAN}Dry-run complete — skipping validate + push-workflows + report.${NC}"
  exit 0
fi

# ── 4. Validate (optional) ────────────────────────────────────────────
if [[ "$NO_VALIDATE" -eq 0 ]]; then
  step 4 "Validate (commit SHA verification)"
  python -m ado2gh validate -c "$PHASE_CONFIG" || echo -e "${RED}validate reported failures — continuing.${NC}"
  ok
fi

# ── 5. Push workflows to destination repos as PRs (optional) ──────────
if [[ "$NO_PUSH" -eq 0 ]]; then
  step 5 "push-workflows (open PR on each destination)"
  python -m ado2gh push-workflows -c "$CONFIG" --input "$INPUT_FILE" || echo -e "${RED}push-workflows reported failures — continuing.${NC}"
  ok
fi

# ── 6. Report ─────────────────────────────────────────────────────────
step 6 "Generate HTML report"
python -m ado2gh report -c "$PHASE_CONFIG" --format html || true
ok

echo -e "\n${BOLD}${GREEN}Migration wrapper complete.${NC}"
echo "  state:   migration_state.db"
echo "  phase:   $PHASE_CONFIG"
echo "  output/  generated workflows, secrets manifests, validation, failed-repo lists"
echo "  report:  migration_report.html"
