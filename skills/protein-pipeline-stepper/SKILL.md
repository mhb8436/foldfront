---
name: protein-pipeline-stepper
description: Connect to and run the protein-pipeline via MCP. Covers one-click token setup (mcp.json) for VS Code/Codex, plus stepwise pipeline.run/pipeline.status execution with safe polling, run_id reuse, stop_after staging, and duplicate-job avoidance. Use when connecting the protein-pipeline MCP server or running staged RFD3/MMseqs2/ProteinMPNN/SoluProt/AF2 jobs and you want output paths (not narrative summaries).
---

# Protein Pipeline Stepper

## Overview

Run the protein pipeline in deterministic stages via MCP tools and return the output paths required for the next step.

## Connecting (MCP auth)

Before running anything, the MCP server `protein-pipeline` must be reachable and authenticated.

1. Open the protein-pipeline web app and sign in (local login or KBF SSO).
2. Get a bearer token for `mcp.json`. **Recommended: a long-lived API key** — it does not expire, so there is no refreshing:
   - On the **MCP** tab open **Advanced › API keys**, create a key (default 90 days, revocable), and copy it once; **or**
   - Use the MCP tab's **master prompt** (step 2) — copying it generates and embeds a long-lived key automatically.
   - Quick alternative: **Copy mcp.json with my token** gives a ready-to-paste `mcp.json`, but that token is the **short-lived** SSO token (expires in minutes).
3. Add it to your client (either token type goes in the same place):
   - **VS Code:** run **MCP: Open User Configuration** and paste into `mcp.json`.
   - **Codex:** add an MCP server named `protein-pipeline` with the URL and the
     `Authorization: Bearer <token>` header.

**Long-running jobs.** Prefer the long-lived **API key** — the compute job runs
async **server-side** and your polling keeps working with no re-auth. If you used
the short-lived SSO token instead, it expires after a few minutes: the job (and its
`run_id`/artifacts/`status.json`) keeps running, but **polling returns 401** until
you re-fetch the token; then resume `pipeline.status(run_id)` polling on the **same
`run_id`** — do not start a duplicate run. Switching to an API key avoids this
entirely.

You can also download this skill from the MCP tab (**Download skill**) so your client
has the connection + execution instructions locally.

## Prerequisites

- Use the MCP server named `protein-pipeline` (it must expose `pipeline.run`, `pipeline.status`, `pipeline.list_runs`).
  - If available, also use `pipeline.list_artifacts` and `pipeline.read_artifact` to fetch intermediate files without asking for filesystem access.
- Treat `pipeline.run` as potentially long-running. **A single `pipeline.run` call with no `stop_after` runs the whole pipeline server-side** (MSA → RFD3/BioEmu → ProteinMPNN → SoluProt → AF2 → novelty), exactly like the web app's full run; `stop_after` only changes *where it stops*. Use `stop_after` for **Studio** stage checkpoints or **standalone** single stages — do **not** chain a full run into many per-stage calls from the client.

## Non-Negotiable Rules

- Always reuse a stable `run_id` across stages. If the user does not supply one, create one with only `[a-zA-Z0-9_.-]` and no spaces.
- Never pass file paths as `target_fasta` / `target_pdb` / `rfd3_input_pdb`. Read the file contents and pass the raw text. **Shortcut for PDB inputs:** instead of raw text you may pass a 4-character **PDB ID** (e.g. `4KL5`) or an **RCSB/AlphaFold URL** for `target_pdb` / `rfd3_input_pdb` (and `protein_pdb`); the server fetches it — use this to avoid sending large PDB text. (Local/edited PDBs still need raw contents.)
- Before calling `pipeline.run` for a `run_id`, call `pipeline.status(run_id)`:
  - If `state=running`, do not call `pipeline.run` again. Poll `pipeline.status` until completion/failure.
  - If `state=failed` (or similar), stop and report the error details, then diagnose and propose a corrected command (see "Result validation & self-correction"). Do not re-run without user confirmation.
- If a `pipeline.run` call times out in the client, assume the remote job may still be running; switch to polling with `pipeline.status`.

## Workflow (Stage Runner)

### First, pick a run mode

Unless the user already said exactly what to run, ask **one** question up front — which mode:

