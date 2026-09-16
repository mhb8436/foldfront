from __future__ import annotations

import sys
import time
import argparse
import calendar
import datetime
import json
import os
import re
import shutil
import signal
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# Ensure imports work from the checked-out project root. PROTEIN_PIPELINE_ROOT can
# override this for CI/CD deployments that promote the same tree across work,
# dev, staging, and production directories.
project_root = Path(
    os.environ.get("PROTEIN_PIPELINE_ROOT") or Path(__file__).resolve().parents[1]
).resolve()
sys.path.append(str(project_root / "pipeline-mcp/src"))

from dotenv import load_dotenv

load_dotenv(str(project_root / "pipeline-mcp/.env"), override=True)

build_runner = None
PipelineRequest = None
ncp_storage = None
RunPaths = None
mark_cancel_requested = None
set_status = None
ToolDispatcher = None

# ColabFold GPU worker가 run 내부에서도 병렬 호출될 수 있으므로 보수적으로 시작합니다.
MAX_CONCURRENT_PIPELINES = 2
MAX_RETRIES_PER_TARGET = 3
STALE_RUNNING_SECONDS = 30 * 60

GLOBAL_STOP_EVENT = False
GLOBAL_RUNNER = None
ACTIVE_RUN_IDS: set[str] = set()
ACTIVE_RUNS_LOCK = threading.Lock()


def _ensure_pipeline_imports() -> None:
    global build_runner
    global PipelineRequest
    global ncp_storage
    global RunPaths
    global mark_cancel_requested
    global set_status
    global ToolDispatcher

    if build_runner is not None:
        return

    from pipeline_mcp.app import build_runner as imported_build_runner
    from pipeline_mcp.models import PipelineRequest as imported_pipeline_request
    from pipeline_mcp.s3 import ncp_storage as imported_ncp_storage
    from pipeline_mcp.storage import RunPaths as imported_run_paths
    from pipeline_mcp.storage import mark_cancel_requested as imported_mark_cancel
    from pipeline_mcp.storage import set_status as imported_set_status
    from pipeline_mcp.tools import ToolDispatcher as imported_tool_dispatcher

    build_runner = imported_build_runner
    PipelineRequest = imported_pipeline_request
    ncp_storage = imported_ncp_storage
    RunPaths = imported_run_paths
    mark_cancel_requested = imported_mark_cancel
    set_status = imported_set_status
    ToolDispatcher = imported_tool_dispatcher


def _first_model_pdb_text(pdb_content: str) -> str:
    """Return a single-model PDB so NMR ensembles do not duplicate residues."""
    lines = pdb_content.splitlines()
    has_model = any(line.startswith("MODEL") for line in lines)
    if not has_model:
        return pdb_content

    out: list[str] = []
    in_first_model = False
    seen_first_model = False
    finished_first_model = False
    for raw in lines:
        if raw.startswith("MODEL"):
            if not seen_first_model:
                seen_first_model = True
                in_first_model = True
            else:
                finished_first_model = True
                in_first_model = False
            continue
        if raw.startswith("ENDMDL"):
            if in_first_model:
                finished_first_model = True
                in_first_model = False
            continue
        if finished_first_model:
            continue
        if in_first_model:
            out.append(raw)
            continue
        if not seen_first_model:
            out.append(raw)
    if not out or out[-1].strip() != "END":
        out.append("END")
    return "\n".join(out) + "\n"


def _cath_chain_from_target_id(target_id: str | None) -> str | None:
    clean = str(target_id or "").strip()
    if len(clean) < 5:
        return None
    chain = clean[4]
    return chain if chain.strip() else None


def _protein_chain_rank(seq: str) -> tuple[float, int, int]:
    canonical = set("ACDEFGHIKLMNPQRSTVWY")
    clean = "".join(ch for ch in str(seq or "").upper() if ch.isalpha())
    informative = sum(1 for ch in clean if ch in canonical)
    length = len(clean)
    fraction = float(informative) / float(length) if length else 0.0
    return fraction, informative, length


