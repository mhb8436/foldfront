import pytest
from pipeline_mcp import chat_agent as ca
from pipeline_mcp.chat_agent import run_chat_turn


def _fake_complete(monkeypatch, scripted):
    seq = list(scripted)
    calls = {"msgs": []}
    def fake(provider, model, api_key, messages, tools, *, system=None, timeout=60.0):
        calls["msgs"].append([dict(m) for m in messages])
        return seq.pop(0)
    monkeypatch.setattr(ca, "_complete", fake)
    return calls


def test_read_tool_round_trip(monkeypatch):
    _fake_complete(monkeypatch, [
        {"text": "", "tool_calls": [{"id": "t1", "name": "pipeline_status", "args": {"run_id": "r1"}}]},
        {"text": "Run r1 is running at the design stage.", "tool_calls": []},
    ])
    seen = {}
    def executor(name, args):
        seen["name"] = name; seen["args"] = args
        return {"state": "running", "stage": "design"}
    out = run_chat_turn("anthropic", "m", "k", [{"role": "user", "content": "status?"}], executor)
    assert out["reply"] == "Run r1 is running at the design stage."
    assert out["actions"] == []
    assert seen == {"name": "pipeline_status", "args": {"run_id": "r1"}}


def test_navigate_collects_action_and_stops(monkeypatch):
    _fake_complete(monkeypatch, [
        {"text": "Taking you to the Fast page.",
         "tool_calls": [{"id": "n1", "name": "navigate", "args": {"page": "fast"}}]},
        {"text": "Now on Fast. Click Run Fast to start.", "tool_calls": []},
    ])
    called = {"n": 0}
    def executor(name, args):
        called["n"] += 1
        return {}
    out = run_chat_turn("openai", "m", "k", [{"role": "user", "content": "start a run"}], executor)
    assert out["actions"] == [{"type": "navigate", "page": "fast"}]
    assert out["reply"] == "Now on Fast. Click Run Fast to start."
    assert called["n"] == 0


def test_navigate_invalid_page_coerced_home(monkeypatch):
    _fake_complete(monkeypatch, [
        {"text": "ok", "tool_calls": [{"id": "n1", "name": "navigate", "args": {"page": "bogus"}}]},
        {"text": "done", "tool_calls": []},
    ])
    out = run_chat_turn("gemini", "m", "k", [{"role": "user", "content": "go"}], lambda n, a: {})
    assert out["actions"] == [{"type": "navigate", "page": "home"}]


def test_unknown_tool_feeds_error_and_continues(monkeypatch):
    _fake_complete(monkeypatch, [
        {"text": "", "tool_calls": [{"id": "x1", "name": "pipeline_delete_run", "args": {}}]},
        {"text": "I cannot do that.", "tool_calls": []},
    ])
    called = {"n": 0}
    def executor(name, args):
        called["n"] += 1
        return {}
    out = run_chat_turn("anthropic", "m", "k", [{"role": "user", "content": "delete"}], executor)
    assert out["reply"] == "I cannot do that."
    assert called["n"] == 0


def test_max_steps_cap(monkeypatch):
    _fake_complete(monkeypatch, [
        {"text": "", "tool_calls": [{"id": "t", "name": "pipeline_status", "args": {}}]}
    ] * 10)
    out = run_chat_turn("openai", "m", "k", [{"role": "user", "content": "loop"}],
                        lambda n, a: {"state": "x"}, max_steps=3)
    assert out["steps"] == 3
    assert isinstance(out["reply"], str)


def test_tool_specs_shape():
    specs = ca.tool_specs()
    names = {s["name"] for s in specs}
    assert "navigate" in names
    assert "pipeline_status" in names
    for s in specs:
        assert set(s) >= {"name", "description", "parameters"}


def test_tool_names_have_no_dots():
    # Anthropic and OpenAI reject tool/function names containing "." (400).
    for s in ca.tool_specs():
        assert "." not in s["name"], f"tool name must be dot-free for providers: {s['name']}"
    for name in ca.READ_TOOLS:
        assert "." not in name


def test_navigate_passes_prefill_through(monkeypatch):
    _fake_complete(monkeypatch, [
        {"text": "Opening Fast with your file.",
         "tool_calls": [{"id": "n1", "name": "navigate",
                         "args": {"page": "fast", "prefill": {"attachment": "seq.fasta"}}}]},
        {"text": "Opened Fast with your target.", "tool_calls": []},
    ])
    out = run_chat_turn("openai", "m", "k", [{"role": "user", "content": "run this"}], lambda n, a: {})
    assert out["actions"] == [{"type": "navigate", "page": "fast",
                               "prefill": {"attachment": "seq.fasta"}}]


def test_navigate_without_prefill_has_no_key(monkeypatch):
    _fake_complete(monkeypatch, [
        {"text": "ok", "tool_calls": [{"id": "n1", "name": "navigate", "args": {"page": "monitor"}}]},
        {"text": "done", "tool_calls": []},
    ])
    out = run_chat_turn("openai", "m", "k", [{"role": "user", "content": "go"}], lambda n, a: {})
    assert out["actions"] == [{"type": "navigate", "page": "monitor"}]