1. **Full pipeline run (recommended default)** — **one `pipeline.run` call without `stop_after`**; the server runs every stage to a final result (same as the web app's full run). Poll `pipeline.status` to completion and report stage progress *from the status* as it advances — do **not** split a full run into per-stage `stop_after` calls.
2. **Studio (stage-by-stage)** — run one stage, then **stop and wait** for the user to review and decide (move forward / rerun / stop) before the next. Best for careful, exploratory, or cost-sensitive work. (See "Stage-by-stage review".)
3. **Single model (standalone)** — run just one model, no pipeline. **Most common: RFD3** (backbone generation) and **ColabFold/AF2** (structure prediction) — suggest these first. Also available: MSA (MMseqs2), ProteinMPNN, SoluProt, DiffDock, BioEmu. (See "Single-model / standalone execution".)

Recommend **Full pipeline** for a typical "run my analysis" request, **Studio** when they want to inspect/tune between stages, **Single model** when they only need one computation. Then continue with the questions below (full/studio) or run the chosen standalone tool directly.

### Interactive setup — ask before a full run

For a **full pipeline run** (not a single stage the user already fully specified), do not silently assume everything. First call `pipeline.plan_from_prompt` with the user's request to detect missing inputs/questions, then ask a short, concrete set of questions in **one** message and wait for answers before calling `pipeline.run`:

1. **Defaults or advanced?** — "Run with sensible defaults, or set advanced options?"
2. **Surrogate triage?** — "Use the surrogate model to triage candidates before AF2 (`surrogate_triage_enabled=true`)? It screens designs with a fast surrogate and runs AF2 only on the top ones — cheaper/faster, slightly less exhaustive." If yes and they want control, offer `surrogate_triage_top_k`, `surrogate_triage_initial_samples`, `surrogate_triage_model`.
3. **Missing required inputs** surfaced by `plan_from_prompt` `questions` (target sequence/PDB, RFD3 inputs, ligand, etc.).
4. If **advanced**, offer the main knobs with their defaults:
   - MSA: `mmseqs_target_db` (uniref90), `mmseqs_max_seqs` (3000)
   - Design: `conservation_tiers` ([0.3, 0.5, 0.7]), `num_seq_per_tier` (16), `sampling_temp`
   - SoluProt: `soluprot_cutoff` (0.5)
   - AF2: `af2_plddt_cutoff` (85), `af2_top_k` (20), `af2_sequence_ids`

Accept "defaults" as a valid answer to everything. After the user answers, **echo back the final settings** (including `surrogate_triage_enabled` and any advanced knobs) and then call `pipeline.run` with exactly those. Don't re-ask on later stages of the same `run_id` unless the user changes scope. For a single standalone stage the user already specified, skip the questions and run it directly.

1) Collect inputs
- Require: one of `target_fasta` or `target_pdb` **or** RFD3 inputs (raw text, not a file path).
  - If only `target_pdb` is provided, the pipeline extracts the sequence from `ATOM` records for `MMseqs2`/conservation.
  - If `target_pdb` is missing and `stop_after!="msa"` and no RFD3 inputs are provided, the pipeline will first run AlphaFold2 to generate a target structure (`target.pdb`) (requires `ALPHAFOLD2_ENDPOINT_ID` or `AF2_URL` configured).
- Optional: DiffDock inputs for ligand placement (used only if ligand coordinates are missing in the PDB):
  - `diffdock_ligand_smiles` **or** `diffdock_ligand_sdf` (raw text).
- Choose: `run_id` (reuse across steps).
- Choose: next stage via `stop_after` (`rfd3` -> `msa` -> `design` -> `soluprot` -> `af2` -> `novelty`).

2) Gate on current status
- Call `pipeline.status` for the `run_id`.
- If `found=false`: start the requested stage with `pipeline.run`.
- If `found=true` and `state=running`: poll with `pipeline.status` (e.g., every 30-60s).
- If `found=true` and not running: proceed to the requested stage with `pipeline.run` (it will reuse cached artifacts unless `force=true`).
- If `state=running` looks stale (e.g., long time since `updated_at` and you know nothing is running), you may proceed with `pipeline.run` to resume, or use a new `run_id`.

3) Execute — by mode
- **Full run (default):** call `pipeline.run` **once with no `stop_after`** — the server runs MSA → … → AF2 → novelty internally. Then poll `pipeline.status` to completion and report the final outputs; surface per-stage progress *from the status* while polling. This matches the web app's full run; **do not issue per-stage `stop_after` calls**.
- **Studio (stage-by-stage):** call `pipeline.run` with `stop_after` set to a single stage, then stop and report a checkpoint before the next (see "Stage-by-stage review"). One stage per call, on user confirmation.
- **Standalone:** a single `stop_after` stage or a dedicated tool (see "Single-model / standalone execution").
- After each call, return:
  - `output_dir`
  - stage-specific file paths from the result (e.g., `msa_a3m_path`, `msa_tsv_path`)
  - (Studio) the next recommended stage

