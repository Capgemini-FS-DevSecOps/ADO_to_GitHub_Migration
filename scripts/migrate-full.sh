#!/usr/bin/env bash
# Full migration — runs every ado2gh command in order, including the
# optional scan/assessment commands that the lighter migrate.sh skips.
#
# Usage:
#   scripts/migrate-full.sh                              # uses in/repo_map.txt by default
#   scripts/migrate-full.sh in/my_repos.txt              # specify input file
#   scripts/migrate-full.sh in/my_repos.txt --no-push    # skip push-workflows step
#   scripts/migrate-full.sh in/my_repos.txt --dry-run    # phase run is dry-run
#
# Difference vs scripts/migrate.sh:
#   + token-status      (sanity-check GH auth + rate limit)
#   + pipeline-readiness (auto/assisted/manual breakdown + effort estimate)
#   + service-connections (ops manifest of secrets needed on destination)
#   + phase plan        (preview before executing)
#   + report --format json   (in addition to html)
#
# Prerequisites:
#   1. .env file at project root with ADO_PAT, ADO_ORG_URL, GH_TOKEN exported
#   2. migration.yaml configured for your ADO org / GitHub destination
#   3. ado2gh installed: pip install -e .
#   4. Optional: scripts/discover.sh has been run once and the input file
#      built from the resulting repos_template.txt

set -euo pipefail

# ── Args ────────────────────────────────────────────────────────────────
case "${1:-}" in
  -h|--help) sed -n '2,22p' "$0" | sed 's/^# \?//'; exit 0 ;;
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
      sed -n '2,22p' "$0" | sed 's/^# \?//'
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
[[ -f .env          ]] || fail 0 ".env not found — see README §Environment Variables"
[[ -f "$CONFIG"     ]] || fail 0 "$CONFIG not found"
[[ -f "$INPUT_FILE" ]] || fail 0 "input file '$INPUT_FILE' not found"

# shellcheck disable=SC1091
source .env
export PYTHONIOENCODING=utf-8

REPO_COUNT=$(grep -cv -E '^\s*(#|$)' "$INPUT_FILE" || true)

# ── Step 0. Token health ───────────────────────────────────────────────
step 0 "Token status"
python -m ado2gh token-status -c "$CONFIG" || fail 0 "token-status"
ok

# ── Step 1. Pipeline inventory ─────────────────────────────────────────
step 1 "Pipeline inventory"
python -m ado2gh pipelines inventory -c "$CONFIG" --input "$INPUT_FILE" || fail 1 "inventory"
ok

# ── Step 2. Pipeline readiness assessment ──────────────────────────────
step 2 "Pipeline readiness (auto/assisted/manual + effort estimate)"
python -m ado2gh pipeline-readiness -c "$CONFIG" --input "$INPUT_FILE" || fail 2 "pipeline-readiness"
ok

# ── Step 3. Service connections manifest ───────────────────────────────
step 3 "Service connections manifest"
python -m ado2gh service-connections -c "$CONFIG" --input "$INPUT_FILE" || fail 3 "service-connections"
ok

# ── Step 4. Risk-score and assign to phases ────────────────────────────
step 4 "Risk-score and assign to phases"
python -m ado2gh phase assign -c "$CONFIG" --input "$INPUT_FILE" || fail 4 "phase assign"
ok

# Auto-detect populated phases.
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
  fail 4 "no phases populated — phase assign produced no waves"
fi

# ── Per-phase output base = output/<first-phase>_run/ ─────────────────
FIRST_PHASE=$(echo "$PHASES_TO_RUN" | awk '{print $1}')
export ADO2GH_OUTPUT_DIR="${ADO2GH_OUTPUT_DIR:-output/${FIRST_PHASE}_run}"
mkdir -p "$ADO2GH_OUTPUT_DIR"

RUN_TS=$(date -u +%Y%m%dT%H%M%SZ)
RUN_DIR="$ADO2GH_OUTPUT_DIR/runs/$RUN_TS"
mkdir -p "$RUN_DIR"

