# K-Biofoundry Protein Pipeline Console: UI + Pipeline 상세 문서 (PPT 작성용)

작성 기준: 이 저장소의 UI 코드(`frontend/index.html`, `frontend/app.js`)와 파이프라인 구현(`pipeline-mcp/src/pipeline_mcp/*`)을 기반으로 정리했습니다.

---

**1) PPT 슬라이드 아웃라인 (추천 구성)**
1. 문제 정의와 목표: 단백질 설계 워크플로우의 표준화와 추적성
2. 전체 아키텍처 한 장 요약: UI ↔ MCP API ↔ 파이프라인 런너 ↔ 외부 모델/서비스
3. UI 구조 개요: 로그인, Setup/Monitor/Analyze 탭
4. Setup 탭 상세: 모드 선택, 입력 첨부, Preflight/Plan, Run 실행
5. Monitor 탭 상세: 상태 카드, 아티팩트, 에이전트 패널, Workflow Review Gate
6. Analyze 탭 상세: Compare Studio, Run-to-Run Compare, Hit List, 피드백/실험/리포트
7. 파이프라인 전체 흐름: MSA → Conservation → (RFD3) → Ligand Mask → Mask Consensus → ProteinMPNN → SoluProt → AF2 → Novelty
8. 단계별 입력/출력 (1): MSA/Conservation
9. 단계별 입력/출력 (2): RFD3/AF2 Target/Preprocess/Query-PDB
10. 단계별 입력/출력 (3): Ligand Mask/ProteinMPNN
11. 단계별 입력/출력 (4): SoluProt/AF2/Novelty
12. 실행 결과물 구조: `outputs/<run_id>/` 트리와 핵심 파일
13. 리포트/에이전트 패널: 점수·해석·추천 로직
14. 운영 포인트: 상태/로그/복구 전략

---

**2) UI 소개 (상세)**

**2.1 로그인/권한**
- 로그인 화면: 사용자명/비밀번호 입력 후 토큰 기반 세션(`auth/login`, `auth/me`).
- 사용자별 `run_id` 접두사 자동 생성: `username` 기준으로 `userprefix_YYYYMMDD_HHMMSS_rand` 형태.
- Admin 계정: UI에서 사용자 생성 가능(`auth/create_user`).

**2.2 상단 바**
- 언어 전환: KO/EN 토글.
- Usage/Settings/Logout, Admin 버튼(관리자만 노출).

**2.3 Setup 탭 (Run Setup)**
- 목적: 실행 모드 선택 → 입력 첨부 → Preflight → Run 실행.
- Run Mode 선택: `pipeline`, `workflow`, `rfd3`, `bioemu`, `msa`, `design`, `soluprot`, `af2`, `diffdock`.
- 입력 첨부: 파일 업로드 (FASTA/PDB/ligand 등). 업로드 시 파일 내용이 API에 직접 전송.
- 선택 옵션: `start_from`, `stop_after`, `design_chains`, `pdb_strip_nonpositive_resseq`, `rfd3_contig` 등.
- Run 버튼: 필수 입력이 모두 채워질 때만 활성화.
- Check Setup 버튼: Preflight 실행(파이프라인/일부 모드에서만 제공).
- 기존 run 선택 시 `request.json`을 폼으로 불러올 수 있으며, 기본 동작은 새 run fork입니다.
- partial rerun은 `start_from > msa`인 경우에만 의미가 있으며 `Continue same run`을 명시적으로 켜야 합니다.
- Reset Inputs: 입력값 초기화.
- Clear Log: 대화 로그 초기화.

**2.3.1 입력 판별/자동 옵션**
- `target_input` 업로드 시 FASTA/PDB 자동 판별. FASTA는 첫 줄이 `>` 또는 서열 문자만 존재.
- PDB는 `ATOM`/`HETATM` 헤더 기반으로 판별.
- PDB 업로드 시 체인 범위를 파싱하여 `design_chains`/`rfd3_contig` 후보를 자동 생성.
- Pipeline 모드에서 `diffdock_ligand`는 토글로 사용/스킵 선택 가능.
- 스킵 시 ligand 입력은 필수 아님.
- Preflight는 `pipeline`, `workflow`, `rfd3`, `bioemu`, `msa`, `design`, `soluprot` 모드에서 제공.
- `af2`, `diffdock` 모드는 Preflight 없이 직접 실행.
- backend는 request diff와 stage별 request hash를 사용해 unsafe partial rerun을 차단합니다.

