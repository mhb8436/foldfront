"""Error codes and message catalogues.

What matters here is that an error keeps its code while its sentence changes
with the reader, and that adding a language cannot silently leave holes.
"""

from __future__ import annotations

import socket

import pytest
from httpx import ASGITransport, AsyncClient

from foldfront.core.errors import (
    DEFAULT_LANGUAGE,
    MESSAGES,
    STATUS,
    ApiError,
    E,
    missing_translations,
    negotiate,
    render,
)


def test_every_code_is_translated_in_every_catalogue():
    """A language added without its messages would silently fall back."""
    assert missing_translations() == {}


def test_every_code_states_the_status_it_answers():
    assert {c for c in E} <= set(STATUS)


def test_the_code_survives_translation():
    """Callers branch on the code, so it must not vary with the language."""
    error = ApiError(E.WORKFLOW_NOT_FOUND, workflow_id="wf-binding")

    assert error.body("ko")["error"]["code"] == "workflow.not_found"
    assert error.body("en")["error"]["code"] == "workflow.not_found"
    assert error.body("ko")["error"]["message"] != error.body("en")["error"]["message"]


def test_placeholders_are_filled_from_the_parameters():
    assert "wf-binding" in render(E.WORKFLOW_NOT_FOUND, "ko", workflow_id="wf-binding")
    assert "wf-binding" in render(E.WORKFLOW_NOT_FOUND, "en", workflow_id="wf-binding")


def test_parameters_travel_beside_the_message():
    """A console may want the identifier itself rather than parsing the sentence."""
    body = ApiError(E.RUN_NOT_FOUND, run_id="run-1").body()

    assert body["error"]["params"] == {"run_id": "run-1"}


def test_a_missing_placeholder_does_not_replace_the_error():
    """Formatting is a detail; losing the real error to a KeyError is not."""
    text = render(E.WORKFLOW_NOT_FOUND, "ko")

    assert "워크플로" in text


@pytest.mark.parametrize(
    "header,expected",
    [
        ("ko", "ko"),
        ("en", "en"),
        ("en-US,en;q=0.9", "en"),
        ("ko-KR,ko;q=0.9,en;q=0.8", "ko"),
        ("fr-FR", DEFAULT_LANGUAGE),
        ("", DEFAULT_LANGUAGE),
        (None, DEFAULT_LANGUAGE),
    ],
)
def test_language_is_negotiated_from_the_header(header, expected):
    assert negotiate(header) == expected


def test_an_unknown_language_falls_back_rather_than_failing():
    assert render(E.AUTH_FORBIDDEN, "fr") == MESSAGES[DEFAULT_LANGUAGE][E.AUTH_FORBIDDEN]


def test_status_comes_from_the_code_unless_overridden():
    assert ApiError(E.AUTH_FORBIDDEN).status == 403
    assert ApiError(E.AUTH_FORBIDDEN, status=418).status == 418


#  ---------------------------------------------------------------- over HTTP


def _mongo_up(host: str = "127.0.0.1", port: int = 27017) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setenv("MONGO_DB", "foldfront_pytest_errors")

    from foldfront.core.config import get_settings
    from foldfront.db import client as dbclient

    get_settings.cache_clear()
    await dbclient.close_client()

    from foldfront.api.app import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c

    await dbclient.close_client()
    get_settings.cache_clear()


@pytest.mark.skipif(not _mongo_up(), reason="MongoDB 미기동")
async def test_the_response_carries_the_code_and_a_message(client):
    r = await client.get("/api/v1/runs/run-없는것")

    assert r.status_code == 404
    body = r.json()["error"]
    assert body["code"] == "run.not_found"
    assert "run-없는것" in body["message"]


@pytest.mark.skipif(not _mongo_up(), reason="MongoDB 미기동")
async def test_accept_language_chooses_the_message(client):
    ko = await client.get("/api/v1/runs/run-x")
    en = await client.get("/api/v1/runs/run-x", headers={"Accept-Language": "en-GB,en;q=0.9"})

    assert ko.json()["error"]["code"] == en.json()["error"]["code"]
    assert ko.json()["error"]["message"] != en.json()["error"]["message"]
    assert en.json()["error"]["message"].startswith("No such run")
