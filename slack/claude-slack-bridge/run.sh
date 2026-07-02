#!/usr/bin/env bash
# supervisor：崩溃后 5 秒自动重启。建议在 tmux 里跑：
#   tmux new -s claude-slack '<本目录>/run.sh'
cd "$(dirname "$0")"

if [ ! -f .env ]; then
    echo "缺少 .env，请先: cp .env.example .env 并填入 token" >&2
    exit 1
fi

set -a
source ./.env
set +a
export PATH="$HOME/.local/bin:$PATH"

# 这台 login 节点 ulimit -u 只有 300，而 uv 默认按 96 核开线程池，会
# EAGAIN panic —— 所以只在建 venv 时用 uv（限住线程数），运行时直接用
# venv 里的 python，不经过 uv
export RAYON_NUM_THREADS=4 UV_CONCURRENT_DOWNLOADS=4 UV_CONCURRENT_INSTALLS=4

if [ ! -f .venv/.deps-ok ]; then
    rm -rf .venv
    uv venv --python 3.12 .venv >> bridge.log 2>&1 \
        && uv pip install --python .venv/bin/python \
            "slack-bolt>=1.21" aiohttp "claude-agent-sdk>=0.1.0" >> bridge.log 2>&1 \
        && touch .venv/.deps-ok \
        || { echo "$(date '+%F %T') venv 构建失败，见 bridge.log" >&2; exit 1; }
fi

while true; do
    echo "$(date '+%F %T') starting bridge" >> bridge.log
    .venv/bin/python bridge.py >> bridge.log 2>&1
    echo "$(date '+%F %T') bridge exited (code $?), restart in 5s" >> bridge.log
    sleep 5
done