**2.4 Monitor 탭**
- Run Monitor 카드: `run_id`, `stage`, `state`, `updated`, `score/evidence/recommendation` 표시.
- Auto Poll: `pipeline.status` 자동 호출.
- Stop Run: `pipeline.cancel_run` 호출로 RunPod 작업 취소 요청(실제 cancel).
- Recent Runs: 사용자별 최근 run 목록. Admin은 전체 run 토글 가능.
- Artifacts 패널: `pipeline.list_artifacts`로 목록 갱신, 경로 필터 제공.
- Artifact Preview: PDB/SDF는 3Dmol로 렌더링, 이미지 파일은 base64 미리보기, 텍스트 파일은 프리뷰, 바이너리는 미리보기 제한.
- Agent Panel: `pipeline.list_agent_events` 결과를 단계별로 표시, Good/Bad 평가 및 메모 기록 가능.
- Workflow Studio 실행 시 checkpoint review gate가 Monitor에 나타나며, 다음 단계로 continue 또는 특정 stage까지 rerun을 제어합니다.
- Report/Agent Report 모달: 언어 설정에 따라 한글/영문 리포트 표시.
- Report 모달: Markdown 렌더/원문 토글, 다운로드 제공.

**2.5 Analyze 탭**
- Compare Studio: 3D structure diff / sequence diff, tier-only Quick Compare(`WT vs RFD3`, `WT vs BioEmu`, `RFD3 vs BioEmu`), 접힌 기준선 strip, residue-linked view 제공.
- Comparison Summary: `Funnel`, `WT vs Design`, `RFD3 vs BioEmu`, `Tier Compare`, `Distribution`, `Sequence Diversity`를 카드 형태로 바로 표시.
- Run-to-Run Compare: 현재 run과 baseline run의 SoluProt/pLDDT/RMSD/pass-rate 차이를 비교.
- Hit List: weighted score 기반 최종 후보 랭킹, 가중치 조정, 상세 markdown/export 지원.
- Feedback: 등급(`good`/`bad`), 이유, 단계, 아티팩트, 코멘트 입력. 저장/조회/CSV·TSV 내보내기.
- Experiment: assay/result, sample id, artifact, metrics(JSON), conditions 입력. 저장/조회/CSV·TSV 내보내기.
- Report: `pipeline.generate_report`, `pipeline.get_report`, `pipeline.save_report` 연동. 리포트 텍스트와 연동된 아티팩트 링크 제공.
- Report Review: 리포트 품질 평가 및 이유 기록.

**2.6 Settings/Help/Admin**
- Settings: API base URL 확인, Report Language 설정(자동/영문/국문), Health Check(`GET /healthz`).
- Help: Setup/Monitor/Analyze 사용 가이드.
- Admin: 사용자 생성.

---

**3) UI ↔ API 호출 요약**

| UI 기능 | API 호출 | 비고 |
| --- | --- | --- |
| 로그인 | `POST /auth/login` | 토큰 발급 |
| 세션 확인 | `GET /auth/me` | 토큰 유효성 |
| 사용자 생성 | `POST /auth/create_user` | Admin 전용 |
| Preflight | `pipeline.preflight` | 입력/서비스 가용성 점검 |
| Prompt 기반 계획 | `pipeline.plan_from_prompt` | 질문/라우팅 생성 |
| 실행 | `pipeline.run` | 전체 파이프라인 |
| 단일 AF2 | `pipeline.af2_predict` | AF2만 실행 |
| DiffDock | `pipeline.diffdock` | docking 전용 |
| 상태 조회 | `pipeline.status` | stage/state/updated |
| 실행 취소 | `pipeline.cancel_run` | RunPod 작업 취소 요청 |
| 최근 run | `pipeline.list_runs` | 사용자 prefix 필터 |
| 아티팩트 목록 | `pipeline.list_artifacts` | 파일/디렉토리 |
| 아티팩트 읽기 | `pipeline.read_artifact` | 텍스트/base64 |
| 에이전트 이벤트 | `pipeline.list_agent_events` | 단계별 판단 |
| Run 비교 | `pipeline.compare_runs` | baseline run 대비 델타 |
| Hit List | `pipeline.get_hit_list` | weighted ranking |
| 결과 패키지 | `pipeline.export_results_package` | 보고서/표/JSON/PDB 묶음 |
| 리포트 | `pipeline.get_report`, `pipeline.generate_report`, `pipeline.save_report` | Markdown |
| 피드백 | `pipeline.submit_feedback`, `pipeline.list_feedback` | 품질 평가 |
| 실험 | `pipeline.submit_experiment`, `pipeline.list_experiments` | wet-lab 결과 |
| Run 삭제 | `pipeline.delete_run` | UI에서 삭제 가능 |

