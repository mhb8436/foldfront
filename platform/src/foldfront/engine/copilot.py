"""The design copilot.

A local model, answering about this installation's own runs. It is given the
facts - the project, its rounds, the recent runs, one run's stages and
metrics, the workflows - in the system prompt, and told to answer from those
and nothing else. It does not call tools and it does not start anything: a
7.8B model on a laptop is a good explainer of data it can see and a poor
agent over data it cannot, and the run button belongs to a person.

What it saw is returned with the answer, so the console can show the person
what the answer was grounded in - and so a wrong answer can be traced to a
missing fact rather than trusted.
"""

from __future__ import annotations

import re
from typing import Any, Awaitable, Callable

import httpx

from foldfront.core.config import get_settings
from foldfront.db.repositories import Repos

SYSTEM = """당신은 foldfront 의 설계 Copilot 입니다. foldfront 는 단백질 설계 모델을 워크플로로 엮어 실행하고,
용해도 예측 결과로 재설계를 반복하는 플랫폼입니다.

규칙:
1. 아래 「현황」에 적힌 사실만 근거로 답합니다. 현황에 없는 것은 "현황에 없어 알 수 없습니다"라고 말합니다.
   수치를 지어내지 않습니다.
2. 실행을 시작하거나, 바꾸거나, 취소하지 않습니다. 그런 요청에는 어느 화면(실행 준비 · 실행 감시 · 워크플로 스튜디오)에서
   무엇을 누르면 되는지 안내합니다.
3. 한국어, 존댓말, 간결하게. 표가 도움이 되면 씁니다. 근거가 된 실행·회차 식별자를 답에 적습니다.
4. 단백질 설계 일반 지식으로 해석을 덧붙일 때는 "일반적으로"라고 표시해 현황의 사실과 구분합니다.
5. 「없음」이라고 적힌 항목은 정말 없는 것입니다. 실행이 없으면 "이 프로젝트에는 아직 실행이 없습니다"라고
   말합니다. 워크플로 정의는 "할 수 있는 것"이지 "한 것"이 아닙니다 — 실행 이력처럼 서술하지 않습니다.

화면 지도 (무엇을 하려면 어디로 가는지):
- 실행 준비: 워크플로를 고르고 서열을 넣어 실행을 시작하는 곳. 회차를 골라 기록한다.
- 실행 감시: 실행의 진행·단계·산출물을 보고, 취소하거나 fork 하는 곳.
- 프로젝트: 프로젝트와 재설계 회차를 개설하는 곳. 회차는 여기서 사람이 열며, 실행이 자동으로 만들지 않는다.
- 워크플로 스튜디오: 워크플로(노드·간선)를 만들고 저장하는 곳. 실행은 여기서 하지 않는다.
- 결과 분석: 실행 둘을 비교하고 지표를 표로 보는 곳.
- 모델 관리: 모델 등록·승인. 운영: 큐·감사 기록·정합성 점검.
"""

#  Bounds, so a chatty installation does not blow past the context window.
RECENT_RUNS = 8
EVENT_TAIL = 12
MAX_CHARS = 9000


async def gather(repos: Repos, *, project_id: str | None, run_id: str | None) -> dict[str, Any]:
    """What the model is allowed to know for this turn."""
    ctx: dict[str, Any] = {"used": []}

    if project_id:
        project = next((p for p in await repos.projects.list(include_archived=True)
                        if p.project_id == project_id), None)
        if project:
            ctx["project"] = {"project_id": project.project_id, "name": project.name,
                              "description": project.description}
            rounds = await repos.rounds.list(project_id)
            ctx["rounds"] = [{"round_id": r.round_id, "index": r.index, "name": r.name,
                              "objective": r.objective, "runs": len(r.linked_run_ids)} for r in rounds]
            ctx["used"].append(f"프로젝트 {project.name} · 회차 {len(rounds)}개")

    runs = await repos.runs.list(project_id=project_id, limit=RECENT_RUNS)
    ctx["runs"] = [{
        "run_id": r.run_id, "status": str(r.status), "workflow_id": r.workflow_id,
        "round_id": r.round_id, "started_at": r.started_at.isoformat() if r.started_at else None,
        "stages": [f"{s.name}:{s.status}" for s in r.stages],
    } for r in runs]
    ctx["used"].append(f"최근 실행 {len(runs)}건")

    if run_id:
        run = await repos.runs.get(run_id)
        if run:
            events = await repos.events.list(run_id, limit=500)
            ctx["run"] = {
                "run_id": run.run_id, "status": str(run.status), "workflow_id": run.workflow_id,
                "round_id": run.round_id, "error": run.error,
                "stages": [{"name": s.name, "status": str(s.status), "error": s.error,
                            "metrics": _scalar(s.metrics)} for s in run.stages],
                "events": [f"{e.created_at.strftime('%H:%M:%S')} {e.message}" for e in events[-EVENT_TAIL:]],
            }
            ctx["used"].append(f"실행 {run_id} 의 단계·지표·사건 {min(len(events), EVENT_TAIL)}건")

    workflows = await repos.workflows.list(project_id=project_id)
    ctx["workflows"] = [{"workflow_id": w.workflow_id, "name": w.name, "version": w.version,
                         "nodes": [f"{n.node_id}({n.kind}{':' + n.model_id if n.model_id else ''})" for n in w.nodes]}
                        for w in workflows]
    ctx["used"].append(f"워크플로 {len(workflows)}종")
    return ctx


