#!/usr/bin/env bash
# Intended for cron: attach to a suitable running job or submit one dedicated job.
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/runtime-path.sh
source "$ROOT/scripts/runtime-path.sh"
ENV_FILE="${BRIDGE_ENV_FILE:-$ROOT/.env}"
[[ -f "$ENV_FILE" ]] || exit 0
ENV_FILE="$(cd "$(dirname "$ENV_FILE")" && pwd)/$(basename "$ENV_FILE")"
export BRIDGE_ENV_FILE="$ENV_FILE"

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

cd "$ROOT"
command -v flock >/dev/null 2>&1 || exit 2
command -v squeue >/dev/null 2>&1 || exit 2
command -v sbatch >/dev/null 2>&1 || exit 2
[[ "${SLACK_BOT_TOKEN:-}" == xoxb-* && "${SLACK_BOT_TOKEN:-}" != *"..."* ]] \
    || exit 0
[[ "${SLACK_APP_TOKEN:-}" == xapp-* && "${SLACK_APP_TOKEN:-}" != *"..."* ]] \
    || exit 0
ALLOWED_USERS="${SLACK_ALLOWED_USER_ID:-${SLACK_ALLOWED_USER_IDS:-}}"
[[ -n "$ALLOWED_USERS" && -d "${CODEX_CWD:-}" ]] || exit 0
STATE_DIR="${BRIDGE_STATE_DIR:-$ROOT/state}"
[[ "$STATE_DIR" = /* ]] || STATE_DIR="$ROOT/$STATE_DIR"
mkdir -p "$STATE_DIR"

exec 9>"$STATE_DIR/keepalive.lock"
flock -n 9 || exit 0

# A locked supervisor file covers manual, tmux, and Slurm launches alike.
if ! flock -n "$STATE_DIR/supervisor.lock" true; then
    exit 0
fi

USER_NAME="${USER:-${LOGNAME:-$(id -un)}}"
JOB_NAME="codex-slack-bridge"
jobs="$(squeue -u "$USER_NAME" --name="$JOB_NAME" -h -o '%i' 2>/dev/null || true)"
if [[ -n "$jobs" ]]; then
    exit 0
fi

# On clusters with a per-user node cap, reuse a sufficiently long-running job
# before requesting another node. The srun client PID closes the cron race while
# the remote supervisor is still starting; the supervisor lock remains the
# authoritative single-instance guard.
ATTACH="${SLURM_ATTACH_TO_EXISTING_JOB:-true}"
SRUN_PID_FILE="$STATE_DIR/overlap-srun.pid"
if [[ "$ATTACH" =~ ^(1|true|yes|on)$ ]] && command -v srun >/dev/null 2>&1; then
    if [[ -s "$SRUN_PID_FILE" ]]; then
        read -r srun_pid <"$SRUN_PID_FILE" || srun_pid=""
        if [[ "$srun_pid" =~ ^[0-9]+$ ]] && kill -0 "$srun_pid" 2>/dev/null; then
            exit 0
        fi
    fi
    host_job="$({
        squeue -u "$USER_NAME" -p "${SLURM_PARTITION:-cpu_long}" -t RUNNING \
            -h --sort=-L -o '%i|%L|%j' 2>/dev/null || true
    } | awk -F '|' -v bridge_name="$JOB_NAME" \
        -v minimum="${SLURM_ATTACH_MIN_SECONDS:-3600}" '
        function seconds(value, fields, count, days) {
            gsub(/[[:space:]]/, "", value)
            days = 0
            count = split(value, fields, "-")
            if (count == 2) {
                days = fields[1] + 0
                value = fields[2]
            }
            count = split(value, fields, ":")
            if (count == 3) return days * 86400 + fields[1] * 3600 + fields[2] * 60 + fields[3]
            if (count == 2) return days * 86400 + fields[1] * 60 + fields[2]
            return days * 86400 + fields[1]
        }
        {
            name = $3
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
            if (name != bridge_name && seconds($2) > minimum) {
                id = $1
                gsub(/[[:space:]]/, "", id)
                print id
                exit
            }
        }')"
    if [[ -n "$host_job" ]]; then
        printf '%(%F %T)T attaching bridge to job %s with srun --overlap\n' \
            -1 "$host_job" >>"$STATE_DIR/keepalive.log"
        nohup srun --jobid="$host_job" --overlap --ntasks=1 --cpus-per-task=1 \
            --job-name="$JOB_NAME" \
            --export="CODEX_SLACK_BRIDGE_ROOT=$ROOT,BRIDGE_ENV_FILE=$ENV_FILE,HOME=$HOME,PATH=$PATH" \
            "$ROOT/run.sh" >>"$STATE_DIR/overlap-srun.log" 2>&1 &
        printf '%s\n' "$!" >"$SRUN_PID_FILE"
        exit 0
    fi
fi

args=(
    --job-name="$JOB_NAME"
    --partition="${SLURM_PARTITION:-cpu_long}"
    --time="${SLURM_TIME:-5-00:00:00}"
    --cpus-per-task="${SLURM_CPUS:-4}"
    --mem="${SLURM_MEMORY:-16G}"
    --output="$STATE_DIR/slurm-%j.log"
    --chdir="$ROOT"
    --export="CODEX_SLACK_BRIDGE_ROOT=$ROOT,BRIDGE_ENV_FILE=$ENV_FILE,HOME=$HOME,PATH=$PATH"
)
if [[ -n "${SLURM_ACCOUNT:-}" ]]; then
    args+=(--account="$SLURM_ACCOUNT")
fi

printf '%(%F %T)T submitting dedicated bridge job\n' -1 >>"$STATE_DIR/keepalive.log"
sbatch "${args[@]}" "$ROOT/bridge.sbatch" >>"$STATE_DIR/keepalive.log" 2>&1
