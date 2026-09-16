from __future__ import annotations

import json

from pipeline_mcp.oidc import OIDCSettings
from pipeline_mcp.session_auth import SessionConfig
from pipeline_mcp.session_auth import SessionManager


def _settings() -> OIDCSettings:
    return OIDCSettings(
        issuer="https://sso.example.test/realms/kbf",
        client_id="protein-pipeline",
        audience="protein-pipeline",
        scopes="openid profile email",
        provider_name="KBF SSO",
        jwks_url=None,
        algorithms=("RS256",),
    )


def test_session_manager_persists_local_sessions_across_reloads(tmp_path):
    store_path = tmp_path / "sessions.json"
    manager = SessionManager(
        SessionConfig(
            store_path=store_path,
            cookie_name="kbf_session",
            local_ttl_s=3600,
            oidc_refresh_leeway_s=60,
            oidc_fallback_ttl_s=300,
        )
    )
    user = {"username": "tester", "role": "user", "run_prefix": "tester", "created_at": ""}

    session_id = manager.create_local_session(user)

    reloaded = SessionManager(
        SessionConfig(
            store_path=store_path,
            cookie_name="kbf_session",
            local_ttl_s=3600,
            oidc_refresh_leeway_s=60,
            oidc_fallback_ttl_s=300,
        )
    )

    assert reloaded.get_user(session_id) == user


def test_session_manager_refreshes_oidc_sessions_before_expiry(tmp_path, monkeypatch):
    store_path = tmp_path / "sessions.json"
    manager = SessionManager(
        SessionConfig(
            store_path=store_path,
            cookie_name="kbf_session",
            local_ttl_s=3600,
            oidc_refresh_leeway_s=60,
            oidc_fallback_ttl_s=300,
        )
    )
    refresh_calls: list[str] = []

    def fake_claims_from_token_data(settings, token_data):
        access_token = str(token_data.get("access_token") or "")
        if access_token == "new-access":
            return {
                "sub": "user-123",
                "preferred_username": "tester@kribb.re.kr",
                "resource_access": {"protein-pipeline": {"roles": ["pipeline-user"]}},
            }
        return {
            "sub": "user-123",
            "preferred_username": "stale@kribb.re.kr",
            "resource_access": {"protein-pipeline": {"roles": ["pipeline-user"]}},
        }

    def fake_refresh(settings, refresh_token):
        refresh_calls.append(str(refresh_token))
        return {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "id_token": "new-id",
            "expires_in": 300,
            "refresh_expires_in": 1800,
        }

    monkeypatch.setattr("pipeline_mcp.session_auth.claims_from_oidc_token_data", fake_claims_from_token_data)
    monkeypatch.setattr("pipeline_mcp.session_auth.refresh_oidc_tokens", fake_refresh)

    session_id = manager.create_oidc_session(
        _settings(),
        {
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "id_token": "old-id",
            "expires_in": 0,
            "refresh_expires_in": 1200,
        },
    )

    user = manager.get_user(session_id, oidc_settings=_settings())

    assert user is not None
    assert user["username"] == "tester@kribb.re.kr"
    assert refresh_calls == ["old-refresh"]

    payload = json.loads(store_path.read_text(encoding="utf-8"))
    stored = payload["sessions"][session_id]
    assert stored["oidc"]["refresh_token"] == "new-refresh"
    assert stored["oidc"]["access_token"] == "new-access"


def test_session_manager_drops_oidc_session_when_refresh_fails(tmp_path, monkeypatch):
    store_path = tmp_path / "sessions.json"
    manager = SessionManager(
        SessionConfig(
            store_path=store_path,
            cookie_name="kbf_session",
            local_ttl_s=3600,
            oidc_refresh_leeway_s=60,
            oidc_fallback_ttl_s=300,
        )
    )

    monkeypatch.setattr(
        "pipeline_mcp.session_auth.claims_from_oidc_token_data",
        lambda settings, token_data: {
            "sub": "user-123",
            "preferred_username": "tester@kribb.re.kr",
            "resource_access": {"protein-pipeline": {"roles": ["pipeline-user"]}},
        },
    )

    def fake_refresh(settings, refresh_token):
        raise ValueError("refresh token expired")

    monkeypatch.setattr("pipeline_mcp.session_auth.refresh_oidc_tokens", fake_refresh)

    session_id = manager.create_oidc_session(
        _settings(),
        {
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "id_token": "old-id",
            "expires_in": 0,
            "refresh_expires_in": 1200,
        },
    )

    assert manager.get_user(session_id, oidc_settings=_settings()) is None

    payload = json.loads(store_path.read_text(encoding="utf-8"))
    assert payload["sessions"] == {}


def test_get_oidc_access_token_returns_token_and_expiry(tmp_path):
    from pipeline_mcp.session_auth import SessionConfig, SessionManager

    manager = SessionManager(
        SessionConfig(
            store_path=tmp_path / "sessions.json",
            cookie_name="kbf_session",
            local_ttl_s=3600,
            oidc_refresh_leeway_s=60,
            oidc_fallback_ttl_s=300,
        )
    )
    import time
    sid = "sess-oidc"
    exp = int(time.time()) + 1800
    manager._sessions[sid] = {
        "auth_type": "oidc",
        "user": {"username": "alice", "role": "user"},
        "created_at": int(time.time()),
        "updated_at": int(time.time()),
        "expires_at": exp,
        "oidc": {"access_token": "AT-123", "access_expires_at": exp},
    }

    token, expires_at = manager.get_oidc_access_token(sid)
    assert token == "AT-123"
    assert expires_at == exp


def test_get_oidc_access_token_empty_for_local_session(tmp_path):
    from pipeline_mcp.session_auth import SessionConfig, SessionManager

    manager = SessionManager(
        SessionConfig(
            store_path=tmp_path / "sessions.json",
            cookie_name="kbf_session",
            local_ttl_s=3600,
            oidc_refresh_leeway_s=60,
            oidc_fallback_ttl_s=300,
        )
    )
    sid = manager.create_local_session({"username": "bob", "role": "user"})
    token, expires_at = manager.get_oidc_access_token(sid)
    assert token == ""
    assert expires_at == 0
