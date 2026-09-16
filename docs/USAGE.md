# Usage Guide

## 목적
- 현재 UI와 MCP 도구 기준으로 안전하게 실행, 재실행, 비교, 리포트 확인하는 방법을 정리합니다.
- 특히 `run_id` 재사용 규칙과 Analyze 탭의 최신 동선을 기준으로 설명합니다.

## 권장 사용 흐름

### 1. Setup에서 새 run 시작
- 기본값은 새 `run_id` 생성입니다.
- `pipeline` 또는 `workflow` 모드를 선택합니다.
- `target_input`에 FASTA/PDB를 넣으면 UI가 타입을 판별하고 필요한 필드를 채웁니다.
- 필요하면 `rfd3_input_pdb`, `rfd3_contig`, `bioemu_use`, ligand 입력, tier 설정을 추가합니다.
- 실행 전 `Check Setup`으로 `pipeline.preflight`를 돌리는 것이 안전합니다.

### 2. 기존 run 이어서 실행
- Setup의 run selector에서 기존 run을 선택하면 해당 run의 `request.json`을 폼으로 불러옵니다.
- 기본 동작은 여전히 새 run fork입니다.
- 같은 run을 이어서 쓰려면 다음 조건이 모두 필요합니다.
  - `pipeline` 또는 `workflow` 모드
  - `start_from > msa`
  - 기존 run 선택
  - `Continue same run` 활성화
- 이 경우 선택한 `start_from` 이후 산출물은 덮어써집니다.

### 3. Monitor에서 상태 확인
- `pipeline.status` 기반으로 현재 `stage`, `state`, `updated`를 확인합니다.
- 아티팩트 목록과 미리보기, agent panel, 리포트 액션을 사용합니다.
- Workflow Studio로 실행한 run은 checkpoint review gate가 Monitor에 나타납니다.

### 4. Analyze에서 비교와 선별
- Compare Studio:
  - 3D 구조 비교와 sequence diff 비교를 지원합니다.
  - Quick Compare는 `WT vs RFD3`, `WT vs BioEmu`, `RFD3 vs BioEmu` 3개 그룹만 노출합니다.
  - 각 그룹은 `Tier 0.30 / 0.50 / 0.70`만 선택합니다.
  - 기준선(`Input Structure`, `Working Backbone`, `WT ColabFold`)은 접힌 reference 영역에서만 확인합니다.
- Comparison Summary:
  - `Funnel`, `WT vs Design`, `RFD3 vs BioEmu`, `Tier Compare`, `Distribution`, `Sequence Diversity`를 카드로 바로 보여줍니다.
- Run-to-Run Compare:
  - 현재 run과 기준 run의 SoluProt, pLDDT, RMSD, pass-rate 변화를 비교합니다.
- Hit List:
  - tier/source 후보를 weighted score로 정렬합니다.
  - `soluprot`, `plddt`, `rmsd`, `novelty` 가중치를 조절할 수 있습니다.

## partial rerun 안전 규칙
- 같은 `run_id` 재사용은 “같은 request를 더 뒤 단계까지 이어서 실행”할 때만 권장됩니다.
- 다음이 바뀌면 새 `run_id`를 쓰거나 `start_from='msa'`로 다시 시작하세요.
  - `target_fasta`, `target_pdb`
  - `rfd3_input_pdb`, `rfd3_contig`, `bioemu_use` 같은 backbone 입력
  - `design_chains`, `fixed_positions_extra`
  - MSA/보존도/ProteinMPNN에 영향을 주는 upstream 파라미터
- 현재 backend는 request diff와 stage별 request hash를 확인해서 unsafe partial rerun을 거부합니다.

## MCP 도구 권장 흐름
1. `pipeline.plan_from_prompt`
   - 자연어를 파싱하고 누락 입력을 질문 형태로 반환합니다.
2. `pipeline.preflight`
   - 실행 전에 입력/서비스/환경 설정을 점검합니다.
3. `pipeline.run`
   - 최종 request로 실행합니다.

빠른 실행이 필요하면:
- `pipeline.run_from_prompt`
- `pipeline.af2_predict` / `pipeline.run_af2`
- `pipeline.diffdock` / `pipeline.run_diffdock`

분석/리포트:
- `pipeline.compare_runs`
- `pipeline.get_hit_list`
- `pipeline.export_results_package`
- `pipeline.generate_report`
- `pipeline.get_report`
- `pipeline.save_report`

운영/조회:
- `pipeline.status`
- `pipeline.list_runs`
- `pipeline.list_artifacts`
- `pipeline.read_artifact`
- `pipeline.list_agent_events`
- `pipeline.cancel_run`
- `pipeline.delete_run`

## 자연어 프롬프트 예시
- `"rfd3 diffusion design with bioemu and af2 filtering"`
- `"msa only for this fasta"`
- `"proteinmpnn design with fixed positions on chain A and wt compare"`

프롬프트 또는 명시 인자에서 자주 쓰는 키:
- `stop_after`
- `start_from`
- `design_chains`
- `conservation_tiers`
- `num_seq_per_tier`
- `fixed_positions_extra`
- `soluprot_cutoff`
- `af2_plddt_cutoff`
- `af2_rmsd_cutoff`
- `bioemu_use`

## 산출물 확인
- `pipeline.list_artifacts`로 경로 목록을 가져옵니다.
- `pipeline.read_artifact`로 텍스트/PDB/JSON/SVG 등을 읽습니다.
- 자주 보는 파일:
  - `summary.json`
  - `comparisons.json`
  - `report.md`, `report_ko.md`
  - `agent_panel_report.md`, `agent_panel_report_ko.md`
  - `tiers/<tier>/af2_scores.json`
  - `tiers/<tier>/designs.fasta`

## Compare Studio 해석 주의
- 구조 diff 색은 mutation이 아니라 CA 좌표 이동량 기준입니다.
- 같은 residue(`ASN->ASN`, `ASP->ASP`)라도 정렬 후 좌표가 다르면 노랑/빨강으로 표시될 수 있습니다.
- sequence diff와 structure diff는 다른 기준이므로 별도로 확인해야 합니다.