def _resolve_cath_design_chain(
    pdb_content: str,
    *,
    target_id: str | None,
) -> tuple[str | None, str | None]:
    from pipeline_mcp.bio.pdb import sequence_by_chain

    seq_by_chain = sequence_by_chain(pdb_content)
    if not seq_by_chain:
        return None, None

    requested = _cath_chain_from_target_id(target_id)
    if requested:
        if requested in seq_by_chain:
            return requested, seq_by_chain[requested]
        requested_lower = requested.lower()
        for chain_id, seq in seq_by_chain.items():
            if chain_id.lower() == requested_lower:
                return chain_id, seq

    ranked = sorted(
        seq_by_chain.items(),
        key=lambda item: _protein_chain_rank(item[1]),
        reverse=True,
    )
    chain_id, sequence = ranked[0]
    return chain_id, sequence


def _target_fasta_for_cath(
    *,
    target_id: str | None,
    chain_id: str | None,
    sequence: str | None,
) -> str:
    seq = "".join(ch for ch in str(sequence or "").upper() if ch.isalpha())
    if not seq:
        return ""
    label = str(target_id or "cath_target").strip() or "cath_target"
    if chain_id:
        label = f"{label}_{chain_id}"
    return f">{label}\n{seq}\n"


def build_cath_request(
    pdb_content: str,
    target_id: str | None = None,
    *,
    num_seq_per_tier: int = 40,
    af2_max_candidates_per_tier: int = 0,
    af2_top_k: int = 0,
) -> PipelineRequest:
    _ensure_pipeline_imports()
    normalized_pdb = _first_model_pdb_text(pdb_content)
    design_chain, target_sequence = _resolve_cath_design_chain(
        normalized_pdb,
        target_id=target_id,
    )
    design_chains = [design_chain] if design_chain else None
    return PipelineRequest(
        target_fasta=_target_fasta_for_cath(
            target_id=target_id,
            chain_id=design_chain,
            sequence=target_sequence,
        ),
        target_pdb=normalized_pdb,
        rfd3_use=False,
        mmseqs_target_db="uniref90",
        design_chains=design_chains,
        conservation_tiers=[0.3, 0.5, 0.7],
        ligand_mask_distance=6.0,
        ligand_mask_use_original_target=True,
        pdb_strip_nonpositive_resseq=True,
        pdb_renumber_resseq_from_1=True,
        bioemu_use=False,
        num_seq_per_tier=max(1, int(num_seq_per_tier)),
        sampling_temp=0.1,
        soluprot_cutoff=0.0,
        af2_provider="colabfold",
        af2_max_candidates_per_tier=max(0, int(af2_max_candidates_per_tier)),
        af2_top_k=max(0, int(af2_top_k)),
        relax_enabled=False,
        novelty_enabled=False,
        wt_compare=False,
        agent_panel_enabled=False,
        stop_after="af2",
        force=False,  # 이전에 완료된 단계는 건너뛰고 재개(Resume)
        auto_recover=True,  # 에러 났던 구간은 자동으로 재시도
    )


def _safe_lock_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(name)).strip("._")
    return safe or "cath_lock"


def _lock_root() -> Path:
    return project_root / "outputs" / "_cath_batch_locks"


def _lock_path(name: str) -> Path:
    return _lock_root() / f"{_safe_lock_name(name)}.lock"


