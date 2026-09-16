# RunPod Endpoint Images

The backend expects RunPod Serverless endpoint IDs in `pipeline-mcp/.env`. The
endpoint IDs are deployment-specific and should not be committed. The Docker
images below are the public images used for the packaged workflow.

| Pipeline stage | `.env` variable | Docker image |
|---|---|---|
| MMseqs2 MSA/search | `MMSEQS_ENDPOINT_ID` | `mimikyou0607/mmseqs-runpod:latest` |
| ProteinMPNN sequence generation | `PROTEINMPNN_ENDPOINT_ID` | `mimikyou0607/proteinmpnn-runpod:latest` |
| ColabFold / AF2 structure prediction | `COLABFOLD_ENDPOINT_ID` or `AF2_ENDPOINT_ID` | `mimikyou0607/colabfold-runpod:20260304_4` |
| RFdiffusion3 backbone generation | `RFD3_ENDPOINT_ID` | `mimikyou0607/rfd3-runpod:260408-3` |
| BioEmu ensemble sampling | `BIOEMU_ENDPOINT_ID` | `mimikyou0607/bioemu-runpod:latest` |
| Rosetta Relax post-processing | `RUNPOD_RELAX_ENDPOINT_ID` | `mimikyou0607/relax_runpod:260428_1` |
| ESM-2 embedding for surrogate/evolution pools | `ESM_EMBEDDING_ENDPOINT_ID` | build from `workers/esm_embedding/` |

## Recommended Practice

- Use pinned tags for manuscript reproduction. Avoid changing endpoint images
  behind the same RunPod endpoint ID during a benchmark run.
- Keep `RUNPOD_API_KEY` and endpoint IDs in `.env` or the server environment.
- Record the image tag, endpoint ID, GPU type, and run date in your lab notebook
  or release notes for each production benchmark.
- If a `latest` image is used, create a release note with the image digest before
  publication so that future users can recover the exact runtime.

## Minimal `.env` Mapping

```env
RUNPOD_API_KEY=
MMSEQS_ENDPOINT_ID=
PROTEINMPNN_ENDPOINT_ID=
COLABFOLD_ENDPOINT_ID=
RFD3_ENDPOINT_ID=
BIOEMU_ENDPOINT_ID=
RUNPOD_RELAX_ENDPOINT_ID=
ESM_EMBEDDING_ENDPOINT_ID=
```

For a persistent self-hosted GPU worker instead of RunPod Serverless, set
`ESM_EMBEDDING_URL` to the worker's `/embed` service base URL.
