#!/bin/bash
#SBATCH --account=nvr_lpr_llm
#SBATCH --partition=cpu_long
#SBATCH --job-name=claude-slack-bridge
#SBATCH --cpus-per-task=32
#SBATCH --mem=64G
#SBATCH --time=5-00:00:00
#SBATCH --output=slurm-%j.log

echo "$(date '+%F %T') bridge job ${SLURM_JOB_ID} on $(hostname)"
BRIDGE_DIR="${BRIDGE_DIR:-${SLURM_SUBMIT_DIR:-$PWD}}"
exec "$BRIDGE_DIR/run.sh"
