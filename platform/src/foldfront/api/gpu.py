"""External GPU operations: endpoints, usage, cost, workers.

The models run on RunPod serverless endpoints, and the original already has
the admin surface for them - `runpod_list_endpoints`, `runpod_get_endpoint`,
`runpod_list_billing`, `runpod_get_history`, `runpod_update_endpoint` and
`queue_eta`. None of it was reachable from this console.

    GET  /gpu/endpoints          pipeline.runpod_list_endpoints
    GET  /gpu/endpoints/{id}     pipeline.runpod_get_endpoint
    GET  /gpu/billing            pipeline.runpod_list_billing
    GET  /gpu/history            pipeline.runpod_get_history
    POST /gpu/endpoints/{id}     pipeline.runpod_update_endpoint
    GET  /gpu/status             whether any of the above can work at all

**The runner here is not the one the analysis paths use.** Those read a run
directory and need nothing but a storage root. These talk to RunPod, and the
admin service finds its client by looking through the runner's model clients
for one carrying a `RunPodClient` - so the runner has to be the fully wired
one the original builds from its own configuration. `pipeline_mcp/app.py`
already does that, and it is called rather than repeated: which endpoint id
belongs to which model, and which provider override wins, is settled there.

**Without credentials this surface says so.** `/gpu/status` answers first and
plainly, because the alternative is five screens each failing separately with
a message about an admin service, and an operator left guessing whether the
endpoints are down or were never configured.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from fastapi import APIRouter, Body, Depends, Query

from foldfront.core.auth import CurrentIdentity, require, require_signed_in
from foldfront.core.errors import ApiError, E
from foldfront.db.models import Role
from foldfront.db.repositories import Repos

log = logging.getLogger(__name__)

router = APIRouter(tags=["GPU"], prefix="/gpu")


def credentials_present() -> bool:
    """Whether a RunPod key is configured at all.

    Read from the environment the same way the original's config does, and
    not cached: an operator who sets the key restarts the service, but one
    who is checking why it does not work should not be told a stale answer.
    """
    return bool(str(os.environ.get("RUNPOD_API_KEY") or "").strip())


def _runner() -> Any:
    """The original's own fully wired runner.

    Built per call rather than held: it carries API clients and endpoint ids
    read from configuration and from the provider store, and a long-lived
    copy would keep serving an endpoint an operator has since repointed.
    """
    from pipeline_mcp.app import build_runner

    return build_runner()


async def _service() -> Any:
    """The original's RunPod admin service, or a refusal that explains itself."""
    if not credentials_present():
        raise ApiError(E.GPU_NOT_CONFIGURED)
    try:
        from pipeline_mcp.runpod_admin import build_runpod_admin_service
    except Exception as exc:  # pragma: no cover - 원본을 뗀 구성
        raise ApiError(E.UPSTREAM_UNAVAILABLE, reason=str(exc)) from exc

    try:
        #  Both the build and every call below reach the network, and this
        #  is a sync library. Off the event loop or one slow endpoint stalls
        #  every other request the API is serving.
        service = await asyncio.to_thread(lambda: build_runpod_admin_service(_runner()))
    except Exception as exc:
        raise ApiError(E.GPU_UNREACHABLE, reason=str(exc)) from exc
    if service is None:
        raise ApiError(E.GPU_NOT_CONFIGURED)
    return service


async def _call(what: str, fn: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return await asyncio.to_thread(lambda: fn(*args, **kwargs))
    except Exception as exc:
        log.warning("RunPod %s 가 실패했다: %s", what, exc)
        raise ApiError(E.GPU_UNREACHABLE, reason=str(exc)) from exc


@router.get("/status", dependencies=[Depends(require_signed_in())],
            summary="Whether external GPU operations can work at all")
async def status() -> dict[str, Any]:
    """Answered without touching the network.

    Three states, and they need telling apart: no key configured, a key that
    the provider will not accept, and a working connection. An operator
    seeing an empty endpoint list needs to know which of the three they are
    looking at.
    """
    if not credentials_present():
        return {
            "configured": False,
            "reachable": False,
            "reason": (
                "RUNPOD_API_KEY 가 없습니다. 외부 GPU 관제는 자격 증명이 있어야 합니다. "
                "지금까지의 실행은 모의 어댑터이거나 자체 호스팅 워커입니다."
            ),
        }
    try:
        service = await _service()
        summary = await _call("list_endpoints", service.list_endpoints, include_workers=False)
    except ApiError as exc:
        return {"configured": True, "reachable": False,
                "reason": exc.params.get("reason") or str(exc.code)}

    endpoints = summary.get("endpoints") if isinstance(summary, dict) else []
    return {
        "configured": True,
        "reachable": True,
        "endpoints": len(endpoints) if isinstance(endpoints, list) else 0,
    }


@router.get("/endpoints", dependencies=[Depends(require_signed_in())],
            summary="Endpoints, their health and their workers")
async def list_endpoints(
    include_workers: bool = Query(default=True),
    managed_only: bool = Query(default=False, description="Only endpoints this install uses"),
) -> dict[str, Any]:
    service = await _service()
    result = await _call("list_endpoints", service.list_endpoints,
                         include_workers=include_workers)
    endpoints = result.get("endpoints") if isinstance(result.get("endpoints"), list) else []
    if managed_only:
        endpoints = [e for e in endpoints if isinstance(e, dict) and e.get("managed")]
    return {**result, "endpoints": endpoints,
            "filters": {"managed_only": managed_only, "include_workers": include_workers}}


@router.get("/endpoints/{endpoint_id}", dependencies=[Depends(require_signed_in())],
            summary="One endpoint in detail")
async def get_endpoint(endpoint_id: str) -> dict[str, Any]:
    service = await _service()
    return await _call("get_endpoint", service.get_endpoint, endpoint_id)


@router.get("/billing", dependencies=[Depends(require(Role.ADMIN))],
            summary="What the endpoints have cost")
async def billing(days: int = Query(default=30, ge=1, le=365)) -> dict[str, Any]:
    """Cost is an operator's business, not every reader's."""
    service = await _service()
    return await _call("list_billing", service.list_billing, days=days)


@router.get("/history", dependencies=[Depends(require_signed_in())],
            summary="Recent jobs on an endpoint")
async def history(
    endpoint_id: str = Query(...),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    service = await _service()
    return await _call("get_history", service.get_history, endpoint_id, limit=limit)


@router.post("/endpoints/{endpoint_id}", dependencies=[Depends(require(Role.ADMIN))],
             summary="Change an endpoint's worker limits")
async def update_endpoint(
    endpoint_id: str, identity: CurrentIdentity, patch: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """Scale an endpoint.

    An operator's decision, and one that costs money either way - idle
    workers bill, and too few make a run queue. Recorded with the values, so
    a later bill can be read against who changed what.
    """
    service = await _service()
    result = await _call("update_endpoint", service.update_endpoint, endpoint_id, patch)
    await Repos().audit.record(
        "gpu.endpoint_update", actor_id=identity.user_id,
        target_type="endpoint", target_id=endpoint_id, detail={"patch": patch},
    )
    return result
