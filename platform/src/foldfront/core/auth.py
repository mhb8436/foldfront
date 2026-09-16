"""인증·인가.

원본 RAPID 의 OIDC 구현(`pipeline_mcp.oidc`, 339줄)을 **그대로 물어 쓴다.**
토큰 검증·JWKS 회전·발급자 정규화는 이미 현장에서 돌던 코드이므로 다시 만들지 않는다.
이 모듈이 하는 일은 둘뿐이다.

  1. 원본이 내는 역할 표기(admin · model_manager · user)를 신규 역할 4종으로 옮긴다.
  2. FastAPI 의존성으로 싸서 경로마다 최소 권한을 걸 수 있게 한다.

★ 운영자 계정을 코드에 두지 않는다. 신원은 전적으로 OIDC 공급자가 정한다.

**개발 모드** — OIDC 를 설정하지 않으면 인증이 꺼진다. 로컬에서 화면을 띄우고
시험을 돌리기 위해서다. 다만 조용히 꺼지지 않는다. 기동 로그에 경고를 남기고
`/healthz` 가 `auth: "disabled"` 를 내보내므로 운영에 그 상태로 올라가면 드러난다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated, Any, Iterable

from fastapi import Depends, Header, HTTPException

from foldfront.core.config import get_settings
from foldfront.db.models import Role

log = logging.getLogger(__name__)

#  원본 표기 → 신규 역할. 원본에는 조회 전용과 연계 계정 구분이 없어 이쪽에서 넓힌다
ROLE_MAP: dict[str, Role] = {
    "admin": Role.ADMIN,
    "model_manager": Role.ADMIN,
    "user": Role.RESEARCHER,
}


@dataclass(frozen=True)
class Identity:
    """검증이 끝난 신원. 화면·감사 로그가 이것만 본다."""

    user_id: str
    subject: str
    email: str
    roles: tuple[Role, ...]
    authenticated: bool

    def has(self, *wanted: Role) -> bool:
        return any(r in self.roles for r in wanted)


#  인증이 꺼진 동안 쓰는 신원. 권한 판정 코드가 분기 없이 같게 돌도록 실제 신원과 같은 모양이다
DEV_IDENTITY = Identity(
    user_id="dev",
    subject="dev",
    email="",
    roles=(Role.ADMIN,),
    authenticated=False,
)


def oidc_enabled() -> bool:
    """OIDC 설정이 갖춰졌는지. 원본의 환경변수 규약을 그대로 따른다."""
    try:
        from pipeline_mcp.oidc import load_oidc_settings
    except Exception:  # pragma: no cover - 원본 없이 돌릴 때
        return False
    return load_oidc_settings() is not None


def identity_from_token(token: str) -> Identity:
    """Bearer 토큰을 신원으로 바꾼다. 검증은 원본에 맡긴다."""
    from pipeline_mcp.oidc import claims_to_user, load_oidc_settings, verify_oidc_token

    settings = load_oidc_settings()
    if settings is None:
        raise HTTPException(status_code=503, detail="OIDC 설정이 없다")

    try:
        claims = verify_oidc_token(token, settings)
        user = claims_to_user(claims, settings.client_id)
    except HTTPException:
        raise
    except Exception as exc:
        #  사유를 그대로 돌려주지 않는다. 검증 실패 원인은 탐색의 단서가 된다
        log.warning("토큰 검증에 실패했다: %s", exc)
        raise HTTPException(status_code=401, detail="토큰을 검증하지 못했다") from exc

    role = ROLE_MAP.get(str(user.get("role")), Role.VIEWER)
    return Identity(
        user_id=str(user.get("username") or user.get("subject") or ""),
        subject=str(user.get("subject") or ""),
        email=str(user.get("email") or ""),
        roles=(role,),
        authenticated=True,
    )


async def current_identity(
    authorization: Annotated[str | None, Header()] = None,
) -> Identity:
    """요청의 신원. OIDC 가 꺼져 있으면 개발용 신원을 돌려준다."""
    if not oidc_enabled():
        return DEV_IDENTITY

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Bearer 토큰이 필요하다")

    return identity_from_token(authorization.split(" ", 1)[1].strip())


CurrentIdentity = Annotated[Identity, Depends(current_identity)]


def require(*roles: Role):
    """최소 권한을 거는 의존성을 만든다.

        @router.post(..., dependencies=[Depends(require(Role.ADMIN))])
    """

    async def guard(identity: CurrentIdentity) -> Identity:
        if not identity.has(*roles):
            raise HTTPException(status_code=403, detail="권한이 없다")
        return identity

    return guard


def describe(roles: Iterable[Role]) -> dict[str, Any]:
    """감사 로그에 남길 형태."""
    return {"roles": [str(r) for r in roles]}


def auth_mode() -> str:
    """상태 조회에 싣는 값. 운영에 인증이 꺼진 채로 올라가면 여기서 드러난다."""
    return "oidc" if oidc_enabled() else "disabled"


def warn_if_open() -> None:
    if not oidc_enabled():
        log.warning(
            "인증이 꺼져 있다 — OIDC 환경변수가 없다. 개발 용도로만 쓴다."
        )
    _ = get_settings()
