# foldfront

> 단백질 설계 모델을 자유형 DAG 워크플로로 조합하여 **실행 · 비교 · 관리**하는 통합 플랫폼

**foldfront**는 다양한 단백질 설계 AI 모델을 유연한 DAG(비순환 유향 그래프) 워크플로로 구성하여 실행하고, 실험 결과를 비교·분석하며, 모델 버전을 체계적으로 관리할 수 있는 플랫폼입니다. 
[RAPID](https://github.com/sblabkribb/protein_pipeline) v1.0.29 (MIT · Yaeseong Park, KRIBB)의 검증된 도메인 로직을 온전히 계승하여 개발되었습니다.

---

## 소개

단백질 설계는 여러 모델을 유기적으로 연결하는 복합적인 과정입니다. 다중 서열 정렬(MSA)을 수행하고, 백본 구조를 생성하며, 서열을 설계한 뒤, 용해도(Solubility)를 스크리닝하고 구조 예측 및 신규성을 평가합니다. 모델마다 요구하는 컴퓨팅 자원과 실행 환경이 다르고, 연구 목적에 따라 실행 순서와 분기 조건도 끊임없이 변화합니다.

foldfront는 이 모든 파이프라인을 **코드가 아닌 웹 화면(GUI)에서 직관적으로 제어**할 수 있도록 지원합니다.

- **직관적인 워크플로 설계**: 노드 드래그 앤 드롭 방식으로 파이프라인을 구축하며, **병렬 분기 · 조건 분기 · 재사용 가능한 템플릿**을 지원합니다.
- **사전 그래프 무결성 검증**: 순환 참조, 고아 노드, 조건식 누락, 미등록 모델 등의 결함을 **실행 단계가 아닌 저장 시점에 검출**하여 불필요한 GPU/컴퓨팅 자원 낭비를 방지합니다.
- **식별자 기반 모델 레지스트리**: 모델을 고유 식별자(ID)로 등록·관리하므로, 새로운 모델을 도입할 때 소스 코드나 환경 변수를 수정할 필요가 없습니다.
- **실행(Run) 단위 비교 및 감사**: 실행 이력, 산출물, 평가지표, 감사 로그를 실행 단위로 자동 적재하며, 실행 간(Run-to-Run) 1:1 대조 분석을 지원합니다.

## 프로젝트 계승 (Upstream)

본 프로젝트는 **RAPID**(*Reproducible Pipeline for Solubility-Oriented Protein Redesign with Resource-Aware Surrogate Triage*, v1.0.29 · `df2140a` · MIT · DOI [10.5281/zenodo.20619413](https://doi.org/10.5281/zenodo.20619413))를 기반으로 합니다.

단백질 설계의 핵심 도메인 지식은 원본을 계승하고, foldfront는 **그 위에 확장 가능한 실행 계층과 데이터 계층을 구축**했습니다.

- `pipeline-mcp/`의 핵심 도메인 로직(`bio/`, `clients/`)을 **수정 없이 유지**하여 원본 프로젝트의 업데이트를 상각 없이 병합할 수 있습니다.
- `frontend/`에는 원본 바닐라 JS 콘솔을 보존하여 동작 검증 및 대조 근거로 활용합니다.
- 상세한 승계 내역은 [`UPSTREAM.md`](UPSTREAM.md)에서, 원본 안내 문서는 [`docs/UPSTREAM-README.md`](docs/UPSTREAM-README.md)에서 확인하실 수 있습니다.

```bash
git diff upstream-v1.0.29 --stat    # 원본 대비 변경 내역 요약
```

## 시스템 구성

```
foldfront/
├── platform/           백엔드 — Python 3.12 · uv · FastAPI · MongoDB (패키지 foldfront)
├── web/                웹 콘솔 — React 18 · TypeScript · Vite · shadcn/ui · Tailwind v4
├── pipeline-mcp/       원본 RAPID — 도메인 로직 원천
├── frontend/           원본 콘솔 — 참조용
└── docs/               화면 정의서 · 동작 실측표 · 원본 문서
```

| 계층 | 역할 및 주요 특징 |
|---|---|
| **DAG 엔진**<br>`platform/src/foldfront/engine/dag.py` | 위상 정렬(Topological Sort)을 기반으로 실행 순서를 결정하고, 동일 계층의 독립 노드를 병렬 실행합니다. 안전성을 위해 `eval`을 배제하고 경로·연산자·수치 기반의 조건식만 파싱합니다. |
| **Model Registry**<br>`engine/router.py` | 모델 식별자를 실제 실행 환경(RunPod → HTTP 워커 → 컨테이너)으로 자동 라우팅합니다. 비활성·미승인 상태이거나 자원 한도를 초과한 버전은 사전에 차단합니다. |
| **작업 큐**<br>`engine/worker.py` | 임대(Lease) 기반 작업 할당 방식을 적용했습니다. 워커가 무상태(Stateless)로 동작하므로 서비스 재기동 시에도 작업이 유실되지 않습니다. |
| **데이터 계층**<br>`db/models.py` | 14종의 문서 스키마와 43개의 인덱스를 갖추고 있습니다. 기존 실행 결과 디렉터리를 손실 없이 가져올 수 있는 마이그레이션 도구를 함께 제공합니다. |
| **HTTP API**<br>`api/routes.py` | OpenAPI 3.1 규격의 24개 엔드포인트를 제공하며, 원본 MCP 도구 명칭을 직관적인 REST 경로로 매핑했습니다. |
| **웹 콘솔**<br>`web/` | 실행 준비, 워크플로 스튜디오, 실행 모니터링, 결과 분석, 모델 관리, 시스템 운영 등 6대 핵심 뷰를 제공합니다. |

콘솔 UI는 **미니멀한 모노크롬(무채색) 톤**을 기본으로 디자인되었습니다. 시각적 피로를 줄이고 5가지 실행 상태에만 명확한 식별 색상을 부여하여, 이상 징후나 오류를 즉각 인지할 수 있습니다. (흑백 인쇄나 문서 캡처 시에도 정보 손실이 없습니다.) 자세한 규격은 [`docs/화면정의서.md`](docs/화면정의서.md)를 참고해 주세요.

## 빠른 시작

**사전 요구사항**: Python 3.12+, Node 20+, [uv](https://docs.astral.sh/uv/), Docker (MongoDB 구동용)

```bash
# 1. 데이터베이스(MongoDB) 실행
docker compose -f platform/docker-compose.yml up -d

# 2. 백엔드 설정 및 실행
uv sync --extra dev
uv run python -m foldfront.cli seed          # 기본 모델 및 워크플로 템플릿 등록
uv run uvicorn foldfront.api.app:app --port 18090

# 3. 워커 실행 (별도 터미널) — GPU 없이 테스트할 경우 --mock 옵션 사용
uv run python -m foldfront.cli worker --mock

# 4. 웹 콘솔 실행 (별도 터미널)
cd web && npm install && npm run dev         # http://localhost:5173 접속
```

- API 대화형 명세서: <http://localhost:18090/docs>
- 헬스체크 엔드포인트: `/healthz`

| 명령어 | 설명 |
|---|---|
| `foldfront.cli seed` | 기본 모델 레지스트리 및 워크플로 템플릿을 등록합니다. |
| `foldfront.cli demo 6` | 데모용 모의 실행 데이터 6건을 생성합니다. |
| `foldfront.cli worker [--mock]` | 백그라운드 작업 큐를 소비하여 태스크를 실행합니다. |
| `foldfront.cli migrate <경로>` | 기존 실행 결과(run) 디렉터리를 이관합니다. (원본 파일 보존) |
| `foldfront.cli status` | 현재 데이터 적재 및 시스템 현황을 출력합니다. |

주요 환경 변수: `MONGO_URI`, `MONGO_DB`, `API_HOST`, `API_PORT`, `RUNPOD_API_KEY`, `JOB_LEASE_SECONDS` (`.env` 파일로 관리)

## 테스트

```bash
# 백엔드 단위 테스트 (147개 항목)
uv run --directory platform pytest -q

# 프론트엔드 테스트 및 빌드 검증 (17개 항목)
cd web && npm test
cd web && npm run typecheck && npm run build
```

## 개발 및 지원 현황

투명하고 검증된 구현 상태만을 안내합니다. 항목별 상세 측정 근거는 [`docs/요구사항-동작실측표.md`](docs/요구사항-동작실측표.md)에 정리되어 있습니다.

| 구분 | 내용 |
|---|---|
| **지원 완료** | DAG 설계·검증·실행 스케줄링, 모델 등록 및 동적 라우팅, 리스 기반 작업 큐, Run 데이터 적재 및 비교, 워크플로 Fork, 기존 데이터 마이그레이션, 웹 콘솔 6개 화면 |
| **진행 중** | **실제 GPU 엔드포인트 연동** (RunPod · HTTP · 컨테이너 어댑터는 구현 완료되었으며, 현재 모의 어댑터를 통해 검증 중) |
| **예정** | 통합 대시보드, 3D 단백질 구조 뷰어, AI 설계 Copilot, 세분화된 사용자 권한 관리(RBAC) |

## 라이선스

MIT 라이선스를 따릅니다. 원본 RAPID의 저작권 고지는 [`LICENSE`](LICENSE)에 유지되어 있으며, 학술 연구에 인용할 경우 [`CITATION.cff`](CITATION.cff)를 참조해 주시기 바랍니다.
