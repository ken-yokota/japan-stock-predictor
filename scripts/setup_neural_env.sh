#!/usr/bin/env bash
# Build the sibling interpreter the sequence models need.
#
#   ./scripts/setup_neural_env.sh
#
# torch has no Python 3.14 distribution, and this project runs 3.14 by
# deliberate choice (commit 7aaa794). Rather than reverse that for one arm,
# the LSTM and Transformer run under their own Python 3.12 environment and
# talk to the main process over a JSON document.
#
# Without this the two sequence arms report UNAVAILABLE and every other part
# of the morning runs exactly as before. It is a setup step, never a
# prerequisite.

set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

if ! command -v python3.12 >/dev/null 2>&1; then
  echo "python3.12 が見つかりません。" >&2
  echo "torch は Python 3.14 に対応していないため、3.12 が必要です。" >&2
  exit 1
fi

python3.12 -m venv .venv-neural
.venv-neural/bin/python -m pip install --quiet --upgrade pip
# numpy is pinned below 2 because torch 2.2.2 -- the newest build available
# for macOS x86_64 -- was compiled against the 1.x ABI.
.venv-neural/bin/python -m pip install "torch==2.2.2" "numpy<2"

.venv-neural/bin/python -c "import torch; print('torch', torch.__version__)"
echo "完了しました。LSTM と Transformer のアームが有効になります。"
