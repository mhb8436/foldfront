#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

ENV_FILE="${ENV_FILE:-$SCRIPT_DIR/.env}"
CATH_DIR="${CATH_DIR:-/opt/protein_pipeline/cath_train}"
OUT_DIR="${OUT_DIR:-/opt/protein_pipeline/phase1_input}"
LIMIT="${LIMIT:-}"
NUM_SEEDS="${NUM_SEEDS:-30}"
NUM_MPNN="${NUM_MPNN:-100}"
WORKERS="${WORKERS:-4}"
BATCH_SIZE="${BATCH_SIZE:-8}"
BUCKET_WIDTH="${BUCKET_WIDTH:-32}"

if [ ! -f "$ENV_FILE" ]; then
  echo "Error: $ENV_FILE not found. Copy config.example.env to .env and fill in credentials."
  exit 1
fi

echo "================================"
echo "Phase 1 Memory Bank — Orchestrator"
echo "================================"
echo "  CATH dir    : $CATH_DIR"
echo "  Output dir  : $OUT_DIR"
echo "  Env file    : $ENV_FILE"
echo "  MPNN/target : $NUM_MPNN"
echo "  Seeds       : $NUM_SEEDS per target"
echo "  Workers     : $WORKERS"
echo "  Batch size  : $BATCH_SIZE"
echo "  Bucket w.   : $BUCKET_WIDTH"
echo "  Limit       : ${LIMIT:-<all>}"
echo

LIMIT_ARG=""
if [ -n "$LIMIT" ]; then
  LIMIT_ARG="--limit $LIMIT"
fi

echo "==> Step 1: Preprocessing (MPNN → SoluProt → ESM → K-means)"
python3 prepare_cath_batches.py \
  --cath-dir "$CATH_DIR" \
  --out-dir "$OUT_DIR" \
  --env "$ENV_FILE" \
  --num-mpnn "$NUM_MPNN" \
  --num-seeds "$NUM_SEEDS" \
  --workers "$WORKERS" \
  $LIMIT_ARG

LATEST_FASTA=$(ls -t "$OUT_DIR"/phase1_seeds_*.fasta 2>/dev/null | head -n 1)
if [ -z "$LATEST_FASTA" ]; then
  echo "Error: No seed FASTA produced."
  exit 1
fi
echo "==> Produced: $LATEST_FASTA"

echo
echo "==> Step 2: Uploading length-bucketed batches to RunPod S3"
python3 submit_batches.py "$LATEST_FASTA" \
  --env "$ENV_FILE" \
  --batch-size "$BATCH_SIZE" \
  --bucket-width "$BUCKET_WIDTH"

echo
echo "==> Step 3: Monitoring job queue (Ctrl+C to detach; workers keep running)"
echo "    Tip: run 'python3 collect_results.py' periodically to drain completed/ to local Expert bank"
python3 monitor.py --env "$ENV_FILE" --interval 60
