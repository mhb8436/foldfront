# RAPID

RAPID is an MCP-enabled, artifact-preserving platform for staged
solubility-aware protein redesign runs and static/local browser execution.

## Highlights
- Pipeline and Workflow Studio execution modes in the frontend
- Optional backbone generation with RFD3 and/or BioEmu
- MSA, conservation, ligand mask/mask consensus, ProteinMPNN tier design, SoluProt, AF2/ColabFold, and novelty/WT-diff stages
- Analyze tab with Compare Studio, Run-to-Run Compare, weighted Hit List, report generation, and export packaging
- RunPod Admin console for managed serverless endpoint monitoring, billing review, worker inspection, and safe scaling patches
- Safe partial reruns: the UI defaults to forking a new `run_id`, and the backend rejects unsafe late-stage reruns when upstream inputs changed

## Core stage order
`msa -> rfd3 -> bioemu -> design -> soluprot -> af2 -> novelty`

The full pipeline also writes conservation, masking, WT comparison, report, and agent-panel artifacts around those core stages.

## Frontend overview
- `Setup`: choose a mode, attach inputs, run preflight, and launch a run
- `Workflow Studio`: checkpoint-based pipeline execution with checkpoint review in Monitor
- `Monitor`: status, artifacts, report actions, agent panel, workflow review gate
- `Analyze`: Compare Studio, comparison summary, run-to-run compare, hit list, candidate charts, feedback, experiments, and report review
- `RunPod Admin`: fleet-level RunPod operations view with calendar-aligned usage/spend charts, endpoint detail, worker state, CSV/SVG exports, and patch controls

Compare Studio currently uses:
- tier-only quick compare presets: `WT vs RFD3`, `WT vs BioEmu`, `RFD3 vs BioEmu`
- collapsed baseline references (`Input Structure`, `Working Backbone`, `WT ColabFold`)
- inline comparison summary cards for funnel, WT-vs-design, source compare, tier compare, distributions, and sequence diversity

## Safe rerun model
- Default behavior is to create a new `run_id`
- Reusing an existing `run_id` is intended only for partial reruns in `pipeline` or `workflow` mode
- In the UI, you must select a run, load its `request.json`, and explicitly enable `Continue same run`
- Reusing the same `run_id` overwrites downstream artifacts from the chosen `start_from`
- If upstream inputs changed, such as target FASTA/PDB, backbone source settings, design chains, or fixed positions, use a new `run_id` or restart from `msa`
- Backend request-diff guards and stage-specific request hashes prevent stale-cache reuse for unsafe partial reruns

## Required environment variables
Required:
- `RUNPOD_API_KEY`
- `MMSEQS_ENDPOINT_ID`
- `PROTEINMPNN_ENDPOINT_ID`

Common optional:
- `RFD3_ENDPOINT_ID`
- `BIOEMU_ENDPOINT_ID`
- `DIFFDOCK_ENDPOINT_ID`
- `ALPHAFOLD2_ENDPOINT_ID`
- `AF2_URL`
- `SOLUPROT_URL`
- `PIPELINE_OUTPUT_ROOT`

## Core MCP tools
Execution:
- `pipeline.run`
- `pipeline.preflight`
- `pipeline.plan_from_prompt`
- `pipeline.run_from_prompt`
- `pipeline.af2_predict`
- `pipeline.run_af2`
- `pipeline.diffdock`
- `pipeline.run_diffdock`

Analysis and reporting:
- `pipeline.compare_runs`
- `pipeline.get_hit_list`
- `pipeline.export_results_package`
- `pipeline.generate_report`
- `pipeline.get_report`
- `pipeline.save_report`

Inspection and operations:
- `pipeline.status`
- `pipeline.list_runs`
- `pipeline.list_artifacts`
- `pipeline.read_artifact`
- `pipeline.list_agent_events`
- `pipeline.cancel_run`
- `pipeline.delete_run`

RunPod admin and monitoring:
- `pipeline.runpod_list_endpoints`
- `pipeline.runpod_get_endpoint`
- `pipeline.runpod_update_endpoint`
- `pipeline.runpod_list_billing`
- `pipeline.runpod_get_history`

Review data:
- `pipeline.submit_feedback`
- `pipeline.list_feedback`
- `pipeline.submit_experiment`
- `pipeline.list_experiments`

