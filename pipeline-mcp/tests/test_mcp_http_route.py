from __future__ import annotations

from pipeline_mcp import http_server
from pipeline_mcp.http_server import Handler


class _FakeDispatcher:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def list_tools(self):
        return {
            "tools": [
                {"name": "pipeline.run", "description": "Run pipeline", "inputSchema": {"type": "object"}},
                {
                    "name": "pipeline.runpod_list_endpoints",
                    "description": "Admin tool",
                    "inputSchema": {"type": "object"},
                },
            ]
        }

    def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, dict(arguments)))
        return {"name": name, "arguments": dict(arguments)}


def _make_handler(path: str, payload: dict, captured: dict):
    handler = Handler.__new__(Handler)
    handler.path = path
    handler.headers = {}
    handler._read_json = lambda: payload
    handler._json = lambda code, payload, extra_headers=None: captured.update(
        {"code": code, "payload": payload, "extra_headers": extra_headers}
    )
    return handler


def test_mcp_initialize_returns_capabilities(monkeypatch):
    captured: dict = {}
    dispatcher = _FakeDispatcher()
    handler = _make_handler(
        "/mcp",
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        captured,
    )

    monkeypatch.setattr(http_server, "_DISPATCHER", dispatcher, raising=False)
    monkeypatch.setattr(http_server, "_AUTH", None, raising=False)
    monkeypatch.setattr(http_server, "_OIDC", None, raising=False)
    monkeypatch.setattr(http_server, "_SESSIONS", None, raising=False)

    handler.do_POST()

    assert captured["code"] == 200
    assert captured["payload"]["id"] == 1
    assert captured["payload"]["result"]["capabilities"]["tools"] == {"listChanged": False}


def test_mcp_tools_list_filters_admin_tools_for_non_admin(monkeypatch):
    captured: dict = {}
    dispatcher = _FakeDispatcher()
    handler = _make_handler(
        "/mcp",
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        captured,
    )

    monkeypatch.setattr(http_server, "_DISPATCHER", dispatcher, raising=False)
    monkeypatch.setattr(http_server, "_AUTH", None, raising=False)
    monkeypatch.setattr(http_server, "_OIDC", None, raising=False)
    monkeypatch.setattr(http_server, "_SESSIONS", None, raising=False)
    handler._require_auth = lambda: {"username": "tester", "role": "user", "run_prefix": "tester"}

    handler.do_POST()

    tools = captured["payload"]["result"]["tools"]
    assert [item["name"] for item in tools] == ["pipeline.run"]


def test_mcp_tools_call_injects_run_id_for_non_admin(monkeypatch):
    captured: dict = {}
    dispatcher = _FakeDispatcher()
    handler = _make_handler(
        "/mcp",
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "pipeline.run",
                "arguments": {"target_fasta": ">a\nAAAA"},
            },
        },
        captured,
    )

    monkeypatch.setattr(http_server, "_DISPATCHER", dispatcher, raising=False)
    monkeypatch.setattr(http_server, "_AUTH", None, raising=False)
    monkeypatch.setattr(http_server, "_OIDC", None, raising=False)
    monkeypatch.setattr(http_server, "_SESSIONS", None, raising=False)
    monkeypatch.setattr(http_server, "new_run_id", lambda prefix: f"{prefix}_job")
    handler._require_auth = lambda: {"username": "tester@example.org", "role": "user", "run_prefix": "tester_example.org"}

    handler.do_POST()

    assert captured["code"] == 200
    assert dispatcher.calls == [
        (
            "pipeline.run",
            {
                "target_fasta": ">a\nAAAA",
                "run_id": "tester_example.org_job",
                "user": {
                    "username": "tester@example.org",
                    "role": "user",
                    "run_prefix": "tester_example.org",
                },
            },
        )
    ]
    assert captured["payload"]["result"]["structuredContent"]["arguments"]["run_id"] == "tester_example.org_job"


