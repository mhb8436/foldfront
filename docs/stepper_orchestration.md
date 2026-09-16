# protein-pipeline-stepper 오케스트레이션 (실행 명령어 / 확인 방법)

`protein-pipeline-stepper`는 “쉘 스크립트”가 아니라, MCP 도구(`pipeline.run`/`pipeline.status`)를 **단계별로 호출**하도록 안내하는 Codex 스킬입니다.

이 문서는 stepper가 수행하는 오케스트레이션을 사람이 터미널에서 그대로 재현할 수 있도록 **동등한 HTTP 호출(curl) 형태의 “명령어”**와, 실행/디버깅 시 어디를 확인해야 하는지 정리합니다.

참고: 이 저장소에 포함된 stepper 스킬 원문은 `tmp/skills-dist/protein-pipeline-stepper.SKILL.md` 입니다.

참고: 운영 환경의 MCP 클라이언트 endpoint는 `https://pipeline.k-biofoundrycopilot.duckdns.org/mcp` 입니다. 이 문서의 `curl` 예시는 stepper가 실제로 위임하는 내부 HTTP tool API(`/tools/call`)를 디버깅/재현하는 목적입니다.

---

## 1) Stepper가 하는 호출 순서(요약)

전체 파이프라인을 “msa → design → soluprot → af2 → novelty”로 나눠 실행할 때 stepper는 아래 규칙을 따릅니다.

1. `pipeline.status(run_id)`로 현재 상태 확인
2. 이미 `state=running`이면 `pipeline.run`을 다시 호출하지 않고 `pipeline.status`만 폴링
3. `state!=running`이면 원하는 단계까지 `pipeline.run(stop_after=...)` 호출
4. 같은 `run_id` 재사용은 **같은 request를 단계적으로 확장할 때만** 수행

즉, stepper가 기대하는 `run_id` 재사용은 아래처럼 request 본문이 사실상 동일할 때입니다.
- 동일한 target FASTA/PDB
- 동일한 backbone 입력과 설계 대상 체인
- orchestration 목적의 변경만 있음 (`stop_after`, 필요 시 `start_from`)

target/backbone/chain/fixed-position 같은 upstream 입력이 바뀌면 새 `run_id`를 쓰거나 `start_from="msa"`로 다시 시작해야 합니다. 현재 backend는 unsafe partial rerun을 거부합니다.

---

## 2) stepper 오케스트레이션을 curl로 재현하기

### 공통 준비

```bash
# 로컬이면 127.0.0.1, 원격이면 서버 IP/도메인 사용
SERVER=http://<SERVER_IP>:18080
RUN_ID=your_run_id_here
```

`target_fasta`/`target_pdb`는 “파일 경로”가 아니라 “파일 내용(text)”을 보내야 합니다. (`jq --rawfile` 사용)

### (A) 상태 확인 (pipeline.status)

```bash
curl -sS -X POST "$SERVER/tools/call" -H 'Content-Type: application/json' \
  -d "$(jq -n --arg run_id "$RUN_ID" '{name:"pipeline.status", arguments:{run_id:$run_id}}')"
```

### (B) 1단계: MSA까지만 (stop_after="msa")

```bash
jq -n --arg run_id "$RUN_ID" --rawfile fasta ./target.fasta \
  '{name:"pipeline.run", arguments:{run_id:$run_id, target_fasta:$fasta, stop_after:"msa", mmseqs_target_db:"uniref90", mmseqs_max_seqs:3000}}' \
| curl -sS -X POST "$SERVER/tools/call" -H 'Content-Type: application/json' -d @-
```

### (C) 2단계: Design까지 (stop_after="design")

가능하면 `target_pdb`를 함께 주는 것을 권장합니다(리간드 마스킹/체인 지정이 명확).

```bash
jq -n --arg run_id "$RUN_ID" --rawfile fasta ./target.fasta --rawfile pdb ./target.pdb \
  '{name:"pipeline.run", arguments:{run_id:$run_id, target_fasta:$fasta, target_pdb:$pdb, stop_after:"design", conservation_tiers:[0.3,0.5,0.7], num_seq_per_tier:16}}' \
| curl -sS -X POST "$SERVER/tools/call" -H 'Content-Type: application/json' -d @-
```

### (D) 3단계: SoluProt까지 (stop_after="soluprot")

```bash
jq -n --arg run_id "$RUN_ID" --rawfile fasta ./target.fasta --rawfile pdb ./target.pdb \
  '{name:"pipeline.run", arguments:{run_id:$run_id, target_fasta:$fasta, target_pdb:$pdb, stop_after:"soluprot", soluprot_cutoff:0.5}}' \
| curl -sS -X POST "$SERVER/tools/call" -H 'Content-Type: application/json' -d @-
```

### (E) 4단계: AF2까지 (stop_after="af2")

```bash
jq -n --arg run_id "$RUN_ID" --rawfile fasta ./target.fasta --rawfile pdb ./target.pdb \
  '{name:"pipeline.run", arguments:{run_id:$run_id, target_fasta:$fasta, target_pdb:$pdb, stop_after:"af2", af2_plddt_cutoff:85, af2_rmsd_cutoff:2.0, af2_top_k:20}}' \
| curl -sS -X POST "$SERVER/tools/call" -H 'Content-Type: application/json' -d @-
```

### (F) 5단계: Novelty까지 (stop_after="novelty")

