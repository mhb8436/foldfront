# RunPod, Remote Server, and Authentication

## Recommended Deployment Pattern

Keep the backend close to the GPU services and credentials. The browser UI can
run locally through Vite or behind a static file server after `npm run build`.
Do not expose development or staging routes without authentication.

For a private server:

```bash
cd pipeline-mcp
PYTHONPATH=src python -m pipeline_mcp.http_server --host 127.0.0.1 --port 18080
```

From your laptop:

```bash
ssh -L 18080:127.0.0.1:18080 user@your-server
cd frontend
npm ci
npm run dev
```

Then open `http://127.0.0.1:5173`.

For shared access, run `npm run build`, serve `frontend/dist` over HTTPS, and
reverse proxy `/api/*` to the backend. Restrict `PIPELINE_CORS_ORIGINS` to the
frontend origin instead of `*` if the frontend and API are served from different
origins. See `deployment_security.md` before publishing any hosted URL.

## RunPod Images

The Docker images used for the RunPod Serverless endpoints are documented in
`runpod_images.md`. Keep endpoint IDs and API keys in `.env`, but keep image
names and tags in public documentation so other users can recreate compatible
endpoints.

## Local Auth

Local auth is enabled by:

```env
PIPELINE_AUTH_ENABLED=true
PIPELINE_ADMIN_USERNAME=admin
PIPELINE_ADMIN_PASSWORD=change-me-before-running
```

The backend stores users under `PIPELINE_AUTH_STORE`, which defaults in this
package to `outputs/.auth/users.json`. That directory is ignored by Git.

## Optional OIDC/SSO

OIDC is disabled unless both issuer and client id are configured:

```env
PIPELINE_OIDC_ISSUER=https://sso.example.org/realms/example
PIPELINE_OIDC_CLIENT_ID=protein-pipeline
PIPELINE_OIDC_PROVIDER_NAME=OIDC SSO
```

Use OIDC when deploying to a managed institutional environment. For a public
GitHub package, keep these fields empty and document that users can connect their
own identity provider.

## Secrets

RunPod keys, S3 keys, OIDC client secrets, and admin passwords must remain in
`.env` or the server environment. They should not be embedded in the frontend,
benchmark CSV files, or manuscript artifacts.

RunPod endpoint IDs are not as sensitive as API keys, but they still describe
private infrastructure and should stay out of the public package unless the
endpoint is intentionally public and cost-controlled.
