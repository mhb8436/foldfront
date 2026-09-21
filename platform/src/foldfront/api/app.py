"""The FastAPI application.

The original serves HTTP from BaseHTTPRequestHandler in the standard library.
Anything integrating with this needs a specification to work from, which is
what moving to FastAPI buys. The MCP JSON-RPC surface is carried over and
mounted on top of the same application.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from foldfront.api.routes import router
from foldfront.api.analysis import router as analysis_router
from foldfront.api.gpu import router as gpu_router
from foldfront.api.mcp import router as mcp_router
from foldfront.core.auth import auth_mode, oidc_enabled, warn_if_open
from foldfront.core.errors import ApiError, negotiate
from foldfront.core.config import get_settings
from foldfront.db.client import close_client, ensure_indexes, get_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    warn_if_open()
    app.state.index_result = await ensure_indexes()
    if oidc_enabled():
        #  The development stand-in was an operator while auth was off. Under
        #  a real provider nobody is 'dev', and a record that says an operator
        #  is would count towards the last-operator rule for no one.
        from foldfront.db.repositories import Repos

        await Repos().users.col.update_many({"subject": "dev"}, {"$set": {"active": False}})
    else:
        #  And back on when the provider is off again - a staging box that
        #  trialled a provider and reverted must not be locked out of itself.
        from foldfront.db.repositories import Repos

        await Repos().users.col.update_many({"subject": "dev", "active": False}, {"$set": {"active": True}})
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


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """Headers every answer carries, whatever path produced it.

    HSTS only when the request actually arrived over TLS, and only on the
    proxy's word: `X-Forwarded-Proto` is set by our own nginx config and not
    passed through from the client, so a client cannot talk the platform into
    claiming a plain connection was encrypted. Sending HSTS from a plain
    deployment would lock a browser out of an installation that has no
    certificate yet.

    The rest hold whether or not there is TLS. `nosniff` because artifacts
    are served from a path a person chooses the name of; `DENY` because this
    console has no reason to be in a frame and framing it is how a click is
    stolen; `no-referrer` because a run id in a URL should not travel to
    whatever a report links out to.
    """
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")

    if request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https":
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


#  Errors answer with a code plus a message chosen for the caller's language.
#  Registered before the routers so every path shares one error shape.
@app.exception_handler(ApiError)
async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
    language = negotiate(request.headers.get("accept-language"))
    return JSONResponse(status_code=exc.status, content=exc.body(language))


app.include_router(router)
#  One surface for the original tools and the ones added here
app.include_router(mcp_router)
app.include_router(analysis_router, prefix="/api/v1")
app.include_router(gpu_router, prefix="/api/v1")


@app.get("/healthz", tags=["Operations"])
async def healthz(request: Request) -> dict[str, object]:
    """Health check. The path matches the original so existing deployment
    scripts keep working."""
    db = get_db()
    behind_tls = (
        request.headers.get("x-forwarded-proto") == "https"
        or request.url.scheme == "https"
    )
    ping = await db.command("ping")
    return {
        "status": "ok",
        "database": get_settings().mongo_db,
        "mongo_ok": bool(ping.get("ok")),
        #  Makes it visible when a deployment reaches production unauthenticated
        "auth": auth_mode(),
        #  Says whether this deployment is behind TLS, the same way the
        #  auth line says whether it is behind a provider. Both are things
        #  an install can silently reach production without.
        "tls": "terminated" if behind_tls else "none",
    }
