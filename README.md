# foldfront

> 단백질 설계 모델을 사용자 정의 DAG 워크플로우로 연계하여 **실행 · 비교 · 관리**하는 통합 엔지니어링 플랫폼

**foldfront**는 다양한 단백질 설계 AI 모델을 방향성 비순환 그래프(DAG, Directed Acyclic Graph) 기반의 워크플로우로 유연하게 조합·실행하고, 실험 결과의 1:1 대조 분석 및 모델 버전 라이프사이클을 체계적으로 관리하는 통합 플랫폼입니다.  
한국생명공학연구원(KRIBB)의 [RAPID](https://github.com/sblabkribb/protein_pipeline) v1.0.29 (MIT · Yaeseong Park, KRIBB)의 검증된 도메인 알고리즘을 온전히 계승하여 높은 신뢰성과 확장성을 제공합니다.

---

## 1. 개요 (Overview)

단백질 설계(Protein Redesign)는 다중 서열 정렬(MSA), 백본 구조 생성, 서열 설계, 용해도(Solubility) 스크리닝, 구조 예측 및 신규성 검증에 이르는 다단계 파이프라인을 유기적으로 연결하는 고도화된 연구 프로세스입니다. 각 단계별 AI 모델마다 요구하는 컴퓨팅 자원과 런타임 환경이 상이하며, 연구 목적과 가설에 따라 파이프라인의 실행 순서와 분기 조건이 수시로 변경됩니다.

foldfront는 이러한 복잡한 엔드포인트 파이프라인을 **코드 작성 없이 직관적인 웹 GUI 콘솔에서 통합 제어**할 수 있는 환경을 제공합니다.

- **직관적인 노드 기반 워크플로우 설계**: 캔버스 기반 노드 인터페이스를 통해 파이프라인을 시각적으로 구성하며, **병렬 분기 · 조건부 분기 · 재사용 가능한 워크플로우 템플릿**을 지원합니다.
- **사전 무결성 검증 (Static Integrity Validation)**: 순환 참조(Cyclic Reference), 고립 노드(Orphan Node), 조건식 누락, 미등록 모델 등 그래프 결함을 **파이프라인 실행 전(저장 시점)에 선제 검출**하여 고비용 GPU 및 컴퓨팅 자원의 불필요한 낭비를 원천 차단합니다.
- **모델 레지스트리 (Model Registry) 기반 식별자 관리**: 모델 식별자(ID) 기반의 메타데이터 관리 체계를 적용하여, 신규 모델 도입 시 소스 코드 변경이나 환경 변수 재설정 없이 레지스트리 등록만으로 즉시 파이프라인에 편입할 수 있습니다.
- **실행(Run) 단위 결과 대조 및 감사 추적 (Audit Trail)**: 실행 파라미터, 산출물, 평가 지표(Metric), 이력 로그를 Run 단위로 표준화하여 적재하며, 다중 실행 결과 간(Run-to-Run) 1:1 대조 분석 및 성과 지표 비교를 지원합니다.

---

## 2. Upstream 프로젝트 계승 및 호환성

본 플랫폼은 **RAPID**(*Reproducible Pipeline for Solubility-Oriented Protein Redesign with Resource-Aware Surrogate Triage*, v1.0.29 · `df2140a` · MIT · DOI [10.5281/zenodo.20619413](https://doi.org/10.5281/zenodo.20619413))를 모태로 개발되었습니다.

단백질 설계의 핵심 도메인 로직은 안정적으로 계승하고, 그 상위에 대규모 작업 스케줄링과 유연한 인터페이스를 위한 **엔터프라이즈 실행 계층 및 데이터 계층을 구축**했습니다.

- `pipeline-mcp/`의 핵심 도메인 모듈(`bio/`, `clients/`)을 **수정 없이 원형 그대로 유지**하여, Upstream 프로젝트의 기능 갱신 시 재개발 비용 없이 최신 릴리스를 즉시 병합(Merge)할 수 있습니다.
- `frontend/` 디렉터리에 원본 바닐라 JS 콘솔을 보존하여 신규 플랫폼과의 기능 동등성(Parity) 검증 및 교차 검증 기준으로 활용합니다.
- 상세한 이관 내역은 [`UPSTREAM.md`](UPSTREAM.md)를 참조하십시오. 원본 기술 가이드는 배포본에 동봉합니다.

```bash
# Upstream(v1.0.29) 대비 변경 사항 요약 확인
git diff upstream-v1.0.29 --stat
```

---

## 3. 시스템 아키텍처 및 구성 (Architecture)

```
foldfront/
├── platform/           백엔드 코어 — Python 3.12 · uv · FastAPI · MongoDB (패키지: foldfront)
├── web/                웹 콘솔 — React 18 · TypeScript · Vite · shadcn/ui · Tailwind CSS v4
├── pipeline-mcp/       RAPID 원본 모듈 — 단백질 도메인 로직 원천
├── frontend/           레거시 웹 콘솔 — 기능 동등성 검증용 참조 코드
└── docs/               기술 문서 (저장소에 포함하지 않으며 배포본에 동봉합니다)
```

### 주요 계층별 아키텍처 명세

| 계층 (Layer) | 구성 모듈 | 주요 기능 및 엔지니어링 특징 |
|---|---|---|
| **DAG 엔진** | `platform/.../engine/dag.py` | 위상 정렬(Topological Sort) 알고리즘 기반 실행 순서 스케줄링 및 동일 뎁스 노드 병렬 디스패치. 보안 취약점(`eval`)을 배제하고 안전한 AST 파서 기반의 조건 분기식 처리 |
| **모델 레지스트리** | `platform/.../engine/router.py` | 모델 식별자를 실제 연산 환경(RunPod 서버리스, HTTP 워커, 로컬 컨테이너)으로 동적 라우팅. 비활성화 모델, 미승인 버전, 자원 임계치 초과 요청에 대한 사전 인가 통제 |
| **분산 작업 큐** | `platform/.../engine/worker.py` | 임대(Lease) 기반 작업 락(Lock) 메커니즘을 적용한 무상태(Stateless) 워커 구조. 노드 장애나 서비스 재기동 시에도 태스크 유실 없이 자동 복구 및 재할당 |
| **데이터 영속 계층** | `platform/.../db/models.py` | MongoDB 기반 14종 도메인 문서 스키마 및 43개 최적화 인덱스 구성. 레거시 Run 디렉터리 데이터를 무손실 적재하는 CLI 마이그레이터 내장 |
| **RESTful API** | `platform/.../api/routes.py` | OpenAPI 3.1 표준 규격의 24개 엔드포인트 제공. 원본 MCP 도구 호출 규격을 표준 RESTful 인터페이스로 추상화 |
| **웹 콘솔 UI** | `web/` | 실행 구성, 워크플로우 스튜디오, 실행 관제, 결과 분석, 모델 관리, 시스템 환경설정 등 6대 업무 화면 제공 |

> **디자인 원칙**: 전문 연구원의 장시간 데이터 판독 피로도를 최소화하기 위해 **모노크롬(Monochrome) 기반의 고대비 미니멀 UI**를 적용했습니다. 상태 인디케이터(5종)에만 정밀하게 정의된 강조 색상을 사용하여 이상 징후를 즉각 식별할 수 있으며, 흑백 출력물이나 화면 캡처 보고서에서도 정보 손실이 발생하지 않습니다. (상세 규격은 화면정의서에 있습니다)

---

## 4. 빠른 시작 (Quick Start)

### 시스템 요구사항
- **Runtime**: Python 3.12 이상, Node.js 20 이상
- **Package Manager**: [uv](https://docs.astral.sh/uv/) (Python 권장), npm
- **Database**: Docker 기반 MongoDB 6.0+

### 단계별 설치 및 구동

```bash
# 1. 데이터베이스(MongoDB) 컨테이너 기동
docker compose -f platform/docker-compose.yml up -d

# 2. 백엔드 가상환경 구축 및 서비스 실행
uv sync --extra dev
uv run python -m foldfront.cli seed          # 기본 모델 레지스트리 및 표준 워크플로우 템플릿 초기화
uv run uvicorn foldfront.api.app:app --port 18090

# 3. 백그라운드 태스크 워커 기동 (별도 터미널 세션)
# ※ 연동된 GPU 환경이 없는 경우 --mock 플래그를 사용하여 모의 모드로 구동
uv run python -m foldfront.cli worker --mock

# 4. 웹 프론트엔드 콘솔 기동 (별도 터미널 세션)
cd web && npm install && npm run dev         # 기본 접속 경로: http://localhost:5173
```

- **OpenAPI 대화형 문서 (Swagger)**: <http://localhost:18090/docs>
- **시스템 상태 점검 (Health Check)**: <http://localhost:18090/healthz>

### 운영 관리 CLI 가이드

| CLI 명령어 | 설명 |
|---|---|
| `uv run python -m foldfront.cli seed` | 기본 모델 메타데이터 및 사전 정의 워크플로우 템플릿을 데이터베이스에 등록합니다. |
| `uv run python -m foldfront.cli demo <N>` | 테스트 및 시연을 위한 모의 실행 이력(Run) 데이터 N건을 자동 생성합니다. |
| `uv run python -m foldfront.cli worker [--mock]` | 큐에 등록된 파이프라인 작업을 폴링하여 순차 실행합니다. (`--mock`: 더미 어댑터 구동) |
| `uv run python -m foldfront.cli migrate <경로>` | 기존 레거시 파일 기반 실행 결과 디렉터리를 DB로 무손실 이관합니다. (원본 데이터 보존) |
| `uv run python -m foldfront.cli status` | 현재 시스템 적재 데이터 현황 및 서비스 상태 요약을 콘솔에 출력합니다. |

*※ 주요 환경 변수(`MONGO_URI`, `MONGO_DB`, `API_HOST`, `API_PORT`, `RUNPOD_API_KEY`, `JOB_LEASE_SECONDS`)는 루트 또는 `platform/.env` 파일에서 통합 관리됩니다.*

---

## 5. 테스트 및 품질 검증 (Verification)

```bash
# 백엔드 단위 및 통합 테스트 수행 (147개 테스트 케이스)
uv run --directory platform pytest -q

# 프론트엔드 컴포넌트 단위 테스트 수행 (17개 테스트 케이스)
cd web && npm test

# 프론트엔드 정적 타입 검사 및 빌드 검증
cd web && npm run typecheck && npm run build
```

---

## 6. 라이선스 및 인용 (License & Citation)

본 프로젝트는 **MIT 라이선스**를 따릅니다. 원본 RAPID의 저작권 고지는 [`LICENSE`](LICENSE) 파일에 명시되어 있으며, 본 소프트웨어 또는 연구 산출물을 인용할 경우 [`CITATION.cff`](CITATION.cff)를 참조하시기 바랍니다.