def _scalar(metrics: dict[str, Any]) -> dict[str, Any]:
    """Numbers and short strings only. A list of per-sequence results is not
    something to hand a language model as prose."""
    out: dict[str, Any] = {}
    for k, v in (metrics or {}).items():
        if k.startswith("_"):
            continue
        if isinstance(v, (int, float, bool)):
            out[k] = v
        elif isinstance(v, str) and len(v) <= 60:
            out[k] = v
        elif isinstance(v, list):
            out[k] = f"{len(v)}개"
    return out


def render(ctx: dict[str, Any]) -> str:
    """The context as text the model reads, bounded in size."""
    lines: list[str] = ["# 현황"]
    if "project" in ctx:
        p = ctx["project"]
        lines.append(f"## 프로젝트: {p['name']} ({p['project_id']})")
        if p.get("description"):
            lines.append(p["description"])
        for r in ctx.get("rounds", []):
            lines.append(f"- {r['index']}차 {r['name'] or ''} ({r['round_id']}): 목표 {r['objective'] or '-'} · 실행 {r['runs']}건")
        if not ctx.get("rounds"):
            lines.append("- 회차: 없음")
    lines.append("## 최근 실행")
    for r in ctx.get("runs", []):
        lines.append(f"- {r['run_id']} [{r['status']}] 워크플로 {r['workflow_id']} 회차 {r['round_id'] or '-'} 단계 {' '.join(r['stages'])}")
    if not ctx.get("runs"):
        #  An empty heading reads as an omission and gets filled in. Said outright.
        lines.append("- 없음 (이 범위에는 아직 실행이 없다)")
    if "run" in ctx:
        run = ctx["run"]
        lines.append(f"## 실행 상세: {run['run_id']} [{run['status']}]")
        if run.get("error"):
            lines.append(f"오류: {run['error']}")
        for s in run["stages"]:
            m = " ".join(f"{k}={v}" for k, v in s["metrics"].items())
            lines.append(f"- {s['name']} [{s['status']}]{' 오류: ' + s['error'] if s.get('error') else ''} {m}")
        lines.append("사건:")
        lines.extend(f"  {e}" for e in run["events"])
    lines.append("## 워크플로 (정의 — 실행할 수 있는 것이지 실행한 이력이 아님)")
    for w in ctx.get("workflows", []):
        lines.append(f"- {w['workflow_id']} v{w['version']} {w['name']}: {' → '.join(w['nodes'])}")
    text = "\n".join(lines)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n(현황이 길어 여기서 잘랐습니다)"
    return text


Completer = Callable[[list[dict[str, str]]], Awaitable[str]]


async def complete_openai(messages: list[dict[str, str]]) -> str:
    """One completion against the configured OpenAI-compatible endpoint."""
    s = get_settings()
    async with httpx.AsyncClient(timeout=s.local_llm_timeout_s) as client:
        r = await client.post(
            f"{s.local_llm_url.rstrip('/')}/chat/completions",
            json={"model": s.local_llm_model, "messages": messages, "temperature": 0.2},
        )
        r.raise_for_status()
        body = r.json()
    text = (body.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    #  Reasoning models think out loud between tags; the person gets the answer.
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


async def available() -> dict[str, Any]:
    """Whether the model answers, said plainly on the screen rather than found
    out on the first question."""
    s = get_settings()
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{s.local_llm_url.rstrip('/')}/models")
            ok = r.status_code == 200
            names = [m.get("id") for m in (r.json().get("data") or [])] if ok else []
    except Exception:  # noqa: BLE001 - down is an answer
        ok, names = False, []
    return {"available": ok and (not names or s.local_llm_model in names),
            "model": s.local_llm_model, "url": s.local_llm_url, "served": names}


async def chat(
    repos: Repos,
    messages: list[dict[str, str]],
    *,
    project_id: str | None = None,
    run_id: str | None = None,
    complete: Completer | None = None,
) -> dict[str, Any]:
    """One turn. The facts are gathered fresh every time: a run may have moved
    since the last question."""
    ctx = await gather(repos, project_id=project_id, run_id=run_id)
    prompt = [{"role": "system", "content": SYSTEM + "\n" + render(ctx)}]
    prompt += [{"role": m["role"], "content": m["content"]} for m in messages if m.get("role") in ("user", "assistant")]
    reply = await (complete or complete_openai)(prompt)
    return {"reply": reply, "context_used": ctx["used"], "model": get_settings().local_llm_model}
