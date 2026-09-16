# Nginx frontend (Docker)

## Purpose
Serve the static UI on ports 5173 and 443, and proxy `/api/` to the local pipeline-mcp server.

## Requirements
- Docker installed on the host
- pipeline-mcp running on `127.0.0.1:18080`
- TLS certs in `deploy/nginx/certs/`:
  - `fullchain.pem`
  - `privkey.pem`

## Start
```bash
cd /opt/protein_pipeline/deploy/nginx
# copy TLS certs into ./certs before running
# fullchain.pem + privkey.pem

docker compose -f docker-compose.frontend.yml up -d
```

## Stop
```bash
docker compose -f docker-compose.frontend.yml down
```

## Notes
- If you do not want TLS yet, comment out the 443 server block in `kbf.conf`.
- The UI defaults to `/api` as API base when opened from http(s) origin.
