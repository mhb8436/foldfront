import json
from pipeline_mcp import chat_agent as ca


class FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
    def json(self):
        return self._payload


def _capture_post(monkeypatch, resp):
    cap = {}
    def fake_post(url, headers=None, json=None, timeout=None):
        cap["url"] = url; cap["headers"] = headers or {}; cap["body"] = json
        return resp
    monkeypatch.setattr(ca.requests, "post", fake_post)
    return cap


def test_anthropic_builds_request_and_parses(monkeypatch):
    cap = _capture_post(monkeypatch, FakeResp(200, {"content": [
        {"type": "text", "text": "hi"},
        {"type": "tool_use", "id": "tu1", "name": "pipeline.status", "input": {"run_id": "r1"}},
    ]}))
    msgs = [{"role": "user", "content": "status?"}]
    out = ca._anthropic_complete("claude-x", "sk-ant", msgs, ca.tool_specs(), "SYS", 60.0)
    assert out["text"] == "hi"
    assert out["tool_calls"] == [{"id": "tu1", "name": "pipeline.status", "args": {"run_id": "r1"}}]
    assert cap["url"] == "https://api.anthropic.com/v1/messages"
    assert cap["headers"].get("x-api-key") == "sk-ant"
    assert cap["headers"].get("anthropic-version") == "2023-06-01"
    assert cap["body"]["system"] == "SYS"
    assert any(t["name"] == "navigate" for t in cap["body"]["tools"])
    assert cap["body"]["tools"][0]["input_schema"]


def test_anthropic_serializes_tool_turns(monkeypatch):
    cap = _capture_post(monkeypatch, FakeResp(200, {"content": [{"type": "text", "text": "done"}]}))
    msgs = [
        {"role": "user", "content": "status?"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "tu1", "name": "pipeline.status", "args": {}}]},
        {"role": "tool", "tool_call_id": "tu1", "name": "pipeline.status", "content": {"state": "running"}},
    ]
    ca._anthropic_complete("m", "k", msgs, ca.tool_specs(), None, 60.0)
    body_msgs = cap["body"]["messages"]
    assert body_msgs[1]["content"][0]["type"] == "tool_use"
    tr = body_msgs[2]["content"][0]
    assert tr["type"] == "tool_result" and tr["tool_use_id"] == "tu1"
    assert json.loads(tr["content"]) == {"state": "running"}


def test_openai_builds_request_and_parses(monkeypatch):
    cap = _capture_post(monkeypatch, FakeResp(200, {"choices": [{"message": {
        "content": "hello",
        "tool_calls": [{"id": "c1", "type": "function",
                        "function": {"name": "pipeline.queue_eta",
                                     "arguments": "{\"run_id\": \"r2\"}"}}],
    }}]}))
    out = ca._openai_complete("gpt-x", "sk", [{"role": "user", "content": "eta?"}],
                              ca.tool_specs(), "SYS", 60.0)
    assert out["text"] == "hello"
    assert out["tool_calls"] == [{"id": "c1", "name": "pipeline.queue_eta", "args": {"run_id": "r2"}}]
    assert cap["url"] == "https://api.openai.com/v1/chat/completions"
    assert cap["headers"].get("Authorization") == "Bearer sk"
    assert cap["body"]["messages"][0] == {"role": "system", "content": "SYS"}
    assert cap["body"]["tools"][0]["type"] == "function"


def test_openai_serializes_tool_turns(monkeypatch):
    cap = _capture_post(monkeypatch, FakeResp(200, {"choices": [{"message": {"content": "done"}}]}))
    msgs = [
        {"role": "user", "content": "eta?"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "pipeline.queue_eta", "args": {}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "pipeline.queue_eta", "content": {"wait_s": 30}},
    ]
    out = ca._openai_complete("m", "k", msgs, ca.tool_specs(), None, 60.0)
    assert out["text"] == "done"
    body_msgs = cap["body"]["messages"]
    assert body_msgs[-1]["role"] == "tool" and body_msgs[-1]["tool_call_id"] == "c1"
    assert body_msgs[-2]["tool_calls"][0]["function"]["name"] == "pipeline.queue_eta"


def test_gemini_builds_request_and_parses(monkeypatch):
    cap = _capture_post(monkeypatch, FakeResp(200, {"candidates": [{"content": {"parts": [
        {"text": "sure"},
        {"functionCall": {"name": "navigate", "args": {"page": "fast"}}},
    ]}}]}))
    out = ca._gemini_complete("gemini-x", "gk", [{"role": "user", "content": "run"}],
                              ca.tool_specs(), "SYS", 60.0)
    assert out["text"] == "sure"
    assert out["tool_calls"][0]["name"] == "navigate"
    assert out["tool_calls"][0]["args"] == {"page": "fast"}
    assert cap["url"].startswith("https://generativelanguage.googleapis.com/v1beta/models/gemini-x:generateContent?key=")
    assert cap["body"]["system_instruction"]["parts"][0]["text"] == "SYS"
    assert cap["body"]["tools"][0]["function_declarations"]


def test_gemini_serializes_tool_turns(monkeypatch):
    cap = _capture_post(monkeypatch, FakeResp(200, {"candidates": [{"content": {"parts": [{"text": "done"}]}}]}))
    msgs = [
        {"role": "user", "content": "status"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "pipeline.status-0", "name": "pipeline.status", "args": {}}]},
        {"role": "tool", "tool_call_id": "pipeline.status-0", "name": "pipeline.status", "content": {"state": "done"}},
    ]
    out = ca._gemini_complete("m", "k", msgs, ca.tool_specs(), None, 60.0)
    assert out["text"] == "done"
    contents = cap["body"]["contents"]
    assert contents[1]["role"] == "model"
    assert contents[1]["parts"][0]["functionCall"]["name"] == "pipeline.status"
    fr = contents[2]["parts"][0]["functionResponse"]
    assert fr["name"] == "pipeline.status" and fr["response"] == {"result": {"state": "done"}}


