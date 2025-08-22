# If you come from bash you might have to change your $PATH.
# export PATH=$HOME/bin:$HOME/.local/bin:/usr/local/bin:$PATH

# Path to your Oh My Zsh installation.
export ZSH="$HOME/.oh-my-zsh"

# Set name of the theme to load --- if set to "random", it will
# load a random theme each time Oh My Zsh is loaded, in which case,
# to know which specific one was loaded, run: echo $RANDOM_THEME
# See https://github.com/ohmyzsh/ohmyzsh/wiki/Themes
ZSH_THEME="robbyrussell"

# Set list of themes to pick from when loading at random
# Setting this variable when ZSH_THEME=random will cause zsh to load
# a theme from this variable instead of looking in $ZSH/themes/
# If set to an empty array, this variable will have no effect.
# ZSH_THEME_RANDOM_CANDIDATES=( "robbyrussell" "agnoster" )

# Uncomment the following line to use case-sensitive completion.
# CASE_SENSITIVE="true"

# Uncomment the following line to use hyphen-insensitive completion.
# Case-sensitive completion must be off. _ and - will be interchangeable.
# HYPHEN_INSENSITIVE="true"

# Uncomment one of the following lines to change the auto-update behavior
# zstyle ':omz:update' mode disabled  # disable automatic updates
# zstyle ':omz:update' mode auto      # update automatically without asking
# zstyle ':omz:update' mode reminder  # just remind me to update when it's time

# Uncomment the following line to change how often to auto-update (in days).
# zstyle ':omz:update' frequency 13

# Uncomment the following line if pasting URLs and other text is messed up.
# DISABLE_MAGIC_FUNCTIONS="true"

# Uncomment the following line to disable colors in ls.
# DISABLE_LS_COLORS="true"

# Uncomment the following line to disable auto-setting terminal title.
# DISABLE_AUTO_TITLE="true"

# Uncomment the following line to enable command auto-correction.
# ENABLE_CORRECTION="true"

# Uncomment the following line to display red dots whilst waiting for completion.
# You can also set it to another string to have that shown instead of the default red dots.
# e.g. COMPLETION_WAITING_DOTS="%F{yellow}waiting...%f"
# Caution: this setting can cause issues with multiline prompts in zsh < 5.7.1 (see #5765)
# COMPLETION_WAITING_DOTS="true"

# Uncomment the following line if you want to disable marking untracked files
# under VCS as dirty. This makes repository status check for large repositories
# much, much faster.
# DISABLE_UNTRACKED_FILES_DIRTY="true"

# Uncomment the following line if you want to change the command execution time
# stamp shown in the history command output.
# You can set one of the optional three formats:
# "mm/dd/yyyy"|"dd.mm.yyyy"|"yyyy-mm-dd"
# or set a custom format using the strftime function format specifications,
# see 'man strftime' for details.
# HIST_STAMPS="mm/dd/yyyy"

# Would you like to use another custom folder than $ZSH/custom?
# ZSH_CUSTOM=/path/to/new-custom-folder

# Which plugins would you like to load?
# Standard plugins can be found in $ZSH/plugins/
# Custom plugins may be added to $ZSH_CUSTOM/plugins/
# Example format: plugins=(rails git textmate ruby lighthouse)
# Add wisely, as too many plugins slow down shell startup.
plugins=(git zsh-syntax-highlighting zsh-autosuggestions)

source $ZSH/oh-my-zsh.sh

# User configuration

# export MANPATH="/usr/local/man:$MANPATH"

# You may need to manually set your language environment
# export LANG=en_US.UTF-8

# Preferred editor for local and remote sessions
# if [[ -n $SSH_CONNECTION ]]; then
#   export EDITOR='vim'
# else
#   export EDITOR='nvim'
# fi

# Compilation flags
# export ARCHFLAGS="-arch $(uname -m)"

# Set personal aliases, overriding those provided by Oh My Zsh libs,
# plugins, and themes. Aliases can be placed here, though Oh My Zsh
# users are encouraged to define aliases within a top-level file in
# the $ZSH_CUSTOM folder, with .zsh extension. Examples:
# - $ZSH_CUSTOM/aliases.zsh
# - $ZSH_CUSTOM/macos.zsh
# For a full list of active aliases, run `alias`.
#
# Example aliases
# alias zshconfig="mate ~/.zshrc"
# alias ohmyzsh="mate ~/.oh-my-zsh"

export WANDB_API_KEY=""
export HF_TOKEN=""

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

# gpu alias
alias nvi="nvidia-smi"
alias gpu="nvitop -m auto"
## kill process using gpu
alias gkill="fuser -v /dev/nvidia0 | awk '{print $0}' | xargs kill -9"
function agpus() {
    export CUDA_VISIBLE_DEVICES="$1"
}

function mkcd() {
    mkdir -p "$1"
    cd "$1"
}

alias tat="tmux attach -t"
alias tns="tmux new-session -s"
alias tls="tmux list-sessions"

function now() {
    date '+%Y-%m-%d-%H-%M-%S-%Z'
}

alias upip="uv pip"

function lsenv() {
    ls -d $HOME/.python/* | xargs -n 1 basename
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
    python_version="${2:-3.12}"

    if [ -z "$env_name" ]; then
        echo "Error: environment name is required."
        return 1
    fi

    # create environment
    uv venv "$HOME/.python/${env_name}" --python "${python_version}"

    # activate environment
    acti "$env_name"
    uv pip install pip uv ipython ipdb
    acti "$env_name"
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

alias ipy="ipython"

# acti py12

. "$HOME/.local/bin/env"

export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"  # This loads nvm
[ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"  # This loads nvm bash_completion

export BROWSER=/usr/local/bin/url-logger
export DISPLAY=""

#THIS MUST BE AT THE END OF THE FILE FOR SDKMAN TO WORK!!!
export SDKMAN_DIR="$HOME/.sdkman"
[[ -s "$HOME/.sdkman/bin/sdkman-init.sh" ]] && source "$HOME/.sdkman/bin/sdkman-init.sh"
export BROWSER=/usr/local/bin/url-logger
