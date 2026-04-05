#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON_BIN=""
if [[ -x "$ROOT/tools/comfyhybrid-venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT/tools/comfyhybrid-venv/bin/python"
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python)"
else
  echo "FAIL: Python was not found. Install Python 3.10+ and rerun from the repo root."
  exit 1
fi

echo "Running ComfyUIhybrid bootstrap from $ROOT"
echo "Using Python: $PYTHON_BIN"

exec "$PYTHON_BIN" "$ROOT/scripts/comfyhybrid_setup_flow.py" bootstrap "$@"
