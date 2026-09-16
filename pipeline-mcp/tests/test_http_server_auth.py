from __future__ import annotations

import io
import zipfile

from pipeline_mcp import http_server
from pipeline_mcp.http_server import Handler
from pipeline_mcp.session_auth import SessionConfig
from pipeline_mcp.session_auth import SessionManager


def test_handler_accepts_cookie_session_without_authorization_header(tmp_path, monkeypatch):
    manager = SessionManager(
        SessionConfig(
            store_path=tmp_path / "sessions.json",
            cookie_name="kbf_session",
            local_ttl_s=3600,
            oidc_refresh_leeway_s=60,
            oidc_fallback_ttl_s=300,
        )
    )
    user = {"username": "tester", "role": "user", "run_prefix": "tester", "created_at": ""}
    session_id = manager.create_local_session(user)

    monkeypatch.setattr(http_server, "_AUTH", None, raising=False)
    monkeypatch.setattr(http_server, "_OIDC", None, raising=False)
    monkeypatch.setattr(http_server, "_SESSIONS", manager, raising=False)

    handler = Handler.__new__(Handler)
    handler.headers = {"Cookie": f"{manager.config.cookie_name}={session_id}"}

    assert handler._require_user() == user


def test_require_auth_rejects_non_admin_when_admin_required(monkeypatch):
    captured = {}

    monkeypatch.setenv("PIPELINE_REQUIRE_ADMIN", "true")

    handler = Handler.__new__(Handler)
    handler._auth_enabled = lambda: True
    handler._require_user = lambda: {"username": "tester", "role": "user"}
    handler._json = lambda status, payload, extra_headers=None: captured.update(  # noqa: ARG005
        status=status,
        payload=payload,
    )

    assert handler._require_auth() is None
    assert captured == {"status": 403, "payload": {"ok": False, "error": "admin required"}}


def test_require_auth_allows_admin_when_admin_required(monkeypatch):
    user = {"username": "admin", "role": "admin"}

    monkeypatch.setenv("PIPELINE_REQUIRE_ADMIN", "true")

    handler = Handler.__new__(Handler)
    handler._auth_enabled = lambda: True
    handler._require_user = lambda: user

    assert handler._require_auth() == user


def test_model_provider_global_update_requires_model_manager_but_user_scope_is_allowed(monkeypatch):
    handler = Handler.__new__(Handler)

    assert handler._can_manage_models({"username": "admin", "role": "admin"}) is True
    assert handler._can_manage_models({"username": "manager", "role": "model_manager"}) is True
    assert handler._can_manage_models({"username": "user", "role": "user"}) is False

    calls = []
    monkeypatch.setattr(
        http_server,
        "_DISPATCHER",
        type(
        "FakeDispatcher",
        (),
        {
            "call_tool": lambda _self, name, arguments: calls.append((name, arguments)) or {"ok": True},
        },
        )(),
        raising=False,
    )

    try:
        handler._call_tool_for_user(
            {"username": "user", "role": "user"},
            "pipeline.model_provider_update",
            {"model_key": "esmfold", "scope": "global"},
        )
    except Exception as exc:
        assert "model manager required" in str(exc)
    else:
        raise AssertionError("plain users must not update global model providers")

    out = handler._call_tool_for_user(
        {"username": "user", "role": "user"},
        "pipeline.model_provider_update",
        {"model_key": "esmfold", "scope": "user", "provider": {"provider_type": "disabled"}},
    )

    assert out == {"ok": True}
    assert calls[0][0] == "pipeline.model_provider_update"
    assert calls[0][1]["scope"] == "user"
    assert calls[0][1]["user"]["username"] == "user"


def test_pending_oidc_user_is_rejected(monkeypatch):
    captured = {}

    handler = Handler.__new__(Handler)
    handler._auth_enabled = lambda: True
    handler._require_user = lambda: {"username": "new.user@example.org", "role": "user", "status": "pending"}
    handler._json = lambda status, payload, extra_headers=None: captured.update(  # noqa: ARG005
        status=status,
        payload=payload,
    )

    assert handler._require_auth() is None
    assert captured == {
        "status": 403,
        "payload": {"ok": False, "error": "approval required"},
    }


