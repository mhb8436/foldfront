# RunPod Admin UI

Standalone static operations console for the RunPod Serverless endpoints used by RAPID.

## Access

Serve the existing frontend root:

```bash
cd /opt/protein_pipeline/frontend
python3 -m http.server 5173
```

Then open:

```text
http://127.0.0.1:5173/runpod-admin/
```

## What it does

- Lists RunPod serverless endpoints and marks the ones wired into RAPID
- Shows current worker pods for the selected endpoint
- Applies safe endpoint patches for GPU/scaling-related settings
- Shows recent endpoint billing totals

## Backend dependency

This UI expects the updated `pipeline-mcp` server to expose:

- `pipeline.runpod_list_endpoints`
- `pipeline.runpod_get_endpoint`
- `pipeline.runpod_update_endpoint`
- `pipeline.runpod_list_billing`
