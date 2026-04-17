#!/usr/bin/env bash
# ==============================================================================
# GPU 利用率监督脚本 (gpu_watchdog.sh)
#
# 功能：持续监测所有 GPU 的利用率，若所有 GPU 连续 WINDOW_MINUTES 分钟内
#       利用率均低于 THRESHOLD，则自动执行预设命令。
#
# 用法：
#   ./gpu_watchdog.sh [--threshold 10] [--window 30] [--interval 60] [--cmd "命令"]
#
# 默认值：
#   --threshold  10        GPU 利用率告警阈值（%）
#   --window     30        持续低于阈值的时间窗口（分钟）
#   --interval   60        采样间隔（秒）
#   --cmd        ""        触发后执行的命令（必须提供，或在脚本内修改 DEFAULT_CMD）
#   --once                 触发一次后退出（默认触发后继续监控）
#   --log        ""        日志文件路径（默认输出到 stdout）
#   --gpu-count  8         监控的 GPU 数量（自动检测，也可手动指定）
#
# 示例：
#   # 连续 30 分钟所有 GPU 利用率 <10% 后发送通知并重启任务
#   ./gpu_watchdog.sh --threshold 10 --window 30 \
#       --cmd "bash /root/restart_training.sh && curl -s 'https://hooks.example.com/alert'"
#
#   # 后台运行并记录日志
#   nohup ./gpu_watchdog.sh --log /root/gpu_watchdog.log &
# ==============================================================================

set -euo pipefail

# ── 默认配置（可在此处修改，也可通过命令行参数覆盖） ────────────────────────
THRESHOLD=10           # GPU 利用率阈值（%），低于此值视为"空闲"
WINDOW_MINUTES=30      # 触发所需的连续空闲时间（分钟）
INTERVAL=60            # 采样间隔（秒）
TRIGGER_CMD=""         # 触发后执行的 shell 命令（留空则仅告警不执行）
LOG_FILE=""            # 日志文件（留空则输出到 stdout）
ONCE=false             # true = 触发一次后退出；false = 触发后继续监控
GPU_COUNT=""           # 留空则自动检测

# ── 内置默认执行命令（当 --cmd 未指定时使用） ────────────────────────────────
# 修改此处来设置默认触发操作，例如：
#   DEFAULT_CMD='echo "All GPUs idle! Restarting job..." && bash /root/restart.sh'
DEFAULT_CMD='echo "[WATCHDOG] All GPUs have been idle for ${WINDOW_MINUTES} minutes. No action configured."'

# ── 颜色输出 ────────────────────────────────────────────────────────────────
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

# ── 日志函数 ─────────────────────────────────────────────────────────────────
log() {
    local level="$1"; shift
    local ts; ts=$(date '+%Y-%m-%d %H:%M:%S')
    local color="$RESET"
    case "$level" in
        INFO)  color="$GREEN"   ;;
        WARN)  color="$YELLOW"  ;;
        ERROR) color="$RED"     ;;
        ALERT) color="$BOLD$RED";;
        DEBUG) color="$CYAN"    ;;
    esac
    local msg="[${ts}] [${level}] $*"
    if [[ -n "$LOG_FILE" ]]; then
        echo "$msg" | tee -a "$LOG_FILE"
    else
        echo -e "${color}${msg}${RESET}"
    fi
}

# ── 解析命令行参数 ────────────────────────────────────────────────────────────
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --threshold) THRESHOLD="$2";     shift 2 ;;
            --window)    WINDOW_MINUTES="$2"; shift 2 ;;
            --interval)  INTERVAL="$2";       shift 2 ;;
            --cmd)       TRIGGER_CMD="$2";    shift 2 ;;
            --log)       LOG_FILE="$2";       shift 2 ;;
            --gpu-count) GPU_COUNT="$2";      shift 2 ;;
            --once)      ONCE=true;           shift   ;;
            --help|-h)
                grep '^#' "$0" | sed 's/^# \?//' | head -40
                exit 0
                ;;
            *) log WARN "未知参数: $1"; shift ;;
        esac
    done

    # 使用默认命令（若 --cmd 未指定）
    if [[ -z "$TRIGGER_CMD" ]]; then
        TRIGGER_CMD="$DEFAULT_CMD"
    fi
}

# ── 单实例检查：用 flock 文件锁防止重复运行 ─────────────────────────────────
# 不依赖进程命令行匹配（cron 包装 shell、tee 管道等都不会干扰）。
# 锁与打开的文件描述符绑定，进程退出时自动释放，无需手动清理。
LOCK_FILE="/tmp/gpu_watchdog.sh.lock"
LOCK_FD=200

