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

**No runner.** The original reaches its admin service through
`build_runner`, which constructs every model client and refuses without
MMSEQS_ENDPOINT_ID and its siblings. Those say where models run and have
nothing to do with administering an account - requiring them would mean an
operator cannot look at their endpoints until they have configured
endpoints, and looking is how they find out what to configure. So the
client is built from the key and the service from the client.

Which endpoints this installation actually uses comes from our own Model
Registry rather than from the original's environment variables, because
the registry is where this platform decides what runs where.

**Without credentials this surface says so.** `/gpu/status` answers first and
plainly, because the alternative is five screens each failing separately with
a message about an admin service, and an operator left guessing whether the
endpoints are down or were never configured.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, Query

from foldfront.core.auth import CurrentIdentity, require, require_signed_in
from foldfront.core.config import get_settings
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


async def _managed_endpoints() -> dict[str, list[dict[str, str]]]:
    """Which endpoints this installation actually routes to, by endpoint id.

    Read from our own Model Registry rather than from the original's
    environment variables. The registry is where this platform decides what
    runs where, so it is the honest answer to "are we using this endpoint" -
    and an account can hold endpoints nothing here points at.
    """
    by_endpoint: dict[str, list[dict[str, str]]] = {}
    for mv in await Repos().models.list(active_only=True):
        endpoint = str(getattr(mv, "endpoint_id", "") or "").strip()
        if not endpoint:
            continue
        by_endpoint.setdefault(endpoint, []).append(
            {"key": mv.model_id, "label": f"{mv.model_id}:{mv.version}",
             "endpoint_id": endpoint, "configured": "true"}
        )
    return by_endpoint


async def _service() -> Any:
    """The original's RunPod admin service, or a refusal that explains itself.

    Built from a RunPodClient directly rather than through the original's
    `build_runner`. That builder constructs every model client and refuses
    without MMSEQS_ENDPOINT_ID and its siblings - which have nothing to do
    with administering an account. Requiring them would mean an operator
    could not look at their endpoints until they had already configured
    endpoints, and looking is how they find out what to configure.
    """
    if not credentials_present():
        raise ApiError(E.GPU_NOT_CONFIGURED)
    try:
        from pipeline_mcp.clients.runpod import RunPodClient
        from pipeline_mcp.runpod_admin import RunPodAdminService
    except Exception as exc:  # pragma: no cover - 원본을 뗀 구성
        raise ApiError(E.UPSTREAM_UNAVAILABLE, reason=str(exc)) from exc

    managed = await _managed_endpoints()
    return RunPodAdminService(
        runpod=RunPodClient(
            api_key=str(os.environ.get("RUNPOD_API_KEY") or "").strip(),
            ca_bundle=os.environ.get("RUNPOD_CA_BUNDLE") or None,
            skip_verify=str(os.environ.get("RUNPOD_SKIP_VERIFY") or "").lower()
            in ("1", "true", "yes"),
            timeout_s=30.0,
        ),
        output_root=str(Path(get_settings().output_root).resolve()),
        managed_endpoint_map=managed,
        managed_services=[s for lst in managed.values() for s in lst],
    )


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
