# foldfront

단백질 설계 모델을 워크플로로 엮어 실행하고, 결과를 비교하며, 실행 이력을 관리하는 플랫폼입니다.

한국생명공학연구원의 [RAPID](https://github.com/sblabkribb/protein_pipeline) v1.0.29 를 승계했습니다.
단백질 계산과 판정 규칙은 원본 그대로 쓰고, 그 위에 실행·저장·권한·관제 계층을 올렸습니다.

```
msa → rfd3 → bioemu → design → soluprot → af2 → novelty
```

원본은 이 순서를 코드에 고정합니다. foldfront 에서는 연구자가 캔버스에서 직접 엮습니다.
분기와 병렬을 넣고, 중간에 사람이 확인하고 넘어가는 지점을 둘 수 있습니다.

---

## 무엇을 할 수 있나

**워크플로를 직접 구성합니다.** 모델 실행 · 변환 · 조건 분기 · 병렬 분기 · 합류 · 검토 지점
여섯 가지 노드를 캔버스에 놓고 잇습니다. 순환이나 끊어진 노드, 빈 조건식은 저장할 때
걸러 내므로 GPU 시간을 쓰기 전에 알 수 있습니다.

**실행을 사람이 제어합니다.** 검토 지점에 닿으면 실행이 멈추고, 승인해야 다음으로
넘어갑니다. 일시중지하면 새 작업만 막고 이미 나간 작업은 끝까지 갑니다. 특정 단계만
다시 돌릴 수 있으며, 이때 그 아래 단계도 함께 되돌려 옛 값이 남지 않게 합니다.
중간 지점에서 갈라 새 실행을 만들 수도 있습니다.

**결과를 견줍니다.** 설계 후보를 가중 합산 점수로 줄 세우고, 야생형과 몇 자리나
다른지 함께 봅니다. 백본 출처별로 묶어 어느 접근이 나았는지 비교합니다. 두 실행을
나란히 놓고 단계별 지표와 3차원 구조를 견줍니다.

**수치를 해석해 줍니다.** 정렬이 얕거나 용해도 통과율이 낮거나 pLDDT 가 기준 밑이면
경고와 함께 다음에 무엇을 바꿀지 제시합니다. 판정에 쓴 값과 기준을 함께 보여 주므로
그 판단에 동의하지 않을 수도 있습니다.

**문장으로 시작할 수 있습니다.** 「rfd3 로 백본을 만들고 용해도로 거른 뒤 af2 까지」라고
쓰면 워크플로 초안을 만들어 스튜디오에서 엽니다. 저장하거나 실행하지는 않습니다.

**외부 GPU 를 관제합니다.** RunPod 엔드포인트의 상태와 워커, 대기 작업, 사용량과 비용을
보고 워커 수를 조정합니다.

화면은 무채색입니다. 색을 갖는 것은 실행 상태뿐이고 나머지는 굵기와 괘선과 형태로
구분하므로, 흑백으로 인쇄하거나 복사해도 정보가 사라지지 않습니다.

---

## 시작하기

### 필요한 것

- Python 3.12 이상, Node.js 20 이상
- [uv](https://docs.astral.sh/uv/), npm
- Docker (MongoDB 용)

### 설치와 실행

```bash
# 1. MongoDB
docker compose -f platform/docker-compose.yml up -d

# 2. 백엔드
uv sync --extra dev
uv run python -m foldfront.cli seed           # 기본 모델과 워크플로 템플릿 등록
uv run uvicorn foldfront.api.app:app --port 18090

# 3. 워커 (별도 터미널)
uv run python -m foldfront.cli worker --mock  # GPU 없이 돌려 볼 때는 --mock

# 4. 웹 콘솔 (별도 터미널)
cd web && npm install && npm run dev
```

콘솔은 <http://localhost:5173>, API 문서는 <http://localhost:18090/docs> 입니다.

### 둘러보기

```bash
uv run python -m foldfront.cli demo 6         # 모의 실행 6건을 만들어 화면을 채웁니다
uv run python -m foldfront.cli demo-gate      # 검토 지점에서 멈춘 실행 1건
```

`--mock` 으로 만든 수치는 난수입니다. 화면에 모의임을 표시하며 실제 값이 아닙니다.

### 명령 목록

| 명령 | 설명 |
|---|---|
| `foldfront.cli seed` | 기본 모델과 워크플로 템플릿을 등록합니다 |
| `foldfront.cli demo <N>` | 모의 실행 N 건을 만듭니다 |
| `foldfront.cli demo-gate` | 검토 지점에서 멈춘 실행을 하나 만듭니다 |
| `foldfront.cli worker [--mock]` | 큐의 작업을 가져와 실행합니다 |
| `foldfront.cli migrate <경로>` | 기존 파일 기반 실행 결과를 데이터베이스로 옮깁니다 |
| `foldfront.cli status` | 적재 현황을 출력합니다 |

### 모델 연결

모델은 RunPod 서버리스 엔드포인트나 자체 호스팅 HTTP 워커에서 돕니다.
`.env` 에 자격 증명과 엔드포인트를 넣으면 연결됩니다.

```env
RUNPOD_API_KEY=
PROTEINMPNN_ENDPOINT_ID=
MMSEQS_ENDPOINT_ID=
COLABFOLD_ENDPOINT_ID=
```

엔드포인트 하나만 연결해도 그 단계는 실제로 돕니다.

```bash
uv run python tools/gpu_smoke.py    # 실제 호출을 한 번 해 보고 단계별로 결과를 찍습니다
```

그 밖의 환경 변수는 `MONGO_URI` · `MONGO_DB` · `API_PORT` · `JOB_LEASE_SECONDS` ·
`LOCAL_LLM_URL` 입니다. 루트 또는 `platform/.env` 에 둡니다.

---

## 구조

```
foldfront/
├── platform/      백엔드 — Python 3.12 · FastAPI · MongoDB
├── web/           웹 콘솔 — React · TypeScript · Vite · shadcn/ui · Tailwind
├── pipeline-mcp/  RAPID 원본 — 단백질 도메인 로직
├── frontend/      원본 콘솔 — 기능 대조용
└── tools/         화면 캡처 · 부하 시험 · GPU 점검
```

| 계층 | 위치 | 하는 일 |
|---|---|---|
| DAG 엔진 | `engine/dag.py` | 위상 정렬로 실행 순서를 정하고 같은 층은 함께 내보냅니다. 조건식은 `eval` 없이 AST 로 읽습니다 |
| 모델 라우팅 | `engine/router.py` | 모델 식별자를 실행 위치로 해석합니다. 비활성·미승인·자원 초과는 실행 전에 막습니다 |
| 작업 큐 | `engine/worker.py` | 점유 기반이라 워커가 죽어도 기한이 지나면 자동 회수됩니다 |
| 원본 도구 연결 | `engine/legacy_view.py` | 실행을 원본 도구가 읽는 형식으로 변환해 원본 도구를 그대로 호출합니다 |
| 외부 인터페이스 | `api/` | REST(OpenAPI 3.1) · MCP JSON-RPC (`POST /mcp`) |

인증은 OIDC/SSO 를 씁니다. 역할과 별개로 실행 단위 접근 범위가 걸리며,
산출물 반출과 로그인·로그아웃은 감사 기록에 남습니다.

---

## 원본 승계

**RAPID** — *Reproducible Pipeline for Solubility-Oriented Protein Redesign with
Resource-Aware Surrogate Triage*, v1.0.29 · `df2140a` · MIT ·
DOI [10.5281/zenodo.20619413](https://doi.org/10.5281/zenodo.20619413)

`pipeline-mcp/` 아래는 **고치지 않습니다.** 의존성으로 포함해 직접 호출하므로
원본의 후속 변경을 그대로 병합할 수 있습니다. `frontend/` 의 원본 콘솔도
기능 대조용으로 남겨 두었습니다.

```bash
#  기준 커밋에 태그를 붙입니다. 원본 이력은 이 저장소에 복제하지 않습니다.
git remote add upstream https://github.com/sblabkribb/protein_pipeline.git
git fetch upstream df2140a84012c280a8d967535a5343fa020d3964
git tag upstream-v1.0.29 df2140a84012c280a8d967535a5343fa020d3964
```

```bash
git diff upstream-v1.0.29 --stat        # 원본 대비 변경 요약
git show upstream-v1.0.29:README.md     # 원본 README
```

승계 기록은 [`UPSTREAM.md`](UPSTREAM.md) 에 있습니다.

---

## 시험

```bash
docker compose -f platform/docker-compose.yml up -d

uv run pytest platform/tests -q     # 서버 — 실제 MongoDB 에 붙어 돕니다
cd web && npm test                  # 화면
cd web && npm run typecheck         # 타입
cd web && npm run e2e               # 브라우저 통합 시나리오
```

---

## 라이선스

**경로마다 라이선스가 다릅니다.** 전문은 [`NOTICE`](NOTICE) 에 있습니다.

| 경로 | 저작권자 | 라이선스 |
|---|---|---|
| `pipeline-mcp/` `frontend/` | Yaeseong Park, 한국생명공학연구원(KRIBB) | **MIT** — [`LICENSE`](LICENSE) |
| `platform/` `web/` | 주식회사 크래프틱시스템즈 | **BUSL 1.1** — [`LICENSE-foldfront`](LICENSE-foldfront) |

`pipeline-mcp/` 와 `frontend/` 는 RAPID v1.0.29 를 승계한 것으로 MIT 조건을 그대로
유지합니다. 승계 경위는 [`UPSTREAM.md`](UPSTREAM.md), 인용은 [`CITATION.cff`](CITATION.cff)
를 참조하십시오.

`platform/` 과 `web/` 은 주식회사 크래프틱시스템즈가 새로 작성해 보유하는 자산이며
Business Source License 1.1 로 배포합니다. 열람·수정·재배포와 평가·연구·시험 목적의
사용은 누구에게나 열려 있고, 비영리 연구·교육기관이 스스로 설치해 쓰는 것도 허용합니다.
제3자가 유상 용역·납품·재판매·관리형 호스팅의 일부로 제공하려면 별도의 상용 라이선스
계약이 필요합니다. 2030-09-22 부로 Apache License 2.0 으로 전환됩니다.
