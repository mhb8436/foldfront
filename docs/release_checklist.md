# Public Release Checklist

Before pushing this folder to GitHub:

1. Fill `CITATION.cff` with the final author list and manuscript title.
2. Choose and add a license file. Do not publish without an explicit license.
3. Confirm that no filled `.env` files are present.
4. Confirm that hosted `dev` and `staging` URLs are private. Do not include them
   in public docs, release notes, or manuscript files.
5. Confirm that `pipeline-mcp/models/experts/`, `outputs/`, `logs/`, `.venv/`,
   `frontend/dist/`, `.pytest_cache/`, `__pycache__/`, and `node_modules/` are
   absent or ignored.
6. Run a generated-file scan:

```bash
find . -type d \( -name node_modules -o -name .pytest_cache -o -name __pycache__ -o -name dist \)
```

The command should print no paths.

7. Run a secret/path scan:

```bash
rg -n "(RUNPOD_API_KEY=.+|ENDPOINT_ID=.+|AWS_SECRET|SECRET_KEY=.+|PRIVATE KEY|\\bAKIA[0-9A-Z]{16}\\b)" .
rg -n "(rapid-dev|rapid-staging|211\\.188\\.)" .
```

Also run your normal secret scanner, for example GitHub secret scanning,
Gitleaks, or TruffleHog.

The scan should return only placeholders or documentation examples.

8. Run the backend health check with a local `.env`.
9. Run `bash scripts/reproduce_paper_tables_figures.sh`.
10. Verify that `manuscript/` and `figures/` match the paper draft.
11. Tag a release and archive the exact released package.
12. Create an immutable data/software DOI if the manuscript requires one.
