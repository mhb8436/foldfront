import json
import uuid
from pathlib import Path

from pipeline_mcp.agent_panel import emit_agent_panel_event
from pipeline_mcp.storage import init_run
from pipeline_mcp.storage import set_status


def _tmp_output_root() -> Path:
    base = Path(__file__).resolve().parent / "_tmp"
    base.mkdir(parents=True, exist_ok=True)
    root = base / f"trace_{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_set_status_writes_control_plane_orchestration_trace() -> None:
    output_root = _tmp_output_root()
    paths = init_run(str(output_root), "trace_run")

    set_status(paths, stage="mmseqs_msa", state="running", detail="search started")

    trace_path = paths.root / "orchestration_trace.jsonl"
    assert trace_path.exists()
    events = _read_jsonl(trace_path)
    assert events[-1]["kind"] == "orchestration_trace"
    assert events[-1]["event_type"] == "stage_status"
    assert events[-1]["plane"] == "control"
    assert events[-1]["run_id"] == "trace_run"
    assert events[-1]["stage"] == "mmseqs_msa"
    assert events[-1]["state"] == "running"
    assert events[-1]["detail"] == "search started"


def test_agent_panel_writes_evidence_agent_verdict_to_orchestration_trace() -> None:
    output_root = _tmp_output_root()
    init_run(str(output_root), "trace_run")

    event = emit_agent_panel_event(
        output_root=str(output_root),
        run_id="trace_run",
        stage="bioemu",
        detail="target RMSD gate",
        error="bioemu failed: missing RMSD values",
        recovery={"attempted": True, "actions": ["normalize chain mapping"]},
    )

    trace_path = output_root / "trace_run" / "orchestration_trace.jsonl"
    events = _read_jsonl(trace_path)
    verdicts = [item for item in events if item.get("event_type") == "agent_verdict"]
    assert verdicts
    verdict = verdicts[-1]
    assert verdict["kind"] == "orchestration_trace"
    assert verdict["plane"] == "evidence"
    assert verdict["source"] == "agent_panel"
    assert verdict["stage"] == "bioemu"
    assert verdict["decision"] == event["consensus"]["decision"]
    assert verdict["confidence"] == event["consensus"]["confidence"]
    assert verdict["error"] == "bioemu failed: missing RMSD values"
    assert verdict["recovery"] == {"attempted": True, "actions": ["normalize chain mapping"]}