---

**4) 파이프라인 아키텍처 개요**

- 구성 요소: UI(웹) → MCP HTTP 서버(`pipeline-mcp`) → PipelineRunner → 외부 모델/서비스.
- 외부 서비스: MMseqs2 (RunPod), ProteinMPNN (RunPod), SoluProt, AlphaFold2 (RunPod 또는 URL), RFD3 (RunPod), DiffDock (RunPod).
- 실행 데이터 저장 위치: `outputs/<run_id>/`에 모든 결과/중간 산출물 저장.
- 상태 관리: `status.json`에 stage/state/updated 기록, `events.jsonl`에 단계 이벤트 축적, `agent_panel.jsonl`에 에이전트 패널 이벤트.

---

**5) 파이프라인 입력 파라미터 (요약)**

아래는 `PipelineRequest` 기준(전체 파이프라인 기준)입니다.

| 구분 | 파라미터 | 설명 |
| --- | --- | --- |
| 기본 입력 | `target_fasta`, `target_pdb` | FASTA/PDB 텍스트 |
| RFD3 | `rfd3_input_pdb`, `rfd3_contig`, `rfd3_inputs*`, `rfd3_cli_args`, `rfd3_partial_t`, `rfd3_max_return_designs` | 백본 생성 옵션 |
| BioEmu | `bioemu_use`, `bioemu_num_samples`, `bioemu_max_return_structures`, `bioemu_sequence` | BioEmu 샘플링 옵션 |
| DiffDock | `diffdock_ligand_smiles`, `diffdock_ligand_sdf`, `diffdock_config`, `diffdock_extra_args` | 도킹 옵션 |
| Design | `design_chains`, `fixed_positions_extra`, `conservation_tiers` | 설계 대상/고정 위치 |
| Mask Consensus | `mask_consensus_apply` | 전문가 패널 합의 마스킹 적용 여부 |
| Mask | `ligand_mask_distance`, `ligand_resnames`, `ligand_atom_chains` | 리간드 마스킹 |
| ProteinMPNN | `num_seq_per_tier`, `batch_size`, `sampling_temp`, `seed` | 샘플링 |
| SoluProt | `soluprot_cutoff` | 컷오프 |
| AF2 | `af2_model_preset`, `af2_db_preset`, `af2_max_template_date`, `af2_plddt_cutoff`, `af2_rmsd_cutoff`, `af2_top_k` | 예측/선정 기준 |
| MSA | `mmseqs_target_db`, `mmseqs_max_seqs`, `mmseqs_threads`, `mmseqs_use_gpu` | MSA 옵션 |
| Novelty | `novelty_target_db` | 서치 대상 DB |
| 제어 | `start_from`, `stop_after`, `force`, `dry_run`, `auto_recover`, `agent_panel_enabled`, `wt_compare` | 실행 제어/partial rerun/WT 비교 |

---

**6) 단계별 상세: 입력/출력/파일**

**6.0 Ingest & Preflight**
- 목적: 입력 정합성 확인, 서비스 준비 상태 점검.
- 입력: UI에서 첨부한 `target_fasta`/`target_pdb`/`rfd3_input_pdb`/`diffdock_ligand_*`, 프롬프트 기반 라우팅(`stop_after`, `conservation_tiers`, `num_seq_per_tier`).
- 출력: `outputs/<run_id>/request.json`, `outputs/<run_id>/status.json`, `outputs/<run_id>/events.jsonl`.

