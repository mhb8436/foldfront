# RAPID Local Setup

## Backend

Run commands from the `pipeline-mcp` directory:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` before starting. The backend requires:

- `RUNPOD_API_KEY`
- `MMSEQS_ENDPOINT_ID`
- `PROTEINMPNN_ENDPOINT_ID`
- one AF2 backend: `COLABFOLD_ENDPOINT_ID`, `ALPHAFOLD2_ENDPOINT_ID`, `AF2_ENDPOINT_ID`, or `AF2_URL`
- `PIPELINE_ADMIN_PASSWORD` for the initial local admin account

Then start the backend:

```bash
PYTHONPATH=src python -m pipeline_mcp.http_server --host 127.0.0.1 --port 18080
```

Health check:

```bash
curl -sS http://127.0.0.1:18080/healthz
```

Expected response:

```json
{"ok": true}
```

## Frontend

Run from `frontend`:

```bash
npm ci
npm run dev
```

Open `http://127.0.0.1:5173`. When the UI is served from localhost, it uses
`http://127.0.0.1:18080` as the default API base.

For a server/reverse-proxy deployment, build the static bundle instead:

```bash
npm ci
npm run build
```

Serve `frontend/dist` through Caddy, Nginx, or another HTTPS
reverse proxy, and route `/api/*` to the backend on `127.0.0.1:18080`.

## Surrogate and Experimental-Feedback Dependencies

Surrogate triage embeds candidate sequences with ESM-2 and can use optional
surrogate families. Experimental-feedback evolution reuses measured labels and
does not require AF2 labels unless the operator chooses to generate them through
another run. Install the benchmark/ML extras if you will run surrogate triage or
local experimental-feedback recommendation:

```bash
python -m pip install -r ../requirements-benchmark.txt
```

The pipeline can still call remote MMseqs2, ProteinMPNN, ColabFold/AF2, RFD3,
BioEmu, Rosetta, and ESM embedding workers through RunPod or HTTP providers.
