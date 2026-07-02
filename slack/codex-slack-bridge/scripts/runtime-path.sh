#!/usr/bin/env bash
# Shared PATH setup for interactive shells, cron, and sanitized Slurm jobs.

existing_path="${PATH:-/usr/bin:/bin}"
node_bin=""
if command -v node >/dev/null 2>&1; then
    node_bin="$(dirname "$(command -v node)")"
elif [[ -d "$HOME/.nvm/versions/node" ]]; then
    node_bin="$({
        for candidate in "$HOME"/.nvm/versions/node/*/bin; do
            [[ -x "$candidate/node" ]] && printf '%s\n' "$candidate"
        done
        :
    } | sort -V | tail -n 1)"
fi

PATH="$HOME/local/bin:$HOME/.local/bin"
[[ -n "$node_bin" ]] && PATH="$PATH:$node_bin"
PATH="$PATH:/cm/shared/apps/slurm/current/bin:$existing_path"
export PATH
