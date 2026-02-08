#!/usr/bin/env bash
set -euo pipefail

# ============================================================
#  Docker Run — 尽量模拟宿主机环境 (全 GPU / host 网络 / host IPC)
#
#  用法:
#    ./run.sh                              # 默认镜像，交互式 zsh
#    ./run.sh -i my-image:latest           # 指定镜像
#    ./run.sh -n my-container              # 指定容器名
#    ./run.sh -v /data:/data               # 额外挂载 (可多次)
#    ./run.sh -d                            # 后台运行 (detach)
#    ./run.sh -e KEY=VAL                   # 额外环境变量 (可多次)
#    ./run.sh -- python train.py           # 自定义命令 (替代默认 zsh)
# ============================================================

IMAGE="ubuntu22.04-cuda13-amd64:v1"
CONTAINER_NAME=""
DETACH=false
EXTRA_VOLUMES=()
EXTRA_ENVS=()

usage() {
    sed -n '/^#  用法/,/^# ===/p' "$0" | head -n -1
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -i|--image)      IMAGE="$2";          shift 2 ;;
        -n|--name)       CONTAINER_NAME="$2";  shift 2 ;;
        -v|--volume)     EXTRA_VOLUMES+=("-v" "$2"); shift 2 ;;
        -e|--env)        EXTRA_ENVS+=("-e" "$2");    shift 2 ;;
        -d|--detach)     DETACH=true;          shift ;;
        -h|--help)       usage ;;
        --)              shift; break ;;
        *)               break ;;
    esac
done

# --- 构建 docker run 参数 ---
ARGS=(
    # ---- GPU ----
    --gpus all

    # ---- 网络: 使用宿主机网络栈 (端口、DNS 完全共享) ----
    --network host

    # ---- IPC: 共享宿主机共享内存 (NCCL 多卡通信必需) ----
    --ipc host

    # ---- PID: 可看到宿主机进程 (方便 debug / nvidia-smi) ----
    --pid host

    # ---- UTS: 共享宿主机 hostname ----
    --uts host

    # ---- 共享内存: 默认 64M 太小，训练 DataLoader 会 OOM ----
    --shm-size 64g

    # ---- 特权能力 (perf/infiniband/调试) ----
    --cap-add SYS_PTRACE
    --cap-add IPC_LOCK
    --security-opt seccomp=unconfined

    # ---- 挂载宿主机常用目录 ----
    # -v /home:/home
    # -v /tmp:/tmp
    # -v /etc/localtime:/etc/localtime:ro
    # -v /etc/timezone:/etc/timezone:ro

    # ---- NVIDIA 环境变量 ----
    -e NVIDIA_VISIBLE_DEVICES=all
)

# 自动检测: 宿主机有 /dev/nvidia-modeset 才启用 graphics,display
if [[ -e /dev/nvidia-modeset ]]; then
    ARGS+=(-e NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics,display,video)
else
    echo ">>> [INFO] /dev/nvidia-modeset not found (headless server), using compute,utility only"
    ARGS+=(-e NVIDIA_DRIVER_CAPABILITIES=compute,utility)
fi

# 容器名
if [[ -n "$CONTAINER_NAME" ]]; then
    ARGS+=(--name "$CONTAINER_NAME")
fi

# 后台 or 交互
if [[ "$DETACH" == true ]]; then
    ARGS+=(-d)
else
    ARGS+=(-it)
fi

# 额外挂载和环境变量
ARGS+=("${EXTRA_VOLUMES[@]+"${EXTRA_VOLUMES[@]}"}")
ARGS+=("${EXTRA_ENVS[@]+"${EXTRA_ENVS[@]}"}")

# 镜像
ARGS+=("$IMAGE")

# 自定义命令 (-- 之后的部分)，默认进入 zsh
if [[ $# -gt 0 ]]; then
    ARGS+=("$@")
fi

echo ">>> docker run ${ARGS[*]}"
exec docker run "${ARGS[@]}"
