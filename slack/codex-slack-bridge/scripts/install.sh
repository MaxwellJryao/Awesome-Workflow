#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=runtime-path.sh
source "$ROOT/scripts/runtime-path.sh"
unset SLACK_BOT_TOKEN SLACK_APP_TOKEN CODEX_API_KEY OPENAI_API_KEY
PYTHON_VERSION="${PYTHON_VERSION:-3.11}"
mkdir -p "$ROOT/state"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$ROOT/state/uv-cache}"

command -v flock >/dev/null 2>&1 || {
    echo "flock (util-linux) is required." >&2
    exit 2
}

exec 9>"$ROOT/state/install.lock"
flock 9

if command -v uv >/dev/null 2>&1; then
    sync_args=(--project "$ROOT" --locked --python "$PYTHON_VERSION")
    if [[ "${INSTALL_DEV:-0}" == "1" ]]; then
        sync_args+=(--extra dev)
    else
        sync_args+=(--no-dev)
    fi
    RAYON_NUM_THREADS="${RAYON_NUM_THREADS:-4}" \
        UV_CONCURRENT_DOWNLOADS="${UV_CONCURRENT_DOWNLOADS:-4}" \
        UV_CONCURRENT_INSTALLS="${UV_CONCURRENT_INSTALLS:-4}" \
        UV_PROJECT_ENVIRONMENT="$ROOT/.venv" \
        uv sync "${sync_args[@]}"
else
    install_target="$ROOT"
    if [[ "${INSTALL_DEV:-0}" == "1" ]]; then
        install_target="$ROOT[dev]"
    fi
    PYTHON_BIN="${PYTHON_BIN:-python3}"
    if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
        echo "Python 3.11+ is required; set PYTHON_BIN to a compatible interpreter." >&2
        exit 2
    fi
    if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
        "$PYTHON_BIN" -m venv "$ROOT/.venv"
    fi
    "$ROOT/.venv/bin/python" -m pip install --upgrade pip
    "$ROOT/.venv/bin/python" -m pip install -e "$install_target"
fi

echo "Environment ready: $ROOT/.venv"