def test_admin_can_list_and_update_users(monkeypatch):
    captured = {}

    class FakeAuth:
        enabled = True

        @staticmethod
        def list_users():
            return [{"username": "new.user@example.org", "role": "user", "status": "pending"}]

        @staticmethod
        def update_user(*, username, role=None, status=None):
            assert username == "new.user@example.org"
            assert role == "model_manager"
            assert status == "approved"
            return {"username": username, "role": role, "status": status}

    monkeypatch.setattr(http_server, "_AUTH", FakeAuth(), raising=False)

    handler = Handler.__new__(Handler)
    handler.path = "/auth/list_users"
    handler.headers = {}
    handler._require_auth = lambda: {"username": "admin", "role": "admin", "status": "approved"}
    handler._json = lambda status, payload, extra_headers=None: captured.update(  # noqa: ARG005
        status=status,
        payload=payload,
    )
    handler.log_error = lambda *args, **kwargs: None  # noqa: ARG005

    handler.do_POST()

    assert captured == {
        "status": 200,
        "payload": {
            "ok": True,
            "users": [{"username": "new.user@example.org", "role": "user", "status": "pending"}],
        },
    }

    handler.path = "/auth/update_user"
    handler._read_json = lambda: {
        "username": "new.user@example.org",
        "role": "model_manager",
        "status": "approved",
    }

    handler.do_POST()

    assert captured == {
        "status": 200,
        "payload": {
            "ok": True,
            "user": {"username": "new.user@example.org", "role": "model_manager", "status": "approved"},
        },
    }


def test_local_login_does_not_create_session_for_non_admin_when_admin_required(monkeypatch):
    captured = {}

    class FakeAuth:
        enabled = True

        @staticmethod
        def authenticate(username, password):
            assert username == "tester"
            assert password == "secret"
            return {"user": {"username": "tester", "role": "user"}, "token": "token"}

    class FakeSessions:
        @staticmethod
        def create_local_session(_user):
            raise AssertionError("non-admin users must not get sessions")

    monkeypatch.setenv("PIPELINE_REQUIRE_ADMIN", "true")
    monkeypatch.setattr(http_server, "_AUTH", FakeAuth(), raising=False)
    monkeypatch.setattr(http_server, "_SESSIONS", FakeSessions(), raising=False)

    handler = Handler.__new__(Handler)
    handler.path = "/auth/login"
    handler.headers = {}
    handler._read_json = lambda: {"username": "tester", "password": "secret"}
    handler._json = lambda status, payload, extra_headers=None: captured.update(  # noqa: ARG005
        status=status,
        payload=payload,
    )
    handler.log_error = lambda *args, **kwargs: None  # noqa: ARG005

    handler.do_POST()

    assert captured == {"status": 403, "payload": {"ok": False, "error": "admin required"}}


def test_handler_accepts_api_prefixed_healthz_route():
    captured = {}

    handler = Handler.__new__(Handler)
    handler.path = "/api/healthz"
    handler.headers = {}
    handler._json = lambda status, payload, extra_headers=None: captured.update(  # noqa: ARG005
        status=status,
        payload=payload,
    )

    handler.do_GET()

    assert captured["status"] == 200
    assert captured["payload"] == {"ok": True}


def test_model_registration_skill_archive_requires_login_when_auth_enabled():
    captured = {}

    handler = Handler.__new__(Handler)
    handler.path = "/api/model_provider_skill.zip"
    handler.headers = {}
    handler._auth_enabled = lambda: True
    handler._require_auth = lambda: captured.update(status=401) or None

    handler.do_GET()

    assert captured == {"status": 401}


