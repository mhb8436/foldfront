# Protein Pipeline Restart Guide

UI/Backend 수정 후 반영 절차를 한 곳에 모은 문서입니다.

## 언제 재시작이 필요한가

- `pipeline-mcp/src/pipeline_mcp/*` 같은 백엔드 Python 코드 변경
  - `pipeline-mcp` 프로세스를 재시작해야 반영됩니다.
- `frontend/app.js`, `frontend/index.html`, `frontend/styles.css` 같은 프런트 정적 파일 변경
  - 현재 운영 UI는 Caddy 컨테이너 `kbf-infra-caddy-1`가 `/opt/protein_pipeline/frontend`를 bind mount로 직접 서빙합니다.
  - 따라서 프런트 파일 수정은 보통 재시작 없이 즉시 반영됩니다.
  - 다만 브라우저 캐시 때문에 hard refresh는 권장합니다.
- Caddy 프록시 설정 변경
  - `kbf-infra-caddy-1` 재시작 또는 reload가 필요합니다.
- `deploy/nginx/*` 변경
  - 이 경로는 저장소에 남아 있는 대체/개발용 구성입니다. 현재 운영 UI 반영 경로는 아닙니다.

## 1. 백엔드 재시작

현재 운영 백엔드는 `127.0.0.1:18080`에서 `pipeline_mcp.http_server`로 실행 중입니다.

```bash
cd /opt/protein_pipeline/pipeline-mcp

# 1. 끈질긴 자동 재시작 프로세스나 기존 시스템 파이썬 프로세스를 완벽하게 죽입니다.
pkill -f "python.*pipeline_mcp.http_server" || true
if [ -f /opt/protein_pipeline/logs/pipeline-mcp_18080.pid ]; then
  kill -9 "$(cat /opt/protein_pipeline/logs/pipeline-mcp_18080.pid)" 2>/dev/null || true
  rm -f /opt/protein_pipeline/logs/pipeline-mcp_18080.pid
fi
sleep 2 # 프로세스가 완전히 죽을 때까지 대기

# 2. 가상환경의 python을 명시적으로 사용하여 mlflow, torch 등 의존성 에러를 방지합니다.
/opt/protein_pipeline/venv/bin/python - <<'PY'
import os
import subprocess
from pathlib import Path

env = os.environ.copy()
for raw in Path(".env").read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    env[key.strip()] = value
env["PYTHONPATH"] = "src"

log_path = Path("/opt/protein_pipeline/logs/pipeline-mcp_18080.log")
with log_path.open("ab") as log:
    proc = subprocess.Popen(
        ["/opt/protein_pipeline/venv/bin/python", "-m", "pipeline_mcp.http_server", "--host", "127.0.0.1", "--port", "18080"],
        cwd="/opt/protein_pipeline/pipeline-mcp",
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

Path("/opt/protein_pipeline/logs/pipeline-mcp_18080.pid").write_text(f"{proc.pid}\n", encoding="utf-8")
print(f"Backend started with PID: {proc.pid}")
PY
```

확인:

```bash
curl -sS http://127.0.0.1:18080/healthz && echo
ps -fp "$(cat /opt/protein_pipeline/logs/pipeline-mcp_18080.pid)"
```

## 2. 프런트엔드 반영

현재 운영 프런트는 Caddy가 정적 파일을 직접 서빙하므로, 일반적인 UI 코드 변경은 별도 재시작이 필요 없습니다.

확인:

```bash
curl -ksS --resolve pipeline.k-biofoundrycopilot.duckdns.org:443:127.0.0.1 \
  https://pipeline.k-biofoundrycopilot.duckdns.org/ \
  | rg 'app\.js\?v=|styles\.css\?v='
```

브라우저에서 안 바뀌면 hard refresh:

- macOS Chrome: `Cmd+Shift+R`
- Windows/Linux Chrome: `Ctrl+Shift+R`

## 3. Caddy 재시작이 필요한 경우

다음 경우에만 Caddy를 재시작합니다.

- `/opt/kbf-infra/Caddyfile` 수정
- Caddy mount/path 설정 변경
- 프록시 라우팅 자체를 바꾼 경우

```bash
docker restart kbf-infra-caddy-1
```

확인:

```bash
docker ps --filter name=kbf-infra-caddy-1
curl -ksS --resolve pipeline.k-biofoundrycopilot.duckdns.org:443:127.0.0.1 \
  https://pipeline.k-biofoundrycopilot.duckdns.org/ \
  | head
```

## 4. 브라우저 반영 확인

- JS 캐시 이슈를 줄이려면 `frontend/index.html`의 asset query string(`app.js?v=...`, `styles.css?v=...`)도 함께 갱신합니다.

## 5. 한 번에 반영

수정 후 현재 운영 경로 기준으로 가장 안전한 반영 순서는 아래와 같습니다.

```bash
cd /opt/protein_pipeline/pipeline-mcp

# 1. 끈질긴 자동 재시작 프로세스나 기존 시스템 파이썬 프로세스를 완벽하게 죽입니다.
pkill -f "python.*pipeline_mcp.http_server" || true
if [ -f /opt/protein_pipeline/logs/pipeline-mcp_18080.pid ]; then
  kill -9 "$(cat /opt/protein_pipeline/logs/pipeline-mcp_18080.pid)" 2>/dev/null || true
  rm -f /opt/protein_pipeline/logs/pipeline-mcp_18080.pid
fi
sleep 2 # 프로세스가 완전히 죽을 때까지 대기

# 2. 가상환경의 python을 명시적으로 사용하여 기동
/opt/protein_pipeline/venv/bin/python - <<'PY'
import os
import subprocess
from pathlib import Path

env = os.environ.copy()
for raw in Path(".env").read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    env[key.strip()] = value
env["PYTHONPATH"] = "src"

log_path = Path("/opt/protein_pipeline/logs/pipeline-mcp_18080.log")
with log_path.open("ab") as log:
    proc = subprocess.Popen(
        ["/opt/protein_pipeline/venv/bin/python", "-m", "pipeline_mcp.http_server", "--host", "127.0.0.1", "--port", "18080"],
        cwd="/opt/protein_pipeline/pipeline-mcp",
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

Path("/opt/protein_pipeline/logs/pipeline-mcp_18080.pid").write_text(f"{proc.pid}\n", encoding="utf-8")
print(f"Backend started with PID: {proc.pid}")
PY

curl -sS http://127.0.0.1:18080/healthz && echo
curl -ksS --resolve pipeline.k-biofoundrycopilot.duckdns.org:443:127.0.0.1 \
  https://pipeline.k-biofoundrycopilot.duckdns.org/ \
  | rg 'app\.js\?v=|styles\.css\?v='
```

필요하면 마지막에 브라우저 hard refresh를 합니다.

## 6. 문제 있을 때

- `curl http://127.0.0.1:18080/healthz`가 실패하면
  - `/opt/protein_pipeline/logs/pipeline-mcp_18080.log` 확인
- `.env`를 `source .env`로 읽지 않습니다
  - 현재 `.env`에는 공백이 들어간 값이 있어 POSIX shell assignment 형식이 아닙니다.
  - 위 문서처럼 Python으로 key/value를 읽어서 프로세스를 띄우는 방식을 사용합니다.
- UI만 예전 상태로 보이면
  - `frontend/index.html` asset version 확인
  - 브라우저 hard refresh
  - 필요 시 `docker restart kbf-infra-caddy-1`
