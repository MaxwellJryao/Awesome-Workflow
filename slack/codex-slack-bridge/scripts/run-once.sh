#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=runtime-path.sh
source "$ROOT/scripts/runtime-path.sh"
ENV_FILE="${BRIDGE_ENV_FILE:-$ROOT/.env}"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "Missing $ENV_FILE; copy .env.example to .env and fill it in." >&2
    exit 2
fi
ENV_FILE="$(cd "$(dirname "$ENV_FILE")" && pwd)/$(basename "$ENV_FILE")"
export BRIDGE_ENV_FILE="$ENV_FILE"

# Install before loading credentials so package/build processes never inherit them.
cd "$ROOT"
"$ROOT/scripts/install.sh"

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

STATE_DIR="${BRIDGE_STATE_DIR:-$ROOT/state}"
[[ "$STATE_DIR" = /* ]] || STATE_DIR="$ROOT/$STATE_DIR"
mkdir -p "$STATE_DIR"
command -v flock >/dev/null 2>&1 || {
    echo "flock (util-linux) is required." >&2
    exit 2
}
exec 9>"$STATE_DIR/supervisor.lock"
if ! flock -n 9; then
    echo "Another bridge supervisor is already running." >&2
    exit 2
fi
exec "$ROOT/.venv/bin/python" "$ROOT/bridge.py"
