#!/bin/bash
# Backstop for GitHub's scheduler, which is best-effort and does drop runs.
#
# On 2026-09-08 every evening tick was dropped: close_update (06:45/06:55/07:10
# UTC) and daily_summary (08:00/08:20/08:40 UTC) produced no runs at all, and
# the morning's fired 1.5-3.6 hours late. Adding more cron ticks does not help
# a scheduler that fires none of them, so this is a *second, independent*
# trigger from a machine that is always on.
#
# It is a backstop, not a replacement. GitHub stays primary; this only acts
# when the outcome is missing, and the pipeline it dispatches is the same one
# the cron would have run. Sending is keyed on the date in email_logs, so a
# race with a late-firing cron cannot produce two mails.
#
#   ensure_delivery.sh morning   # after the 08:45-08:55 JST window
#   ensure_delivery.sh evening   # after the 15:45-17:40 JST windows
set -uo pipefail

WINDOW="${1:?window required: morning|evening}"
REPO="$HOME/dev/japan-stock-predictor"
LOGDIR="$REPO/local_logs"
mkdir -p "$LOGDIR"
STAMP="$(TZ=Asia/Tokyo date +%Y-%m-%d)"
LOG="$LOGDIR/${STAMP}_ensure_${WINDOW}.log"
PY="$REPO/.venv/bin/python"

# launchd starts jobs with a minimal PATH -- not the login shell's -- so gh is
# not on it. On 2026-09-09 this backstop detected the missing evening
# correctly, tried to dispatch, and died on "gh: command not found", which is
# the one failure mode a backstop cannot afford. Resolved explicitly, and the
# run aborts loudly rather than proceeding without it.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
GH="$(command -v gh || true)"

cd "$REPO" || exit 1

exec >>"$LOG" 2>&1
echo "=== ensure ${WINDOW} $(TZ=Asia/Tokyo date '+%F %T %Z') ==="

# The production database, not the empty local copy. This reads only.
NEON=$(grep '^NEON_DATABASE_URL=' .env | cut -d= -f2- | tr -d '"')
export DATABASE_URL="$NEON"

# The watchdog already knows what "delivered" means for each window, including
# that a JPX holiday is not a failure and that a window before its time is
# NOT_YET_DUE. Reusing it means the backstop and the alarm cannot disagree
# about whether the day succeeded.
verdict() {
  "$PY" -m scripts.verify_daily_delivery --window "$WINDOW" --dry-run 2>/dev/null \
    | grep -oE '\{"window".*\}' | tail -1
}

BEFORE="$(verdict)"
echo "before: ${BEFORE:-<no verdict>}"
if [ -z "$BEFORE" ]; then
  echo "watchdog produced no verdict; leaving the day alone rather than guessing"
  exit 1
fi

# The decision lives in scripts/delivery_backstop.py, with tests. Exit 0 means
# something this window owed is missing.
needs_repair() {
  printf '%s' "$1" | "$PY" -m scripts.delivery_backstop "$WINDOW"
}

if ! needs_repair "$BEFORE"; then
  echo "nothing missing; GitHub delivered this window"
  exit 0
fi

echo "--- missing; dispatching the same workflows the cron would have run ---"
if [ -z "$GH" ]; then
  echo "gh not found on PATH; cannot dispatch"
  "$PY" -m scripts.send_progress_report \
    --note "GitHubが${WINDOW}の実行を落としましたが、代替起動に必要な gh が見つからず復旧できませんでした。手動での実行が必要です。ログ: ${LOG}"
  exit 1
fi
DATE="$(TZ=Asia/Tokyo date +%F)"
if [ "$WINDOW" = "evening" ]; then
  "$GH" workflow run close_update.yml -f prediction_date="$DATE" -f dry_run=false \
    && sleep 420
  "$GH" workflow run daily_summary.yml -f for_date="$DATE" -f dry_run=false \
    && sleep 180
else
  "$GH" workflow run morning_kick.yml && sleep 900
fi

AFTER="$(verdict)"
echo "after: ${AFTER:-<no verdict>}"

if [ -n "$AFTER" ] && ! needs_repair "$AFTER"; then
  NOTE="GitHubのスケジュールが${WINDOW}の実行を落としたため、このMacから代わりに起動して復旧しました（${DATE}）。"
else
  NOTE="GitHubのスケジュールが${WINDOW}の実行を落としたので代わりに起動しましたが、まだ完了していません（${DATE}）。ログ: ${LOG}"
fi
# Reported either way. A backstop that repairs silently hides that the primary
# schedule is failing, and that is the thing worth knowing.
"$PY" -m scripts.send_progress_report --note "$NOTE"
