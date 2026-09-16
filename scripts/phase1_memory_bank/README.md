# Phase 1 Memory Bank — GPU Worker Pipeline

CATH 1,470개 타겟에 대해 대규모 ColabFold 추론을 실행하여 Target-Expert Memory Bank를 구축하기 위한 분산 워커 시스템입니다.

**관련 기획 문서**: `docs/plans/2026-04-24-local-expert-memory-bank-strategy-ko.md`

## 아키텍처

```
┌──────────────────────┐        ┌────────────────────────┐        ┌───────────────────────┐
│ CPU Server (여기)   │        │ RunPod Network Volume   │        │ NCP L4 4ea (GPU)     │
│                      │        │ (S3-compatible API)     │        │                       │
│ - CATH 타겟 로드    │──PUT──▶│ jobs/pending/*.json     │──GET──▶│ 4× gpu_worker.py      │
│ - submit_batches.py  │        │ jobs/processing/*.json  │        │  - claim → fold       │
│                      │        │ jobs/completed/*/       │◀──PUT──│  - upload result      │
│ - collect_results.py │◀──GET──│ jobs/failed/*.json      │        │                       │
│   → Expert 모델      │        │                         │        │                       │
└──────────────────────┘        └────────────────────────┘        └───────────────────────┘
```

- **CPU Server (현재 /opt/protein_pipeline)**: 작업 제출 및 결과 수집 전담. GPU 없이 동작.
- **RunPod Network Volume**: S3 호환 API로 CPU/GPU 서버 간 공유 스토리지 역할.
- **NCP L4 4ea**: ColabFold 전용 워커. 각 GPU당 1 프로세스씩 병렬 가동.

## 파일 구성

### GPU Server (L4 4ea)
| 파일 | 역할 |
|---|---|
| `setup_gpu_server.sh` | 1회성 설치: ColabFold + JAX + boto3 + AF2 가중치 |
| `gpu_worker.py` | S3 폴링 → 작업 claim → ColabFold 실행 → 결과 업로드 (영구 루프) |
| `launch_workers.sh` | 4개 GPU에 워커를 `CUDA_VISIBLE_DEVICES=0..3`으로 고정하여 병렬 실행 |

### CPU Server (현재 /opt/protein_pipeline)
| 파일 | 역할 |
|---|---|
| `prepare_cath_batches.py` | **전처리**: CATH PDB → MPNN → SoluProt → ESM → K-means → FASTA+Embeddings CSV |
| `submit_batches.py` | FASTA를 길이별 버킷팅하여 S3 `jobs/pending/`에 업로드 |
| `monitor.py` | **실시간 모니터링**: Pending/Processing/Completed/Failed 카운트 + ETA + stale 복구 |
| `collect_results.py` | `jobs/completed/` 결과 다운로드 → 데이터셋 CSV 생성 → Expert RF 학습 및 저장 |
| `memory_bank_router.py` | **Phase 2**: Expert Memory Bank 로드 + k-Nearest Experts 라우팅 API |
| `run_phase1.sh` | **원스톱 오케스트레이터**: 전처리 → 업로드 → 모니터링 자동 실행 |

### 공통
| 파일 | 역할 |
|---|---|
| `config.example.env` | 환경 변수 템플릿 (RunPod S3 자격증명 등) |

## 실행 순서

### 1) RunPod에서 Network Volume 생성 및 S3 자격증명 발급
- RunPod 콘솔에서 Network Volume 생성 (예: region `eur-no-1`).
- "Access via S3 API" 탭에서 Bucket name, Endpoint URL, Access Key, Secret Key 확보.

### 2) 양쪽 서버에 `.env` 배치
```bash
cp config.example.env .env
# .env 파일에 실제 자격증명 기입
```

### 3) GPU 서버 1회 세팅

**`setup_gpu_server.sh`이 다운로드하는 것 (총 ~6.2GB):**

| 단계 | 크기 | 출처 | 내용 |
|---|---|---|---|
| ① APT 패키지 | ~200MB | Ubuntu repos | `build-essential`, `python3-venv`, `awscli`, `jq`, `flock` |
| ② Python 패키지 | ~2GB | PyPI | `colabfold[alphafold]==1.5.5`, `jax[cuda12_pip]==0.4.26`, `boto3`, `biopython` |
| ③ AF2 가중치 | ~4GB | **`.env`의 `AF2_WEIGHTS_S3_PATH` 우선** → 없으면 Google Storage fallback | `params/` (5개 모델 + monomer_ptm + multimer) |