def test_model_registration_skill_archive_downloads_zip_for_signed_in_user(tmp_path, monkeypatch):
    skill_root = tmp_path / "protein-model-api-registration"
    skill_root.mkdir()
    (skill_root / "SKILL.md").write_text("# Protein Model API Registration\n", encoding="utf-8")
    (skill_root / "references").mkdir()
    (skill_root / "references" / "current-local-services.md").write_text("18101 ProteinMPNN\n", encoding="utf-8")
    monkeypatch.setenv("PIPELINE_MODEL_REGISTRATION_SKILL_DIR", str(skill_root))

    sent = {"status": None, "headers": [], "ended": False}
    handler = Handler.__new__(Handler)
    handler.path = "/api/model_provider_skill.zip"
    handler.headers = {}
    handler.wfile = io.BytesIO()
    handler._auth_enabled = lambda: True
    handler._require_auth = lambda: {"username": "tester", "role": "user", "status": "approved"}
    handler._set_cors_headers = lambda: None
    handler.send_response = lambda status: sent.update(status=status)
    handler.send_header = lambda key, value: sent["headers"].append((key, value))
    handler.end_headers = lambda: sent.update(ended=True)

    handler.do_GET()

    assert sent["status"] == 200
    assert sent["ended"] is True
    headers = dict(sent["headers"])
    assert headers["Content-Type"] == "application/zip"
    assert "protein-model-api-registration.zip" in headers["Content-Disposition"]
    with zipfile.ZipFile(io.BytesIO(handler.wfile.getvalue())) as archive:
        assert set(archive.namelist()) == {
            "protein-model-api-registration/SKILL.md",
            "protein-model-api-registration/references/current-local-services.md",
        }


def test_run_export_archive_downloads_zip_for_signed_in_owner(tmp_path, monkeypatch):
    run_id = "tester_20260527_090000_abc12345"
    export_dir = tmp_path / run_id / "exports"
    export_dir.mkdir(parents=True)
    package_path = export_dir / "result_package_test.zip"
    package_path.write_bytes(b"zip bytes")

    runner = type("FakeRunner", (), {"output_root": str(tmp_path)})()
    dispatcher = type("FakeDispatcher", (), {"runner": runner})()
    monkeypatch.setattr(http_server, "_DISPATCHER", dispatcher, raising=False)

    sent = {"status": None, "headers": [], "ended": False}
    handler = Handler.__new__(Handler)
    handler.path = f"/api/runs/{run_id}/exports/{package_path.name}"
    handler.headers = {}
    handler.wfile = io.BytesIO()
    handler._auth_enabled = lambda: True
    handler._require_auth = lambda: {
        "username": "tester",
        "role": "user",
        "status": "approved",
    }
    handler._set_cors_headers = lambda: None
    handler.send_response = lambda status: sent.update(status=status)
    handler.send_header = lambda key, value: sent["headers"].append((key, value))
    handler.end_headers = lambda: sent.update(ended=True)

    handler.do_GET()

    assert sent["status"] == 200
    assert sent["ended"] is True
    headers = dict(sent["headers"])
    assert headers["Content-Type"] == "application/zip"
    assert headers["Content-Length"] == str(package_path.stat().st_size)
    assert package_path.name in headers["Content-Disposition"]
    assert handler.wfile.getvalue() == b"zip bytes"


def test_auth_me_returns_public_session_type(monkeypatch):
    captured = {}

    class FakeSessions:
        config = type("Config", (), {"cookie_name": "kbf_session"})()

        @staticmethod
        def get_session(session_id, *, oidc_settings=None):  # noqa: ARG004
            assert session_id == "sid"
            return {"auth_type": "local"}

    monkeypatch.setattr(http_server, "_SESSIONS", FakeSessions(), raising=False)

    handler = Handler.__new__(Handler)
    handler.path = "/auth/me"
    handler.headers = {"Cookie": "kbf_session=sid"}
    handler._require_auth = lambda: {"username": "admin", "role": "admin", "status": "approved"}
    handler._json = lambda status, payload, extra_headers=None: captured.update(  # noqa: ARG005
        status=status,
        payload=payload,
    )

    handler.do_GET()

    assert captured == {
        "status": 200,
        "payload": {
            "ok": True,
            "user": {"username": "admin", "role": "admin", "status": "approved"},
            "session": {"auth_type": "local"},
        },
    }


