# change HOME path
export HOME="/shared/storage-01/$(whoami)"

# git alias
alias gp="git push"
alias gpl="git pull"
alias gco="git checkout"
alias gcb="git checkout -b"
alias gcm="git commit -m"
alias ga="git add"
alias gs="git status"
alias gl="git log"
alias gd="git diff"
alias gaa="git add --all"

# python alias
alias python="~/.python/base/bin/python"
alias python3="~/.python/base/bin/python3"
alias py="python3"
alias upip="python3 -m uv pip"

# gpu alias
alias nvi="nvidia-smi"
alias gpu="nvitop -m auto"
## kill process using gpu
alias gkill="fuser -v /dev/nvidia0 | awk '{print $0}' | xargs kill -9"
function agpus() {
    export CUDA_VISIBLE_DEVICES="$1"
}

# dirs and files alias
function mkcd() {
    mkdir -p "$1"
    cd "$1"
}
alias du="du -ah --max-depth=1 | sort -h"

# tmux alias
alias tat="tmux attach -t"
alias tns="tmux new-session -s"
alias tls="tmux list-sessions"

# ray
export RAY_ROOT_DIR="/shared/storage-01/$(whoami)/.cache/ray"

# wandb
export WANDB_API_KEY=""

# huggingface
export HF_HOME="/shared/storage-01/$(whoami)/.cache/huggingface"

# timestamp
# yyyy-mm-dd-hh-mm-ss-timezone
function now() {
    date '+%Y-%m-%d-%H-%M-%S-%Z'
}

# activate environment
function acti() {
    env_name="$1"
    activation_script="$HOME/.python/${env_name}/bin/activate"

    if [ ! -f "$activation_script" ]; then
        echo "Error: script '$activation_script' does not exist."
        return 1
    fi

    # activate
    source "$activation_script"

    ceiling="===== Activated Env: ${env_name} ====="
    echo "$ceiling"

    # print python path and version 
    python3_path="$HOME/.python/${env_name}/bin/python3"
    python_path="$HOME/.python/${env_name}/bin/python"
    alias python3=$python3_path
    alias python=$python_path
    echo "Python path: $python_path"
    $HOME/.python/${env_name}/bin/python --version
}

# create a new python environment
function mkenv() {
    env_name="$1"

    if [ -z "$env_name" ]; then
        echo "Error: environment name is required."
        return 1
    fi

    # create environment
    python3 -m venv "$HOME/.python/${env_name}"

    # activate environment
    acti "$env_name"
    pip install --upgrade pip
    pip install uv
    $HOME/.python/${env_name}/bin/python3 -m uv pip install ipython ipdb
}

# list available environments
function lsenv() {
    ls -d $HOME/.python/* | xargs -n 1 basename
}

function rmenv() {
    env_name="$1"

    if [ -z "$env_name" ]; then
        echo "Error: environment name is required."
        return 1
    fi

    # confirm
    echo "Are you sure you want to remove environment '$env_name'? (y/n): "
    read REPLY
    case $REPLY in
        y|Y)
            rm -r "$HOME/.python/${env_name}"
            echo "Environment '$env_name' removed."
            ;;
        n|N)
            echo "Aborted."
            return 1
    esac

    rm -r "$HOME/.python/${env_name}"
}

# slurm
## allocate gpus on a slurm cluster
function igpu() {
    local gpu=${1:-1}
    local time=${2:-24}
    local mem=${3:-256}
    local cpu=${4:-16}
    srun --account=YOUR_ACCOUNT --partition=YOUR_PARTITION --nodes=1 --tasks=1 --tasks-per-node=1 --cpus-per-task=$cpu --mem=${mem}g --gpus-per-node=${gpu} --time=${time}:00:00 --pty zsh
}

function lsproc() {
    local proc_name=$1
    if [ -z "$proc_name" ]; then
        echo "Error: process name is required."
        return 1
    fi
    ps -ux | grep $proc_name
}

function kproc() {
    local proc_name=$1
    if [ -z "$proc_name" ]; then
        echo "Error: process name is required."
        return 1
    fi
    local proc_id=$(pgrep -f $proc_name)
    if [ -z "$proc_id" ]; then
        echo "Error: process '$proc_name' not found."
        return 1
    fi

    # confirm
    echo "Are you sure you want to kill processes '$proc_name'? (y/n): "
    read REPLY
    case $REPLY in
        y|Y)
            ps -ux | grep $proc_name | awk '{print $2}' | xargs -r kill -9
            ;;
        n|N)
            echo "Aborted."
            return 1
    esac
}

alias sqq="squeue | grep $(whoami)"

cd ~
acti base