**기존 ColabFold 가중치를 RunPod S3에서 사용하려면** `.env`에 다음 한 줄 추가:
```bash
AF2_WEIGHTS_S3_PATH=colabfold/weights   # 실제 S3 경로 (params/가 들어 있는 디렉토리)
```

```bash
scp setup_gpu_server.sh gpu_worker.py launch_workers.sh .env gpu-server:/workspace/phase1/
ssh gpu-server
cd /workspace/phase1
bash setup_gpu_server.sh   # 약 30분 (S3 사용 시 5~10분으로 단축)
```

### 4) CPU 서버에서 전처리 실행
```bash
# 옵션 A: 원스톱 오케스트레이터 (전처리 + 업로드 + 모니터링까지)
LIMIT=5 bash run_phase1.sh   # 먼저 5개 타겟으로 dry-run
bash run_phase1.sh            # 전체 CATH train(1,177개) 실행

# 옵션 B: 단계별 수동 실행
python3 prepare_cath_batches.py \
  --cath-dir /opt/protein_pipeline/cath_train \
  --out-dir /opt/protein_pipeline/phase1_input \
  --num-mpnn 100 \
  --num-seeds 30 \
  --workers 4 \
  --limit 5                   # 먼저 5개 타겟으로 dry-run

python3 submit_batches.py \
  /opt/protein_pipeline/phase1_input/phase1_seeds_YYYYMMDD_HHMMSS.fasta \
  --batch-size 8 \
  --bucket-width 32
```

- `--batch-size 8`: 한 작업에 서열 8개씩 묶음 (L4 24GB 기준 안전)
- `--bucket-width 32`: 32 aa 단위로 길이 버킷팅 → JAX 재컴파일 최소화
- `--num-mpnn 100`: 타겟당 MPNN 생성 서열 수 (기본 1000 → 초기 Cold-Start는 100으로 빠르게)

### 5) GPU 서버에서 워커 실행
```bash
ssh gpu-server
cd /path/to/phase1_memory_bank
bash launch_workers.sh
# tail -f /workspace/logs/worker_gpu*.log
```

### 6) CPU 서버에서 실시간 모니터링 (권장)
```bash
python3 monitor.py --interval 60
# 스테일 작업 자동 복구 모드
python3 monitor.py --recover-stale --stale-minutes 20
```

### 7) CPU 서버에서 결과 수집
```bash
python3 collect_results.py \
  --output-dir /opt/protein_pipeline/pipeline-mcp/models/experts \
  --dataset-csv /opt/protein_pipeline/phase1_dataset.csv \
  --embeddings-csv /opt/protein_pipeline/phase1_input/phase1_embeddings_YYYYMMDD_HHMMSS.csv \
  --archive-completed
```

- `--embeddings-csv`를 제공하면 Target별 RF Expert 모델이 자동 학습되어 `models/experts/` 아래 저장됩니다.
- `--archive-completed`는 완료된 S3 작업을 `jobs/archive/`로 이동시켜 재처리 방지.

### 8) Phase 2 — Memory Bank 활용 (Experts 축적 후)
```bash
# Memory Bank 통계 확인
python3 memory_bank_router.py --stats

# 신규 Target에 대해 k-Nearest Experts 조회
python3 memory_bank_router.py \
  --query-embedding-file /tmp/new_target_embedding.json \
  -k 3

# 파이프라인에서 Phase 2 라우팅 켜기 (PipelineRequest에 추가)
use_memory_bank=True
```

## MSA 전략 (중요)

### Phase 1: MSA **사용 안 함** (Single-Sequence)
- ProteinMPNN 설계 서열은 자연계에 없는 신규 서열이므로 MMseqs2를 호출해도 효용 없음
- GPU 서버에 MMseqs2를 설치하지 **않습니다** (2TB DB, 시간 낭비)
- `MSA_MODE=single_sequence` 기본값으로 충분

### Phase 2 Hybrid: 선택적 MSA (기존 RunPod 엔드포인트 활용)

