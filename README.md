# foldfront

**foldfront**는 다양한 단백질 설계 AI 모델을 파이프라인으로 연결하여 유연하게 실행하고, 실험 결과를 다각도로 비교·분석하며, 전체 실행 이력을 체계적으로 관리하는 엔터프라이즈급 바이오인포매틱스 플랫폼입니다.

한국생명공학연구원(KRIBB)의 오픈소스 단백질 엔지니어링 파이프라인인 [RAPID](https://github.com/sblabkribb/protein_pipeline) v1.0.29를 기반으로 개발되었습니다. 검증된 단백질 연산 로직과 판정 규칙은 그대로 계승하고, 그 상위에 **워크플로 엔진·분산 스케줄링·결과 분석·RBAC 권한 관리·GPU 인프라 관제 계층**을 새롭게 구축했습니다.

```
[MSA] ──> [RFdiffusion] ──> [BioEmu] ──> [ProteinMPNN] ──> [SoluProt] ──> [AlphaFold2] ──> [Novelty Analysis]
```

기존 파이프라인이 코드 레벨에서 선형적인 순서로 고정되어 있었다면, foldfront에서는 연구자가 웹 콘솔의 인터랙티브 캔버스에서 드래그 앤 드롭으로 노드를 직접 배치하고 연결합니다. 복잡한 조건 분기, 대규모 병렬 실행, 연구자가 직접 결과를 검토하고 승인하는 **휴먼 인 더 루프(Human-in-the-loop)** 단계까지 자유롭게 설계할 수 있습니다.

---

## 핵심 기능 (Key Features)

### 1. 인터랙티브 워크플로 캔버스
- **6가지 핵심 노드 지원**: 모델 실행(Model Run), 데이터 변환(Transform), 조건 분기(Condition), 병렬 분기(Parallel Split), 병합(Join), 검토 지점(Approval Gate) 노드를 제공합니다.
- **사전 유효성 검증(Validation)**: 순환 참조(Cycle), 연결이 끊긴 노드, 미설정 조건식 등을 워크플로 저장 시점에 자동 검사하여, 실제 고비용 GPU 자원이 소모되기 전에 오류를 방지합니다.

### 2. 정밀한 실행 제어 및 휴먼 인 더 루프 (Human-in-the-loop)
- **승인 게이트(Gate)**: 워크플로가 검토 지점에 도달하면 연산이 안전하게 일시 정지되며, 연구자의 승인/반려에 따라 후속 단계 진행 여부가 결정됩니다.
- **안전한 일시중지 & 롤백 재실행**: 실행을 일시중지하면 신규 작업 할당만 차단되고 이미 실행 중인 연산은 안전하게 완료됩니다. 특정 단계만 재실행(Retry)할 경우, 하위 종속 단계들도 함께 초기화되어 이전 데이터가 오염되는 것을 방지합니다.
- **실행 분기(Forking)**: 과거 완료된 실행의 중간 체크포인트에서 파이프라인을 분기하여 새로운 조건으로 실험을 이어갈 수 있습니다.

### 3. 다차원 결과 비교 및 구조 분석 (Side-by-side Comparison)
- **후보군 스코어링 & 랭킹**: 생성된 단백질 설계 후보군을 사용자 정의 가중치 기반 종합 점수로 정렬하고, 야생형(Wild-type) 대비 아미노산 변이 위치와 개수를 즉시 확인합니다.
- **백본(Backbone) 전략 비교**: 구조 생성 모델(출처)별로 결과를 그룹화하여 어떤 설계 접근 방식의 성공률이 높은지 대조 분석합니다.
- **병렬 비교 뷰어**: 서로 다른 두 개 이상의 실행 결과를 나란히 배치하여 단계별 정량 지표와 3D 단백질 구조(PDB)를 직관적으로 비교·검증할 수 있습니다.

### 4. 지능형 메트릭 해석 및 가이드
- **이상 징후 감지 및 처방**: 다중서열정렬(MSA) 깊이 부족, 용해도(Solubility) 필터 통과율 저하, pLDDT 신뢰도 미달 등의 이슈 발생 시 원인 분석과 함께 다음 실험에서 조정할 파라미터 권장값을 제시합니다.
- **판정 투명성**: 시스템이 제시하는 가이드는 근거가 된 측정값과 판정 임계값을 함께 투명하게 표시하므로, 연구자가 도메인 지식에 따라 유연하게 판단을 내릴 수 있습니다.

### 5. 자연어 기반 워크플로 생성 (Prompt-to-Workflow)
- *"RFdiffusion으로 백본을 생성하고 SoluProt으로 필터링한 뒤 AlphaFold2로 예측해줘"* 와 같이 일상적인 문장으로 요청하면, 워크플로 초안을 자동으로 생성하여 캔버스 스튜디오에 로드합니다.
- 초안은 임의로 자동 실행되거나 저장되지 않으므로, 연구자가 시각적으로 확인하고 미세 조정한 뒤 안전하게 실행할 수 있습니다.

### 6. 서버리스 GPU 인프라 관제 및 비용 최적화
- RunPod 서버리스 엔드포인트의 헬스체크 상태, 활성 워커, 큐 대기 작업 수, 리소스 사용량 및 발생 비용을 실시간으로 모니터링합니다.
- 워크플로 부하에 맞춰 워커 풀 인스턴스 수를 유연하게 스케일링할 수 있습니다.

### 7. 미니멀 모노크롬 디자인 시스템
- 인터페이스 전반에 고대비 모노크롬 톤을 적용하여 시각적 피로도를 낮추고, 오직 실행 상태(Status)에만 포인트 컬러를 부여했습니다.
- 정보의 위계는 굵기, 경계선, 여백, 타이포그래피로 명확하게 표현되므로, 연구 보고서 출력이나 흑백 복사 시에도 데이터 손실 없이 완벽한 가독성을 보장합니다.

---

## 빠른 시작 (Quick Start)

### 시스템 요구사항

- **Python**: 3.12 이상
- **Node.js**: 20 이상
- **패키지 매니저**: [uv](https://docs.astral.sh/uv/), npm
- **컨테이너**: Docker & Docker Compose (MongoDB 구동용)

### 설치 및 로컬 서버 실행

```bash
# 1. MongoDB 컨테이너 구동
docker compose -f platform/docker-compose.yml up -d

# 2. 백엔드 의존성 설치 및 API 서버 실행
uv sync --extra dev
uv run python -m foldfront.cli seed           # 기본 모델 메타데이터 및 워크플로 템플릿 등록
uv run uvicorn foldfront.api.app:app --port 18090

# 3. 작업 워커 구동 (별도 터미널)
# GPU가 없는 환경에서는 --mock 플래그를 사용하여 모의 연산 모드로 실행할 수 있습니다.
uv run python -m foldfront.cli worker --mock

# 4. 웹 콘솔 빌드 및 개발 서버 실행 (별도 터미널)
cd web && npm install && npm run dev
```

- **웹 콘솔 대시보드**: <http://localhost:5173>
- **백엔드 API 문서 (Swagger UI)**: <http://localhost:18090/docs>

### 둘러보기 (데모 데이터 적재)

```bash
# 모의 실행 데이터 6건을 생성하여 대시보드를 채웁니다.
uv run python -m foldfront.cli demo 6

# 검토 지점(Gate)에서 승인 대기 중인 모의 실행 1건을 생성합니다.
uv run python -m foldfront.cli demo-gate
```

> **참고**: `--mock` 모드로 생성된 수치는 시뮬레이션용 난수 데이터입니다. 화면상에 Mock 데이터임이 명시되며, 실제 생물학적 연산 결과가 아닙니다.

---

## CLI 명령어 가이드

| 명령어 | 설명 |
|---|---|
| `foldfront.cli seed` | 기본 제공 모델 및 표준 워크플로 템플릿을 데이터베이스에 초기화합니다. |
| `foldfront.cli demo <N>` | 대시보드 확인용 모의 실행 데이터 N건을 생성합니다. |
| `foldfront.cli demo-gate` | 검토 지점(Approval Gate)에서 대기 중인 시연용 실행 건을 생성합니다. |
| `foldfront.cli worker [--mock]` | 작업 큐에서 태스크를 폴링하여 순차적으로 연산을 수행합니다. |
| `foldfront.cli migrate <경로>` | 기존 파일 시스템 기반의 실행 결과 아티팩트를 데이터베이스로 마이그레이션합니다. |
| `foldfront.cli status` | 현재 시스템 리소스 상태 및 작업 큐 적재 현황을 조회합니다. |

---

## 외부 GPU 및 AI 모델 연동

단백질 연산 모델은 RunPod 서버리스 엔드포인트 또는 사내에 구축된 자체 호스팅 HTTP 워커와 연동됩니다. 루트 경로 또는 `platform/.env` 파일에 자격 증명과 엔드포인트 ID를 설정합니다.

```env
# RunPod 연동 설정
RUNPOD_API_KEY=your_api_key_here
PROTEINMPNN_ENDPOINT_ID=your_endpoint_id
MMSEQS_ENDPOINT_ID=your_endpoint_id
COLABFOLD_ENDPOINT_ID=your_endpoint_id

# 플랫폼 공통 설정
MONGO_URI=mongodb://localhost:27017
MONGO_DB=foldfront
API_PORT=18090
JOB_LEASE_SECONDS=300
LOCAL_LLM_URL=http://localhost:11434
```

> 단일 엔드포인트만 설정된 상태에서도 해당 단계는 실제 GPU 연산으로 정상 동작하며, 미설정 단계만 Mocking할 수 있습니다.

```bash
# GPU 엔드포인트 연결 상태 및 호출 정상 여부 점검
uv run python tools/gpu_smoke.py
```

---

## 시스템 아키텍처 (Architecture)

```
foldfront/
├── platform/      백엔드 코어 (Python 3.12, FastAPI, MongoDB)
├── web/           웹 콘솔 (React, TypeScript, Vite, shadcn/ui, Tailwind CSS)
├── pipeline-mcp/  RAPID 엔진 (단백질 생물학적 도메인 연산 로직)
├── frontend/      오리지널 콘솔 (기능 호환성 대조용)
└── tools/         테스트 및 자동화 유틸리티 (E2E 캡처, 부하 테스트, GPU 헬스체크)
```

| 컴포넌트 | 소스 위치 | 주요 역할 및 기술 스펙 |
|---|---|---|
| **DAG 엔진** | `engine/dag.py` | 위상 정렬(Topological Sort)을 통해 최적의 실행 경로를 산출하고 병렬 가능 노드를 동시 스케줄링합니다. 조건식 평가는 보안을 위해 `eval` 대신 안전한 AST 파서를 사용합니다. |
| **모델 라우터** | `engine/router.py` | 모델 식별자를 실제 실행 인프라(로컬/서버리스)로 매핑합니다. 비활성화, 미승인, 자원 한도 초과 작업을 런타임 진입 전에 차단합니다. |
| **분산 작업 큐** | `engine/worker.py` | 점유(Lease) 기반 분산 큐 시스템입니다. 연산 워커 장애 발생 시 하트비트 만료 후 태스크가 안전하게 자동 회수(Failover)됩니다. |
| **레거시 어댑터** | `engine/legacy_view.py` | 실행 파이프라인의 입출력을 RAPID 원본 형식으로 매핑하여 원본 연산 도구를 손실 없이 구동합니다. |
| **외부 인터페이스** | `api/` | 표준 RESTful API (OpenAPI 3.1)와 LLM/외부 에이전트 연동을 위한 Model Context Protocol(MCP JSON-RPC, `POST /mcp`)을 지원합니다. |

- **보안 및 접근 제어**: OIDC/SSO 기반 통합 인증을 제공합니다. 전역 역할(RBAC)과 더불어 실행 단위(Run-level) 세분화 권한을 제어하며, 결과물 다운로드(반출) 및 인증 이벤트는 감사 로그(Audit Log)에 보존됩니다.

---

## 원본 오픈소스 계승 (Upstream Lineage)

본 프로젝트는 한국생명공학연구원(KRIBB) 구조생물학연구센터의 오픈소스 파이프라인을 기반으로 합니다.
- **프로젝트**: **RAPID** (*Reproducible Pipeline for Solubility-Oriented Protein Redesign with Resource-Aware Surrogate Triage*)
- **버전 및 커밋**: v1.0.29 (`df2140a`)
- **라이선스**: MIT License
- **학술 인용**: DOI [10.5281/zenodo.20619413](https://doi.org/10.5281/zenodo.20619413)

`pipeline-mcp/` 디렉터리는 **업스트림 원본 코드를 보존**하며 외부 의존성 형태로 호출합니다. 이를 통해 원본 연구의 최신 알고리즘 업데이트가 발생할 경우 충돌 없이 원활하게 병합(Upstream Sync)할 수 있습니다. `frontend/` 디렉터리 역시 기능 및 UI 호환성 대조 목적으로 유지됩니다.

```bash
# 업스트림 원본 저장소 연결 및 태그 확인
git remote add upstream https://github.com/sblabkribb/protein_pipeline.git
git fetch upstream df2140a84012c280a8d967535a5343fa020d3964
git tag upstream-v1.0.29 df2140a84012c280a8d967535a5343fa020d3964

# 원본 대비 변경점 및 차이점 확인
git diff upstream-v1.0.29 --stat
git show upstream-v1.0.29:README.md
```

자세한 계승 내역과 변경 사항은 [`UPSTREAM.md`](UPSTREAM.md)를 참고하시기 바랍니다.

---

## 테스트 및 품질 검증

```bash
# 1. 테스트용 데이터베이스 준비
docker compose -f platform/docker-compose.yml up -d

# 2. 백엔드 단위 및 통합 테스트 (실제 MongoDB 기반)
uv run pytest platform/tests -q

# 3. 프론트엔드 테스트 및 정적 분석
cd web
npm test                  # 단위 및 컴포넌트 테스트
npm run typecheck         # TypeScript 정적 타입 검사
npm run e2e               # Playwright 기반 브라우저 E2E 통합 시나리오
```

---

## 라이선스 정책 (License)

본 저장소는 모듈별로 서로 다른 라이선스가 적용되는 듀얼 라이선스 체계를 따릅니다. 상세 내용은 [`NOTICE`](NOTICE) 문서를 확인하십시오.

| 컴포넌트 경로 | 저작권자 | 적용 라이선스 |
|---|---|---|
| `pipeline-mcp/`, `frontend/` | Yaeseong Park, 한국생명공학연구원(KRIBB) | **MIT License** ([`LICENSE-MIT-upstream`](LICENSE-MIT-upstream)) |
| `platform/`, `web/` | 주식회사 크래프틱시스템즈 | **Business Source License 1.1 (BUSL 1.1)** ([`LICENSE`](LICENSE)) |

- `pipeline-mcp/` 및 `frontend/`는 RAPID v1.0.29의 원본 소스로서 MIT 라이선스 조건을 유지합니다. 학술 논문 인용 정보는 [`CITATION.cff`](CITATION.cff)를 참조하십시오.
- `platform/` 및 `web/`은 주식회사 크래프틱시스템즈의 독자 자산으로서 Business Source License 1.1 하에 배포됩니다.
  - 소스 코드 열람, 수정, 재배포 및 비상업적 목적의 평가·학술 연구·사내 테스트는 누구에게나 자유롭게 허용됩니다.
  - 비영리 연구기관 및 교육기관의 온프레미스 자체 설치 및 내부 연구 활용 역시 무상 허용됩니다.
  - 단, 제3자를 대상으로 한 유상 용역, 상용 솔루션 납품, 재판매 또는 관리형 호스팅(SaaS) 서비스의 일부로 제공하는 상업적 이용의 경우 별도의 상용 라이선스 계약이 체결되어야 합니다.
  - 본 라이선스는 **2030년 9월 22일**부로 오픈소스인 **Apache License 2.0**으로 완전 전환됩니다.