def test_mcp_tools_call_normalizes_custom_run_id_for_non_admin(monkeypatch):
    captured: dict = {}
    dispatcher = _FakeDispatcher()
    handler = _make_handler(
        "/mcp",
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "pipeline.run",
                "arguments": {"target_fasta": ">a\nAAAA", "run_id": "full_pipeline"},
            },
        },
        captured,
    )

    monkeypatch.setattr(http_server, "_DISPATCHER", dispatcher, raising=False)
    monkeypatch.setattr(http_server, "_AUTH", None, raising=False)
    monkeypatch.setattr(http_server, "_OIDC", None, raising=False)
    monkeypatch.setattr(http_server, "_SESSIONS", None, raising=False)
    handler._require_auth = lambda: {"username": "tester@example.org", "role": "user", "run_prefix": "tester_example.org"}

    handler.do_POST()

    assert captured["code"] == 200
    assert dispatcher.calls == [
        (
            "pipeline.run",
            {
                "target_fasta": ">a\nAAAA",
                "run_id": "tester_example.org_full_pipeline",
                "user": {
                    "username": "tester@example.org",
                    "role": "user",
                    "run_prefix": "tester_example.org",
                },
            },
        )
    ]
    assert (
        captured["payload"]["result"]["structuredContent"]["arguments"]["run_id"]
        == "tester_example.org_full_pipeline"
    )


def test_mcp_tools_call_injects_user_for_pipeline_run(monkeypatch):
    captured: dict = {}
    dispatcher = _FakeDispatcher()
    handler = _make_handler(
        "/mcp",
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "pipeline.run",
                "arguments": {"target_fasta": ">a\nAAAA", "project_id": "tev", "round_id": "round_01"},
            },
        },
        captured,
    )

    monkeypatch.setattr(http_server, "_DISPATCHER", dispatcher, raising=False)
    monkeypatch.setattr(http_server, "_AUTH", None, raising=False)
    monkeypatch.setattr(http_server, "_OIDC", None, raising=False)
    monkeypatch.setattr(http_server, "_SESSIONS", None, raising=False)
    monkeypatch.setattr(http_server, "new_run_id", lambda prefix: f"{prefix}_job")
    handler._require_auth = lambda: {"username": "tester@example.org", "role": "user", "run_prefix": "tester_example.org"}

    handler.do_POST()

    assert captured["code"] == 200
    assert dispatcher.calls == [
        (
            "pipeline.run",
            {
                "target_fasta": ">a\nAAAA",
                "project_id": "tev",
                "round_id": "round_01",
                "run_id": "tester_example.org_job",
                "user": {
                    "username": "tester@example.org",
                    "role": "user",
                    "run_prefix": "tester_example.org",
                },
            },
        )
    ]


def test_mcp_tools_call_injects_user_for_pipeline_preflight(monkeypatch):
    captured: dict = {}
    dispatcher = _FakeDispatcher()
    handler = _make_handler(
        "/mcp",
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "pipeline.preflight",
                "arguments": {"target_fasta": ">a\nAAAA", "project_id": "tev", "round_id": "round_01"},
            },
        },
        captured,
    )

    monkeypatch.setattr(http_server, "_DISPATCHER", dispatcher, raising=False)
    monkeypatch.setattr(http_server, "_AUTH", None, raising=False)
    monkeypatch.setattr(http_server, "_OIDC", None, raising=False)
    monkeypatch.setattr(http_server, "_SESSIONS", None, raising=False)
    handler._require_auth = lambda: {"username": "tester@example.org", "role": "user", "run_prefix": "tester_example.org"}

    handler.do_POST()

    assert captured["code"] == 200
    assert dispatcher.calls == [
        (
            "pipeline.preflight",
            {
                "target_fasta": ">a\nAAAA",
                "project_id": "tev",
                "round_id": "round_01",
                "user": {
                    "username": "tester@example.org",
                    "role": "user",
                    "run_prefix": "tester_example.org",
                },
            },
        )
    ]