GPU 서버에 MMseqs2를 깔지 않고, **이미 운영 중인 RunPod serverless `MMSEQS_ENDPOINT_ID`**를 사용합니다.

```
┌──────────────┐  ① MSA 요청          ┌──────────────────────┐
│ CPU Server   │─────────────────────▶│ RunPod MMseqs2       │
│              │◀──── .a3m 파일 ─────│ (기존 serverless)    │
│              │                       └──────────────────────┘
│              │  ② job + .a3m URL 업로드
│              │─────────────────────▶┌──────────────┐
│              │                       │ RunPod S3    │
│              │                       └──────────────┘
└──────────────┘                              │ ③ download
                                              ▼
                                       ┌──────────────┐
                                       │ NCP L4 4ea   │ (MMseqs2 미설치)
                                       │ folding only │
                                       └──────────────┘
```

**이 방식의 장점:**
- GPU 서버 디스크에 2TB MMseqs2 DB 미설치 → 운영 단순
- MSA 생성과 폴딩이 물리적으로 분리되어 GPU 가동률 100%
- 이미 검증된 기존 RunPod 엔드포인트 재활용

```bash
# Phase 2 Hybrid 실행 예시
python3 submit_batches.py input.fasta \
  --hybrid-rescore-from /opt/protein_pipeline/phase1_dataset.csv \
  --rescore-threshold 70 \
  --msa-mode mmseqs2_uniref_env
```

> 단, 현재 `submit_batches.py`는 MSA 모드 플래그만 GPU에 전달합니다. CPU 서버가 RunPod 엔드포인트로 직접 MSA 받아서 `.a3m`을 S3에 업로드하는 사전 계산 모드는 Phase 2 진입 시점에 추가 구현 예정입니다.

## 입력 포맷

### FASTA
```
>seq_001__1a2b_A
MKTVRQERLKSIVRILERSKEPVSGAQLAEELSVSRQVIVQDIAYLRSLGYNIVATPRGYVLAGG
>seq_002__1a2b_A
MKTVRQERLKNIVRILERS...
```

FASTA 헤더의 `>seq_001__1a2b_A` 중 **`__` 뒤쪽이 `target_id`**로 해석됩니다. (예: `1a2b_A`)

### CSV (권장)
```csv
seq_id,sequence,target_id
seq_001,MKTVRQERLK...,1a2b_A
seq_002,MKTVRQERLK...,1a2b_A
```

## S3 스키마

```
t0x7g7z3gv/
  phase1_memory_bank/
    jobs/
      pending/        # 제출 대기 중인 batch (*.json)
      processing/     # 워커가 claim한 작업 (중간 상태)
      completed/
        <job_id>/
          artifacts/  # *.pdb, *_scores_rank_001.json 등 ColabFold 원본
          result.json # {plddt_scores, elapsed_sec, settings}
      failed/
        <job_id>.json # _error, _worker 메타데이터 포함
      archive/        # collect 후 이동 (선택)
```

## 병목 방지 팁

1. **Batch Size와 GPU 메모리**: L4 24GB 기준 배치당 8개가 안전. 300aa 넘어가면 4로 낮추세요.
2. **Warm Pool**: RunPod MPNN/SoluProt 엔드포인트도 `min_workers >= 1`로 유지.
3. **모니터링**: `jobs/processing/`에 오래 남아있는 작업은 실패 전조 — 15분 이상이면 수동 복구 필요.
4. **폭주 방지**: `submit_batches.py`는 1,470 × 30 = 44,100 배치를 한 번에 올리지 말고, 300 타겟씩 나눠 올리면 S3 API 호출 과부하 방지.

## 문제 해결

| 증상 | 원인 | 조치 |
|---|---|---|
| `ModuleNotFoundError: colabfold` | venv 미활성화 | `source /workspace/venv/bin/activate` |
| JAX OOM | 배치가 너무 크거나 길이가 긺 | `--batch-size` 낮추거나 타겟 필터링 |
| 작업이 pending에서 사라지지 않음 | 모든 워커가 죽었거나 자격증명 만료 | `launch_workers.sh` 재시작 |
| `NoSuchBucket` | `.env` 버킷명 오타 | RunPod 콘솔의 실제 Bucket name 확인 |
