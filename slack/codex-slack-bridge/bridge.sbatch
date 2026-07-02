#!/usr/bin/env bash
# Resource settings are supplied by keepalive.sh so accounts and partitions stay configurable.
set -euo pipefail
ROOT="${CODEX_SLACK_BRIDGE_ROOT:-${SLURM_SUBMIT_DIR:-}}"
if [[ -z "$ROOT" ]]; then
    echo "Cannot locate bridge root; submit through keepalive.sh or from the project directory." >&2
    exit 2
fi
echo "$(date '+%F %T') bridge job ${SLURM_JOB_ID:-manual} on $(hostname)"
exec "$ROOT/run.sh"
