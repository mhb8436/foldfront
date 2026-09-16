# Paper Run Scripts

This directory contains operational wrappers for the RAPID manuscript data
refresh. These scripts may require private RunPod/NCP S3 credentials and are
not part of the public reproducibility package.

Use this directory for live data-generation jobs:

1. `01_fetch_cath_s3_results.py` downloads completed CATH artifacts from NCP S3.
2. `02_launch_structural_context_ablation.py` launches the 8-target structural-context ablation.
3. `03_launch_surrogate_triage_budget.py` launches the AF2-budgeted surrogate-triage runs used for claim 2. Its manuscript default generates 3,333 candidates in each 30%, 50%, and 70% conservation tier, pools the SoluProt-passing candidates across tiers, labels 30 diverse bootstrap candidates, evaluates 20 Top-K acquisitions, and uses the configured ESM embedding provider plus the RunPod ColabFold backend so AF2 job identifiers are visible. Its default Top-K selection method is Auto-CV over RF, Ridge, LightGBM, and XGBoost. Rank-mean ensemble is optional and is only evaluated when `--ensemble-models` is set or the policy is forced to `ensemble`. The main budget benchmark keeps RFD3/BioEmu off to isolate the surrogate triage effect; use `--rfd3-use` and/or `--bioemu-use` only for supplementary structural-context-plus-surrogate compatibility runs.
4. `03_launch_multiround_evolution.py` launches experimental-feedback or legacy in-silico evolution traces.
5. `05_collect_surrogate_triage_budget.py` collects surrogate-triage AF2 budget summaries from completed runs.
6. `09_run_surrogate_wt_baselines.py` adds one WT SoluProt/ColabFold reference baseline per strict surrogate-triage run. These WT calls are interpretation references and are not counted in the 250 candidate AF2-record budget.
7. `scripts/benchmark/18_make_surrogate_triage_budget_figure.py` builds the manuscript composite figure for claim 2 from the run-level budget CSV, CV metrics, acquired Top-K outcomes, and WT reference baselines.
8. `06_run_pooled_surrogate_scaling.py` reuses completed CATH AF2 labels to test whether pooled surrogate labels improve held-out target-tier ranking over per-target calibration. The current ESM-plus-composition result is a negative/guardrail analysis, not a main performance claim.

Keep reusable analysis and figure-generation code in `scripts/benchmark/`.
Keep public, credential-free reproduction code in `scripts/`.
