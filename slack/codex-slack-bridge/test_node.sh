#!/usr/bin/env bash
# Validate prerequisites without sending a Slack message or making a model request.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/runtime-path.sh
source "$ROOT/scripts/runtime-path.sh"
ENV_FILE="${BRIDGE_ENV_FILE:-$ROOT/.env}"
[[ -f "$ENV_FILE" ]] || { echo "FAIL: missing $ENV_FILE"; exit 1; }
ENV_FILE="$(cd "$(dirname "$ENV_FILE")" && pwd)/$(basename "$ENV_FILE")"
export BRIDGE_ENV_FILE="$ENV_FILE"

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

failed=0
check() {
    if "$@"; then
        return 0
    fi
    failed=1
    return 1
}

echo "node: $(hostname); ulimit -u: $(ulimit -u)"
command -v node >/dev/null 2>&1 && node --version || echo "WARN: node not found"
[[ -d "${CODEX_CWD:-}" ]] || { echo "FAIL: CODEX_CWD is not a directory"; failed=1; }
[[ "${SLACK_BOT_TOKEN:-}" == xoxb-* && "${SLACK_BOT_TOKEN:-}" != *"..."* ]] \
    || { echo "FAIL: invalid SLACK_BOT_TOKEN"; failed=1; }
[[ "${SLACK_APP_TOKEN:-}" == xapp-* && "${SLACK_APP_TOKEN:-}" != *"..."* ]] \
    || { echo "FAIL: invalid SLACK_APP_TOKEN"; failed=1; }
unset SLACK_BOT_TOKEN SLACK_APP_TOKEN

check command -v "${CODEX_BIN:-codex}" >/dev/null || echo "FAIL: codex CLI not found"
"${CODEX_BIN:-codex}" --version || failed=1
"${CODEX_BIN:-codex}" login status || failed=1
"${CODEX_BIN:-codex}" exec --help >/dev/null || {
    echo "FAIL: codex exec is unavailable"
    failed=1
}
"${CODEX_BIN:-codex}" exec resume --help >/dev/null || {
    echo "FAIL: codex exec resume is unavailable"
    failed=1
}
if command -v squeue >/dev/null 2>&1 && command -v sbatch >/dev/null 2>&1; then
    echo "slurm CLI: ok"
else
    echo "WARN: Slurm CLI not found (only required for keepalive.sh)"
fi

if [[ -x "$ROOT/.venv/bin/python" ]]; then
    "$ROOT/.venv/bin/python" -c 'import aiohttp, slack_bolt; print("python deps: ok")' \
        || failed=1
else
    echo "WARN: .venv missing; run scripts/install.sh"
fi

if command -v curl >/dev/null 2>&1; then
    curl --fail --silent --show-error --max-time 10 https://slack.com/api/api.test >/dev/null \
        || { echo "FAIL: cannot reach Slack API"; failed=1; }
fi

exit "$failed"
