"""One MCP tool surface.

The original serves 62 tools over stdio JSON-RPC. This platform added 25 HTTP
paths of its own. To anything outside, that was two surfaces that did not know
about each other.

    POST /mcp   JSON-RPC 2.0: initialize, tools/list, tools/call

tools/list answers with the original 62 alongside the ones added here.
tools/call splits on the name: platform.* is handled locally, everything else
goes to the original dispatcher.

That dispatcher needs a PipelineRunner, which needs an execution environment
that is not settled yet. It is built lazily and its failure is returned as a
reason rather than swallowed, so listing keeps working meanwhile - a surface
that answers "not configured" is more use than one that answers nothing.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends

from foldfront.core.auth import CurrentIdentity, require
from foldfront.db.models import Role, Workflow
from foldfront.db.repositories import Repos
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


def _call_upstream(name: str, args: dict[str, Any]) -> Any:
    """Run an original tool, reporting a runner that cannot be built."""
    try:
        from pipeline_mcp.pipeline import PipelineRunner
        from pipeline_mcp.tools import ToolDispatcher
    except Exception as exc:
        return {"ok": False, "error": f"원본 도구를 불러오지 못했습니다: {exc}"}

    try:
        dispatcher = ToolDispatcher(runner=PipelineRunner())
    except Exception as exc:
        #  Needs an execution environment - endpoints, a storage root
        return {"ok": False, "error": f"실행 러너를 세우지 못했습니다: {exc}"}

    return dispatcher.call_tool(name, args)


# ---------------------------------------------------------------- JSON-RPC

@router.post("/mcp", summary="MCP JSON-RPC (원본 도구 + 신규 계층 도구)",
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
            return ok(_text(_call_upstream(name, args)))
        except KeyError:
            return err(-32601, f"없는 도구입니다: {name}")
        except Exception as exc:
            log.warning("도구 실행에 실패했다 %s: %s", name, exc)
            return err(-32603, str(exc))

    return err(-32601, f"지원하지 않는 메서드입니다: {method}")
