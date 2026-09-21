"""Dynamic routing over the model registry.

Turns a model id into somewhere to send the work, weighing version,
availability and what the caller can afford to spend.

The original reads endpoints straight from the environment.

    RFD3_ENDPOINT_ID · PROTEINMPNN_ENDPOINT_ID · AF2_URL · SOLUPROT_URL …

Adding a model therefore means editing code or the environment. Here a model
is registered by id and the address is looked up, so neither has to change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from foldfront.db.models import ModelVersion, ResourceSpec
from foldfront.db.repositories import ModelRepo

#  The environment variable names the original used, kept so a migration can
#  carry those endpoints into the registry.
LEGACY_ENV_BY_MODEL: dict[str, tuple[str, ...]] = {
    "mmseqs": ("MMSEQS_ENDPOINT_ID",),
    "proteinmpnn": ("PROTEINMPNN_ENDPOINT_ID",),
    "rfd3": ("RFD3_ENDPOINT_ID",),
    "bioemu": ("BIOEMU_ENDPOINT_ID",),
    "diffdock": ("DIFFDOCK_ENDPOINT_ID",),
    "af2": ("ALPHAFOLD2_ENDPOINT_ID", "AF2_URL"),
    "soluprot": ("SOLUPROT_URL",),
}


class RoutingError(LookupError):
    """No endpoint could be chosen for the model."""


@dataclass(frozen=True)
class Route:
    """One place to send work to."""

    model_id: str
    version: str
    transport: str          # runpod · http · container
    target: str             # Endpoint id, URL, or image name
    resources: ResourceSpec
    timeout_seconds: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "version": self.version,
            "transport": self.transport,
            "target": self.target,
            "gpu_count": self.resources.gpu_count,
            "timeout_seconds": self.timeout_seconds,
        }


def choose_transport(mv: ModelVersion) -> tuple[str, str]:
    """Choose how to reach the model.

    A RunPod endpoint wins when one is set, since that is how the original runs
    and it scales without anyone provisioning hardware. Then a self-hosted HTTP
    worker, then a container image. A model registered with only a base_url
    never touches RunPod, which is what keeps an on-premises deployment from
    depending on a third party.
    """
    #  A local runner wins: it needs no endpoint and is how cheap in-process
    #  work (surrogate triage) reaches its adapter without a GPU.
    if mv.local_runner:
        return "local", mv.local_runner
    if mv.endpoint_id:
        return "runpod", mv.endpoint_id
    if mv.base_url:
        return "http", mv.base_url
    if mv.container_image:
        return "container", mv.container_image
    raise RoutingError(
        f"{mv.model_id}:{mv.version} 에 실행 위치가 없습니다. "
        f"엔드포인트·URL·이미지·로컬러너 가운데 하나가 필요합니다"
    )


class ModelRouter:
    """Resolves a model id to an endpoint."""

    def __init__(self, models: ModelRepo) -> None:
        self.models = models

    async def route(
        self,
        model_id: str,
        version: str | None = None,
        *,
        max_gpu: int | None = None,
    ) -> Route:
        mv = await self.models.resolve(model_id, version)
        if mv is None:
            raise RoutingError(
                f"실행할 모델을 찾지 못했습니다: {model_id}"
                + (f":{version}" if version else " (활성 기본 버전이 없습니다)")
            )
        if not mv.active:
            raise RoutingError(f"비활성 버전입니다: {model_id}:{mv.version}")
        if mv.approval_status != "approved":
            raise RoutingError(
                f"승인되지 않은 버전입니다: {model_id}:{mv.version} ({mv.approval_status})"
            )

        #  Refuse rather than dispatch work the cluster cannot take
        if max_gpu is not None and mv.resources.gpu_count > max_gpu:
            raise RoutingError(
                f"{model_id}:{mv.version} 은 GPU {mv.resources.gpu_count}개를 요구합니다 "
                f"(가용 {max_gpu}개)"
            )

        transport, target = choose_transport(mv)
        return Route(
            model_id=mv.model_id,
            version=mv.version,
            transport=transport,
            target=target,
            resources=mv.resources,
            timeout_seconds=mv.resources.timeout_seconds,
        )

    async def route_many(
        self, requests: list[tuple[str, str | None]], *, max_gpu: int | None = None
    ) -> tuple[list[Route], list[str]]:
        """Resolve several models at once, collecting the failures.

        This is what preflight uses. Telling someone up front which models are
        missing beats stopping halfway through a run that already spent GPU
        time on the stages before it.
        """
        routes: list[Route] = []
        errors: list[str] = []
        for model_id, version in requests:
            try:
                routes.append(await self.route(model_id, version, max_gpu=max_gpu))
            except RoutingError as exc:
                errors.append(str(exc))
        return routes, errors

    async def preflight(
        self, workflow_models: list[tuple[str, str | None]], *, max_gpu: int | None = None
    ) -> dict[str, Any]:
        """The preflight verdict, in the spirit of the original's tool."""
        routes, errors = await self.route_many(workflow_models, max_gpu=max_gpu)
        return {
            "ok": not errors,
            "resolved": [r.as_dict() for r in routes],
            "errors": errors,
            "total_gpu": sum(r.resources.gpu_count for r in routes),
        }
