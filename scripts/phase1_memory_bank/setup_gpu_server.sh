#!/usr/bin/env bash
set -euo pipefail

echo "==> Phase 1 Memory Bank: GPU Server Setup"
echo "==> Target: NCP L4 4ea instance with ColabFold (AlphaFold2)"

ENV_FILE="${ENV_FILE:-$(dirname "$0")/.env}"
if [ -f "$ENV_FILE" ]; then
  set -a
  source "$ENV_FILE"
  set +a
else
  echo "    Warning: $ENV_FILE not found; AF2 weights will fall back to Google Storage."
fi

echo
echo "==> Step 1/4: Installing system packages (~200 MB)"
sudo apt-get update -y
sudo apt-get install -y \
    build-essential git wget curl unzip \
    python3-venv python3-pip \
    awscli jq flock

export WORKSPACE="${WORKSPACE:-/workspace}"
sudo mkdir -p "$WORKSPACE"
sudo chown -R "$(whoami)" "$WORKSPACE"
cd "$WORKSPACE"

if [ ! -d "$WORKSPACE/venv" ]; then
  python3 -m venv "$WORKSPACE/venv"
fi

source "$WORKSPACE/venv/bin/activate"

echo
echo "==> Step 2/4: Installing Python packages (~2 GB)"
pip install --upgrade pip setuptools wheel

pip install "colabfold[alphafold]==1.5.5" \
    --extra-index-url https://download.pytorch.org/whl/cu121

pip install -U "jax[cuda12_pip]==0.4.26" \
    -f https://storage.googleapis.com/jax-releases/jax_cuda_releases.html

pip install boto3 numpy tqdm biopython

mkdir -p "$WORKSPACE/jobs/pending" \
         "$WORKSPACE/jobs/processing" \
         "$WORKSPACE/jobs/completed" \
         "$WORKSPACE/jobs/failed" \
         "$WORKSPACE/af2_cache" \
         "$WORKSPACE/af2_weights" \
         "$WORKSPACE/logs"

export XLA_PYTHON_CLIENT_PREALLOCATE=false
export TF_FORCE_UNIFIED_MEMORY=1
export XLA_FLAGS="--xla_gpu_deterministic_ops=false"

echo
echo "==> Step 3/4: AF2 weights (~5 GB final, up to ~10 GB during install)"

DISK_FREE_GB=$(df -BG --output=avail "$WORKSPACE" 2>/dev/null | tail -1 | tr -dc '0-9')
if [ -n "$DISK_FREE_GB" ] && [ "$DISK_FREE_GB" -lt 15 ]; then
  echo "    Warning: only ${DISK_FREE_GB} GB free on $WORKSPACE. Recommended: >= 15 GB free for safe install."
fi

if [ -d "$WORKSPACE/af2_weights/params" ] && [ -n "$(ls -A "$WORKSPACE/af2_weights/params" 2>/dev/null)" ]; then
  echo "    AF2 weights already present at $WORKSPACE/af2_weights/params, skipping."
elif [ -n "${AF2_WEIGHTS_S3_PATH:-}" ]; then
  echo "    Downloading from RunPod S3: s3://${RUNPOD_S3_BUCKET}/${AF2_WEIGHTS_S3_PATH}"
  if [ -z "${RUNPOD_S3_ENDPOINT:-}" ] || [ -z "${RUNPOD_S3_ACCESS_KEY:-}" ] || [ -z "${RUNPOD_S3_SECRET_KEY:-}" ]; then
    echo "    Error: RUNPOD_S3_ENDPOINT/ACCESS_KEY/SECRET_KEY required"
    exit 1
  fi

  export AWS_ACCESS_KEY_ID="$RUNPOD_S3_ACCESS_KEY"
  export AWS_SECRET_ACCESS_KEY="$RUNPOD_S3_SECRET_KEY"
  export AWS_DEFAULT_REGION="${RUNPOD_S3_REGION:-eur-no-1}"

  aws s3 sync \
    --endpoint-url "$RUNPOD_S3_ENDPOINT" \
    --region "${RUNPOD_S3_REGION:-eur-no-1}" \
    "s3://${RUNPOD_S3_BUCKET}/${AF2_WEIGHTS_S3_PATH}" \
    "$WORKSPACE/af2_weights/"

  if [ ! -d "$WORKSPACE/af2_weights/params" ]; then
    echo "    Warning: After sync, $WORKSPACE/af2_weights/params not found."
    echo "    Adjust AF2_WEIGHTS_S3_PATH so the synced root contains a 'params/' directory."
    exit 1
  fi
else
  echo "    AF2_WEIGHTS_S3_PATH not set; downloading from Google Storage (slower)"
  cd "$WORKSPACE/af2_weights"
  wget -q --show-progress https://storage.googleapis.com/alphafold/alphafold_params_2022-12-06.tar
  mkdir -p params
  tar -xf alphafold_params_2022-12-06.tar -C params
  rm -f alphafold_params_2022-12-06.tar
fi

echo
echo "==> Step 4/4: Verifying GPU access"
nvidia-smi || echo "    Warning: nvidia-smi failed; check NVIDIA driver installation."

echo
echo "==> Setup complete."
echo "    Activate venv: source $WORKSPACE/venv/bin/activate"
echo "    AF2 weights:   $WORKSPACE/af2_weights/params/"
echo "    Next:          bash launch_workers.sh"
