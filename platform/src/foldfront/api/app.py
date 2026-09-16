"""FastAPI 응용.

현행 RAPID 는 표준 라이브러리 BaseHTTPRequestHandler 로 HTTP 를 직접 구현한다.
외부 연계에 OpenAPI 수준의 명세가 필요하므로 FastAPI 로 옮긴다.
MCP JSON-RPC 표면은 현행 구현을 승계하여 이 응용 위에 얹는다.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from foldfront.api.routes import router
from foldfront.api.mcp import router as mcp_router
from foldfront.core.auth import auth_mode, warn_if_open
from foldfront.core.errors import ApiError, negotiate
from foldfront.core.config import get_settings
from foldfront.db.client import close_client, ensure_indexes, get_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    warn_if_open()
    app.state.index_result = await ensure_indexes()
    yield
    await close_client()


app = FastAPI(
    title="foldfront — 단백질 설계 자동화 플랫폼",
    description=(
        "단백질 설계 모델을 자유형 DAG 워크플로로 조합해 실행·비교·관리한다. "
        "RAPID v1.0.29(MIT, Park, KRIBB, DOI 10.5281/zenodo.20619413)를 승계한다."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


#  Errors answer with a code plus a message chosen for the caller's language.
#  Registered before the routers so every path shares one error shape.
@app.exception_handler(ApiError)
async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
    language = negotiate(request.headers.get("accept-language"))
    return JSONResponse(status_code=exc.status, content=exc.body(language))


app.include_router(router)
#  원본 MCP 도구와 신규 계층 도구를 한 표면으로 낸다
app.include_router(mcp_router)


@app.get("/healthz", tags=["운영"])
async def healthz() -> dict[str, object]:
    """상태 확인. 현행 배포 절차가 /healthz 를 보므로 경로를 같게 둔다."""
    db = get_db()
    ping = await db.command("ping")
    return {
        "status": "ok",
        "database": get_settings().mongo_db,
        "mongo_ok": bool(ping.get("ok")),
        #  인증이 꺼진 채로 운영에 올라가면 여기서 드러난다
        "auth": auth_mode(),
    }