echo -e "\n${BOLD}Migration full-run config${NC}"
echo "  input:        $INPUT_FILE  ($REPO_COUNT repo(s))"
echo "  output base:  $ADO2GH_OUTPUT_DIR"
echo "  run dir:      $RUN_DIR"
echo "  phases:       $PHASES_TO_RUN"
echo "  dry-run:      $DRY_RUN"

# ── Step 5. Phase plan (preview) ───────────────────────────────────────
step 5 "Phase plan (preview)"
for PHASE in $PHASES_TO_RUN; do
  python -m ado2gh phase plan -c "$PHASE_CONFIG" --phase "$PHASE" || fail 5 "phase plan $PHASE"
done
ok

# ── Step 6. Phase run for each populated phase ─────────────────────────
DRY_FLAG=""
[[ "$DRY_RUN" -eq 1 ]] && DRY_FLAG="--dry-run"
FORCE_FLAG="--force"
[[ "$FORCE" -eq 1 ]] && FORCE_FLAG="--force"

for PHASE in $PHASES_TO_RUN; do
  step 6 "phase run --phase $PHASE"
  if ! python -m ado2gh phase run -c "$PHASE_CONFIG" --phase "$PHASE" $FORCE_FLAG $DRY_FLAG; then
    echo -e "${RED}phase $PHASE failed/blocked — stopping pipeline.${NC}" >&2
    echo "Failed-repo lists are at: $ADO2GH_OUTPUT_DIR/failed_repos_${PHASE}.txt + .csv"
    exit 1
  fi
  FORCE_FLAG=""
done
ok

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo -e "\n${CYAN}Dry-run complete — skipping validate + push-workflows + report.${NC}"
  exit 0
fi

# ── Step 7. Validate ───────────────────────────────────────────────────
if [[ "$NO_VALIDATE" -eq 0 ]]; then
  step 7 "Validate (commit SHA verification)"
  python -m ado2gh validate -c "$PHASE_CONFIG" \
    -o "$RUN_DIR/validation_report.csv" \
    || echo -e "${RED}validate reported failures — continuing.${NC}"
  ok
fi

# ── Step 8. Push workflows ─────────────────────────────────────────────
if [[ "$NO_PUSH" -eq 0 ]]; then
  step 8 "push-workflows (open PR on each destination)"
  python -m ado2gh push-workflows -c "$CONFIG" --input "$INPUT_FILE" \
    || echo -e "${RED}push-workflows reported failures — continuing.${NC}"
  ok
fi

# ── Step 9. Reports (HTML + JSON) ──────────────────────────────────────
step 9 "Generate HTML + JSON reports"
python -m ado2gh report -c "$PHASE_CONFIG" --format html \
  --output "$RUN_DIR/migration_report.html" || true
python -m ado2gh report -c "$PHASE_CONFIG" --format json \
  --output "$RUN_DIR/migration_report.json" || true
ok

# ── Audit copies into RUN_DIR ─────────────────────────────────────────
for f in "$ADO2GH_OUTPUT_DIR"/failed_repos_*.txt "$ADO2GH_OUTPUT_DIR"/failed_repos_*.csv; do
  [[ -f "$f" ]] && cp "$f" "$RUN_DIR/" 2>/dev/null || true
done
[[ -f "$PHASE_CONFIG" ]] && cp "$PHASE_CONFIG" "$RUN_DIR/migration_phase.yaml" || true

echo -e "\n${BOLD}${GREEN}Full migration complete.${NC}"
echo "  state:           migration_state.db"
echo "  output base:     $ADO2GH_OUTPUT_DIR"
echo "  per-run audit:   $RUN_DIR"
echo "  bundle includes: discovery (if discover ran), pipeline_readiness, service_connection_manifest,"
echo "                   workflows/, secrets/, wikis/, validation, html+json reports, failed-repos lists"