def test_handler_accepts_api_prefixed_tools_call_route():
    captured = {}

    handler = Handler.__new__(Handler)
    handler.path = "/api/tools/call"
    handler.headers = {}
    handler._auth_enabled = lambda: False
    handler._require_auth = lambda: None
    handler._read_json = lambda: {"name": "pipeline.list_runs", "arguments": {"limit": 1}}
    handler._call_tool_for_user = lambda user, name, arguments: {  # noqa: ARG005
        "name": name,
        "arguments": arguments,
    }
    handler._json = lambda status, payload, extra_headers=None: captured.update(  # noqa: ARG005
        status=status,
        payload=payload,
    )
    handler.log_error = lambda *args, **kwargs: None  # noqa: ARG005

    handler.do_POST()

    assert captured["status"] == 200
    assert captured["payload"] == {
        "ok": True,
        "result": {"name": "pipeline.list_runs", "arguments": {"limit": 1}},
    }


def test_mcp_token_returns_oidc_access_token(tmp_path, monkeypatch):
    import time
    from pipeline_mcp import http_server
    from pipeline_mcp.http_server import Handler
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
    exp = int(time.time()) + 1800
    sid = "sess-oidc"
    manager._sessions[sid] = {
        "auth_type": "oidc",
        "user": {"username": "alice", "role": "user"},
        "created_at": int(time.time()),
        "updated_at": int(time.time()),
        "expires_at": exp,
        "oidc": {"access_token": "AT-XYZ", "access_expires_at": exp},
    }

    monkeypatch.setattr(http_server, "_AUTH", None, raising=False)
    monkeypatch.setattr(http_server, "_OIDC", None, raising=False)
    monkeypatch.setattr(http_server, "_SESSIONS", manager, raising=False)

    captured = {}
    handler = Handler.__new__(Handler)
    handler.headers = {"Cookie": f"kbf_session={sid}"}
    handler._json = lambda status, payload, extra_headers=None: captured.update(
        status=status, payload=payload, extra_headers=extra_headers
    )

    handler._send_mcp_token()

    assert captured["status"] == 200
    assert captured["payload"]["ok"] is True
    assert captured["payload"]["token"] == "AT-XYZ"
    assert captured["payload"]["auth_type"] == "oidc"
    assert captured["payload"]["expires_at"] == exp
    assert ("Cache-Control", "no-store") in (captured["extra_headers"] or [])


def test_mcp_token_requires_auth_when_enabled(monkeypatch):
    from pipeline_mcp.http_server import Handler

    captured = {}
    handler = Handler.__new__(Handler)
    handler._auth_enabled = lambda: True
    handler._require_auth = lambda: None  # simulates unauthorized (401 already emitted)
    handler._json = lambda status, payload, extra_headers=None: captured.update(
        status=status, payload=payload
    )

    handler._send_mcp_token()
    assert captured.get("status") != 200


def test_mcp_token_mints_local_token_for_authenticated_user(monkeypatch):
    from pipeline_mcp import http_server
    from pipeline_mcp.http_server import Handler

    class _Auth:
        enabled = True

        def issue_token(self, user):
            return {"token": "LOCAL-TKN", "expires_at": 4242}

    class _Sessions:
        def get_session(self, session_id, *, oidc_settings=None):
            return {"auth_type": "local", "user": {"username": "bob"}}

    monkeypatch.setattr(http_server, "_AUTH", _Auth(), raising=False)
    monkeypatch.setattr(http_server, "_OIDC", None, raising=False)
    monkeypatch.setattr(http_server, "_SESSIONS", _Sessions(), raising=False)

    captured = {}
    handler = Handler.__new__(Handler)
    handler._auth_enabled = lambda: True
    handler._require_auth = lambda: {"username": "bob", "role": "user"}
    handler._session_id_from_cookie = lambda: "sid-local"
    handler._json = lambda status, payload, extra_headers=None: captured.update(
        status=status, payload=payload, extra_headers=extra_headers
    )

    handler._send_mcp_token()

    assert captured["status"] == 200
    assert captured["payload"]["ok"] is True
    assert captured["payload"]["token"] == "LOCAL-TKN"
    assert captured["payload"]["auth_type"] == "local"
    assert captured["payload"]["expires_at"] == 4242
    assert ("Cache-Control", "no-store") in (captured["extra_headers"] or [])