```bash
jq -n --arg run_id "$RUN_ID" --rawfile fasta ./target.fasta --rawfile pdb ./target.pdb \
  '{name:"pipeline.run", arguments:{run_id:$run_id, target_fasta:$fasta, target_pdb:$pdb, stop_after:"novelty", novelty_target_db:"uniref90"}}' \
| curl -sS -X POST "$SERVER/tools/call" -H 'Content-Type: application/json' -d @-
```

### (G) “중복 실행 방지” 폴링 루프(예시)

stepper의 핵심 규칙(이미 running이면 run을 다시 부르지 않기)을 bash로 재현하면:

```bash
while true; do
  OUT="$(curl -sS -X POST "$SERVER/tools/call" -H 'Content-Type: application/json' \
    -d "$(jq -n --arg run_id "$RUN_ID" '{name:"pipeline.status", arguments:{run_id:$run_id}}')")"
  echo "$OUT" | jq .
  STATE="$(echo "$OUT" | jq -r '.result.status.state // empty')"
  [ "$STATE" != "running" ] && break
  sleep 60
done
```

### (H) 같은 request로 late stage만 다시 돌리는 예시

아래는 같은 run의 upstream 산출물을 유지한 채 AF2 이후만 다시 돌리는 경우입니다.

```bash
jq -n --arg run_id "$RUN_ID" --rawfile fasta ./target.fasta --rawfile pdb ./target.pdb \
  '{name:"pipeline.run", arguments:{run_id:$run_id, target_fasta:$fasta, target_pdb:$pdb, start_from:"af2", stop_after:"novelty"}}' \
| curl -sS -X POST "$SERVER/tools/call" -H 'Content-Type: application/json' -d @-
```

이 호출은 같은 request를 유지할 때만 사용하세요. upstream 입력이 바뀌었다면 새 `run_id`가 맞습니다.

---

## 3) “어디서 실행되는지” 코드로 확인하기

### HTTP 서버 경로
- 엔트리포인트: `pipeline-mcp/src/pipeline_mcp/http_server.py`
- 툴 디스패치: `pipeline-mcp/src/pipeline_mcp/tools.py` (`ToolDispatcher.call_tool`)
- 실제 오케스트레이션 로직: `pipeline-mcp/src/pipeline_mcp/pipeline.py` (`PipelineRunner.run`)

### MCP(stdio) 경로 (Codex/Copilot 연동)
- stdio 서버: `pipeline-mcp/src/pipeline_mcp/mcp_stdio_server.py`
- (원격 HTTP 서버에 붙는) MCP 프록시: `pipeline-mcp/scripts/mcp_http_proxy_server.py`

### 외부 연산(실제 “작업 실행”)이 일어나는 곳
`pipeline-mcp`는 로컬에서 바이너리를 실행하기보다, 대부분 아래 HTTP 호출로 일을 시킵니다.
- RunPod 호출: `pipeline-mcp/src/pipeline_mcp/clients/runpod.py` (`https://api.runpod.ai/v2/<endpoint>/run`)
- MMseqs/ProteinMPNN/AF2 클라이언트: `pipeline-mcp/src/pipeline_mcp/clients/*.py`

---

## 4) “지금 잘 실행되는지” 확인 포인트(아티팩트/로그)

### 서버 레벨
- `GET $SERVER/healthz`
- `POST $SERVER/tools/list`

### run_id 레벨 (가장 중요)
`PIPELINE_OUTPUT_ROOT/<run_id>/` 아래를 봅니다.
- 진행상태: `status.json`, 타임라인: `events.jsonl`
- 체인 전략 기록: `chain_strategy.json`
- 비교/요약: `summary.json`, `comparisons.json`
- MSA: `msa/result.a3m`, `msa/quality.json`, `msa/runpod_job.json`
- 고정/리간드/정렬: `conservation.json`, `ligand_mask.json`, `query_pdb_alignment.json`
- ProteinMPNN: `tiers/<tier>/proteinmpnn.json`, `tiers/<tier>/designs.fasta`, `tiers/<tier>/runpod_job.json`
- SoluProt: `tiers/<tier>/soluprot.json`, `tiers/<tier>/designs_filtered.fasta`
- AF2: `tiers/<tier>/af2_scores.json`, `tiers/<tier>/af2_selected.fasta`, `tiers/<tier>/af2/<seq_id>/*`
- 요약: `summary.json` (오류 시 `errors` 필드)

### 로그 파일(운영)
권장 nohup 실행을 따랐다면:
- `logs/pipeline-mcp_18080.log` (HTTP 서버 로그)
- `logs/soluprot_18081.log` (SoluProt 서버 로그, 운영 시)

---

## 5) 빠른 스모크 테스트(외부 의존성 없이)

외부 RunPod/AF2/SoluProt 설정이 없어도 파이프라인 “흐름”만 확인하려면 `dry_run=true`를 사용합니다.

```bash
jq -n --arg run_id "$RUN_ID" --rawfile fasta ./target.fasta \
  '{name:"pipeline.run", arguments:{run_id:$run_id, target_fasta:$fasta, dry_run:true, stop_after:"af2", num_seq_per_tier:2, conservation_tiers:[0.3]}}' \
| curl -sS -X POST "$SERVER/tools/call" -H 'Content-Type: application/json' -d @-
```

---

## 6) RunPod 내부 실행 커맨드(로컬 재현용)
- `docs/runpod_model_execution.md` 참고
