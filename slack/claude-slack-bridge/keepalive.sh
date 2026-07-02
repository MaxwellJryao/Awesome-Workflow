#!/usr/bin/env bash
# 由 login 节点 cron 定期调用：bridge 不在运行时自动拉起。
# 策略：已有 RUNNING 的 cpu_long job（剩余 >1 小时）就 srun --overlap 挂上去
# （QOSMaxNodePerUserLimit 按节点计数，寄生不占新节点配额）；
# 没有可寄生的 job 时才 sbatch 一个 5 天的 cpu_long 独立小 job。
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# cron 环境没有 $USER（只有 LOGNAME），不补上的话下面所有 squeue 查询全是空的
USER="${USER:-$(id -un)}"

# 文件锁：防止 cron 和手动执行同时跑造成重复提交
exec 9>"$DIR/.keepalive.lock"
flock -n 9 || exit 0

# .env 缺失或 token 还是占位符时静默退出，避免刷日志
[ -f "$DIR/.env" ] || exit 0
grep -q '^SLACK_BOT_TOKEN=xoxb-\.\.\.' "$DIR/.env" && exit 0

# 已在运行就不动：overlap 模式看 srun 客户端进程，sbatch 模式看队列（含排队中）
# 锚定 ^srun，避免误匹配到命令行里恰好含该路径的无关进程（如 tail/编辑器/监控脚本）
pgrep -f "^srun .*claude-slack-bridge/run\.sh" > /dev/null && exit 0
n=$(squeue -u "$USER" --name=claude-slack-bridge -h 2>/dev/null | wc -l)
[ "$n" -ge 1 ] && exit 0

# 只在 cpu_long 分区里挑宿主：剩余时间最长且 >1 小时的 RUNNING job
host=$(squeue -u "$USER" -p cpu_long -t RUNNING -h --sort=-L -o "%i %L %j" \
       | awk '$3 != "claude-slack-bridge" && ($2 ~ /-/ || $2 ~ /^[0-9]+:[0-9]+:[0-9]+$/) {print $1; exit}')

if [ -n "$host" ]; then
    echo "$(date '+%F %T') attaching bridge to cpu_long job $host via srun --overlap" >> "$DIR/keepalive.log"
    nohup srun --jobid="$host" --overlap --ntasks=1 --job-name=claude-slack-bridge \
        "$DIR/run.sh" >> "$DIR/bridge.log" 2>&1 &
    exit 0
fi

echo "$(date '+%F %T') no cpu_long job to attach, submitting own 5-day job" >> "$DIR/keepalive.log"
sbatch --chdir="$DIR" --export=ALL,BRIDGE_DIR="$DIR" "$DIR/bridge.sbatch" \
    >> "$DIR/keepalive.log" 2>&1
