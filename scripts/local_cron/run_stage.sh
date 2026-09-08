#!/bin/bash
# Local stand-in for the GitHub Actions morning workflows while the hosted
# database is unreachable. Same scripts, same order, same email path -- only
# the scheduler changes (launchd instead of Actions cron).
#
# Usage: run_stage.sh <prefetch|predict|email|snapshot>
set -uo pipefail

REPO="$HOME/dev/japan-stock-predictor"
LOGDIR="$REPO/local_logs"
STAGE="${1:?stage required}"
mkdir -p "$LOGDIR"
STAMP="$(TZ=Asia/Tokyo date +%Y-%m-%d)"
LOG="$LOGDIR/${STAMP}_${STAGE}.log"

cd "$REPO" || exit 1

# The local Postgres copy is the database now; DATABASE_URL in .env already
# points at it. Cap BLAS threads: this machine has 4 cores and the morning
# fits three tickers at a time.
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1 NUMEXPR_NUM_THREADS=1
PY="$REPO/.venv/bin/python"

run_stage() {
  echo "=== $STAGE start $(TZ=Asia/Tokyo date '+%Y-%m-%d %H:%M:%S %Z') ==="
  case "$STAGE" in
    prefetch) "$PY" -m scripts.prefetch_morning_data ;;
    predict)  "$PY" -m scripts.run_morning_prediction ;;
    email_defer)
      # First attempt of the morning, mirroring the Actions schedule: if no
      # prediction set exists yet it stays quiet, so the operator does not get
      # a "no prediction" notice for a set that is merely a few minutes late.
      "$PY" -m scripts.send_morning_email --defer-missing ;;
    email)
      # Final attempt. This one sends the fallback notice and exits non-zero
      # when there is still nothing, so a missing morning stays visible.
      "$PY" -m scripts.send_morning_email ;;
    snapshot)
      "$PY" -m scripts.export_dashboard_snapshot --output "$LOGDIR/dashboard_snapshot.json" \
        && "$REPO/scripts/local_cron/publish_snapshot.sh" "$LOGDIR/dashboard_snapshot.json"
      ;;
    *) echo "unknown stage: $STAGE"; return 2 ;;
  esac
  local rc=$?
  echo "=== $STAGE exit $rc $(TZ=Asia/Tokyo date '+%H:%M:%S') ==="
  return $rc
}

run_stage >> "$LOG" 2>&1
RC=$?
# Silence is not success: a failed stage must reach the operator by mail, and
# the mail is built from live state rather than from anything cached here.
if [ $RC -ne 0 ]; then
  # exit 2 from the prediction step is "published, but not every ticker made
  # it" -- the set is usable and the mail will still go out at 08:45. Saying
  # so matters: an operator reading "失敗" on a phone would otherwise assume
  # there is no prediction at all.
  case "$STAGE:$RC" in
    predict:2) DETAIL="予測は公開されましたが、一部の銘柄が欠けています（自動化の健全性としては赤）。朝メールは予定どおり送信されます。" ;;
    email:2)   DETAIL="予測が存在しないため「予測なし」通知を送りました。" ;;
    *)         DETAIL="このステージは完了していません。" ;;
  esac
  "$PY" -m scripts.send_progress_report \
    --note "ローカル朝処理の ${STAGE} が exit ${RC} で終了しました。${DETAIL} ログ: ${LOG}" \
    >> "$LOG" 2>&1
fi
exit $RC
