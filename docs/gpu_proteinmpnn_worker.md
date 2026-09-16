# GPU ProteinMPNN Worker

This runbook moves only ProteinMPNN from RunPod Serverless to the NCP GPU server.
MMseqs2, ColabFold/AF2, RFD3, BioEmu, and Relax can stay on their existing providers.

ProteinMPNN does not need the large AlphaFold/MMseqs databases. The current
`mimikyou0607/proteinmpnn-runpod:latest` image already contains `/opt/ProteinMPNN`
and `/workspace/handler.py`, so the GPU server only needs the image plus a small
HTTP wrapper.

## Network

Open the GPU server worker port only from the current pipeline server.

```text
GPU server inbound:
TCP 18101 source <pipeline-server-ip>/32
```

If a different port is opened, use the same port in `PROTEINMPNN_GPU_URL` and
`PROTEINMPNN_WORKER_PORT`.

## Start Worker On GPU Server

Run as the `pipeline` user on the GPU server.

```bash
cd ~/protein_pipeline
docker pull mimikyou0607/proteinmpnn-runpod:latest

mkdir -p ~/protein_pipeline_runtime/proteinmpnn
cp deploy/gpu/proteinmpnn_http_worker.py ~/protein_pipeline_runtime/proteinmpnn/

export PROTEINMPNN_WORKER_TOKEN='<shared-worker-token>'
export PROTEINMPNN_WORKER_PORT=18101

docker run -d \
  --name proteinmpnn-worker \
  --restart unless-stopped \
  --gpus all \
  -p 18101:18101 \
  -e PROTEINMPNN_WORKER_TOKEN="$PROTEINMPNN_WORKER_TOKEN" \
  -e PROTEINMPNN_WORKER_PORT="$PROTEINMPNN_WORKER_PORT" \
  -v "$HOME/protein_pipeline_runtime/proteinmpnn:/worker:ro" \
  --entrypoint python \
  mimikyou0607/proteinmpnn-runpod:latest \
  /worker/proteinmpnn_http_worker.py --host 0.0.0.0 --port "$PROTEINMPNN_WORKER_PORT"
```

Validate locally on the GPU server:

```bash
curl -sS http://127.0.0.1:18101/healthz
```

Validate from the current pipeline server:

```bash
curl -sS http://211.188.35.221:18101/healthz
```

## Configure Pipeline Server

Set these in `/opt/protein_pipeline/pipeline-mcp/.env` for production, or in the
matching dev/staging `.env` file.

```env
PROTEINMPNN_PROVIDER=gpu_http
PROTEINMPNN_GPU_URL=http://211.188.35.221:18101
PROTEINMPNN_GPU_TOKEN=<shared-worker-token>
PROTEINMPNN_GPU_TIMEOUT_S=21600
```

Keep `PROTEINMPNN_ENDPOINT_ID` in the file for rollback if desired. It is not
required while `PROTEINMPNN_PROVIDER=gpu_http`.

Restart the pipeline backend after changing `.env`.

