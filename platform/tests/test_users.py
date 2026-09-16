"""Accounts, and what an operator decides about them.

The provider says who; the operator says what, here. What is under test is
that the operator's word wins, that a first sign-in is recorded, and that an
installation cannot be left without anyone to operate it.
"""

from __future__ import annotations

import pytest

from foldfront.db.models import Role

pytest_plugins: list[str] = []


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setenv("MONGO_DB", "foldfront_pytest_users")
    from foldfront.core.config import get_settings
    from foldfront.db import client as dbclient

    get_settings.cache_clear()
    await dbclient.close_client()
    from foldfront.api.app import app
    from foldfront.db.client import C, ensure_indexes, get_db
    from httpx import ASGITransport, AsyncClient

    db = get_db()
    for key, name in vars(C).items():
        if not key.startswith("__") and isinstance(name, str):
            await db[name].delete_many({})
    await ensure_indexes(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    await dbclient.close_client()
    get_settings.cache_clear()


async def test_처음_들어온_계정은_공급자가_준_역할로_기록된다(client):
    me = (await client.get("/api/v1/me")).json()
    users = (await client.get("/api/v1/users")).json()["items"]

    assert me["roles"] == ["admin"]
    assert [(u["user_id"], u["roles"]) for u in users] == [("dev", ["admin"])]
    assert users[0]["last_login_at"]


async def test_운영자가_정한_역할이_공급자의_역할을_이긴다(client, monkeypatch):
    """This is the point: the provider can only say admin or user."""
    from foldfront.db.repositories import Repos

    await client.get("/api/v1/me")  # first seen, as admin
    await Repos().users.set_roles("dev", [Role.VIEWER])

    me = (await client.get("/api/v1/me")).json()

    assert me["roles"] == ["viewer"]
    #  And the write paths follow the record, not the provider
    assert (await client.post("/api/v1/projects", json={"project_id": "", "name": "x"})).status_code == 403


async def test_꺼진_계정은_들어오지_못한다(client):
    from foldfront.db.repositories import Repos

    await client.get("/api/v1/me")
    await Repos().users.set_active("dev", False)

    assert (await client.get("/api/v1/me")).status_code == 403


async def test_마지막_운영자의_운영_권한은_뺄_수_없다(client):
    await client.get("/api/v1/me")

    r = await client.patch("/api/v1/users/dev", json={"roles": ["researcher"]})

    assert r.status_code == 409
    assert r.json()["error"]["code"] == "user.last_admin"
    assert (await client.get("/api/v1/me")).json()["roles"] == ["admin"]


async def test_다른_운영자가_있으면_역할을_바꿀_수_있다(client):
    from foldfront.db.models import User
    from foldfront.db.repositories import Repos

    await client.get("/api/v1/me")
    await Repos().users.col.insert_one(User(user_id="other", roles=[Role.ADMIN]).model_dump())

    r = await client.patch("/api/v1/users/other", json={"roles": ["researcher", "service"]})

    assert r.status_code == 200
    assert r.json()["roles"] == ["researcher", "service"]
    actions = [a.action for a in await Repos().audit.search(action="user.update")]
    assert actions == ["user.update"]


async def test_없는_계정은_없다고_한다(client):
    await client.get("/api/v1/me")
    assert (await client.patch("/api/v1/users/nobody", json={"active": False})).status_code == 404


async def test_조회자는_이용자_목록을_보지_못한다(client, monkeypatch):
    from foldfront.core.config import get_settings

    monkeypatch.setenv("DEV_ROLE", "viewer")
    get_settings.cache_clear()

    assert (await client.get("/api/v1/users")).status_code == 403
