#!/usr/bin/env bash
set -euo pipefail

cd /opt/protein_pipeline
target="data/benchmark/cath_pilot_emb_640d.npy"
log="logs/esm_ablation_after_640d.log"
mkdir -p logs

echo "[watcher] $(date -Iseconds) waiting for $target ..." | tee -a "$log"
while [[ ! -f "$target" ]]; do
    sleep 30
    if ! pgrep -f "01_compute_embeddings.py" > /dev/null 2>&1; then
        if [[ ! -f "$target" ]]; then
            echo "[watcher] $(date -Iseconds) embedding process died without producing $target. abort." | tee -a "$log"
            exit 1
        fi
    fi
done

echo "[watcher] $(date -Iseconds) $target ready, running ESM-size ablation ..." | tee -a "$log"
/opt/protein_pipeline/venv/bin/python3 scripts/benchmark/04_esm_size_ablation.py >> "$log" 2>&1
echo "[watcher] $(date -Iseconds) ESM-size ablation done" | tee -a "$log"
