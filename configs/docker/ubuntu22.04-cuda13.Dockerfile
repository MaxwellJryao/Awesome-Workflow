# 1. 基础镜像：Cuda 13.1 devel (ARM64)
FROM nvidia/cuda:13.1.1-cudnn-devel-ubuntu22.04

# 2. 环境变量
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC
ENV PYTHONUNBUFFERED=1

# NVIDIA 容器运行时变量
# 默认 compute,utility (无头服务器安全); 需要图形时 docker run -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics,display
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility

# CUDA 环境
ENV CUDA_HOME=/usr/local/cuda
ENV PATH=${CUDA_HOME}/bin:${PATH}
ENV LD_LIBRARY_PATH=${CUDA_HOME}/lib64:${CUDA_HOME}/compat:${LD_LIBRARY_PATH}

# 3. 安装系统依赖
RUN apt update && apt install -y --no-install-recommends \
    # --- 基础工具 ---
    zsh vim wget curl git git-lfs unzip \
    net-tools tmux htop ca-certificates \
    software-properties-common \
    # --- C/C++ 构建工具链 ---
    build-essential cmake ninja-build pkg-config \
    gcc-12 g++-12 \
    # --- Python 构建依赖 ---
    python3-dev libpython3-dev libffi-dev libssl-dev \
    # --- 图像/媒体库 (torchvision, PIL 等) ---
    libjpeg-dev libpng-dev zlib1g-dev libfreeimage-dev \
    libavcodec-dev libavformat-dev libswscale-dev \
    # --- OpenGL/EGL/Vulkan (Isaac Sim, 3D 渲染) ---
    libgl1-mesa-dev libglu1-mesa-dev libglvnd-dev libegl1-mesa-dev \
    libvulkan1 mesa-utils \
    # --- X11/VNC (远程桌面) ---
    tigervnc-standalone-server tigervnc-common tigervnc-tools \
    xfce4 xfce4-goodies xfce4-terminal xvfb x11vnc \
    # --- 分布式训练 (MPI) ---
    libopenmpi-dev openmpi-bin \
    # --- CUDA 兼容 ---
    cuda-compat-13-0 && \
    apt clean && rm -rf /var/lib/apt/lists/*

# 4. 配置 GCC-12 作为默认编译器
RUN update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-12 12 \
    --slave /usr/bin/g++ g++ /usr/bin/g++-12

# 5. 安装 uv (Astral.sh)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh && \
    ln -s /root/.local/bin/uv /usr/local/bin/uv

# 6. 安装 Oh My Zsh + 插件
RUN sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended && \
    git clone https://github.com/zsh-users/zsh-autosuggestions ${ZSH_CUSTOM:-~/.oh-my-zsh/custom}/plugins/zsh-autosuggestions && \
    git clone https://github.com/zsh-users/zsh-syntax-highlighting.git ${ZSH_CUSTOM:-~/.oh-my-zsh/custom}/plugins/zsh-syntax-highlighting

# 7. 克隆 Awesome-Workflow 并配置 .zshrc
WORKDIR /root
RUN git clone https://github.com/MaxwellJryao/Awesome-Workflow.git && \
    cp Awesome-Workflow/shell/ubuntu22.04-cuda13-arm64.zshrc ~/.zshrc && \
    rm -rf Awesome-Workflow

# 8. 为 Apptainer 挂载点创建占位符（HPC 适配）
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

# 9. 设置默认 Shell
ENV SHELL=/bin/zsh
ENTRYPOINT ["/bin/zsh"]