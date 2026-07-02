#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=runtime-path.sh
source "$ROOT/scripts/runtime-path.sh"
INSTALL_DEV=1 "$ROOT/scripts/install.sh" >/dev/null
scripts=(
    "$ROOT/run.sh"
    "$ROOT/keepalive.sh"
    "$ROOT/bridge.sbatch"
    "$ROOT/test_node.sh"
    "$ROOT/scripts/install.sh"
    "$ROOT/scripts/run-once.sh"
    "$ROOT/scripts/runtime-path.sh"
    "$ROOT/scripts/check.sh"
)
bash -n "${scripts[@]}"
if command -v shellcheck >/dev/null 2>&1; then
    shellcheck "${scripts[@]}"
fi
"$ROOT/.venv/bin/python" -m ruff check "$ROOT"
"$ROOT/.venv/bin/python" -m ruff format --check "$ROOT"
"$ROOT/.venv/bin/python" -m pytest -q "$ROOT/tests"
