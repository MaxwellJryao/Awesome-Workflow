# 1. 基础镜像：使用你指定的 Cuda 13.1 镜像 (ARM64)
FROM nvidia/cuda:13.1.1-cudnn-devel-ubuntu22.04

# 设置环境变量，防止 apt 安装过程中出现交互提示
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

# 2. 安装系统依赖
RUN apt update && apt install -y --no-install-recommends \
    zsh \
    vim \
    wget \
    curl \
    git \
    git-lfs \
    gcc-12 \
    g++-12 \
    tigervnc-standalone-server \
    tigervnc-common \
    tigervnc-tools \
    xfce4 \
    xfce4-goodies \
    xfce4-terminal \
    net-tools \
    tmux \
    xvfb \
    x11vnc \
    software-properties-common \
    mesa-utils \
    cuda-compat-13-0 \
    ca-certificates && \
    # 清理缓存以减小体积
    apt clean && rm -rf /var/lib/apt/lists/*

# 3. 配置 GCC-12 作为默认编译器
RUN update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-12 12 \
    --slave /usr/bin/g++ g++ /usr/bin/g++-12

# 4. 安装 uv (Astral.sh)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh && \
    # 将 uv 路径添加到全局，方便后续使用
    ln -s /root/.local/bin/uv /usr/local/bin/uv

# 5. 安装 Oh My Zsh (使用无人值守模式避免安装脚本卡死)
RUN sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended

# 6. 安装 Zsh 插件
RUN git clone https://github.com/zsh-users/zsh-autosuggestions ${ZSH_CUSTOM:-~/.oh-my-zsh/custom}/plugins/zsh-autosuggestions && \
    git clone https://github.com/zsh-users/zsh-syntax-highlighting.git ${ZSH_CUSTOM:-~/.oh-my-zsh/custom}/plugins/zsh-syntax-highlighting

# 7. 克隆你的 Awesome-Workflow 并配置 .zshrc
WORKDIR /root
RUN git clone https://github.com/MaxwellJryao/Awesome-Workflow.git && \
    cp Awesome-Workflow/shell/ubuntu22.04-cuda13-arm64.zshrc ~/.zshrc && \
    rm -rf Awesome-Workflow

# 为 Apptainer 挂载点创建占位符（HPC 适配优化）
RUN mkdir -p /usr/bin \
             /usr/share/glvnd/egl_vendor.d \
             /usr/share/vulkan/implicit_layer.d \
             /usr/share/egl/egl_external_platform.d \
             /usr/share/nvidia \
             /var/run/nvidia-persistenced && \
    touch /usr/bin/nvidia-smi \
          /usr/bin/nvidia-debugdump \
          /usr/bin/nvidia-persistenced \
          /usr/bin/nvidia-cuda-mps-control \
          /usr/bin/nvidia-cuda-mps-server \
          /usr/share/glvnd/egl_vendor.d/10_nvidia.json \
          /usr/share/vulkan/implicit_layer.d/nvidia_layers.json \
          /usr/share/egl/egl_external_platform.d/10_nvidia_wayland.json \
          /usr/share/egl/egl_external_platform.d/15_nvidia_gbm.json \
          /usr/share/nvidia/nvoptix.bin \
          /var/run/nvidia-persistenced/socket

# 8. 设置默认 Shell
ENV SHELL=/bin/zsh
ENTRYPOINT ["/bin/zsh"]