## Stage-by-stage review (Studio-style checkpoints)

**This section applies to Studio mode only.** Mirror the web app's **Workflow Studio**: run one stage, **stop, show the result, and let the user decide** before the next stage — don't silently chain stages. (A **full run** is the opposite: one `pipeline.run` call does chain every stage server-side — that's expected, not something to split up.)

After each stage completes, **pause and report a short checkpoint**, then wait for the user:

1. **Summarize the result** — read the stage's key outputs and present them concisely (not just paths):
   - Use `pipeline.read_artifact` on `summary.json` / `status.json`, and `pipeline.list_artifacts` for the produced files.
   - Stage-specific signal: MSA → depth/`msa_*` paths; design → number of sequences per tier; soluprot → solubility scores; **af2 → `pipeline.get_hit_list`** (pLDDT/top hits); compare runs with `pipeline.compare_runs`; full write-up with `pipeline.generate_report`.
2. **Sanity-check** it (see "Result validation & self-correction") and flag anything implausible.
3. **Offer the Studio choices** and wait for the answer:
   - **Move forward** to the next stage (name it, e.g. `design` → `soluprot`),
   - **Rerun this stage** with adjusted parameters (re-run the same `run_id` with `force=true`),
   - **Adjust and stop**, or **stop here / done**.
4. Proceed only on the user's choice; reuse the **same `run_id`** so completed stages stay cached.

**Share the web view.** `pipeline.run`/`pipeline.status` results include a `ui_url` field (e.g. `https://rapid.kbiofoundry.kr/?run=<run_id>`) that opens this run live in the web app — 3D structures, pLDDT plots, and artifact downloads that a text summary can't show. When you report a checkpoint, give the user this link: *"View it visually here: <ui_url>"*. It opens straight to the Monitor tab for that run (they sign in once if needed).

Exception: if the user explicitly asked up front to run several stages back-to-back without stopping, honor that — but still post a one-line checkpoint after each stage so they can interrupt. For a long-running stage, poll `pipeline.status` to completion **before** presenting the checkpoint (do not re-run).

## Stage Templates (Arguments)

Use these argument shapes when calling `pipeline.run`:

### RFD3 (`stop_after="rfd3"`)
- Required: `run_id` and RFD3 inputs, using one of:
  - `rfd3_inputs_text` (JSON/YAML string), or
  - `rfd3_inputs` (dict), or
  - simple builder: `rfd3_input_pdb` + `rfd3_contig`
- Recommended:
  - `rfd3_design_index=0`
- `rfd3_contig` format: `A1-229` (no colon). `A:1-229` is normalized but avoid using it.
- `rfd3_cli_args` for `n_batches`, etc. (if not provided, `diffusion_batch_size=<rfd3_max_return_designs> n_batches=1` is auto-injected)
  - Note: `rfd3_partial_t` defaults to 20 and is injected into inputs if missing.
  - If PDB residue numbers are non-standard: set `pdb_strip_nonpositive_resseq=true` and/or `pdb_renumber_resseq_from_1=true` **after** RFD3 for downstream steps.
- After completion:
  - Read `outputs/<run_id>/rfd3/selected.pdb` and pass its **contents** as `target_pdb` for the next stage.

### MSA (`stop_after="msa"`)
- Required: `run_id` and one of `target_fasta` / `target_pdb`
- Recommended:
  - `mmseqs_target_db="uniref90"`
  - `mmseqs_max_seqs=3000`
  - `mmseqs_use_gpu=false` (recommended default; set `true` only after you’ve validated the GPU image/output mapping on your deployment)
  - Optional (paper parity, `target_pdb` only): `pdb_strip_nonpositive_resseq=true`, `pdb_renumber_resseq_from_1=true` (writes `pdb_numbering.json`)
  - Optional (weighted conservation): `conservation_weighting="mmseqs_cluster"` (requires MMseqs endpoint to support `cluster`)
  - If RunPod Serverless CPU jobs time out with large DBs, try a smaller DB (e.g. `swissprot`), reduce `mmseqs_max_seqs`, or use a dedicated pod/volume-warmed setup.

### Design (`stop_after="design"`)
- Required: `run_id` and either:
  - `target_pdb` (recommended), or
  - `target_fasta` (pipeline will generate `target.pdb` via AF2 first; AF2 must be configured)
- Recommended:
  - `conservation_tiers=[0.3, 0.5, 0.7]`
  - `num_seq_per_tier=16`
  - Optional (PyMOL-style 6Å masking): `ligand_mask_distance=6.0`; use `ligand_resnames=[...]` (HETATM) and/or `ligand_atom_chains=[...]` (ATOM substrate chains)
  - Optionally: `design_chains`, `seed`, `sampling_temp`, `batch_size`
  - If ligand coordinates are missing and you have a ligand description, set:
    - `diffdock_ligand_smiles` **or** `diffdock_ligand_sdf`
    - DiffDock will run automatically before ligand masking and uses the rank1 pose **only for ligand mask** (ProteinMPNN/AF2 inputs remain the original PDB).

### Ligand & residue selection (the UI 3D picker, via MCP)

AF2/ColabFold itself does not take a ligand — "ligand selection" here is the **design-stage masking** that keeps the binding pocket fixed while ProteinMPNN redesigns the rest. The web app's 3D picker just sets the parameters below; over MCP, set them directly and **make it easy by detecting the ligand for the user**:

1. **Auto-detect the ligand.** Before design, read the `target_pdb` `HETATM` records and list the candidate ligand residue names (ignore water `HOH`/`WAT` and ions unless relevant). If there is one obvious ligand, propose it; otherwise ask the user in one line which to protect. The user should never need to hunt through the PDB.
2. **Map their answer to parameters:**

| Goal (what the UI picker does) | MCP parameter |
| --- | --- |
| Protect a HETATM ligand and its pocket | `ligand_resnames=["HEM", ...]` + `ligand_mask_distance=6.0` |
| Substrate is a separate ATOM chain (enzyme-substrate) | `ligand_atom_chains=["B"]` (keep it out of `design_chains`) |
| **Design only surface-exposed residues** | `surface_only=true` (tune `surface_min_rel`=0.2, `surface_min_abs`=10.0) |
| Pick a region (surface / core / interface), like the UI | `pipeline.classify_residues(target_pdb)` → choose a region → pass as `fixed_positions_extra` |
| Fix exact residues you choose | `fixed_positions_extra={"A":[57,102,195]}` |
| Choose which chain(s) to mutate | `design_chains=["A"]` |
| Dock a *new* ligand, then mask around its pose | `diffdock_ligand_smiles` / `diffdock_ligand_sdf` (DiffDock runs first; pose used for masking only) |

3. **Region presets — surface / core / interface (over MCP).** Call `pipeline.classify_residues` with the `target_pdb` (optional `surface_area_cutoff`, default 2.5) — it returns `{surface, core, interface, counts}` per chain, **matching the web app's 3D-picker numbers** (same SASA + cutoff). Show the counts (e.g. "surface 225 / core 57 / interface 19"), let the user pick a region, and pass those residues as `fixed_positions_extra` (or restrict design to them). Surface alone can also use the server preset `surface_only=true`. Only fine **visual** hand-picking of an exact pocket still benefits from the web app's 3D viewer.

Always confirm the final selection (ligand resnames, masked chains, surface/fixed positions) back to the user before running design.

**Multi-chain / multi-model targets.** A multi-chain `target_pdb` is reduced to ONE design chain (the pipeline does not model the complex). If the user gives a multi-chain PDB without `design_chains` or a `target_fasta`, `pipeline.preflight` returns a warning and the run records `chain_strategy` with an auto-selected chain (longest protein chain). **Surface that warning and the auto-selected chain to the user, and confirm it is the intended chain/domain before a full run** — set `design_chains=[...]` to override. A multi-model (NMR) `target_pdb` is automatically reduced to its first model (preflight notes this); mention it so the user can confirm the first model is the intended one.

### Preset: Paper-Parity Enzyme+Substrate (PDB input)
- Use when the “ligand/substrate” is modeled as a separate `ATOM` chain (common for enzyme-substrate complexes).
- Set `design_chains` to the chain(s) you mutate; set `ligand_atom_chains` to substrate chain(s).
- Recommended knobs: `pdb_strip_nonpositive_resseq=true`, `pdb_renumber_resseq_from_1=true`, `conservation_weighting="mmseqs_cluster"`, `ligand_mask_distance=6.0`

### Choosing Knobs (Quick)
- If the substrate is a separate `ATOM` chain: set `ligand_atom_chains=[...]` and keep those chains out of `design_chains`.
- If the PDB has tag-like numbering: set `pdb_strip_nonpositive_resseq=true`; use `pdb_renumber_resseq_from_1=true` only if you want a clean 1..N residue numbering (check `pdb_numbering.json`).
- If ligand masking is too broad because of many HETATM: set `ligand_resnames=[...]` to include only the ligand(s) you want to protect.
- If the MSA is large/redundant: set `conservation_weighting="mmseqs_cluster"`; if clustering is unavailable/slow, keep `conservation_weighting="none"`.

### SoluProt (`stop_after="soluprot"`)
- Required: `run_id` and either `target_pdb` or (`target_fasta` + AF2 configured)
- Recommended: `soluprot_cutoff=0.5`

### AlphaFold2 (`stop_after="af2"`)
- Required: `run_id` and either `target_pdb` or (`target_fasta` + AF2 configured)
- Recommended:
  - `af2_plddt_cutoff=85`
  - `af2_top_k=20`
  - `af2_sequence_ids=["1"]` (run AF2 only for selected design ids to save time)

## Single-model / standalone execution

Run one model on its own (no full pipeline) when you only need a single computation. **Every standalone ("Single Stage") mode in the web app maps to an MCP call** — two have dedicated tools, the rest run through `pipeline.run` with `stop_after` set to a single stage:

| Standalone model | MCP call |
| --- | --- |
| RFD3 (backbone) | `pipeline.run` with `stop_after="rfd3"`, `rfd3_use=true`, and RFD3 inputs (`rfd3_input_pdb` + `rfd3_contig`, or `rfd3_inputs`/`rfd3_inputs_text`) |
| BioEmu (backbone) | `pipeline.run` with `stop_after="bioemu"`, `bioemu_use=true`, and a `target_pdb`/`target_fasta` |
| MSA (MMseqs2) | `pipeline.run` with `stop_after="msa"` and `target_fasta` or `target_pdb` |
| ProteinMPNN | `pipeline.run` with `stop_after="design"` and `target_pdb` |
| SoluProt | `pipeline.run` with `stop_after="soluprot"` and `target_pdb` (or `target_fasta` + AF2 configured) |
| ColabFold / AlphaFold2 | `pipeline.run_af2` or `pipeline.af2_predict` (standalone), **or** `pipeline.run` with `stop_after="af2"` |
| DiffDock | `pipeline.diffdock` or `pipeline.run_diffdock` (standalone) |

### Dedicated standalone tools

There are **two AF2 tools and two DiffDock tools** with different input field names — do not mix them up. Call `tools/list` if unsure, then match the schema exactly:

- **`pipeline.run_af2`** — AF2/ColabFold from a plain sequence. Input: `fasta` **or** `sequence` (note: `fasta`, *not* `target_fasta`). Optional: `af2_provider` (`colabfold`/`af2`), `af2_chain_ids` (array), `af2_model_preset` (e.g. `multimer` for multi-chain), `sequence_id`, `run_id`, `force`, `dry_run`. Best when you already have a sequence/FASTA.
- **`pipeline.af2_predict`** — AF2/ColabFold from pipeline-style inputs. Input: `target_fasta` **or** `target_pdb`. Optional: `af2_provider`, `af2_model_preset`, `af2_db_preset`, `af2_extra_flags`, `run_id`, `dry_run`. Best when your input is a `target_pdb`/`target_fasta` you'd also feed the pipeline.
- **`pipeline.run_diffdock`** — docking, lighter args. Input: `protein_pdb` + `ligand_smiles`. Optional: `run_id`, `force`, `dry_run`.
- **`pipeline.diffdock`** — docking, pipeline-style args. Input: `protein_pdb`/`target_pdb` + `diffdock_ligand_smiles`/`diffdock_ligand_sdf` (also accepts `ligand_smiles`/`ligand_sdf`). Optional: `complex_name`, `diffdock_extra_args`, `run_id`, `dry_run`.

### Multi-chain / complex (multimer) AF2 input

For an AF2/ColabFold **complex** (more than one chain), the pipeline splits chains on `/` — **not** ColabFold's `:` notation. So:

- Join the chains in **one** record with `/`: `SEQ_A/SEQ_B` (FASTA: `>NAME` then `SEQ_A/SEQ_B`), and set `af2_model_preset="multimer"`.
- Do **not** use `:` as the API separator — that is ColabFold-doc notation, not the pipeline's input format. Passing `:` will not be interpreted as a chain break.
- A **monomer** preset with a `/`-separated multi-chain sequence is rejected — either pass a single chain, or switch to `multimer`.
- With `multimer`, the number of `/`-separated chains must match `af2_chain_ids` (`pipeline.run_af2`) or `design_chains` (`pipeline.run`), e.g. `["A","B"]`. A count mismatch is rejected before any GPU time is spent.
- A multimer needs `af2_provider="colabfold"` (the default). The stock AlphaFold2 worker cannot take a complex on this path and now rejects it instead of folding the chains into one fused polypeptide.
- The predicted structure is checked after the run: if a multimer comes back with fewer chains than requested, the sequence is reported as **failed** (`failed_count`, `af2/<id>/error.json`) rather than counted as a success. A high `best_plddt` on a fused single chain is not a valid complex.
- `dry_run` previews one dummy chain per requested chain, so the chain count can be confirmed before spending GPU time.

### Single stage via `pipeline.run` + `stop_after`
- Set `stop_after` to exactly one of `rfd3`, `bioemu`, `msa`, `design`, `soluprot`, `af2` and pass a stable `run_id`. The pipeline runs only that stage's prerequisites and stops.
- **RFD3 and BioEmu must be explicitly enabled** with `rfd3_use=true` / `bioemu_use=true`; otherwise `stop_after` for that stage is rejected.
- Cached artifacts from earlier stages are reused; pass `force=true` to recompute.

Always pass raw file **contents** (not paths) for `target_pdb`/`target_fasta`/`rfd3_input_pdb`. Gate and poll standalone runs like staged runs: call `pipeline.status(run_id)` first; if `state=running`, poll instead of calling `pipeline.run`/the standalone tool again.

## Result validation & self-correction

A command can be wrong (bad params, wrong inputs) even when it "succeeds." Validate before and after, and propose fixes — but never silently re-run expensive jobs.

**Before running:** for a non-trivial or first-time command, call `pipeline.preflight` (validates inputs/config without running) and fix anything it flags before calling `pipeline.run`.

**After running, sanity-check the result:**
- Check `pipeline.status(run_id)` state plus the run's `summary.json`/`status.json`.
- Watch for implausible or empty outputs: 0 designs, empty MSA, pLDDT/solubility far outside expected ranges, or a stage that "succeeded" with no artifacts.

**If a stage failed or a result looks wrong:**
1. Diagnose — read the error details / `status.json` / `summary.json` and identify the likely cause (wrong `stop_after`, missing input, bad `rfd3_contig`, wrong `design_chains`, a file passed as a path instead of its contents, etc.).
2. Propose a corrected command — state exactly what you would change and why.
3. **Do not re-run automatically.** Ask the user to confirm before re-running, especially for GPU stages (`rfd3`, `af2`, `design`, `diffdock`). Re-run only after confirmation, using `force=true` (to override cached artifacts) or a fresh `run_id`.

This keeps a human in the loop for cost while still letting the AI catch and explain mistakes.

## Output Expectations

- Always return `output_dir` and any stage-specific paths from the tool result.
- Point users to `PIPELINE_OUTPUT_ROOT/<run_id>/` on the execution host for artifacts (`msa/`, `tiers/`, `status.json`, `summary.json`).
- If DiffDock ran, mention `outputs/<run_id>/diffdock/` (e.g., `rank1.sdf`, `ligand.pdb`, `complex.pdb`, `out_dir.zip`).

## Example (Minimal)

Run MSA only:
- Call `pipeline.run` with `run_id`, `stop_after="msa"`, `mmseqs_target_db`, `mmseqs_max_seqs`, and FASTA text.
- If the call is slow, poll with `pipeline.status(run_id)` until it completes.

## Quick Start Prompts (Copy/Paste)

These are example user prompts that should trigger this skill and result in MCP tool calls.

### Full pipeline (requires target_pdb)

Request:
- "Use protein-pipeline-stepper. Run msa -> design -> soluprot -> af2 sequentially for run_id=intein_full_001. Read target_fasta from the pasted FASTA text. Read target_pdb from ./target.pdb file contents. Use defaults for all other params. If any stage is running, poll pipeline.status every 60s and do not re-run pipeline.run. After each stage, return output_dir and the paths needed for the next stage."

### MSA only (no PDB required)

Request:
- "Use protein-pipeline-stepper. Run MSA only (stop_after=msa) for run_id=intein_msa_001 using the pasted FASTA text. Use defaults. Poll status if needed. Return output_dir, msa_a3m_path, msa_tsv_path."