## Recommended execution flow
1. Call `pipeline.plan_from_prompt` or fill Setup in the UI.
2. Run `pipeline.preflight` for pipeline/workflow-style runs.
3. Launch `pipeline.run`.
4. Monitor with `pipeline.status` and artifact/report views.
5. Use Analyze for Compare Studio, run-to-run comparison, hit list, and exports.

## Key output files
Outputs are written under `PIPELINE_OUTPUT_ROOT/<run_id>/`.

Common top-level artifacts:
- `request.json`
- `status.json`
- `events.jsonl`
- `summary.json`
- `comparisons.json`
- `report.md`
- `report_ko.md`
- `agent_panel_report.md`
- `agent_panel_report_ko.md`

Stage outputs are written under subdirectories such as:
- `msa/`
- `backbones/`
- `rfd3/`
- `bioemu/`
- `tiers/<tier>/`
- `wt/`
- `agent_panel/`

Use `pipeline.list_artifacts` and `pipeline.read_artifact` to inspect outputs remotely.

## Frontend local run
The frontend is static and does not require a build step.

```bash
cd /opt/protein_pipeline/frontend
python3 -m http.server 5173
```

Open `http://127.0.0.1:5173`.

Available UI routes:
- Main console: `http://127.0.0.1:5173/`
- RunPod Admin: `http://127.0.0.1:5173/runpod-admin/`

RunPod Admin is a standalone operations console for the RunPod Serverless endpoints wired into `protein_pipeline`.

- Starts with fleet-wide monitoring for the selected calendar window (`week`, `month`, `last 6 months`)
- Lets you filter to managed endpoints only, then drill into a single endpoint
- Shows worker state, queued/running job counts, spend history, and per-endpoint downloads
- Supports safe endpoint patches for GPU types, scaler settings, worker min/max, timeouts, template, and network volume
- Falls back to health-only monitoring if the RunPod key cannot access admin or billing APIs

## Auth and CORS
- `PIPELINE_AUTH_ENABLED=1`
- `PIPELINE_ADMIN_USERNAME`
- `PIPELINE_ADMIN_PASSWORD`
- `PIPELINE_AUTH_TOKEN_TTL_S`
- `PIPELINE_CORS_ORIGINS`
- `PIPELINE_AUTH_TOKEN` or `PIPELINE_AUTH_USERNAME` + `PIPELINE_AUTH_PASSWORD`
- `PIPELINE_OIDC_ISSUER`
- `PIPELINE_OIDC_CLIENT_ID`
- `PIPELINE_OIDC_AUDIENCE` (optional, defaults to `PIPELINE_OIDC_CLIENT_ID`)
- `PIPELINE_OIDC_SCOPES` (optional, defaults to `openid profile email`)
- `PIPELINE_OIDC_PROVIDER_NAME` (optional, defaults to `KBF SSO`)
- `PIPELINE_OIDC_JWKS_URL` (optional override)
- `PIPELINE_OIDC_ALGORITHMS` (optional, defaults to `RS256`)

`PIPELINE_AUTH_*` keeps the legacy local admin login available. `PIPELINE_OIDC_*` enables KBF SSO and is the preferred mode for the production portal/subdomain deployment.

## Remote MCP endpoint
- Public team-shared endpoint: `https://pipeline.k-biofoundrycopilot.duckdns.org/mcp`
- Auth: `Authorization: Bearer <KBF SSO access token>`
- Remote MCP reuses the same authorization rules as the HTTP tool server:
  - non-admin users only see non-admin tools
  - non-admin `pipeline.run` and `pipeline.run_from_prompt` calls get a user-scoped `run_id` when omitted
  - run-scoped tools only allow `run_id` values owned by the caller
- `/tools/list` and `/tools/call` remain available for direct HTTP automation and internal ops; `/mcp` is the MCP-facing JSON-RPC surface routed through Caddy/TLS

## Docs
- `docs/USAGE.md`: UI and API usage guide
- `docs/runbook.md`: operator runbook
- `docs/stepper_orchestration.md`: stepwise orchestration and safe `run_id` reuse
- `docs/runpod_model_execution.md`: RunPod execution model details
- `docs/ui_pipeline_ppt_ko.md`: Korean UI/pipeline slide notes
- `frontend/runpod-admin/README.md`: RunPod Admin UI access and backend dependency summary
- `frontend/runpod-admin/TODO.md`: RunPod Admin scope, rationale, and follow-up work

## Repo structure
- `frontend/`: static console UI
- `pipeline-mcp/`: MCP server, tools, pipeline runner, and reporting
- `docs/`: user/operator documentation
- `deploy/`: deployment assets