check_already_running() {
    if ! command -v flock >/dev/null 2>&1; then
        echo "[WATCHDOG] 系统未安装 flock，跳过单实例检查。" >&2
        return 0
    fi

    # 打开锁文件到固定 fd；用 eval 是因为 fd 号是变量。
    if ! eval "exec ${LOCK_FD}>\"$LOCK_FILE\"" 2>/dev/null; then
        echo "[WATCHDOG] 无法打开锁文件 $LOCK_FILE，跳过单实例检查。" >&2
        return 0
    fi

    # 非阻塞抢锁；抢不到说明已有实例持锁。
    if ! flock -n "$LOCK_FD"; then
        local holder=""
        if command -v fuser >/dev/null 2>&1; then
            holder=$(fuser "$LOCK_FILE" 2>/dev/null | tr -s ' ' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        fi
        echo "[WATCHDOG] 检测到 $(basename "$0") 已在运行${holder:+ (PID: ${holder})}，当前实例退出以避免重复。" >&2
        exit 0
    fi
}

# ── 检测 GPU 数量 ─────────────────────────────────────────────────────────────
detect_gpu_count() {
    if [[ -z "$GPU_COUNT" ]]; then
        GPU_COUNT=$(nvidia-smi --query-gpu=index --format=csv,noheader,nounits 2>/dev/null | wc -l)
    fi
    if [[ "$GPU_COUNT" -eq 0 ]]; then
        log ERROR "未检测到 GPU，请确认 nvidia-smi 正常工作。"
        exit 1
    fi
    log INFO "检测到 ${GPU_COUNT} 张 GPU"
}

# ── 获取所有 GPU 当前利用率 ───────────────────────────────────────────────────
# 返回：每行一个利用率数字（0-100）
get_gpu_utilization() {
    nvidia-smi --query-gpu=utilization.gpu \
               --format=csv,noheader,nounits 2>/dev/null \
    | tr -d ' '
}

# ── 检查所有 GPU 是否都低于阈值 ──────────────────────────────────────────────
# 返回 0 = 全部低于阈值，1 = 至少有一张不低于阈值
all_gpus_idle() {
    local utils; utils=$(get_gpu_utilization)
    local gpu_idx=0
    local all_idle=true
    local status_parts=()

    while IFS= read -r util; do
        if [[ -z "$util" ]]; then
            log WARN "GPU ${gpu_idx}: 无法读取利用率"
            all_idle=false
        elif (( util >= THRESHOLD )); then
            all_idle=false
            status_parts+=("GPU${gpu_idx}:${util}%↑")
        else
            status_parts+=("GPU${gpu_idx}:${util}%")
        fi
        gpu_idx=$((gpu_idx + 1))
    done <<< "$utils"

    # 打印当前状态（一行摘要）
    local status_line; status_line=$(IFS=', '; echo "${status_parts[*]}")
    if $all_idle; then
        log DEBUG "所有 GPU 利用率均低于阈值 | ${status_line}"
        return 0
    else
        log DEBUG "检测到活跃 GPU | ${status_line}"
        return 1
    fi
}

# ── 执行触发命令 ─────────────────────────────────────────────────────────────
run_trigger() {
    log ALERT "===== 触发条件满足：所有 GPU 连续 ${WINDOW_MINUTES} 分钟利用率低于 ${THRESHOLD}% ====="
    log ALERT "执行命令: ${TRIGGER_CMD}"

    # 将变量导出供命令使用
    export WATCHDOG_THRESHOLD="$THRESHOLD"
    export WATCHDOG_WINDOW_MINUTES="$WINDOW_MINUTES"
    export WATCHDOG_GPU_COUNT="$GPU_COUNT"

    if bash -c "$TRIGGER_CMD"; then
        log INFO "触发命令执行成功。"
    else
        log ERROR "触发命令执行失败（退出码: $?）"
    fi
}

# ── 主监控循环 ────────────────────────────────────────────────────────────────
main() {
    check_already_running
    parse_args "$@"
    detect_gpu_count

    local window_seconds; window_seconds=$(awk "BEGIN { printf \"%d\", ${WINDOW_MINUTES} * 60 + 0.5 }")
    local idle_start_ts=0
    local idle_samples=0

    log INFO "======================================================"
    log INFO "  GPU 监督脚本启动"
    log INFO "  GPU 数量    : ${GPU_COUNT} 张"
    log INFO "  空闲阈值    : < ${THRESHOLD}%"
    log INFO "  触发窗口    : ${WINDOW_MINUTES} 分钟"
    log INFO "  采样间隔    : ${INTERVAL} 秒"
    log INFO "  实际窗口    : ${window_seconds} 秒"
    log INFO "  触发后动作  : ${TRIGGER_CMD}"
    log INFO "  触发后行为  : $( $ONCE && echo '执行一次后退出' || echo '继续监控' )"
    log INFO "======================================================"

    while true; do
        if all_gpus_idle; then
            local now; now=$(date +%s)
            if (( idle_start_ts == 0 )); then
                idle_start_ts=$now
            fi
            idle_samples=$((idle_samples + 1))

            local elapsed=$(( now - idle_start_ts ))
            local remaining=$(( window_seconds - elapsed ))
            log INFO "空闲采样: ${idle_samples} 次 | 已持续: ${elapsed}s | 距触发还需: $( (( remaining > 0 )) && echo ${remaining}s || echo '即将触发' )"

            if (( elapsed >= window_seconds )); then
                run_trigger
                idle_start_ts=0
                idle_samples=0

                if $ONCE; then
                    log INFO "--once 模式：触发后退出。"
                    exit 0
                fi
                log INFO "继续监控中（等待下一次触发）..."
            fi
        else
            if (( idle_samples > 0 )); then
                local reset_elapsed=$(( $(date +%s) - idle_start_ts ))
                log INFO "检测到 GPU 活动，重置空闲状态（之前已连续空闲 ${reset_elapsed}s，采样 ${idle_samples} 次）"
            fi
            idle_start_ts=0
            idle_samples=0
        fi

        sleep "$INTERVAL"
    done
}

# ── 信号处理：优雅退出 ───────────────────────────────────────────────────────
trap 'log INFO "收到退出信号，脚本终止。"; exit 0' SIGINT SIGTERM

main "$@"
