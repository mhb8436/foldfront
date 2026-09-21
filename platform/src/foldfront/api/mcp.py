"""One MCP tool surface.

The original serves 62 tools over stdio JSON-RPC. This platform added 25 HTTP
paths of its own. To anything outside, that was two surfaces that did not know
about each other.

    POST /mcp   JSON-RPC 2.0: initialize, tools/list, tools/call

tools/list answers with the original 62 alongside the ones added here.
tools/call splits on the name: platform.* is handled locally, everything else
goes to the original dispatcher.

That dispatcher needs a PipelineRunner, and a PipelineRunner needs a storage
root. It was built without one, which raised TypeError before any tool ran:
the reason 62 of the 70 listed tools returned an error instead of a result.

It gets the root now, and the run the call names is projected out of MongoDB
into it first (engine/legacy_view.py), so a tool written against the
filesystem can read a run this platform started. Role and run-scope checks
happen here, because the original tools have no idea who is calling.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends

from foldfront.core.auth import CurrentIdentity, may_see_run, require
from foldfront.core.config import get_settings
from foldfront.db.models import Role, Workflow
from foldfront.db.repositories import Repos
from foldfront.engine.legacy_view import LegacyProjector
from foldfront.engine.dag import build_graph
from foldfront.engine.router import ModelRouter
from foldfront.engine.service import ExecutionService

log = logging.getLogger(__name__)

router = APIRouter(tags=["MCP"])

PROTOCOL_VERSION = "2024-11-05"


def _text(payload: Any) -> dict[str, Any]:
    """MCP expects a content array, shaped as the original _result_text does."""
    import json

    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, default=str)}]}


# ---------------------------------------------------------------- tools added here

def _obj(**props: Any) -> dict[str, Any]:
    return {"type": "object", "properties": props}


PLATFORM_TOOLS: list[dict[str, Any]] = [
    {
        "name": "platform.workflow_list",
        "description": "저장된 DAG 워크플로 목록을 낸다.",
        "inputSchema": _obj(templates_only={"type": "boolean"}),
    },
    {
        "name": "platform.workflow_preflight",
        "description": "워크플로 그래프를 검증하고 실행 층과 소요 GPU 를 낸다.",
        "inputSchema": {**_obj(workflow_id={"type": "string"}), "required": ["workflow_id"]},
    },
    {
        "name": "platform.run_start",
        "description": "워크플로로 실행을 시작한다.",
        "inputSchema": {
            **_obj(workflow_id={"type": "string"}, request={"type": "object"}),
            "required": ["workflow_id"],
        },
    },
    {
        "name": "platform.run_status",
        "description": "실행 상태와 단계별 진척을 낸다.",
        "inputSchema": {**_obj(run_id={"type": "string"}), "required": ["run_id"]},
    },
    {
        "name": "platform.run_list",
        "description": "최근 실행 목록을 낸다.",
        "inputSchema": _obj(status={"type": "string"}, limit={"type": "integer"}),
    },
    {
        "name": "platform.model_list",
        "description": "Model Registry 에 등록된 모델과 버전을 낸다.",
        "inputSchema": _obj(active_only={"type": "boolean"}),
    },
    {
        "name": "platform.model_resolve",
        "description": "모델 식별자를 실행 엔드포인트로 해석한다.",
        "inputSchema": {
            **_obj(model_id={"type": "string"}, version={"type": "string"}),
            "required": ["model_id"],
        },
    },
    {
        "name": "platform.job_stats",
        "description": "작업 큐 적체 현황을 낸다.",
        "inputSchema": _obj(),
    },
]


async def _call_platform(name: str, args: dict[str, Any], identity: Any) -> Any:
    repos = Repos()

    if name == "platform.workflow_list":
        items = await repos.workflows.list(templates_only=bool(args.get("templates_only")))
        return [{"workflow_id": w.workflow_id, "version": w.version, "name": w.name,
                 "nodes": len(w.nodes)} for w in items]

    if name == "platform.workflow_preflight":
        wf: Workflow | None = await repos.workflows.get(args["workflow_id"])
        if wf is None:
            return {"ok": False, "error": "워크플로가 없습니다"}
        graph = build_graph(wf)
        return {"ok": True, "node_count": len(wf.nodes), "levels": graph.levels}

    if name == "platform.run_start":
        wf = await repos.workflows.get(args["workflow_id"])
        if wf is None:
            return {"ok": False, "error": "워크플로가 없다"}
        run = await ExecutionService(repos).start(
            wf, request=args.get("request") or {}, owner_id=getattr(identity, "user_id", None)
        )
        return {"run_id": run.run_id, "status": str(run.status)}

    if name == "platform.run_status":
        run = await repos.runs.get(args["run_id"])
        if run is None:
            return {"ok": False, "error": "실행이 없습니다"}
        return {"run_id": run.run_id, "status": str(run.status),
                "stages": [{"name": s.name, "status": str(s.status)} for s in run.stages]}

    if name == "platform.run_list":
        runs = await repos.runs.list(status=args.get("status"), limit=int(args.get("limit") or 20))
        return [{"run_id": r.run_id, "status": str(r.status), "workflow_id": r.workflow_id}
                for r in runs]

    if name == "platform.model_list":
        models = await repos.models.list(active_only=bool(args.get("active_only")))
        return [{"model_id": m.model_id, "version": m.version, "kind": str(m.kind),
                 "active": m.active, "approval": str(m.approval_status)} for m in models]

    if name == "platform.model_resolve":
        route = await ModelRouter(repos.models).resolve(args["model_id"], args.get("version"))
        return {"model_id": route.model_id, "version": route.version,
                "transport": route.transport, "target": route.target}

    if name == "platform.job_stats":
        #  Same shape as GET /jobs/stats. One surface must not answer the
        #  same question two ways.
        stats = await repos.jobs.stats()
        return {"by_status": stats, "total": sum(stats.values())}

    raise KeyError(name)


# ---------------------------------------------------------------- the original tools

def _upstream_definitions() -> list[dict[str, Any]]:
    """The original tool definitions. Readable without a runner."""
    try:
        from pipeline_mcp.tools import tool_definitions

        return list(tool_definitions())
    except Exception as exc:  # pragma: no cover - 원본을 뗀 구성
        log.warning("원본 도구 정의를 읽지 못했다: %s", exc)
        return []


#  Tools that change or start something. They are refused for a viewer, and
#  the ones that start a model are refused outright: routing, queueing and
#  accounting for a run belong to this platform's engine, and a second path
#  into a GPU that the queue knows nothing about would run work nobody is
#  holding a lease for. `platform.run_start` is the way in.
_UPSTREAM_STARTS_WORK = frozenset({
    "pipeline.run", "pipeline.run_af2", "pipeline.run_diffdock",
    "pipeline.diffdock", "pipeline.af2_predict", "pipeline.run_from_prompt",
    "pipeline.cath_launch_batch", "pipeline.cath_launch_training",
})

_UPSTREAM_WRITES = frozenset({
    "pipeline.save_project", "pipeline.save_round", "pipeline.save_report",
    "pipeline.save_workflow_session", "pipeline.submit_feedback",
    "pipeline.submit_experiment", "pipeline.delete_project",
    "pipeline.delete_round", "pipeline.delete_run", "pipeline.archive_project",
    "pipeline.archive_round", "pipeline.restore_project", "pipeline.restore_round",
    "pipeline.cancel_run", "pipeline.model_provider_update",
    "pipeline.runpod_update_endpoint", "pipeline.cath_stop_job",
    "pipeline.cath_delete_job", "pipeline.chat.send", "chat.send",
})


def _runner_root() -> str:
    """Where the original looks for runs. The same root this platform writes."""
    return str(Path(get_settings().output_root).resolve())


async def _call_upstream(name: str, args: dict[str, Any], identity: Any) -> Any:
    """Run an original tool against a projected view of this platform's data.

    Three things stand between the call and the dispatcher.

    **The runner needs a storage root.** It is a required field, so building
    one without it raised TypeError and every original tool answered with
    that instead of a result. That was the whole of why 62 of the 70 listed
    tools did nothing.

    **The run has to exist on disk.** The original resolves
    `<output_root>/<run_id>` and reads request.json out of it. A run started
    here lives in MongoDB, so it is projected first - which is why the
    analysis tools can read a run they never wrote.

    **The caller has to be allowed.** The original tools know nothing about
    roles; the check has to happen here or not at all.
    """
    if name in _UPSTREAM_STARTS_WORK:
        return {
            "ok": False,
            "error": (
                f"{name} 은(는) 이 경로로 실행하지 않습니다. "
                "모델 실행은 platform.run_start 로 큐를 거칩니다"
            ),
        }

    if name in _UPSTREAM_WRITES and not _may_write(identity):
        return {"ok": False, "error": f"{name} 을(를) 부를 권한이 없습니다"}

    try:
        from pipeline_mcp.pipeline import PipelineRunner
        from pipeline_mcp.tools import ToolDispatcher
    except Exception as exc:
        return {"ok": False, "error": f"원본 도구를 불러오지 못했습니다: {exc}"}

    #  Project the run the call names, so the tool finds a directory to read.
    run_id = str(args.get("run_id") or "").strip()
    projected: dict[str, Any] | None = None
    if run_id:
        allowed = await may_see_run(identity, run_id)
        if not allowed:
            return {"ok": False, "error": f"{run_id} 을(를) 볼 권한이 없습니다"}
        view = await LegacyProjector(Repos()).project_if_present(run_id)
        projected = view.as_dict() if view else None

    try:
        dispatcher = ToolDispatcher(runner=PipelineRunner(output_root=_runner_root()))
    except Exception as exc:
        return {"ok": False, "error": f"실행 러너를 세우지 못했습니다: {exc}"}

    try:
        result = dispatcher.call_tool(name, args)
    except Exception as exc:
        #  The original raises ValueError for a missing argument and for a
        #  run it cannot find. Both are answers, not faults of this layer.
        return {"ok": False, "error": str(exc), "tool": name}

    if projected and isinstance(result, dict):
        #  So a reader can tell a value read from a projected run from one
        #  read off a directory the original itself wrote.
        result.setdefault("_projected", projected)
    return result


def _may_write(identity: Any) -> bool:
    roles = tuple(getattr(identity, "roles", ()) or ())
    return bool({Role.RESEARCHER, Role.ADMIN, Role.SERVICE} & set(roles))


# ---------------------------------------------------------------- JSON-RPC

@router.post("/mcp", summary="MCP JSON-RPC: the original tools and the ones added here",
             dependencies=[Depends(require(Role.RESEARCHER, Role.SERVICE, Role.ADMIN))])
async def mcp_rpc(identity: CurrentIdentity, message: dict[str, Any] = Body(...)) -> dict[str, Any]:
    msg_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}

    def ok(result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def err(code: int, text: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": text}}

    if method == "initialize":
        return ok({"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}},
                   "serverInfo": {"name": "foldfront", "version": "0.1.0"}})

    if method == "tools/list":
        return ok({"tools": _upstream_definitions() + PLATFORM_TOOLS})

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(name, str):
            return err(-32602, "name 이 필요합니다")
        try:
            if name.startswith("platform."):
                return ok(_text(await _call_platform(name, args, identity)))
            return ok(_text(await _call_upstream(name, args, identity)))
        except KeyError:
            return err(-32601, f"없는 도구입니다: {name}")
        except Exception as exc:
            log.warning("도구 실행에 실패했다 %s: %s", name, exc)
            return err(-32603, str(exc))

    return err(-32601, f"지원하지 않는 메서드입니다: {method}")
