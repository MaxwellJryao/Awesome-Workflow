#!/bin/bash
# 在 CPU 计算节点上验证 bridge 的全部前置条件（不连 Slack，不耗 token）
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "node: $(hostname)  ulimit -u: $(ulimit -u)"
echo -n "slack.com:      "; curl -sS --max-time 10 https://slack.com/api/api.test || echo FAIL
echo
echo -n "anthropic api:  "; curl -sS --max-time 10 -o /dev/null -w "%{http_code}" https://api.anthropic.com; echo
[ -x "$HOME/.local/bin/claude" ] && echo "claude CLI:     visible" || echo "claude CLI:     MISSING"
[ -f "$HOME/.claude/.credentials.json" ] && echo "credentials:    visible" || echo "credentials:    MISSING"
"$DIR/.venv/bin/python" -c "import slack_bolt, claude_agent_sdk; print('python deps:    ok')" || echo "python deps:    FAIL"