def _utc_timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def acquire_cath_lock(name: str, payload: dict) -> Path | None:
    """Create a process-owned lock directory, or return None if it is active."""
    path = _lock_path(name)
    while True:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.mkdir()
            meta = {
                **payload,
                "pid": os.getpid(),
                "created_at": _utc_timestamp(),
            }
            (path / "lock.json").write_text(
                json.dumps(meta, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            return path
        except FileExistsError:
            meta = _load_json(path / "lock.json")
            pid = int(meta.get("pid") or 0)
            if _pid_alive(pid):
                return None
            shutil.rmtree(path, ignore_errors=True)


def release_cath_lock(lock_path: Path | None) -> None:
    if lock_path is None:
        return
    meta = _load_json(lock_path / "lock.json")
    pid = int(meta.get("pid") or 0)
    if pid and pid != os.getpid():
        return
    shutil.rmtree(lock_path, ignore_errors=True)


def _run_root(run_id: str) -> Path:
    return project_root / "outputs" / run_id


def _load_status(run_id: str) -> dict:
    return _load_json(_run_root(run_id) / "status.json")


def _status_age_seconds(status: dict) -> float | None:
    updated_at = str(status.get("updated_at") or "").strip()
    if not updated_at:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            ts = calendar.timegm(time.strptime(updated_at[:19], fmt))
            return max(0.0, time.time() - ts)
        except ValueError:
            continue
    return None


def _target_lock_is_live_elsewhere(run_id: str) -> bool:
    lock_path = _lock_path(f"target_{run_id}")
    meta = _load_json(lock_path / "lock.json")
    pid = int(meta.get("pid") or 0)
    return pid > 0 and pid != os.getpid() and _pid_alive(pid)


def cancel_interrupted_run(runner, run_id: str) -> bool:
    _ensure_pipeline_imports()
    try:
        ToolDispatcher(runner).call_tool("pipeline.cancel_run", {"run_id": run_id})
        return True
    except Exception as exc:
        print(f"[{run_id}] ⚠️ cancel tool failed; marking local run cancelled: {exc}")
        output_root = str(project_root / "outputs")
        root = _run_root(run_id)
        root.mkdir(parents=True, exist_ok=True)
        status = _load_status(run_id)
        stage = str(status.get("stage") or "cancel")
        mark_cancel_requested(output_root, run_id, reason="cath_batch_restart_cleanup")
        set_status(
            RunPaths(run_id=run_id, root=root),
            stage=stage,
            state="cancelled",
            detail="cath batch restart cleanup",
        )
        return True


def prepare_cath_run_for_start(
    runner,
    run_id: str,
    *,
    stale_after_seconds: int = STALE_RUNNING_SECONDS,
    cancel_func=cancel_interrupted_run,
) -> str:
    status = _load_status(run_id)
    state = str(status.get("state") or "").lower()
    if state != "running":
        return "ready"

    if _target_lock_is_live_elsewhere(run_id):
        return "skip_active_lock"

    age = _status_age_seconds(status)
    if age is not None and age < stale_after_seconds:
        return "skip_recent_running"

    if cancel_func(runner, run_id):
        return "cancelled_stale"
    return "skip_cancel_failed"


def _active_run_ids() -> list[str]:
    with ACTIVE_RUNS_LOCK:
        return sorted(ACTIVE_RUN_IDS)


def _register_active_run(run_id: str) -> None:
    with ACTIVE_RUNS_LOCK:
        ACTIVE_RUN_IDS.add(run_id)


def _unregister_active_run(run_id: str) -> None:
    with ACTIVE_RUNS_LOCK:
        ACTIVE_RUN_IDS.discard(run_id)


def cancel_active_runs_for_stop(reason: str) -> None:
    for run_id in _active_run_ids():
        print(f"[{run_id}] 🛑 {reason}; cancelling active target run.")
        if GLOBAL_RUNNER is not None:
            cancel_interrupted_run(GLOBAL_RUNNER, run_id)
        else:
            root = _run_root(run_id)
            root.mkdir(parents=True, exist_ok=True)
            status = _load_status(run_id)
            stage = str(status.get("stage") or "cancel")
            mark_cancel_requested(
                str(project_root / "outputs"),
                run_id,
                reason=reason,
            )
            set_status(
                RunPaths(run_id=run_id, root=root),
                stage=stage,
                state="cancelled",
                detail=reason,
            )


def request_stop(signum, _frame) -> None:
    global GLOBAL_STOP_EVENT
    GLOBAL_STOP_EVENT = True
    cancel_active_runs_for_stop(f"received signal {signum}")
    raise SystemExit(128 + int(signum))


def get_log_paths(subset: str):
    return {
        "success": project_root / f"batch_success_{subset}.csv",
        "failed": project_root / f"batch_failed_{subset}.csv",
    }


def record_success(subset: str, run_id: str):
    path = get_log_paths(subset)["success"]
    if not path.exists():
        path.write_text("timestamp,run_id\n", encoding="utf-8")
    with path.open("a", encoding="utf-8") as f:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"{now},{run_id}\n")


def record_failure(subset: str, run_id: str, error: str):
    path = get_log_paths(subset)["failed"]
    if not path.exists():
        path.write_text("timestamp,run_id,error\n", encoding="utf-8")
    with path.open("a", encoding="utf-8") as f:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        clean_err = str(error).replace("\n", " ").replace(",", ";")[:200]
        f.write(f"{now},{run_id},{clean_err}\n")


def is_already_done(subset: str, run_id: str) -> bool:
    # 1. 로컬에 report.md가 있으면 완료
    if (project_root / "outputs" / run_id / "report.md").exists():
        return True
    # 2. 성공 로그 파일에 기록되어 있으면 완료 (로컬 삭제된 경우 대응)
    path = get_log_paths(subset)["success"]
    if path.exists():
        if run_id in path.read_text(encoding="utf-8"):
            return True

    # 3. 실패 로그 파일에 기록되어 있으면 일단 스킵 (무한 재실행 방지)
    #    (나중에 진짜 재실행이 필요하면 batch_failed_XXX.csv 에서 해당 줄을 지우고 실행하면 됨)
    fail_path = get_log_paths(subset)["failed"]
    if fail_path.exists():
        # 로그 파일의 내용에 run_id가 들어있는지 검사
        if run_id in fail_path.read_text(encoding="utf-8"):
            return True

    return False


def process_target(
    pdb_path: Path,
    runner,
    subset_name: str,
    *,
    keep_local: bool,
    stop_on_error: bool = False,
    num_seq_per_tier: int = 40,
    af2_max_candidates_per_tier: int = 0,
    af2_top_k: int = 0,
) -> bool:
    global GLOBAL_STOP_EVENT
    if GLOBAL_STOP_EVENT:
        return False

    _ensure_pipeline_imports()
    run_id = f"cath_{subset_name}_{pdb_path.stem}"

    if is_already_done(subset_name, run_id):
        return True

    target_lock = acquire_cath_lock(
        f"target_{run_id}",
        {"kind": "cath_target", "subset": subset_name, "run_id": run_id},
    )
    if target_lock is None:
        print(f"[{run_id}] ⏭️ active lock detected; skipping duplicate target.")
        return False

    _register_active_run(run_id)
    try:
        start_decision = prepare_cath_run_for_start(runner, run_id)
        if start_decision.startswith("skip_"):
            print(f"[{run_id}] ⏭️ {start_decision}; not starting duplicate run.")
            return False
        if start_decision == "cancelled_stale":
            print(f"[{run_id}] ♻️ stale running status cancelled before restart.")

        print(f"[{run_id}] 🚀 고유 진화 공정 투입...")

        pdb_content = pdb_path.read_text(encoding="utf-8")

        final_success = False
        last_error = "Unknown"

        for attempt in range(MAX_RETRIES_PER_TARGET):
            if GLOBAL_STOP_EVENT:
                return False
            try:
                request = build_cath_request(
                    pdb_content,
                    target_id=pdb_path.stem,
                    num_seq_per_tier=num_seq_per_tier,
                    af2_max_candidates_per_tier=af2_max_candidates_per_tier,
                    af2_top_k=af2_top_k,
                )

                runner.run(request, run_id=run_id)

                final_success = True
                print(f"[{run_id}] ✅ 완료.")
                break

            except Exception as e:
                last_error = str(e)
                err_msg = last_error.lower()
                if attempt < MAX_RETRIES_PER_TARGET - 1 and any(
                    x in err_msg
                    for x in ["endpoint", "colabfold", "timeout", "failed to run"]
                ):
                    wait = 60 * (attempt + 1)
                    print(
                        f"[{run_id}] 🔄 서버 지연으로 재시도 ({attempt + 1}/{MAX_RETRIES_PER_TARGET}). {wait}s 대기..."
                    )
                    time.sleep(wait)
                    continue
                else:
                    break

        # 마무리 작업
        try:
            # 무조건 S3 동기화 (부분 결과라도 보존)
            ncp_storage.sync_outputs(run_id, local_root=str(project_root / "outputs"))

            if final_success:
                record_success(subset_name, run_id)
                if not keep_local:
                    # 성공 시에만 로컬 삭제하여 용량 확보
                    local_dir = project_root / "outputs" / run_id
                    if local_dir.exists():
                        shutil.rmtree(local_dir)
            else:
                record_failure(subset_name, run_id, last_error)
                if stop_on_error:
                    print(f"[{run_id}] 🛑 에러 발생으로 인한 전체 작업 중지 설정 활성화됨.")
                    GLOBAL_STOP_EVENT = True

        except Exception as final_e:
            print(f"[{run_id}] ⚠️ 최종 기록 단계 에러: {final_e}")
            if stop_on_error:
                GLOBAL_STOP_EVENT = True

        if GLOBAL_STOP_EVENT:
            raise RuntimeError(
                f"Pipeline stopped on error at target {run_id}: {last_error}"
            )

        return final_success
    finally:
        _unregister_active_run(run_id)
        release_cath_lock(target_lock)


def main():
    global GLOBAL_RUNNER
    parser = argparse.ArgumentParser(description="Smart 24/7 Metadata-managed Batch")
    parser.add_argument(
        "--subset", type=str, required=True, choices=["train", "val", "test"]
    )
    parser.add_argument("--max-workers", type=int, default=MAX_CONCURRENT_PIPELINES)
    parser.add_argument(
        "--limit", type=int, default=0, help="Run only the first N targets after offset."
    )
    parser.add_argument(
        "--offset", type=int, default=0, help="Skip this many targets before applying limit."
    )
    parser.add_argument("--num-seq-per-tier", type=int, default=40)
    parser.add_argument("--af2-max-candidates-per-tier", type=int, default=0)
    parser.add_argument("--af2-top-k", type=int, default=0)
    parser.add_argument("--keep-local", action="store_true")
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop processing if an error occurs",
    )
    args = parser.parse_args()

    targets_dir = project_root / f"cath_{args.subset}"
    pdb_files_all = sorted(list(targets_dir.glob("*.pdb")))
    offset = max(0, int(args.offset or 0))
    limit = max(0, int(args.limit or 0))
    pdb_files = pdb_files_all[offset:]
    if limit > 0:
        pdb_files = pdb_files[:limit]

    log_paths = get_log_paths(args.subset)
    max_workers = max(1, int(args.max_workers or MAX_CONCURRENT_PIPELINES))
    print(
        f"💎 배치 가동 (타겟: {len(pdb_files)}/{len(pdb_files_all)}, "
        f"offset={offset}, limit={limit or 'all'}, 동시성: {max_workers})"
    )
    print(f"📝 성공 로그: {log_paths['success'].name}")
    print(f"📝 실패 로그: {log_paths['failed'].name}")
    print(f"🧷 keep_local={'yes' if args.keep_local else 'no'}")
    print(f"🛑 stop_on_error={'yes' if args.stop_on_error else 'no'}")
    print(
        "⚙️ pilot knobs: "
        f"num_seq_per_tier={max(1, int(args.num_seq_per_tier or 40))}, "
        f"af2_max_candidates_per_tier={max(0, int(args.af2_max_candidates_per_tier or 0))}, "
        f"af2_top_k={max(0, int(args.af2_top_k or 0))}"
    )

    batch_lock = acquire_cath_lock(
        f"batch_{args.subset}",
        {
            "kind": "cath_batch",
            "subset": args.subset,
            "max_workers": max_workers,
        },
    )
    if batch_lock is None:
        print(f"⏭️ CATH subset '{args.subset}' is already running; skipping duplicate batch.")
        return 0

    try:
        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        _ensure_pipeline_imports()
        runner = build_runner()
        GLOBAL_RUNNER = runner
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            list(
                executor.map(
                    lambda p: process_target(
                        p,
                        runner,
                        args.subset,
                        keep_local=bool(args.keep_local),
                        stop_on_error=bool(args.stop_on_error),
                        num_seq_per_tier=max(1, int(args.num_seq_per_tier or 40)),
                        af2_max_candidates_per_tier=max(
                            0, int(args.af2_max_candidates_per_tier or 0)
                        ),
                        af2_top_k=max(0, int(args.af2_top_k or 0)),
                    ),
                    pdb_files,
                )
            )
    except Exception as e:
        print(f"\n❌ 작업 중단됨: {e}")
        return 1
    finally:
        release_cath_lock(batch_lock)
        GLOBAL_RUNNER = None

    print(f"\n✨ '{args.subset}' 작업 종료.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
