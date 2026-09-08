#!/bin/bash
# Publish the dashboard snapshot to the `snapshot` branch.
#
# Guarded, unlike the Actions workflow it stands in for: that one force-pushes
# whatever the exporter produced, so on 2026-08-31 it replaced a good snapshot
# with `0 tickers / predictions=UNAVAILABLE` and kept doing so 26 times. An
# empty snapshot is never worth publishing -- the previous good one is strictly
# better than a blank page, so refuse the push instead.
set -euo pipefail

SNAP="${1:?snapshot json required}"
REPO="$HOME/dev/japan-stock-predictor"

COUNT=$("$REPO/.venv/bin/python" - "$SNAP" <<'PY'
import json, sys
with open(sys.argv[1]) as handle:
    data = json.load(handle)
print(len(data.get("predictions") or []))
PY
)

if [ "$COUNT" -eq 0 ]; then
  echo "refusing to publish an empty snapshot (0 predictions); keeping the published one"
  exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
git -C "$REPO" worktree add --detach "$WORK" >/dev/null 2>&1 || {
  echo "could not create worktree"; exit 1; }

cd "$WORK"
git checkout --orphan snapshot-publish >/dev/null 2>&1
git rm -rf --cached . >/dev/null 2>&1 || true
find . -maxdepth 1 ! -name . ! -name .git -exec rm -rf {} + 2>/dev/null || true
cp "$SNAP" dashboard_snapshot.json
git add --force dashboard_snapshot.json
git -c user.name="local-morning" -c user.email="ky3141120@icloud.com" \
    commit -m "Publish dashboard snapshot $(date -u +%Y-%m-%dT%H:%M:%SZ) [skip ci]" >/dev/null
git push --force origin snapshot-publish:snapshot
echo "published $COUNT predictions to the snapshot branch"

cd "$REPO"
git worktree remove --force "$WORK" >/dev/null 2>&1 || true
