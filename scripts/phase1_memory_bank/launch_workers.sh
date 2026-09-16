#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ENV_FILE="${ENV_FILE:-$SCRIPT_DIR/.env}"

if [ ! -f "$ENV_FILE" ]; then
  echo "Error: $ENV_FILE not found. Copy config.example.env to .env and fill in credentials."
  exit 1
fi

source "${WORKSPACE:-/workspace}/venv/bin/activate"

set -a
source "$ENV_FILE"
set +a

NUM_GPUS="${NUM_GPUS:-4}"
LOG_DIR="${LOG_DIR:-/workspace/logs}"
mkdir -p "$LOG_DIR"

echo "==> Launching $NUM_GPUS workers on GPUs 0..$((NUM_GPUS - 1))"

PIDS=()
for ((i=0; i<NUM_GPUS; i++)); do
  export WORKER_GPU_ID=$i
  export WORKER_ID="gpu${i}"
  LOG_FILE="$LOG_DIR/worker_gpu${i}.log"
  echo "  -> GPU $i, logging to $LOG_FILE"
  python3 "$SCRIPT_DIR/gpu_worker.py" --env "$ENV_FILE" --gpu $i > "$LOG_FILE" 2>&1 &
  PIDS+=($!)
done

echo "==> Started PIDs: ${PIDS[*]}"
echo "==> Follow logs with: tail -f $LOG_DIR/worker_gpu*.log"

trap 'echo "Stopping workers..."; kill "${PIDS[@]}" 2>/dev/null || true; exit 0' INT TERM

wait
