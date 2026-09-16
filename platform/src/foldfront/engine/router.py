"""Model Registry 동적 라우팅.

모델 ID·버전·가용성·자원 정책에 따라 실제 실행 엔드포인트를 고른다.

현행 RAPID 는 모델 엔드포인트를 환경변수로 직접 읽는다.

    RFD3_ENDPOINT_ID · PROTEINMPNN_ENDPOINT_ID · AF2_URL · SOLUPROT_URL …

그래서 새 모델을 붙이려면 코드나 환경변수를 고쳐야 한다. 이 플랫폼은
「URL 직접 수정 없이 고유 ID 로 등록」을 요구한다. 이 모듈이 그 사이를 갈라놓는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from foldfront.db.models import ModelVersion, ResourceSpec
from foldfront.db.repositories import ModelRepo

#  현행이 쓰던 환경변수 이름. 마이그레이션에서 Registry 로 옮길 때 참조한다.
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
    """실행할 엔드포인트를 정하지 못했다."""


@dataclass(frozen=True)
class Route:
    """실행 대상 한 곳."""

    model_id: str
    version: str
    transport: str          # runpod · http · container
    target: str             # 엔드포인트 ID · URL · 이미지 이름
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
    """실행 경로를 고른다.

    RunPod 엔드포인트가 있으면 그것을 우선한다 — 현행 운영 방식이고 자원 확장이 쉽다.
    다음이 자체 HTTP 워커, 마지막이 컨테이너 이미지다.
    """
    if mv.endpoint_id:
        return "runpod", mv.endpoint_id
    if mv.base_url:
        return "http", mv.base_url
    if mv.container_image:
        return "container", mv.container_image
    raise RoutingError(
        f"{mv.model_id}:{mv.version} 에 실행 위치가 없다 — 엔드포인트·URL·이미지 가운데 하나가 필요하다"
    )


class ModelRouter:
    """모델 ID 를 실행 엔드포인트로 바꾼다."""

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
                f"실행할 모델을 찾지 못했다: {model_id}"
                + (f":{version}" if version else " (활성 기본 버전 없음)")
            )
        if not mv.active:
            raise RoutingError(f"비활성 버전이다: {model_id}:{mv.version}")
        if mv.approval_status != "approved":
            raise RoutingError(
                f"승인되지 않은 버전이다: {model_id}:{mv.version} ({mv.approval_status})"
            )

        #  자원 정책 — 감당하지 못하는 요구량이면 라우팅하지 않는다
        if max_gpu is not None and mv.resources.gpu_count > max_gpu:
            raise RoutingError(
                f"{model_id}:{mv.version} 은 GPU {mv.resources.gpu_count}개를 요구한다 "
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
        """여러 모델을 한 번에 해석한다. 실패한 것은 사유를 모아 돌려준다.

        워크플로 실행 전 점검(preflight)에 쓴다 — 중간에 멈추는 것보다 미리 알려주는 편이 낫다.
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
        """실행 전 점검 결과. 현행 pipeline.preflight 도구의 취지를 승계한다."""
        routes, errors = await self.route_many(workflow_models, max_gpu=max_gpu)
        return {
            "ok": not errors,
            "resolved": [r.as_dict() for r in routes],
            "errors": errors,
            "total_gpu": sum(r.resources.gpu_count for r in routes),
        }