def test_configure_advanced_collects_action(monkeypatch):
    _fake_complete(monkeypatch, [
        {"text": "Filled recommended values on Advanced.",
         "tool_calls": [{"id": "c1", "name": "configure_advanced",
                         "args": {"answers": {"num_seq_per_tier": 4, "bioemu_use": True},
                                  "prefill": {"attachment": "4KL5.pdb"}}}]},
        {"text": "Set num_seq_per_tier=4 and enabled BioEmu.", "tool_calls": []},
    ])
    out = run_chat_turn("openai", "m", "k", [{"role": "user", "content": "fill params"}], lambda n, a: {})
    assert out["actions"] == [{"type": "configure",
                               "answers": {"num_seq_per_tier": 4, "bioemu_use": True},
                               "prefill": {"attachment": "4KL5.pdb"}}]


def test_configure_advanced_in_tool_specs():
    names = {s["name"] for s in ca.tool_specs()}
    assert "configure_advanced" in names


def test_client_action_returns_followup_explanation(monkeypatch):
    _fake_complete(monkeypatch, [
        {"text": "I will set them.",
         "tool_calls": [{"id": "c1", "name": "configure_advanced",
                         "args": {"answers": {"num_seq_per_tier": 4}}}]},
        {"text": "num_seq_per_tier=4 balances diversity and cost.", "tool_calls": []},
    ])
    out = run_chat_turn("openai", "m", "k", [{"role": "user", "content": "fill"}], lambda n, a: {})
    assert out["actions"] == [{"type": "configure", "answers": {"num_seq_per_tier": 4}}]
    assert out["reply"] == "num_seq_per_tier=4 balances diversity and cost."
    assert out["steps"] == 2


def test_run_pipeline_collects_action(monkeypatch):
    _fake_complete(monkeypatch, [
        {"text": "Ready — press Run now.",
         "tool_calls": [{"id": "r1", "name": "run_pipeline", "args": {}}]},
        {"text": "The Run now button is ready.", "tool_calls": []},
    ])
    out = run_chat_turn("openai", "m", "k", [{"role": "user", "content": "run it"}], lambda n, a: {})
    assert {"type": "run"} in out["actions"]


def test_run_pipeline_in_tool_specs():
    assert "run_pipeline" in {s["name"] for s in ca.tool_specs()}


def test_run_chat_turn_routes_exaone_no_key(monkeypatch):
    # End-to-end through the real _complete: exaone must reach the local endpoint
    # with NO Authorization header and no api_key, and parse tool_calls.
    class FakeResp:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {
                "content": "<think>reasoning</think>Run r1 is running.",
                "tool_calls": [{"id": "c1", "type": "function",
                                "function": {"name": "pipeline_status",
                                             "arguments": "{\"run_id\": \"r1\"}"}}],
            }}]}
    cap = {}
    def fake_post(url, headers=None, json=None, timeout=None):
        cap.setdefault("urls", []).append(url)
        cap.setdefault("headers", []).append(headers or {})
        # second call: model replies without tool calls to end the loop
        if len(cap["urls"]) >= 2:
            class Done:
                status_code = 200
                def json(self):
                    return {"choices": [{"message": {"content": "Run r1 is running."}}]}
            return Done()
        return FakeResp()
    monkeypatch.setattr(ca.requests, "post", fake_post)
    monkeypatch.setenv("LOCAL_LLM_URL", "http://local-host:8000/v1")
    seen = {}
    def executor(name, args):
        seen["name"] = name; seen["args"] = args
        return {"state": "running"}
    out = run_chat_turn("exaone", "", "", [{"role": "user", "content": "status?"}], executor)
    assert out["reply"] == "Run r1 is running."
    assert seen == {"name": "pipeline_status", "args": {"run_id": "r1"}}
    assert all(u == "http://local-host:8000/v1/chat/completions" for u in cap["urls"])
    assert all("Authorization" not in h for h in cap["headers"])


def test_configure_and_run_in_one_turn(monkeypatch):
    _fake_complete(monkeypatch, [
        {"text": "Setting up and offering Run.",
         "tool_calls": [
             {"id": "c1", "name": "configure_advanced", "args": {"answers": {"num_seq_per_tier": 8}}},
             {"id": "r1", "name": "run_pipeline", "args": {}},
         ]},
        {"text": "Ready. Press Run now.", "tool_calls": []},
    ])
    out = run_chat_turn("openai", "m", "k", [{"role": "user", "content": "fill and run"}], lambda n, a: {})
    assert {"type": "configure", "answers": {"num_seq_per_tier": 8}} in out["actions"]
    assert {"type": "run"} in out["actions"]
    assert out["reply"] == "Ready. Press Run now."