def test_anthropic_skips_empty_assistant_turn(monkeypatch):
    cap = _capture_post(monkeypatch, FakeResp(200, {"content": [{"type": "text", "text": "ok"}]}))
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": ""},          # empty, no tool_calls
        {"role": "user", "content": "still there?"},
    ]
    ca._anthropic_complete("m", "k", msgs, ca.tool_specs(), None, 60.0)
    roles = [m["role"] for m in cap["body"]["messages"]]
    assert roles == ["user", "user"]                    # empty assistant dropped
    for m in cap["body"]["messages"]:
        for block in m["content"]:
            if block.get("type") == "text":
                assert block["text"] != ""              # no empty text block


def test_gemini_skips_empty_assistant_turn(monkeypatch):
    cap = _capture_post(monkeypatch, FakeResp(200, {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}))
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": "again"},
    ]
    ca._gemini_complete("m", "k", msgs, ca.tool_specs(), None, 60.0)
    roles = [c["role"] for c in cap["body"]["contents"]]
    assert roles == ["user", "user"]


def test_openai_skips_empty_assistant_turn(monkeypatch):
    cap = _capture_post(monkeypatch, FakeResp(200, {"choices": [{"message": {"content": "ok"}}]}))
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": "again"},
    ]
    ca._openai_complete("m", "k", msgs, ca.tool_specs(), None, 60.0)
    roles = [m["role"] for m in cap["body"]["messages"] if m["role"] != "system"]
    assert roles == ["user", "user"]


def test_exaone_routes_to_local_endpoint_no_auth(monkeypatch):
    cap = _capture_post(monkeypatch, FakeResp(200, {"choices": [{"message": {
        "content": "hi there",
        "tool_calls": [{"id": "c1", "type": "function",
                        "function": {"name": "pipeline.status",
                                     "arguments": "{\"run_id\": \"r9\"}"}}],
    }}]}))
    monkeypatch.setenv("LOCAL_LLM_URL", "http://local-host:8000/v1")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "EXAONE-TEST")
    # No api_key is required for exaone; _complete must route before the key check.
    out = ca._complete("exaone", "", "ignored-key", [{"role": "user", "content": "status?"}],
                       ca.tool_specs(), system="SYS", timeout=60.0)
    assert out["text"] == "hi there"
    assert out["tool_calls"] == [{"id": "c1", "name": "pipeline.status", "args": {"run_id": "r9"}}]
    assert cap["url"] == "http://local-host:8000/v1/chat/completions"
    # keyless: NO Authorization header ever sent to the local endpoint
    assert "Authorization" not in cap["headers"]
    assert cap["body"]["model"] == "EXAONE-TEST"


def test_exaone_defaults_model_when_unset(monkeypatch):
    cap = _capture_post(monkeypatch, FakeResp(200, {"choices": [{"message": {"content": "ok"}}]}))
    monkeypatch.delenv("LOCAL_LLM_URL", raising=False)
    monkeypatch.delenv("LOCAL_LLM_MODEL", raising=False)
    out = ca._exaone_complete("", [{"role": "user", "content": "hi"}], ca.tool_specs(), None, 60.0)
    assert out["text"] == "ok"
    assert cap["url"] == ca._LOCAL_LLM_DEFAULT.rstrip("/") + "/chat/completions"
    assert cap["body"]["model"] == ca._LOCAL_LLM_MODEL


def test_exaone_strips_think_chain_of_thought(monkeypatch):
    _capture_post(monkeypatch, FakeResp(200, {"choices": [{"message": {
        "content": "<think>let me reason\nabout this</think>The answer is 42.",
    }}]}))
    out = ca._exaone_complete("m", [{"role": "user", "content": "q"}], ca.tool_specs(), None, 60.0)
    assert out["text"] == "The answer is 42."
    assert "<think>" not in out["text"] and "reason" not in out["text"]


def test_openai_style_complete_sends_auth_only_with_key(monkeypatch):
    cap = _capture_post(monkeypatch, FakeResp(200, {"choices": [{"message": {"content": "x"}}]}))
    ca._openai_style_complete("m", "", [{"role": "user", "content": "hi"}],
                              ca.tool_specs(), None, 60.0, base_url="http://h/v1")
    assert "Authorization" not in cap["headers"]
    ca._openai_style_complete("m", "sk", [{"role": "user", "content": "hi"}],
                              ca.tool_specs(), None, 60.0, base_url="http://h/v1")
    assert cap["headers"].get("Authorization") == "Bearer sk"


def test_post_json_surfaces_provider_error_body(monkeypatch):
    import pytest
    from pipeline_mcp.chat_agent import ChatProviderError, _post_json

    class R:
        status_code = 400
        text = "tools[0].function.name does not match '^[a-zA-Z0-9_-]+$'"
        def json(self):
            return {}

    monkeypatch.setattr(ca.requests, "post", lambda *a, **k: R())
    with pytest.raises(ChatProviderError) as exc:
        _post_json("https://api.example.com", {}, {}, 5.0)
    assert exc.value.kind == "upstream"
    assert "does not match" in exc.value.message  # provider reason surfaced