**6.1 MSA (MMseqs2)**
- 목적: 타깃 서열의 MSA 생성 및 품질 메타 기록.
- 입력: `target_fasta` 또는 `target_pdb`에서 추출한 서열, `mmseqs_target_db`, `mmseqs_max_seqs`, `mmseqs_threads`, `mmseqs_use_gpu`.
- 출력: `msa/result.a3m`, `msa/result.tsv`, `msa/quality.json`, `msa/runpod_job.json`.
- 옵션 출력: `msa/cluster.tsv`, `msa/sequence_weights.json` (가중치 기반 보정 사용 시).

**6.2 Conservation**
- 목적: 보존도 기반 고정 위치 계산.
- 입력: MSA(`result.a3m`), `conservation_tiers`, `conservation_mode`, `conservation_weighting`.
- 출력: `conservation.json` (tier별 고정 위치), 이후 tier별 `fixed_positions.json` 생성에 사용.

**6.3 RFD3 (옵션)**
- 목적: backbone 생성 또는 입력 PDB 기반 새로운 백본 생성.
- 입력: `rfd3_input_pdb`, `rfd3_contig`, `rfd3_inputs*`, `rfd3_cli_args`, `rfd3_partial_t`.
- 출력: `rfd3/inputs.json`, `rfd3/selected.pdb`, `rfd3/selected.json`, `rfd3/designs.json`, `rfd3/designs/*.pdb`, `rfd3/runpod_job.json`.

**6.4 AF2 Target (옵션)**
- 목적: target_pdb가 없을 때 target 구조 예측.
- 입력: `target_fasta`, AF2 설정.
- 출력: `target.pdb`, `af2_target_metrics.json`, `af2_target_ranking_debug.json`, `af2_target_runpod_job.json`.

**6.5 PDB Preprocess**
- 목적: PDB 정리(음수 residue 제거, renumber 등).
- 입력: `pdb_strip_nonpositive_resseq`, `pdb_renumber_resseq_from_1`.
- 출력: `target.original.pdb`, `target.pdb`, `pdb_numbering.json`, `backbones/<id>/target.original.pdb`, `backbones/<id>/target.pdb`.
- `backbones.json`에 각 backbone의 경로/메타 기록.

**6.6 Chain Strategy**
- 목적: 설계 대상 체인 자동 결정 및 기록.
- 입력: `design_chains`, `af2_model_preset`, PDB 체인 정보.
- 출력: `chain_strategy.json`.
- 단일체 preset에서 체인 다중일 경우 첫 체인만 사용.

**6.7 Query-PDB Check**
- 목적: FASTA와 PDB 체인 정합성 검사.
- 입력: `query_pdb_min_identity`, `query_pdb_policy`.
- 출력: `query_pdb_alignment.json`, `backbones/<id>/query_pdb_alignment.json`.

**6.8 DiffDock (옵션)**
- 목적: 리간드 좌표가 없는 경우 docking 결과 추가.
- 입력: `diffdock_ligand_smiles` 또는 `diffdock_ligand_sdf`.
- 출력: `diffdock/<id>/rank1.sdf`, `diffdock/<id>/ligand.pdb`, `diffdock/<id>/complex.pdb`, `diffdock/<id>/runpod_job.json`, `diffdock/<id>/output.json`.
- 최종 복합체는 ligand mask 계산에 사용.

**6.9 Ligand Mask**
- 목적: 리간드 근접 잔기 고정.
- 입력: `ligand_mask_distance`, `ligand_resnames`, `ligand_atom_chains`.
- 출력: `ligand_mask.json`, `backbones/<id>/ligand_mask.json`.

**6.10 Mask Consensus (tier별)**
- 목적: 전문가 패널 합의를 통해 고정 위치 추천(마스킹 개선).
- 입력: MSA 품질, 보존도(`conservation.json`), ligand mask(`ligand_mask.json`), query-PDB 정합성.
- 출력: `mask_consensus.json`, `tiers/<tier>/fixed_positions_consensus.json`.
- `mask_consensus_apply=true`인 경우 ProteinMPNN 고정 위치로 적용.

