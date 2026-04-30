#!/usr/bin/env bash
# Discovery — scan the entire ADO organisation once and produce inputs.
#
# Usage:
#   scripts/discover.sh                            # output -> $ADO2GH_OUTPUT_DIR/discovery (default: output/discovery)
#   ADO2GH_OUTPUT_DIR=/path/to/dir scripts/discover.sh
#
# Outputs:
#   <output>/discovery/repos.csv          one row per repo (size, branches, last commit)
#   <output>/discovery/pipelines.csv      one row per pipeline (linked to its repo)
#   <output>/discovery/discovery.json     full structured detail
#   <output>/discovery/repos_template.txt pre-formatted input file with every discovered repo commented out
#
# After running this once, copy the lines you want to migrate from
# repos_template.txt into in/repo_map.txt (uncommenting them, optionally
# adding ::gh_org/gh_repo destination overrides), then run
# scripts/migrate.sh or scripts/migrate-full.sh.

set -euo pipefail

# ── Args ────────────────────────────────────────────────────────────────
case "${1:-}" in
  -h|--help) sed -n '2,18p' "$0" | sed 's/^# \?//'; exit 0 ;;
esac

CONFIG="migration.yaml"

# ── Pretty output ──────────────────────────────────────────────────────
GREEN='\033[0;32m'; CYAN='\033[0;36m'; RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'
step() { echo -e "\n${BOLD}${CYAN}== $1 ==${NC}"; }
fail() { echo -e "${RED}FAILED: $1${NC}" >&2; exit 1; }

# ── Pre-flight ─────────────────────────────────────────────────────────
[[ -f .env       ]] || fail ".env not found — see README §Environment Variables"
[[ -f "$CONFIG"  ]] || fail "$CONFIG not found"

# shellcheck disable=SC1091
source .env
export PYTHONIOENCODING=utf-8

OUT_BASE="${ADO2GH_OUTPUT_DIR:-output}"
DISCOVERY_DIR="$OUT_BASE/discovery"
mkdir -p "$DISCOVERY_DIR"

echo -e "${BOLD}ADO Discovery${NC}"
echo "  config:        $CONFIG"
echo "  output base:   $OUT_BASE"
echo "  discovery dir: $DISCOVERY_DIR"

# ── 1. Token health ────────────────────────────────────────────────────
step "Token status (verifies GH auth + remaining rate limit)"
python -m ado2gh token-status -c "$CONFIG" || fail "token-status"

# ── 2. Discovery scan ──────────────────────────────────────────────────
step "Discovery scan (reads ADO org, writes CSV/JSON inventory)"
python -m ado2gh discover -c "$CONFIG" -o "$DISCOVERY_DIR" || fail "discover"

echo -e "\n${BOLD}${GREEN}Discovery complete.${NC}"
echo "  $DISCOVERY_DIR/repos.csv           — one row per repo, review and select"
echo "  $DISCOVERY_DIR/pipelines.csv       — one row per pipeline (linked to repos)"
echo "  $DISCOVERY_DIR/discovery.json      — full JSON detail"
echo "  $DISCOVERY_DIR/repos_template.txt  — copy + uncomment lines into in/repo_map.txt"
echo
echo "Next step:"
echo "  1. Open $DISCOVERY_DIR/repos_template.txt"
echo "  2. Uncomment the repos you want to migrate (one per line)"
echo "  3. Optionally add ::gh_org/gh_repo destination overrides"
echo "  4. Save the list to in/repo_map.txt"
echo "  5. Run: bash scripts/migrate-full.sh   (or scripts/migrate.sh for minimal)"
