# foldfront

> 단백질 설계 모델을 자유형 DAG 워크플로로 조합해 **실행 · 비교 · 관리**하는 플랫폼.

**foldfront** composes protein-design models into free-form DAG workflows — running them,
comparing runs, and versioning the models behind them. It succeeds
[RAPID](https://github.com/sblabkribb/protein_pipeline) v1.0.29 (MIT · Yaeseong Park, KRIBB)
and keeps its domain logic intact.

---

## 무엇인가

단백질 설계는 여러 모델을 잇는 작업이다 — 정렬을 뜨고, 백본을 생성하고, 서열을 설계하고,
가용성을 거르고, 구조를 예측하고, 신규성을 본다. 모델마다 실행 위치와 자원 요구가 다르고,
연구 목적에 따라 순서와 분기가 달라진다.

foldfront 는 그 순서를 **코드가 아니라 화면에서 정하게 한다.**

- 노드를 조합해 흐름을 설계한다. **병렬 분기 · 조건 분기 · 재사용 템플릿**을 지원한다.
- 그래프 결함(순환 · 고아 노드 · 조건식 누락 · 미등록 모델)을 **실행 시점이 아니라 저장 시점에** 잡는다.
  실행 자원을 낭비하지 않는다.
- 모델을 **고유 식별자로 등록**한다. 새 모델을 붙이려고 코드나 환경변수를 고치지 않는다.
- 실행 이력 · 산출물 · 지표 · 감사 기록을 run 단위로 적재하고 run 대 run 으로 대조한다.

## 승계

원본은 **RAPID** — *Reproducible Pipeline for Solubility-Oriented Protein Redesign with
Resource-Aware Surrogate Triage* (v1.0.29 · `df2140a` · MIT · DOI
[10.5281/zenodo.20619413](https://doi.org/10.5281/zenodo.20619413)).

단백질 도메인 지식은 원본에 있고, foldfront 는 **그 위에 실행 계층과 데이터 계층을 세운다.**

- `pipeline-mcp/` 의 도메인 로직(`bio/` · `clients/`)을 **고치지 않는다.** 원본 변경분을 언제든 병합한다.
- `frontend/` 는 원본 바닐라 JS 콘솔이다. 대조 근거로 남긴다.
- 승계 관계는 [`UPSTREAM.md`](UPSTREAM.md), 원본 README 는 [`docs/UPSTREAM-README.md`](docs/UPSTREAM-README.md) 에 있다.

```bash
git diff upstream-v1.0.29 --stat    # 원본 대비 변경 요약
```

## 구성

```
foldfront/
├── platform/           백엔드 — Python 3.12 · uv · FastAPI · MongoDB (패키지 foldfront)
├── web/                콘솔 — React 18 · TypeScript · Vite · shadcn/ui · Tailwind v4
├── pipeline-mcp/       원본 RAPID — 도메인 로직 원천
├── frontend/           원본 콘솔 — 참조용
└── docs/               화면정의서 · 동작 실측표 · 원본 문서
```

| 계층 | 내용 |
|---|---|
| **DAG 엔진** `platform/src/foldfront/engine/dag.py` | 위상 정렬로 실행 순서를 정하고 같은 층을 병렬로 묶는다. 조건식은 `eval` 을 쓰지 않고 경로·비교연산자·수 형태만 받는다 |
| **Model Registry** `engine/router.py` | 모델 식별자를 실행 위치로 해석한다. RunPod → HTTP 워커 → 컨테이너 순. 비활성·미승인·자원 초과 버전은 라우팅하지 않는다 |
| **작업 큐** `engine/worker.py` | 점유(lease) 방식. 워커가 상태를 들고 있지 않아 재기동에 안전하다 |
| **데이터 계층** `db/models.py` | 문서 스키마 14종 · 인덱스 43개. 기존 run 디렉토리를 옮기는 이관 경로를 함께 둔다 |
| **HTTP API** `api/routes.py` | 24경로 · OpenAPI 3.1. 원본 MCP 도구 이름을 경로에 대응시킨다 |
| **콘솔** `web/` | 실행 준비 · 워크플로 스튜디오 · 실행 감시 · 결과 분석 · 모델 관리 · 운영 |

콘솔은 **무채색 한 벌**이다. 화면에서 색을 갖는 것은 실행 상태 5종뿐이라
흑백 인쇄에서 손실이 없고, 유일한 색이 상태이므로 시선이 이상 징후로 곧장 간다.
자세한 규격은 [`docs/화면정의서.md`](docs/화면정의서.md) 에 있다.

## 빠른 시작

요구 — Python 3.12+ · Node 20+ · [uv](https://docs.astral.sh/uv/) · Docker(MongoDB 용)

```bash
# 1. 저장소 — MongoDB 를 띄운다
docker compose -f platform/docker-compose.yml up -d

# 2. 백엔드
uv sync --extra dev
uv run python -m foldfront.cli seed          # 모델·워크플로 기본 템플릿 등록
uv run uvicorn foldfront.api.app:app --port 18090

# 3. 워커 (별도 터미널) — GPU 없이 흐름만 볼 때는 --mock
uv run python -m foldfront.cli worker --mock

# 4. 콘솔 (별도 터미널)
cd web && npm install && npm run dev         # http://localhost:5173
```

명세는 <http://localhost:18090/docs>, 상태 조회는 `/healthz` 다.

| 명령 | 하는 일 |
|---|---|
| `foldfront.cli seed` | 모델·워크플로 기본 템플릿을 등록한다 |
| `foldfront.cli demo 6` | 모의 실행 6건을 만든다 |
| `foldfront.cli worker [--mock]` | 작업 큐를 소비한다 |
| `foldfront.cli migrate <경로>` | 기존 run 디렉토리를 이관한다. **원본은 지우지 않는다** |
| `foldfront.cli status` | 적재 현황을 낸다 |

주요 환경변수 — `MONGO_URI` · `MONGO_DB` · `API_HOST` · `API_PORT` ·
`RUNPOD_API_KEY` · `JOB_LEASE_SECONDS`. `.env` 로 읽는다.

## 시험

```bash
uv run --directory platform pytest -q    # 백엔드 147건
cd web && npm test                       # 콘솔 17건
cd web && npm run typecheck && npm run build
```

## 지금 되는 것과 안 되는 것

지어내지 않는다. 항목별 실측은 [`docs/요구사항-동작실측표.md`](docs/요구사항-동작실측표.md) 에 근거와 함께 있다.

| | |
|---|---|
| **된다** | DAG 설계·검증·실행 순서 결정 · 모델 등록과 동적 라우팅 · 작업 큐 · run 적재와 대조 · fork · 이관 · 콘솔 6화면 |
| **진행 중** | **실제 GPU 엔드포인트 연결.** 실행 어댑터(RunPod · HTTP · 컨테이너)는 구현했으나 실증은 모의 어댑터 단계다 |
| **예정** | 대시보드 · 3차원 구조 열람 · 설계 Copilot · 이용자 권한 관리 화면 |

## 라이선스

MIT. 원본 RAPID 의 저작권 고지를 [`LICENSE`](LICENSE) 에 그대로 유지한다.
원본을 인용할 때는 [`CITATION.cff`](CITATION.cff) 를 따른다.