def test_pipeline_skill_archive_contains_skill_md(tmp_path, monkeypatch):
    from pipeline_mcp import http_server
    from pipeline_mcp.http_server import Handler

    skill_dir = tmp_path / "protein-pipeline-stepper"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Protein Pipeline Stepper\n", encoding="utf-8")
    monkeypatch.setenv("PIPELINE_STEPPER_SKILL_DIR", str(skill_dir))

    monkeypatch.setattr(http_server, "_AUTH", None, raising=False)
    monkeypatch.setattr(http_server, "_OIDC", None, raising=False)
    monkeypatch.setattr(http_server, "_SESSIONS", None, raising=False)

    captured = {}
    handler = Handler.__new__(Handler)
    handler.headers = {}
    handler._binary = lambda status, body, content_type, extra_headers=None: captured.update(
        status=status, body=body, content_type=content_type, extra_headers=extra_headers
    )

    handler._send_pipeline_skill_archive()

    assert captured["status"] == 200
    assert captured["content_type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(captured["body"])).namelist()
    assert "protein-pipeline-stepper/SKILL.md" in names


def _pat_store(tmp_path):
    from pipeline_mcp.pat_store import PatStore, PatConfig

    return PatStore(PatConfig(store_path=tmp_path / "mcp_keys.json", default_ttl_days=90))


def test_require_user_accepts_valid_pat(tmp_path, monkeypatch):
    from pipeline_mcp import http_server
    from pipeline_mcp.http_server import Handler

    store = _pat_store(tmp_path)
    created = store.create_key({"username": "alice", "role": "user"}, label="t")

    monkeypatch.setattr(http_server, "_AUTH", None, raising=False)
    monkeypatch.setattr(http_server, "_OIDC", None, raising=False)
    monkeypatch.setattr(http_server, "_SESSIONS", None, raising=False)
    monkeypatch.setattr(http_server, "_PAT_KEYS", store, raising=False)

    handler = Handler.__new__(Handler)
    handler.headers = {"Authorization": f"Bearer {created['token']}"}
    assert handler._require_user() == {"username": "alice", "role": "user", "auth_type": "pat"}


def test_require_user_rejects_bad_pat(tmp_path, monkeypatch):
    from pipeline_mcp import http_server
    from pipeline_mcp.http_server import Handler

    store = _pat_store(tmp_path)
    monkeypatch.setattr(http_server, "_AUTH", None, raising=False)
    monkeypatch.setattr(http_server, "_OIDC", None, raising=False)
    monkeypatch.setattr(http_server, "_SESSIONS", None, raising=False)
    monkeypatch.setattr(http_server, "_PAT_KEYS", store, raising=False)

    handler = Handler.__new__(Handler)
    handler.headers = {"Authorization": "Bearer kbfpat_nope"}
    assert handler._require_user() is None


def test_create_list_revoke_mcp_key(tmp_path, monkeypatch):
    from pipeline_mcp import http_server
    from pipeline_mcp.http_server import Handler

    store = _pat_store(tmp_path)
    monkeypatch.setattr(http_server, "_PAT_KEYS", store, raising=False)

    def make_handler(body):
        captured = {}
        handler = Handler.__new__(Handler)
        handler._auth_enabled = lambda: True
        handler._require_auth = lambda: {"username": "bob", "role": "user"}
        handler._read_json = lambda: body
        handler._json = lambda status, payload, extra_headers=None: captured.update(
            status=status, payload=payload, extra_headers=extra_headers
        )
        return handler, captured

    # create
    h, cap = make_handler({"label": "laptop", "ttl_days": 30})
    h._create_mcp_key()
    assert cap["status"] == 200
    token = cap["payload"]["token"]
    key_id = cap["payload"]["id"]
    assert token.startswith("kbfpat_")
    assert cap["payload"]["label"] == "laptop"
    assert ("Cache-Control", "no-store") in (cap["extra_headers"] or [])
    assert store.verify(token)["username"] == "bob"

    # list (no token leaked)
    h, cap = make_handler({})
    h._send_mcp_keys_list()
    assert cap["status"] == 200
    assert [k["id"] for k in cap["payload"]["keys"]] == [key_id]
    assert "token" not in cap["payload"]["keys"][0]

    # revoke
    h, cap = make_handler({"id": key_id})
    h._revoke_mcp_key()
    assert cap["status"] == 200 and cap["payload"]["ok"] is True
    assert store.verify(token) is None


def test_create_mcp_key_requires_auth_enabled(monkeypatch):
    from pipeline_mcp.http_server import Handler

    captured = {}
    handler = Handler.__new__(Handler)
    handler._auth_enabled = lambda: False
    handler._json = lambda status, payload, extra_headers=None: captured.update(status=status, payload=payload)
    handler._create_mcp_key()
    assert captured["status"] == 400


def test_mcp_tool_list_hides_management_but_keeps_execution(monkeypatch):
    from pipeline_mcp import http_server
    from pipeline_mcp.http_server import Handler

    class _Disp:
        def list_tools(self):
            return {"tools": [{"name": n} for n in [
                "pipeline.run", "pipeline.preflight", "pipeline.status",
                "pipeline.classify_residues", "pipeline.get_hit_list", "pipeline.cancel_run",
                "pipeline.save_project", "pipeline.agent_chat",
                "pipeline.model_provider_list", "pipeline.list_feedback",
                "pipeline.cath_list_jobs", "pipeline.runpod_get_history",
            ]]}

    monkeypatch.setattr(http_server, "_DISPATCHER", _Disp(), raising=False)
    monkeypatch.delenv("PIPELINE_MCP_FULL_TOOL_LIST", raising=False)
    h = Handler.__new__(Handler)
    names = [t["name"] for t in h._list_tools_for_user({"username": "u", "role": "user"})["tools"]]
    # execution + monitor + analyze stay visible
    for keep in ("pipeline.run", "pipeline.preflight", "pipeline.status",
                 "pipeline.classify_residues", "pipeline.get_hit_list", "pipeline.cancel_run"):
        assert keep in names, keep
    # management/UI hidden
    for hide in ("pipeline.save_project", "pipeline.agent_chat",
                 "pipeline.model_provider_list", "pipeline.list_feedback"):
        assert hide not in names, hide
    # admin-only hidden from non-admin
    assert "pipeline.cath_list_jobs" not in names
    assert "pipeline.runpod_get_history" not in names


def test_mcp_full_tool_list_env_shows_everything(monkeypatch):
    from pipeline_mcp import http_server
    from pipeline_mcp.http_server import Handler

    class _Disp:
        def list_tools(self):
            return {"tools": [{"name": n} for n in
                               ["pipeline.run", "pipeline.save_project", "pipeline.cath_list_jobs"]]}

    monkeypatch.setenv("PIPELINE_MCP_FULL_TOOL_LIST", "1")
    monkeypatch.setattr(http_server, "_DISPATCHER", _Disp(), raising=False)
    h = Handler.__new__(Handler)
    names = [t["name"] for t in h._list_tools_for_user({"username": "a", "role": "admin"})["tools"]]
    assert names == ["pipeline.run", "pipeline.save_project", "pipeline.cath_list_jobs"]
