import nvitop
import os
import argparse
import re
import subprocess

# Parse arguments
parser = argparse.ArgumentParser(description='Allocate GPUs')
parser.add_argument('--min_free_mem', type=str, default='24GB', help='Minimum free memory')
parser.add_argument('--min_count', type=int, default=1, help='Minimum count of GPUs')
parser.add_argument('--format', type=str, default='index', help='Format of the output')
parser.add_argument('--max_gpu_utilization', type=int, default=50, help='Maximum GPU utilization in percentage')
parser.add_argument('--usage', type=str, default='check', help='Check or allocate GPUs', choices=['check', 'allocate'])
args = parser.parse_args()

# match the dimension of memory
pattern = re.compile(r'(?i)(\d+)\s*([KMG]i?B)')
dimension = pattern.match(args.min_free_mem)
min_free_mem, dim = dimension.group(1), dimension.group(2)

if dim.lower() in ['mb', 'mib']:
    dim = 'MiB'
elif dim.lower() in ['gb', 'gib']:
    dim = 'GiB'
else:
    raise ValueError(f"Unsupported memory value: {dimension}")

available_gpus = nvitop.select_devices(format='index', min_free_memory=f'{min_free_mem}{dim}',
                                       min_count=args.min_count, max_gpu_utilization=args.max_gpu_utilization)

if args.usage == 'check':
    print('='*5, ' Available GPUs ', '='*5)
    print(available_gpus)
elif args.usage == 'allocate':
    # Set the environment variable
    available_gpus = [str(gpu) for gpu in available_gpus]
    os.environ['CUDA_VISIBLE_DEVICES'] = ','.join(available_gpus)
    print(f"export CUDA_VISIBLE_DEVICES={os.environ['CUDA_VISIBLE_DEVICES']}")