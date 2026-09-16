# RAPID Data and Release Notes

## Included Data

The release includes the data required to reproduce the manuscript benchmark
tables and figures:

- processed benchmark table in `data/benchmark/cath_pilot_dataset.csv`
- cached ESM-2 8M embeddings in `data/benchmark/cath_pilot_emb_320d.npy`
- benchmark result files in `data/benchmark/results/`
- corrected-chain refresh manifest in
  `data/benchmark/results/rapid_target_manifest.csv`
- representative 3RGK/1LVM direct and multi-round run summaries in
  `data/case_studies/`
- QC-filtered pre-refresh CATH benchmark summaries in `data/cath_curated/`
- raw lightweight summaries of the expanded 73-run CATH execution corpus in
  `data/cath_73/`
- generated figures and LaTeX tables in `figures/benchmark/`

The included benchmark and summary files are currently small enough for GitHub.
The full expanded CATH archive under `/opt/protein_pipeline/cath_outputs`
contains 73 completed pre-refresh run directories and is several gigabytes, so
it should be deposited as a separate large artifact or S3-backed dataset rather
than committed to the source repository. The included `data/cath_curated/`
tables are retained for component-level active-learning analysis; population-
level CATH claims should be refreshed from the corrected-chain manifest before
submission. The package intentionally excludes runtime logs, temporary test
outputs, Python environments, frontend `node_modules`, and private `.env` files.

## GitHub vs Separate Data Deposit

For the source package and lightweight curated summaries, keeping the data in
GitHub is acceptable and makes reproduction easier. For journal submission,
deposit the full 73-run CATH corpus on Zenodo, OSF, Figshare, institutional
storage, or S3 with a stable manifest, and cite the DOI or persistent URL in the
manuscript.

Use a separate data repository or Git LFS if future files include:

- raw full pipeline outputs, including the full 73-run CATH archive
- ESM/model cache directories
- many AF2/RFD3/BioEmu structure files
- full production `outputs/` directories beyond compact representative exports
- files approaching GitHub's per-file or repository-size limits

## What Should Not Be Public

Do not publish:

- filled `.env` files
- API keys or endpoint tokens
- SSO client secrets
- private server URLs that identify a non-public deployment
- runtime output directories containing unrelated user jobs