def test_mcp_tools_call_injects_user_for_round_delete_archive_tools(monkeypatch):
    captured: dict = {}
    dispatcher = _FakeDispatcher()
    handler = _make_handler(
        "/mcp",
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "pipeline.delete_round",
                "arguments": {"project_id": "tev", "round_id": "round_01"},
            },
        },
        captured,
    )

    monkeypatch.setattr(http_server, "_DISPATCHER", dispatcher, raising=False)
    monkeypatch.setattr(http_server, "_AUTH", None, raising=False)
    monkeypatch.setattr(http_server, "_OIDC", None, raising=False)
    monkeypatch.setattr(http_server, "_SESSIONS", None, raising=False)
    handler._require_auth = lambda: {"username": "tester@example.org", "role": "user", "run_prefix": "tester_example.org"}

    handler.do_POST()

    assert captured["code"] == 200
    assert dispatcher.calls == [
        (
            "pipeline.delete_round",
            {
                "project_id": "tev",
                "round_id": "round_01",
                "user": {
                    "username": "tester@example.org",
                    "role": "user",
                    "run_prefix": "tester_example.org",
                },
            },
        )
    ]


def test_mcp_unknown_method_returns_jsonrpc_error(monkeypatch):
    captured: dict = {}
    dispatcher = _FakeDispatcher()
    handler = _make_handler(
        "/mcp",
        {"jsonrpc": "2.0", "id": 4, "method": "unknown/method", "params": {}},
        captured,
    )

    monkeypatch.setattr(http_server, "_DISPATCHER", dispatcher, raising=False)
    monkeypatch.setattr(http_server, "_AUTH", None, raising=False)
    monkeypatch.setattr(http_server, "_OIDC", None, raising=False)
    monkeypatch.setattr(http_server, "_SESSIONS", None, raising=False)

    handler.do_POST()

    assert captured["code"] == 200
    assert captured["payload"]["error"]["code"] == -32601


def test_mcp_tools_call_injects_user_for_round_restore_tools(monkeypatch):
    captured: dict = {}
    dispatcher = _FakeDispatcher()
    handler = _make_handler(
        "/mcp",
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {
                "name": "pipeline.restore_round",
                "arguments": {"project_id": "tev", "round_id": "round_01"},
            },
        },
        captured,
    )

    monkeypatch.setattr(http_server, "_DISPATCHER", dispatcher, raising=False)
    monkeypatch.setattr(http_server, "_AUTH", None, raising=False)
    monkeypatch.setattr(http_server, "_OIDC", None, raising=False)
    monkeypatch.setattr(http_server, "_SESSIONS", None, raising=False)
    handler._require_auth = lambda: {"username": "tester@example.org", "role": "user", "run_prefix": "tester_example.org"}

    handler.do_POST()

    assert captured["code"] == 200
    assert dispatcher.calls == [
        (
            "pipeline.restore_round",
            {
                "project_id": "tev",
                "round_id": "round_01",
                "user": {
                    "username": "tester@example.org",
                    "role": "user",
                    "run_prefix": "tester_example.org",
                },
            },
        )
    ]


def test_attach_run_ui_url_injects_deeplink():
    """Run-producing tool results carry a ui_url deep-link to the web view."""
    handler = Handler.__new__(Handler)
    handler.headers = {"Host": "rapid.kbiofoundry.kr", "X-Forwarded-Proto": "https"}
    out = {"run_id": "tester_job", "state": "running"}
    handler._attach_run_ui_url("pipeline.run", out)
    assert out["ui_url"] == "https://rapid.kbiofoundry.kr/?run=tester_job"


def test_attach_run_ui_url_uses_head_run_id_fallback():
    handler = Handler.__new__(Handler)
    handler.headers = {"Host": "dev-pipeline.duckdns.org", "X-Forwarded-Proto": "https"}
    out = {"head_run_id": "u_head"}
    handler._attach_run_ui_url("pipeline.status", out)
    assert out["ui_url"] == "https://dev-pipeline.duckdns.org/?run=u_head"


