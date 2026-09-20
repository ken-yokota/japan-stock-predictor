#!/usr/bin/env bash
# Isolated Close TEST; credentials are loaded by Python, never sourced or printed.
set -euo pipefail
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
python_bin="${JSP_PYTHON:-.venv/bin/python}"
[[ -x "$python_bin" ]] || python_bin="python3"
exec "$python_bin" -m scripts.send_test_close_email "$@"
