# GPU Allocation
1. Install the [nvitop](https://github.com/XuehaiPan/nvitop) package.
   ```bash
   pip install nvitop
   ```
2. If you want to check current available gpus, run
   ```bash
   python alloc_gpus.py
   ```
   If you want to allocate gpus, run
   ```bash
   source <(python alloc_gpus.py --usage=allocate)
   ```