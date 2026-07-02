#!/usr/bin/env bash
# Crash-restarting supervisor. For a foreground one-shot run, use scripts/run-once.sh.
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/runtime-path.sh
source "$ROOT/scripts/runtime-path.sh"
ENV_FILE="${BRIDGE_ENV_FILE:-$ROOT/.env}"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "Missing $ENV_FILE; copy .env.example to .env and fill it in." >&2
    exit 2
fi
ENV_FILE="$(cd "$(dirname "$ENV_FILE")" && pwd)/$(basename "$ENV_FILE")"
export BRIDGE_ENV_FILE="$ENV_FILE"

# Install before loading credentials so package/build processes never inherit them.
mkdir -p "$ROOT/state"
"$ROOT/scripts/install.sh" >>"$ROOT/state/install.log" 2>&1

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

cd "$ROOT"
STATE_DIR="${BRIDGE_STATE_DIR:-$ROOT/state}"
[[ "$STATE_DIR" = /* ]] || STATE_DIR="$ROOT/$STATE_DIR"
mkdir -p "$STATE_DIR"
LOG_FILE="${BRIDGE_LOG_FILE:-$STATE_DIR/bridge.log}"

command -v flock >/dev/null 2>&1 || {
    echo "flock (util-linux) is required." >&2
    exit 2
}

# A supervisor-lifetime lock prevents duplicate tmux/cron/Slurm launches.
exec 9>"$STATE_DIR/supervisor.lock"
if ! flock -n 9; then
    echo "Another bridge supervisor is already running." >&2
    exit 0
fi

backoff=5
shutdown_requested=0
child_pid=""
forward_shutdown() {
    shutdown_requested=1
    if [[ -n "$child_pid" ]]; then
        kill -TERM "$child_pid" 2>/dev/null || true
    fi
}
trap forward_shutdown TERM HUP INT

while true; do
    started=$SECONDS
    printf '%(%F %T)T starting bridge\n' -1 >>"$LOG_FILE"
    set +e
    "$ROOT/.venv/bin/python" "$ROOT/bridge.py" >>"$LOG_FILE" 2>&1 &
    child_pid=$!
    wait "$child_pid"
    code=$?
    set -e
    runtime=$((SECONDS - started))
    if (( shutdown_requested )); then
        set +e
        wait "$child_pid" 2>/dev/null
        set -e
        child_pid=""
        printf '%(%F %T)T supervisor stopped\n' -1 >>"$LOG_FILE"
        exit 0
    fi
    child_pid=""
    if (( code == 2 )); then
        printf '%(%F %T)T fatal configuration/startup error; not restarting\n' -1 \
            >>"$LOG_FILE"
        exit 2
    fi
    printf '%(%F %T)T bridge exited (code %d); restart in %ds\n' \
        -1 "$code" "$backoff" >>"$LOG_FILE"
    # A shutdown signal can arrive while no child is running and the supervisor
    # is backing off. Do not start a fresh bridge after that signal.
    sleep "$backoff" || true
    if (( shutdown_requested )); then
        printf '%(%F %T)T supervisor stopped during restart backoff\n' -1 >>"$LOG_FILE"
        exit 0
    fi
    if (( runtime >= 300 )); then
        backoff=5
    elif (( backoff < 300 )); then
        backoff=$((backoff * 2))
        (( backoff > 300 )) && backoff=300
    fi
done
