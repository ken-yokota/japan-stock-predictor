#!/usr/bin/env bash
# Launch the read-only Streamlit dashboard.
#
# The dashboard only issues SELECTs, so starting it never fetches provider data,
# trains a model, or sends mail. It is safe to leave running.
#
#   ./scripts/start_dashboard.sh              # localhost only
#   ./scripts/start_dashboard.sh --lan        # also reachable from this network
#
# --lan binds 0.0.0.0 so a phone on the same Wi-Fi can open it. Only use it on a
# network you trust: the page shows prediction data without authentication.

set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

address="localhost"
if [[ "${1:-}" == "--lan" ]]; then
  address="0.0.0.0"
  shift
fi

local_python312_venv="${JPSTOCK_VENV:-${HOME}/.venvs/japan-stock-predictor-python312}"
if [[ -x "${local_python312_venv}/bin/python" ]]; then
  # Keep the runtime outside Desktop/iCloud: dataless package files there can
  # make Streamlit's startup scan stall for several minutes.
  # shellcheck disable=SC1091
  source "${local_python312_venv}/bin/activate"
elif [[ -d .venv ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# The dashboard is SELECT-only, so show the same hosted data used by Actions
# when that optional local alias is configured. Set
# JPSTOCK_DASHBOARD_DATABASE=local to inspect the workstation database instead.
dashboard_database="${JPSTOCK_DASHBOARD_DATABASE:-production}"
if [[ "$dashboard_database" == "production" && -n "${NEON_DATABASE_URL:-}" ]]; then
  DATABASE_URL="$NEON_DATABASE_URL"
  e