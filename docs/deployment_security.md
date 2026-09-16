# RAPID Deployment Security

This document defines the boundary between the public RAPID source package and
hosted RAPID services.

## Environment Policy

Use separate deployment environments for development, staging, and production.

| Environment | Audience | Public URL policy | Recommended protection |
| --- | --- | --- | --- |
| `dev` | maintainers only | never publish | VPN, IP allowlist, or reverse-proxy auth |
| `staging` | maintainers and invited reviewers | never publish | reverse-proxy auth or institutional SSO |
| `production` | intended users | publish only when ready | app auth, quotas, monitoring, cost controls |

Development and staging routes should not appear in the manuscript, README,
paper supplement, or public GitHub release notes. Use them only for internal
validation.

## Reverse Proxy Requirements

Run the backend on loopback and expose it only through HTTPS:

```bash
PYTHONPATH=src python -m pipeline_mcp.http_server --host 127.0.0.1 --port 18080
```

Proxy `/api/*` and `/mcp` to the loopback backend. Protect those routes at the
reverse proxy if the application is not meant to be public.

For a minimal private staging route with Caddy basic auth:

```caddyfile
staging.example.org {
  encode zstd gzip
  basic_auth {
    reviewer <hashed-password>
  }

  handle /api/* {
    reverse_proxy 127.0.0.1:18085
  }
  handle /mcp* {
    reverse_proxy 127.0.0.1:18085
  }
  handle {
    root * /srv/protein_pipeline_staging
    try_files {path} /index.html
    file_server
  }
}
```

Use `caddy hash-password` to generate the password hash. For institutional
deployments, prefer OIDC or an existing `forward_auth` gateway over shared
passwords.

## Secrets

Keep these values out of Git and out of frontend bundles:

- `RUNPOD_API_KEY`
- RunPod endpoint IDs
- S3 access and secret keys
- OIDC client secrets
- local admin passwords
- provider bearer tokens such as `PROTEINMPNN_GPU_TOKEN` or
  `ESM_EMBEDDING_TOKEN`

Store them in the server-local `.env` file or GitHub Environment secrets. The
public `.env.example` file must contain placeholders only.

## Public Production Guardrails

Before a production URL is advertised, configure:

- authentication for the UI and API;
- job-level quotas or an approval gate for expensive GPU calls;
- CORS restricted to the production frontend origin;
- object-storage lifecycle rules for large outputs;
- log retention that does not expose sequences, credentials, or tokens;
- health checks for backend, frontend, and provider connectivity.

## Release Checklist

Before publishing a release package, run:

```bash
rg -n "(RUNPOD_API_KEY=.+|ENDPOINT_ID=.+|AWS_SECRET|SECRET_KEY=.+|PRIVATE KEY|\\bAKIA[0-9A-Z]{16}\\b)" .
find . -type d \( -name node_modules -o -name .pytest_cache -o -name __pycache__ -o -name dist \)
```

The first command should return only placeholders or documentation examples.
The second command should return no generated dependency/build/cache
directories.
