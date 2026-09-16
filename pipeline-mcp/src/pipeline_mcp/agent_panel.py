from __future__ import annotations

from dataclasses import dataclass
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any
try:
    import mlflow
except ImportError:
    mlflow = None

from .storage import append_run_event
from .storage import resolve_run_path
from .storage import write_json


_SAFE_STAGE_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _safe_stage(stage: str) -> str:
    safe = _SAFE_STAGE_RE.sub("_", stage).strip("._-")
    return safe or "stage"


def _load_json(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _load_jsonl(path: Path, *, limit: int = 50) -> list[dict[str, object]]:
    if not path.exists():
        return []
    items: list[dict[str, object]] = []
    for raw in path.read_text(encoding="utf-8").splitlines()[-max(0, int(limit)) :]:
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        if isinstance(payload, dict):
            items.append(payload)
    return items


def _stage_parts(stage: str) -> tuple[str, str | None]:
    if "_" not in stage:
        return stage, None
    head, tail = stage.split("_", 1)
    if head in {"proteinmpnn", "soluprot", "af2", "novelty"}:
        return head, tail
    return stage, None


def _status_score(status: str) -> float:
    return {
        "ok": 1.0,
        "info": 0.6,
        "warn": 0.3,
        "error": 0.1,
    }.get(status, 0.5)


def _summarize_msa(run_root: Path) -> tuple[str, str, dict[str, object]]:
    quality = _load_json(run_root / "msa" / "quality.json")
    if not quality:
        return "info", "MSA quality data not available yet.", {}
    warnings = quality.get("warnings") if isinstance(quality.get("warnings"), list) else []
    usable_hits = quality.get("usable_hits")
    median_cov = None
    if isinstance(quality.get("coverage"), dict):
        median_cov = quality["coverage"].get("p50")
    summary = f"MSA usable_hits={usable_hits}, median_coverage={median_cov}"
    status = "warn" if warnings else "ok"
    if warnings:
        summary += f"; warnings={len(warnings)}"
    return status, summary, {"warnings": warnings, "usable_hits": usable_hits, "median_coverage": median_cov}


def _summarize_conservation(run_root: Path) -> tuple[str, str, dict[str, object]]:
    payload = _load_json(run_root / "conservation.json")
    if not payload:
        return "info", "Conservation data not available yet.", {}
    fixed = payload.get("fixed_positions_by_tier") or {}
    query_len = payload.get("query_length")
    totals: dict[str, int] = {}
    warn = False
    for tier, positions in fixed.items() if isinstance(fixed, dict) else []:
        count = len(positions) if isinstance(positions, list) else 0
        totals[str(tier)] = count
        if query_len and count >= 0.8 * float(query_len):
            warn = True
    if query_len and all(v == 0 for v in totals.values()):
        warn = True
    status = "warn" if warn else "ok"
    summary = f"Conservation fixed positions per tier: {totals}"
    return status, summary, {"fixed_positions": totals, "query_length": query_len}


def _summarize_mask_consensus(run_root: Path) -> tuple[str, str, dict[str, object]]:
    payload = _load_json(run_root / "mask_consensus.json")
    if not payload:
        return "info", "Mask consensus not available yet.", {}
    consensus = payload.get("consensus") if isinstance(payload, dict) else None
    fixed_by_tier = {}
    if isinstance(consensus, dict):
        fixed_by_tier = consensus.get("fixed_positions_by_tier") or {}
    totals: dict[str, int] = {}
    if isinstance(fixed_by_tier, dict):
        for tier_key, per_chain in fixed_by_tier.items():
            count = 0
            if isinstance(per_chain, dict):
                for positions in per_chain.values():
                    if isinstance(positions, list):
                        count += len(positions)
            totals[str(tier_key)] = count
    status = "ok" if totals else "info"
    summary = f"Mask consensus fixed positions per tier: {totals if totals else 'none'}"
    return status, summary, {"fixed_positions": fixed_by_tier, "counts": totals}


def _summarize_ligand_mask(run_root: Path) -> tuple[str, str, dict[str, object]]:
    payload = _load_json(run_root / "ligand_mask.json")
    if not payload:
        return "info", "Ligand mask not available yet.", {}
    total = 0
    if isinstance(payload, dict):
        for positions in payload.values():
            if isinstance(positions, list):
                total += len(positions)
    status = "warn" if total == 0 else "ok"
    summary = f"Ligand proximity masked residues: {total}"
    return status, summary, {"masked_total": total}


def _summarize_proteinmpnn(run_root: Path, tier: str | None) -> tuple[str, str, dict[str, object]]:
    if not tier:
        return "info", "ProteinMPNN tier not specified.", {}
    payload = _load_json(run_root / "tiers" / str(tier) / "proteinmpnn.json")
    if not payload:
        return "info", "ProteinMPNN output not available yet.", {}
    samples = payload.get("samples") if isinstance(payload, dict) else None
    count = len(samples) if isinstance(samples, list) else 0
    status = "warn" if count == 0 else "ok"
    summary = f"ProteinMPNN samples: {count}"
    return status, summary, {"samples": count}


def _summarize_soluprot(run_root: Path, tier: str | None) -> tuple[str, str, dict[str, object]]:
    if not tier:
        return "info", "SoluProt tier not specified.", {}
    payload = _load_json(run_root / "tiers" / str(tier) / "soluprot.json")
    if not payload:
        return "info", "SoluProt output not available yet.", {}
    scores = payload.get("scores") if isinstance(payload.get("scores"), dict) else {}
    passed_ids = payload.get("passed_ids") if isinstance(payload.get("passed_ids"), list) else []
    total = len(scores) if isinstance(scores, dict) else 0
    passed = len(passed_ids)
    frac = (passed / total) if total else 0.0
    status = "warn" if total and frac < 0.2 else "ok"
    summary = f"SoluProt passed {passed}/{total} ({frac:.1%})"
    return status, summary, {"passed": passed, "total": total, "fraction": frac}


def _summarize_af2(run_root: Path, tier: str | None) -> tuple[str, str, dict[str, object]]:
    if not tier:
        return "info", "ColabFold tier not specified.", {}
    payload = _load_json(run_root / "tiers" / str(tier) / "af2_scores.json")
    if not payload:
        return "info", "ColabFold scores not available yet.", {}
    scores = payload.get("scores") if isinstance(payload.get("scores"), dict) else {}
    selected = payload.get("selected_ids") if isinstance(payload.get("selected_ids"), list) else []
    selected_scores = [scores.get(seq_id) for seq_id in selected if isinstance(scores.get(seq_id), (int, float))]
    avg_plddt = (sum(selected_scores) / len(selected_scores)) if selected_scores else None
    status = "warn" if not selected else "ok"
    summary = f"ColabFold selected {len(selected)} designs"
    if avg_plddt is not None:
        summary += f", avg pLDDT={avg_plddt:.1f}"
    return status, summary, {"selected": len(selected), "avg_plddt": avg_plddt}


def _summarize_rfd3(run_root: Path) -> tuple[str, str, dict[str, object]]:
    exists = (run_root / "rfd3" / "selected.pdb").exists()
    status = "ok" if exists else "warn"
    summary = "RFD3 selected backbone available." if exists else "RFD3 backbone missing."
    return status, summary, {"selected_pdb": bool(exists)}


def _summarize_experiments(run_root: Path) -> tuple[str, str, dict[str, object]]:
    feedback = _load_jsonl(run_root / "feedback.jsonl", limit=50)
    experiments = _load_jsonl(run_root / "experiments.jsonl", limit=50)
    summary = f"Feedback={len(feedback)}, Experiments={len(experiments)}"
    return "info", summary, {"feedback": len(feedback), "experiments": len(experiments)}


def _interpret_msa(metrics: dict[str, object]) -> list[str]:
    out: list[str] = []
    usable = metrics.get("usable_hits")
    median_cov = metrics.get("median_coverage")
    warnings = metrics.get("warnings") if isinstance(metrics.get("warnings"), list) else []
    if usable is None:
        out.append("MSA quality not available yet.")
    else:
        try:
            usable_i = int(usable)
            if usable_i < 50:
                out.append("MSA depth is low; consider increasing mmseqs_max_seqs or changing target DB.")
            elif usable_i < 200:
                out.append("MSA depth is modest; results may be sensitive to thresholds.")
        except Exception:
            pass
    if isinstance(median_cov, (int, float)):
        if float(median_cov) < 0.3:
            out.append("Median MSA coverage is low; consider filtering or alternative inputs.")
    if warnings:
        out.append("MSA warnings present; review msa/quality.json before downstream steps.")
    return out


def _interpret_conservation(metrics: dict[str, object]) -> list[str]:
    out: list[str] = []
    fixed = metrics.get("fixed_positions") if isinstance(metrics.get("fixed_positions"), dict) else {}
    query_len = metrics.get("query_length")
    if query_len and isinstance(query_len, (int, float)):
        for tier, count in fixed.items():
            try:
                if float(count) >= 0.8 * float(query_len):
                    out.append(f"Tier {tier}: many positions fixed; design space is limited.")
                    break
            except Exception:
                continue
        if fixed and all(int(v) == 0 for v in fixed.values() if isinstance(v, (int, float, str))):
            out.append("No conserved positions detected; check MSA quality.")
    return out


def _interpret_ligand(metrics: dict[str, object]) -> list[str]:
    out: list[str] = []
    masked = metrics.get("masked_total")
    if isinstance(masked, (int, float)) and float(masked) <= 0:
        out.append("No ligand proximity residues; verify ligand_resnames or ligand coordinates in PDB.")
    return out


def _interpret_proteinmpnn(metrics: dict[str, object]) -> list[str]:
    out: list[str] = []
    samples = metrics.get("samples")
    if isinstance(samples, int) and samples <= 0:
        out.append("ProteinMPNN returned no sequences; check fixed_positions and input PDB.")
    return out


def _interpret_soluprot(metrics: dict[str, object]) -> list[str]:
    out: list[str] = []
    total = metrics.get("total")
    passed = metrics.get("passed")
    frac = metrics.get("fraction")
    if isinstance(total, int) and total > 0:
        if isinstance(passed, int) and passed == 0:
            out.append("No sequences passed SoluProt; consider lowering soluprot_cutoff.")
        elif isinstance(frac, (int, float)) and float(frac) < 0.2:
            out.append("Low solubility pass rate; consider lowering sampling_temp or relaxing constraints.")
    return out


def _interpret_af2(metrics: dict[str, object]) -> list[str]:
    out: list[str] = []
    selected = metrics.get("selected")
    avg_plddt = metrics.get("avg_plddt")
    if isinstance(selected, int) and selected == 0:
        out.append("No ColabFold-selected designs; consider lowering pLDDT/RMSD cutoffs or adjusting design.")
    if isinstance(avg_plddt, (int, float)) and float(avg_plddt) < 75.0:
        out.append("Average pLDDT is low; structures may be unreliable.")
    return out


def _interpret_rfd3(metrics: dict[str, object]) -> list[str]:
    out: list[str] = []
    if metrics.get("selected_pdb") is False:
        out.append("RFD3 selected backbone missing; check rfd3 inputs or endpoint.")
    return out


def _agent_structure(stage: str, run_root: Path, tier: str | None) -> dict[str, object]:
    base, _ = _stage_parts(stage)
    if stage == "rfd3":
        status, summary, metrics = _summarize_rfd3(run_root)
        interpretation = _interpret_rfd3(metrics)
    elif stage == "af2_target":
        exists = (run_root / "target.pdb").exists()
        status = "ok" if exists else "warn"
        summary = "Target structure available." if exists else "Target structure missing."
        metrics = {"target_pdb": bool(exists)}
        interpretation = [] if exists else ["Target structure missing; ensure target_pdb or ColabFold target prediction."]
    elif base == "af2":
        status, summary, metrics = _summarize_af2(run_root, tier)
        interpretation = _interpret_af2(metrics)
    else:
        status, summary, metrics = "info", "No structure-specific checks for this stage.", {}
        interpretation = []
    return {"name": "structure", "status": status, "summary": summary, "metrics": metrics, "interpretation": interpretation}


def _agent_protein(stage: str, run_root: Path, tier: str | None) -> dict[str, object]:
    if stage == "mmseqs_msa":
        status, summary, metrics = _summarize_msa(run_root)
        interpretation = _interpret_msa(metrics)
    elif stage == "conservation":
        status, summary, metrics = _summarize_conservation(run_root)
        interpretation = _interpret_conservation(metrics)
    elif stage == "mask_consensus":
        status, summary, metrics = _summarize_mask_consensus(run_root)
        interpretation = []
    else:
        base, _ = _stage_parts(stage)
        if base == "proteinmpnn":
            status, summary, metrics = _summarize_proteinmpnn(run_root, tier)
            interpretation = _interpret_proteinmpnn(metrics)
        elif base == "soluprot":
            status, summary, metrics = _summarize_soluprot(run_root, tier)
            interpretation = _interpret_soluprot(metrics)
        else:
            status, summary, metrics = "info", "No protein-specific checks for this stage.", {}
            interpretation = []
    return {"name": "protein", "status": status, "summary": summary, "metrics": metrics, "interpretation": interpretation}


def _agent_ligand(stage: str, run_root: Path, tier: str | None) -> dict[str, object]:
    if stage in {"ligand_mask", "diffdock"}:
        status, summary, metrics = _summarize_ligand_mask(run_root)
        interpretation = _interpret_ligand(metrics)
    else:
        status, summary, metrics = "info", "No ligand-specific checks for this stage.", {}
        interpretation = []
    return {"name": "ligand", "status": status, "summary": summary, "metrics": metrics, "interpretation": interpretation}


def _agent_experimental(stage: str, run_root: Path, tier: str | None) -> dict[str, object]:
    status, summary, metrics = _summarize_experiments(run_root)
    interpretation = []
    if isinstance(metrics.get("experiments"), int) and metrics.get("experiments") == 0:
        interpretation.append("No experimental results logged yet.")
    return {"name": "experimental", "status": status, "summary": summary, "metrics": metrics, "interpretation": interpretation}


def _build_consensus(
    agents: list[dict[str, object]],
    *,
    error: str | None = None,
    recovery: dict[str, object] | None = None,
) -> dict[str, object]:
    statuses = [str(a.get("status") or "info") for a in agents]
    has_error = error is not None or "error" in statuses
    has_warn = "warn" in statuses
    if has_error:
        decision = "recover"
    elif has_warn:
        decision = "monitor"
    else:
        decision = "proceed"
    scores = [_status_score(s) for s in statuses] or [0.5]
    confidence = sum(scores) / len(scores)
    rationale = "; ".join(
        f"{a.get('name')}: {a.get('summary')}"
        for a in agents
        if str(a.get("status") or "info") in {"warn", "error"}
    )
    actions: list[str] = []
    if has_warn:
        actions.append("Review warnings before downstream interpretation.")
    if recovery and isinstance(recovery.get("actions"), list):
        actions.extend([str(x) for x in recovery["actions"] if x])
    interpretations: list[str] = []
    for agent in agents:
        interp = agent.get("interpretation") if isinstance(agent, dict) else None
        if isinstance(interp, list):
            interpretations.extend([str(x) for x in interp if x])
    return {
        "decision": decision,
        "confidence": round(confidence, 3),
        "rationale": rationale or "No blocking issues detected.",
        "actions": actions,
        "interpretations": interpretations,
    }


def build_agent_panel_event(
    *,
    output_root: str,
    run_id: str,
    stage: str,
    detail: str | None = None,
    error: str | None = None,
    recovery: dict[str, object] | None = None,
) -> dict[str, object]:
    run_root = resolve_run_path(output_root, run_id)
    base, tier = _stage_parts(stage)
    agents = [
        _agent_structure(stage, run_root, tier),
        _agent_protein(stage, run_root, tier),
        _agent_ligand(stage, run_root, tier),
        _agent_experimental(stage, run_root, tier),
    ]
    consensus = _build_consensus(agents, error=error, recovery=recovery)
    return {
        "id": uuid.uuid4().hex,
        "kind": "agent_panel",
        "run_id": run_id,
        "stage": stage,
        "stage_base": base,
        "tier": tier,
        "detail": detail,
        "error": error,
        "recovery": recovery,
        "agents": agents,
        "consensus": consensus,
        "created_at": _now_iso(),
    }


def emit_agent_panel_event(
    *,
    output_root: str,
    run_id: str,
    stage: str,
    detail: str | None = None,
    error: str | None = None,
    recovery: dict[str, object] | None = None,
) -> dict[str, object]:
    event = build_agent_panel_event(
        output_root=output_root,
        run_id=run_id,
        stage=stage,
        detail=detail,
        error=error,
        recovery=recovery,
    )
    append_run_event(output_root, run_id, filename="agent_panel.jsonl", payload=event)
    consensus = event.get("consensus") if isinstance(event.get("consensus"), dict) else {}
    agents = event.get("agents") if isinstance(event.get("agents"), list) else []
    append_run_event(
        output_root,
        run_id,
        filename="orchestration_trace.jsonl",
        payload={
            "kind": "orchestration_trace",
            "event_type": "agent_verdict",
            "plane": "evidence",
            "source": "agent_panel",
            "run_id": run_id,
            "stage": stage,
            "detail": detail,
            "decision": consensus.get("decision"),
            "confidence": consensus.get("confidence"),
            "rationale": consensus.get("rationale"),
            "error": error,
            "recovery": recovery,
            "agent_count": len(agents),
            "created_at": event.get("created_at"),
        },
    )
    
    # Log to MLflow
    if mlflow is not None:
        try:
            mlflow.set_tracking_uri("http://127.0.0.1:18050")
            mlflow.set_experiment("Agent_Operations_Trace")
            with mlflow.start_run(run_name=f"Agent_{run_id}_{stage}", nested=True):
                mlflow.log_param("run_id", run_id)
                mlflow.log_param("stage", stage)
                consensus = event.get("consensus", {})
                if isinstance(consensus, dict):
                    mlflow.log_param("decision", consensus.get("decision"))
                    mlflow.log_param("confidence", consensus.get("confidence"))
                    mlflow.log_param("rationale", consensus.get("rationale")[:250] if consensus.get("rationale") else "")
                if error:
                    mlflow.log_param("error", error[:250])
        except Exception as mle:
            pass # Non-critical

    run_root = resolve_run_path(output_root, run_id)
    safe_stage = _safe_stage(stage)
    write_json(run_root / "agent_panel" / f"{safe_stage}.json", event)
    try:
        write_agent_panel_report(output_root, run_id)
    except Exception:
        pass
    return event


def _derive_agent_interpretations(stage: str, agent: dict[str, object]) -> list[str]:
    interp = agent.get("interpretation") if isinstance(agent.get("interpretation"), list) else None
    if interp:
        return [str(x) for x in interp if x]
    metrics = agent.get("metrics") if isinstance(agent.get("metrics"), dict) else {}
    name = str(agent.get("name") or "")
    base, _ = _stage_parts(stage)
    if name == "structure":
        if stage == "rfd3":
            return _interpret_rfd3(metrics)
        if stage == "af2_target":
            if metrics.get("target_pdb") is False:
                return ["Target structure missing; ensure target_pdb or ColabFold target prediction."]
            return []
        if base == "af2":
            return _interpret_af2(metrics)
        return []
    if name == "protein":
        if stage == "mmseqs_msa":
            return _interpret_msa(metrics)
        if stage == "conservation":
            return _interpret_conservation(metrics)
        if base == "proteinmpnn":
            return _interpret_proteinmpnn(metrics)
        if base == "soluprot":
            return _interpret_soluprot(metrics)
        return []
    if name == "ligand":
        if stage in {"ligand_mask", "diffdock"}:
            return _interpret_ligand(metrics)
        return []
    if name == "experimental":
        if isinstance(metrics.get("experiments"), int) and metrics.get("experiments") == 0:
            return ["No experimental results logged yet."]
        return []
    return []


def _derive_consensus_interpretations(stage: str, agents: list[dict[str, object]], consensus: dict[str, object]) -> list[str]:
    interp = consensus.get("interpretations") if isinstance(consensus.get("interpretations"), list) else None
    if interp:
        return [str(x) for x in interp if x]
    out: list[str] = []
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        out.extend(_derive_agent_interpretations(stage, agent))
    return out


def build_agent_panel_report(events: list[dict[str, object]], *, run_id: str) -> str:
    lines: list[str] = []
    lines.append(f"# Agent Panel Report: {run_id}")
    lines.append("")
    if not events:
        lines.append("No agent panel events recorded.")
        return "\n".join(lines).strip() + "\n"

    lines.append("## Timeline")
    for item in events:
        stage = str(item.get("stage") or "-")
        created_at = str(item.get("created_at") or "-")
        consensus = item.get("consensus") if isinstance(item.get("consensus"), dict) else {}
        agents = item.get("agents") if isinstance(item.get("agents"), list) else []
        decision = str(consensus.get("decision") or "-")
        confidence = consensus.get("confidence")
        rationale = str(consensus.get("rationale") or "")
        error = item.get("error")
        line = f"- {created_at} · {stage} · decision={decision}"
        if isinstance(confidence, (int, float)):
            line += f" (confidence={confidence:.2f})"
        if error:
            line += f" · error={error}"
        lines.append(line)
        if rationale:
            lines.append(f"  - rationale: {rationale}")
        actions = consensus.get("actions") if isinstance(consensus, dict) else None
        if isinstance(actions, list) and actions:
            lines.append("  - actions: " + "; ".join(str(a) for a in actions))
        interpretations = _derive_consensus_interpretations(stage, agents, consensus)
        if interpretations:
            lines.append("  - interpretation: " + "; ".join(str(a) for a in interpretations))

    lines.append("")
    lines.append("## Latest Signals")
    latest_by_stage: dict[str, dict[str, object]] = {}
    for item in events:
        stage = str(item.get("stage") or "")
        if stage:
            latest_by_stage[stage] = item
    for stage, item in latest_by_stage.items():
        lines.append(f"- {stage}")
        agents = item.get("agents") if isinstance(item.get("agents"), list) else []
        for agent in agents:
            if not isinstance(agent, dict):
                continue
            name = agent.get("name") or "agent"
            status = agent.get("status") or "info"
            summary = agent.get("summary") or ""
            lines.append(f"  - {name} [{status}]: {summary}")
            interp = _derive_agent_interpretations(stage, agent)
            if interp:
                lines.append("    - interpretation: " + "; ".join(str(x) for x in interp))
    return "\n".join(lines).strip() + "\n"


def build_agent_panel_report_ko(events: list[dict[str, object]], *, run_id: str) -> str:
    lines: list[str] = []
    lines.append(f"# 에이전트 패널 리포트: {run_id}")
    lines.append("")
    if not events:
        lines.append("에이전트 패널 이벤트가 아직 없습니다.")
        return "\n".join(lines).strip() + "\n"

    lines.append("## 타임라인")
    for item in events:
        stage = str(item.get("stage") or "-")
        created_at = str(item.get("created_at") or "-")
        consensus = item.get("consensus") if isinstance(item.get("consensus"), dict) else {}
        agents = item.get("agents") if isinstance(item.get("agents"), list) else []
        decision = str(consensus.get("decision") or "-")
        confidence = consensus.get("confidence")
        rationale = str(consensus.get("rationale") or "")
        error = item.get("error")
        line = f"- {created_at} · {stage} · 결정={decision}"
        if isinstance(confidence, (int, float)):
            line += f" (신뢰도={confidence:.2f})"
        if error:
            line += f" · 오류={error}"
        lines.append(line)
        if rationale:
            lines.append(f"  - 근거: {rationale}")
        actions = consensus.get("actions") if isinstance(consensus, dict) else None
        if isinstance(actions, list) and actions:
            lines.append("  - 조치: " + "; ".join(str(a) for a in actions))
        interpretations = _derive_consensus_interpretations(stage, agents, consensus)
        if interpretations:
            lines.append("  - 해석: " + "; ".join(str(a) for a in interpretations))

    lines.append("")
    lines.append("## 최신 신호")
    latest_by_stage: dict[str, dict[str, object]] = {}
    for item in events:
        stage = str(item.get("stage") or "")
        if stage:
            latest_by_stage[stage] = item
    for stage, item in latest_by_stage.items():
        lines.append(f"- {stage}")
        agents = item.get("agents") if isinstance(item.get("agents"), list) else []
        for agent in agents:
            if not isinstance(agent, dict):
                continue
            name = agent.get("name") or "agent"
            status = agent.get("status") or "info"
            summary = agent.get("summary") or ""
            lines.append(f"  - {name} [{status}]: {summary}")
            interp = _derive_agent_interpretations(stage, agent)
            if interp:
                lines.append("    - 해석: " + "; ".join(str(x) for x in interp))
    return "\n".join(lines).strip() + "\n"


def write_agent_panel_report(output_root: str, run_id: str, *, limit: int = 200) -> str:
    run_root = resolve_run_path(output_root, run_id)
    events = _load_jsonl(run_root / "agent_panel.jsonl", limit=limit)
    report = build_agent_panel_report(events, run_id=run_id)
    (run_root / "agent_panel_report.md").write_text(report, encoding="utf-8")
    report_ko = build_agent_panel_report_ko(events, run_id=run_id)
    (run_root / "agent_panel_report_ko.md").write_text(report_ko, encoding="utf-8")
    return report
