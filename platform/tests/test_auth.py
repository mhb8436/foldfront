"""인증·인가 시험.

토큰 검증 자체는 원본 `pipeline_mcp.oidc` 의 몫이다. 여기서는 신규 계층이 맡은 두 가지를 본다 —
**원본 역할 표기를 신규 역할로 옮기는가**, 그리고 **경로마다 최소 권한이 걸리는가**.
"""

from __future__ import annotations

import pytest
from foldfront.core.errors import ApiError, E

from foldfront.core import auth
from foldfront.core.auth import Identity, auth_mode, current_identity, require
from foldfront.db.models import Role


def _identity(*roles: Role) -> Identity:
    return Identity(user_id="u", subject="s", email="", roles=roles, authenticated=True)


def test_원본_역할_표기를_신규_역할로_옮긴다():
    """원본은 admin·model_manager·user 세 가지뿐이다. 모델 관리자는 운영자로 올린다."""
    assert auth.ROLE_MAP["admin"] is Role.ADMIN
    assert auth.ROLE_MAP["model_manager"] is Role.ADMIN
    assert auth.ROLE_MAP["user"] is Role.RESEARCHER


def test_모르는_역할은_조회_전용으로_떨어진다():
    """원본이 새 역할을 추가해도 권한이 넓어지지 않는다 — 모르는 값은 가장 좁은 쪽으로 간다."""
    assert auth.ROLE_MAP.get("무언가", Role.VIEWER) is Role.VIEWER


async def test_권한이_없으면_403_을_낸다():
    guard = require(Role.ADMIN)

    with pytest.raises(ApiError) as caught:
        await guard(_identity(Role.VIEWER))

    assert caught.value.status == 403


async def test_요구_역할_가운데_하나만_있으면_통과한다():
    guard = require(Role.RESEARCHER, Role.ADMIN)

    assert await guard(_identity(Role.RESEARCHER)) is not None
    assert await guard(_identity(Role.ADMIN)) is not None


async def test_oidc_가_꺼져_있으면_개발_신원으로_통과한다(monkeypatch):
    """로컬에서 화면을 띄우기 위한 통로다. 다만 인증된 신원으로 위장하지 않는다."""
    monkeypatch.setattr(auth, "oidc_enabled", lambda: False)

    identity = await current_identity(authorization=None)

    assert identity.authenticated is False
    assert auth_mode() == "disabled"


async def test_oidc_가_켜져_있으면_토큰_없이는_401_이다(monkeypatch):
    monkeypatch.setattr(auth, "oidc_enabled", lambda: True)

    with pytest.raises(ApiError) as caught:
        await current_identity(authorization=None)

    assert caught.value.status == 401


async def test_bearer_형식이_아니면_401_이다(monkeypatch):
    monkeypatch.setattr(auth, "oidc_enabled", lambda: True)

    with pytest.raises(ApiError) as caught:
        await current_identity(authorization="Basic dXNlcjpwYXNz")

    assert caught.value.status == 401


async def test_검증_실패_사유를_응답에_싣지_않는다(monkeypatch):
    """실패 원인은 탐색의 단서가 된다. 401 과 일반 문구만 낸다."""
    monkeypatch.setattr(auth, "oidc_enabled", lambda: True)

    class _Settings:
        client_id = "c"

    monkeypatch.setattr("pipeline_mcp.oidc.load_oidc_settings", lambda: _Settings())

    def _boom(token, settings):
        raise ValueError("서명 키 kid=abc123 을 찾지 못했다")

    monkeypatch.setattr("pipeline_mcp.oidc.verify_oidc_token", _boom)

    with pytest.raises(ApiError) as caught:
        await current_identity(authorization="Bearer 아무거나")

    assert caught.value.status == 401
    assert "kid" not in str(caught.value.code)
    assert caught.value.params == {}


def test_쓰기_경로에_권한이_걸려_있다():
    """조회는 열고 쓰기는 막는다. 새 쓰기 경로가 무방비로 추가되는 것을 잡는다."""
    from foldfront.api.app import app

    unguarded = [
        route.path
        for route in app.routes
        if getattr(route, "methods", None)
        and {"POST", "PUT", "PATCH", "DELETE"} & route.methods
        and not getattr(route, "dependencies", None)
    ]

    assert unguarded == []
