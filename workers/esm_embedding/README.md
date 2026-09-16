# RAPID ESM Embedding Worker

This worker embeds protein sequences with ESM-2 and returns a compressed `npz`
matrix for RAPID surrogate triage and experimental-feedback evolution.

## Build

```bash
cd /opt/protein_pipeline-work/workers/esm_embedding
docker build -t mimikyou0607/esm-embedding-runpod:YYYYMMDD_1 .
docker push mimikyou0607/esm-embedding-runpod:YYYYMMDD_1
```

## RunPod Endpoint

Create a RunPod Serverless endpoint from the pushed image.

Recommended settings:

- GPU: A4000, L4, or better
- Container disk: 20 GB or more
- Volume mount: `/workspace` for Hugging Face cache
- Timeout: 30-60 minutes for large pools
- Idle timeout: keep warm during batch experiments if possible

Environment:

```bash
ESM_MODEL_NAME=facebook/esm2_t6_8M_UR50D
ESM_BATCH_SIZE=64
ESM_MAX_LENGTH=1024
HF_HOME=/workspace/hf_cache
TRANSFORMERS_CACHE=/workspace/hf_cache
```

## RAPID Configuration

After creating the endpoint, configure RAPID:

```bash
ESM_EMBEDDING_PROVIDER=runpod
ESM_EMBEDDING_ENDPOINT_ID=<runpod-endpoint-id>
```

For a persistent HTTP GPU worker instead:

```bash
ESM_EMBEDDING_PROVIDER=http_api
ESM_EMBEDDING_URL=http://<gpu-host>:18170
```

## Input Contract

```json
{
  "model_name": "facebook/esm2_t6_8M_UR50D",
  "batch_size": 64,
  "max_length": 1024,
  "sequences": [
    {"id": "seq_1", "sequence": "ACDEFGHIKLMNPQRSTVWY"}
  ]
}
```

## Output Contract

```json
{
  "ok": true,
  "model_name": "facebook/esm2_t6_8M_UR50D",
  "device": "cuda",
  "count": 1,
  "dimension": 320,
  "dtype": "float32",
  "ids": ["seq_1"],
  "sequence_hashes": ["..."],
  "embedding_key": "embeddings",
  "embeddings_npz_b64": "...",
  "elapsed_s": 0.42
}
```

`embeddings_npz_b64` is a base64-encoded compressed NumPy archive containing an
array named `embeddings`.

## Local HTTP Mode

```bash
cd /opt/protein_pipeline-work/workers/esm_embedding
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn http_server:app --host 0.0.0.0 --port 18170
```

Validate:

```bash
curl -fsS http://127.0.0.1:18170/healthz
curl -fsS -X POST http://127.0.0.1:18170/embed \
  -H 'content-type: application/json' \
  --data @test_payload.json
```