def test_attach_run_ui_url_skips_non_run_tools():
    handler = Handler.__new__(Handler)
    handler.headers = {"Host": "rapid.kbiofoundry.kr", "X-Forwarded-Proto": "https"}
    out = {"run_id": "a"}
    handler._attach_run_ui_url("pipeline.read_artifact", out)
    assert "ui_url" not in out


def test_attach_run_ui_url_noop_without_run_id():
    handler = Handler.__new__(Handler)
    handler.headers = {"Host": "rapid.kbiofoundry.kr", "X-Forwarded-Proto": "https"}
    out = {"ok": True}
    handler._attach_run_ui_url("pipeline.run", out)
    assert "ui_url" not in out


def test_public_base_url_env_override(monkeypatch):
    handler = Handler.__new__(Handler)
    handler.headers = {"Host": "internal:8080"}
    monkeypatch.setenv("PIPELINE_PUBLIC_BASE_URL", "https://rapid.kbiofoundry.kr/")
    assert handler._public_base_url() == "https://rapid.kbiofoundry.kr"


def test_handler_speaks_http_1_1_for_expect_100_continue():
    """The handler must be HTTP/1.1 so it answers `Expect: 100-continue`. An
    HTTP/1.0 server never sends the 100 interim response, deadlocking large
    request-body uploads (e.g. a raw PDB as target_pdb) behind the proxy."""
    from pipeline_mcp.http_server import Handler

    assert Handler.protocol_version == "HTTP/1.1"
    # idle keep-alive connections must be reaped so handler threads don't pile up
    assert getattr(Handler, "timeout", None)


def test_advertised_tool_schemas_are_strict_client_safe():
    """Strict function-calling clients (OpenAI strict mode, some MCP clients)
    require each tool's top-level inputSchema to be type:object with no
    top-level oneOf/anyOf/allOf/not. list_tools() must not advertise those."""
    from pipeline_mcp.tools import sanitize_tool_for_strict_clients, tool_definitions

    forbidden = {"oneOf", "anyOf", "allOf", "not"}
    advertised = [sanitize_tool_for_strict_clients(t) for t in tool_definitions()]
    offenders = {
        t["name"]: sorted(forbidden & set(t["inputSchema"].keys()))
        for t in advertised
        if t["inputSchema"].get("type") != "object"
        or (forbidden & set(t["inputSchema"].keys()))
    }
    assert offenders == {}, f"top-level forbidden schema keys: {offenders}"


def test_sanitizer_folds_anyof_required_into_description():
    from pipeline_mcp.tools import sanitize_tool_for_strict_clients

    tool = {
        "name": "pipeline.af2_predict",
        "description": "Run AF2.",
        "inputSchema": {
            "type": "object",
            "properties": {"target_fasta": {"type": "string"}, "target_pdb": {"type": "string"}},
            "anyOf": [{"required": ["target_fasta"]}, {"required": ["target_pdb"]}],
        },
    }
    out = sanitize_tool_for_strict_clients(tool)
    assert "anyOf" not in out["inputSchema"]
    assert "Provide at least one of: target_fasta, target_pdb." in out["description"]
    # nested property schemas are untouched
    assert out["inputSchema"]["properties"]["target_fasta"] == {"type": "string"}


def test_mcp_tool_result_uses_standard_mcp_content_type():
    """tools/call results must use a standard MCP content type (text), not the
    non-standard type:'json' that strict clients (VS Code, mcp SDK) reject."""
    import json as _json

    from pipeline_mcp.http_server import Handler

    handler = Handler.__new__(Handler)
    payload = {"ok": True, "run_id": "x", "n": 2}
    out = handler._mcp_tool_result(payload)

    block = out["content"][0]
    assert block["type"] == "text"
    assert "json" not in block  # the invalid {"type":"json","json":...} shape is gone
    assert _json.loads(block["text"]) == payload  # serialized JSON in the text block
    assert out["structuredContent"] == payload  # machine-readable structured result
    assert out["isError"] is False
