import pytest

from pipeline_mcp import tools
from pipeline_mcp.chat_agent import ChatProviderError


@pytest.fixture(autouse=True)
def _reset_exaone_rate():
    tools._exaone_rate_hits.clear()
    yield
    tools._exaone_rate_hits.clear()


class _Runner:
    output_root = None


def test_chat_send_success(monkeypatch):
    captured = {}
    def fake_run(provider, model, api_key, messages, tool_executor, *, system=None, **kw):
        captured["system"] = system
        captured["executor"] = tool_executor
        return {"reply": "hello", "actions": [{"type": "navigate", "page": "fast"}], "steps": 2}
    monkeypatch.setattr(tools, "run_chat_turn", fake_run)
    out = tools._chat_send_tool(object(), {
        "provider": "openai", "model": "gpt-x", "api_key": "sk",
        "messages": [{"role": "user", "content": "hi"}],
        "context": {"tab": "monitor", "run_id": "r1"},
    })
    assert out["provider"] == "openai" and out["model"] == "gpt-x"
    assert out["reply"] == "hello"
    assert out["actions"] == [{"type": "navigate", "page": "fast"}]
    assert "monitor" in captured["system"] and "r1" in captured["system"]


def test_chat_send_error_shape(monkeypatch):
    def fake_run(*a, **k):
        raise ChatProviderError("auth", "provider rejected the API key")
    monkeypatch.setattr(tools, "run_chat_turn", fake_run)
    out = tools._chat_send_tool(object(), {"provider": "anthropic", "model": "m",
                                           "api_key": "bad", "messages": []})
    assert out == {"error": {"kind": "auth", "message": "provider rejected the API key"},
                   "provider": "anthropic", "saved": []}


def test_chat_send_executor_allowlist(monkeypatch):
    holder = {}
    def fake_run(provider, model, api_key, messages, tool_executor, *, system=None, **kw):
        holder["executor"] = tool_executor
        return {"reply": "", "actions": [], "steps": 1}
    monkeypatch.setattr(tools, "run_chat_turn", fake_run)
    dispatched = {"names": []}
    monkeypatch.setattr(tools.ToolDispatcher, "call_tool",
                        lambda self, name, args: dispatched["names"].append(name) or {"ok": True})
    tools._chat_send_tool(object(), {"provider": "openai", "model": "m", "api_key": "k",
                                     "messages": []})
    executor = holder["executor"]
    # the model sees dot-free wire names; unknown/non-read → refused, never dispatched
    assert executor("pipeline_delete_run", {}) == {"error": "tool not available"}
    # a read wire name maps to the dotted MCP tool name for dispatch
    assert executor("pipeline_status", {"run_id": "r1"}) == {"ok": True}
    assert dispatched["names"] == ["pipeline.status"]


def test_chat_send_allows_empty_key_for_exaone(monkeypatch):
    captured = {}
    def fake_run(provider, model, api_key, messages, tool_executor, *, system=None, **kw):
        captured["api_key"] = api_key
        captured["provider"] = provider
        return {"reply": "hi from exaone", "actions": [], "steps": 1}
    monkeypatch.setattr(tools, "run_chat_turn", fake_run)
    out = tools._chat_send_tool(object(), {
        "provider": "exaone", "model": "", "api_key": "",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert out["reply"] == "hi from exaone"
    assert captured["api_key"] == ""  # empty key passed through, not rejected


def test_chat_send_rate_limits_exaone_per_session(monkeypatch):
    monkeypatch.setenv("LOCAL_LLM_RATE_MAX", "3")
    monkeypatch.setenv("LOCAL_LLM_RATE_WINDOW", "60")
    monkeypatch.setattr(tools, "session_attachment_context", lambda *a, **k: "")
    monkeypatch.setattr(tools, "primary_target_text", lambda *a, **k: "")
    monkeypatch.setattr(tools, "run_chat_turn",
                        lambda *a, **k: {"reply": "ok", "actions": [], "steps": 1})
    args = {"provider": "exaone", "model": "", "api_key": "",
            "messages": [{"role": "user", "content": "hi"}], "session_id": "sess-A"}
    for _ in range(3):
        assert "error" not in tools._chat_send_tool(_Runner(), args)
    limited = tools._chat_send_tool(_Runner(), args)
    assert limited["error"]["kind"] == "upstream"
    assert "Too many requests" in limited["error"]["message"]
    # a different session has its own budget
    other = dict(args, session_id="sess-B")
    assert "error" not in tools._chat_send_tool(_Runner(), other)


def test_chat_send_rate_limit_does_not_affect_keyed_providers(monkeypatch):
    monkeypatch.setenv("LOCAL_LLM_RATE_MAX", "1")
    monkeypatch.setattr(tools, "session_attachment_context", lambda *a, **k: "")
    monkeypatch.setattr(tools, "primary_target_text", lambda *a, **k: "")
    monkeypatch.setattr(tools, "run_chat_turn",
                        lambda *a, **k: {"reply": "ok", "actions": [], "steps": 1})
    args = {"provider": "openai", "model": "gpt-x", "api_key": "sk",
            "messages": [{"role": "user", "content": "hi"}], "session_id": "sess-C"}
    for _ in range(5):
        assert "error" not in tools._chat_send_tool(_Runner(), args)
