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
  export DATABASE_URL
fi

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "注意: DATABASE_URL が未設定です。画面は開きますが、各ページは PENDING を表示します。" >&2
fi

if ! python -c "import streamlit" >/dev/null 2>&1; then
  echo "streamlit が見つかりません。python -m pip install -r requirements.txt を実行してください。" >&2
  exit 1
fi

port="${DASHBOARD_PORT:-8501}"
echo "Dashboard: http://localhost:${port}"
if [[ "$address" == "0.0.0.0" ]]; then
  echo "同一ネットワークからは http://$(ipconfig getifaddr en0 2>/dev/null || echo '<this-mac-ip>'):${port}"
fi

exec streamlit run app.py \
  --server.address "$address" \
  --server.port "$port" \
  --server.headless true \
  --browser.gatherUsageStats false \
  "$@"