**6.11 ProteinMPNN (tier별)**
- 목적: 서열 디자인 생성.
- 입력: PDB, 고정 위치(`fixed_positions.json`), `num_seq_per_tier`, `sampling_temp`, `seed`.
- 출력: `tiers/<tier>/proteinmpnn.json`, `tiers/<tier>/designs.fasta`, `tiers/<tier>/fixed_positions.json`, `tiers/<tier>/fixed_positions_check.json`, `tiers/<tier>/mutation_report.json`, `tiers/<tier>/mutations_by_position.tsv`, `tiers/<tier>/mutations_by_position.svg`, `tiers/<tier>/mutations_by_sequence.tsv`, `tiers/<tier>/runpod_job.json`.
- RFD3 앙상블의 경우 `backbones/<id>/tiers/<tier>/`에 분산 저장.
- `tiers/<tier>/proteinmpnn_backbones.json`에 backbone별 메타 요약 저장.

**6.12 SoluProt (tier별)**
- 목적: 용해도 필터링.
- 입력: `soluprot_cutoff`.
- 출력: `tiers/<tier>/soluprot.json` (score/chain score/통과 id), `tiers/<tier>/designs_filtered.fasta`.

**6.13 AlphaFold2 (tier별)**
- 목적: 구조 예측 및 선정.
- 입력: `af2_model_preset`, `af2_db_preset`, `af2_max_template_date`, `af2_plddt_cutoff`, `af2_rmsd_cutoff`, `af2_top_k`.
- 출력: `tiers/<tier>/af2_scores.json` (scores, RMSD, selected_ids), `tiers/<tier>/af2_selected.fasta`, `tiers/<tier>/af2/runpod_jobs.json`, `tiers/<tier>/af2/<seq_id>/ranked_0.pdb`, `metrics.json`, `ranking_debug.json`.
- RMSD는 target PDB와 CA RMSD로 계산되며 `metrics.json`에 기록.

**6.14 Novelty Search (tier별)**
- 목적: AF2 통과 서열에 대한 novelty 확인.
- 입력: `novelty_target_db`, `mmseqs_max_seqs`.
- 출력: `tiers/<tier>/novelty.tsv`.

**6.15 Summary/Report**
- 목적: run 요약 및 점수/추천 제공.
- 출력: `summary.json` (Tier별 결과 요약), `comparisons.json` (Analyze 비교 요약), `report.md`, `report_ko.md`, `report_revisions.jsonl`.
- WT 비교(`wt_compare=true`): pLDDT/RMSD/SoluProt WT vs 디자인 비교 값 포함.
- Mask Consensus 상세: per-tier 합의 마스킹 상세 포함.

**6.16 Agent Panel**
- 목적: 단계별 신호 해석 및 합의.
- 출력: `agent_panel.jsonl`, `agent_panel_report.md`, `agent_panel_report_ko.md`, `agent_panel/<stage>.json`.

---

**7) 실행 결과 디렉토리 구조 예시**

`outputs/<run_id>/`에 저장되는 핵심 파일 구조:

- `request.json`, `status.json`, `events.jsonl`
- `target.fasta`, `target.pdb`, `target.original.pdb`
- `msa/` (result.a3m, result.tsv, quality.json)
- `conservation.json`
- `ligand_mask.json`
- `mask_consensus.json`
- `chain_strategy.json`
- `query_pdb_alignment.json`
- `tiers/<tier>/` (proteinmpnn, soluprot, af2, novelty)
- `tiers/<tier>/fixed_positions_consensus.json`
- `wt/` (soluprot.json, af2/metrics.json, metrics.json)
- `summary.json`, `comparisons.json`
- `report.md`, `report_ko.md`, `report_revisions.jsonl`
- `agent_panel.jsonl`, `agent_panel_report.md`, `agent_panel_report_ko.md`

---

**8) 상태/로그/복구 전략**

- 단계 상태는 `status.json`과 `events.jsonl`에 누적 기록.
- 실패 시 `auto_recover=true`이면 fallback 로직으로 부분 복구.
- 복구 이벤트는 `agent_panel` 및 `*_recovery.json` 기록.
- UI에서 `Monitor` 탭으로 단계별 진행 확인 가능.

---

**9) 추가 참고 파일**
- 실행 가이드: `docs/USAGE.md`
- 단계별 호출 예시: `docs/stepper_orchestration.md`
- RunPod 실행 관련: `docs/runpod_model_execution.md`
