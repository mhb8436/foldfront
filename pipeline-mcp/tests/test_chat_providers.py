import pytest

from pipeline_mcp import chat_providers as cp
from pipeline_mcp.chat_providers import ChatProviderError, list_chat_models


class FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


def _patch_get(monkeypatch, resp, capture=None):
    def fake_get(url, headers=None, timeout=None):
        if capture is not None:
            capture["url"] = url
            capture["headers"] = headers or {}
        if isinstance(resp, Exception):
            raise resp
        return resp
    monkeypatch.setattr(cp.requests, "get", fake_get)


def _patch_get_seq(monkeypatch, responses):
    """Return responses[0], responses[1], ... on successive calls; record urls."""
    calls = {"urls": []}
    seq = list(responses)
    def fake_get(url, headers=None, timeout=None):
        calls["urls"].append(url)
        resp = seq.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp
    monkeypatch.setattr(cp.requests, "get", fake_get)
    return calls


def test_anthropic_maps_id_and_display_name(monkeypatch):
    cap = {}
    _patch_get(monkeypatch, FakeResp(200, {"data": [
        {"id": "claude-opus-4-8", "display_name": "Claude Opus 4.8"},
        {"id": "claude-haiku-4-5", "display_name": "Claude Haiku 4.5"},
    ]}), cap)
    models = list_chat_models("anthropic", "sk-ant-xxx")
    assert {"id": "claude-opus-4-8", "label": "Claude Opus 4.8"} in models
    assert cap["headers"].get("anthropic-version") == "2023-06-01"
    assert cap["headers"].get("x-api-key") == "sk-ant-xxx"
    assert [m["id"] for m in models] == sorted(m["id"] for m in models)


def test_openai_filters_non_chat(monkeypatch):
    _patch_get(monkeypatch, FakeResp(200, {"data": [
        {"id": "gpt-4o"},
        {"id": "text-embedding-3-large"},
        {"id": "whisper-1"},
        {"id": "dall-e-3"},
    ]}))
    ids = [m["id"] for m in list_chat_models("openai", "sk-xxx")]
    assert ids == ["gpt-4o"]


def test_gemini_keeps_only_generate_content_and_strips_prefix(monkeypatch):
    _patch_get(monkeypatch, FakeResp(200, {"models": [
        {"name": "models/gemini-2.5-pro", "displayName": "Gemini 2.5 Pro",
         "supportedGenerationMethods": ["generateContent", "countTokens"]},
        {"name": "models/embedding-001", "displayName": "Embedding",
         "supportedGenerationMethods": ["embedContent"]},
    ]}))
    models = list_chat_models("gemini", "key-xxx")
    assert models == [{"id": "gemini-2.5-pro", "label": "Gemini 2.5 Pro"}]


def test_auth_error_on_401(monkeypatch):
    _patch_get(monkeypatch, FakeResp(401, {}))
    with pytest.raises(ChatProviderError) as exc:
        list_chat_models("anthropic", "bad")
    assert exc.value.kind == "auth"


def test_upstream_error_on_network_failure(monkeypatch):
    _patch_get(monkeypatch, cp.requests.RequestException("boom"))
    with pytest.raises(ChatProviderError) as exc:
        list_chat_models("openai", "sk-xxx")
    assert exc.value.kind == "upstream"


def test_unknown_provider_no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("should not call network")
    monkeypatch.setattr(cp.requests, "get", boom)
    with pytest.raises(ChatProviderError) as exc:
        list_chat_models("mistral", "k")
    assert exc.value.kind == "unknown_provider"


def test_empty_key_no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("should not call network")
    monkeypatch.setattr(cp.requests, "get", boom)
    with pytest.raises(ChatProviderError) as exc:
        list_chat_models("anthropic", "  ")
    assert exc.value.kind == "auth"


def test_provider_aliases(monkeypatch):
    _patch_get(monkeypatch, FakeResp(200, {"data": [{"id": "gpt-4o"}]}))
    assert list_chat_models("codex", "sk")[0]["id"] == "gpt-4o"


def test_gemini_network_error_does_not_leak_key(monkeypatch):
    secret = "AIzaSecretKey123"
    _patch_get(monkeypatch, cp.requests.RequestException(
        f"HTTPSConnectionPool: Max retries exceeded with url: "
        f"/v1beta/models?key={secret} (Caused by ...)"))
    with pytest.raises(ChatProviderError) as exc:
        list_chat_models("gemini", secret)
    assert exc.value.kind == "upstream"
    assert secret not in exc.value.message
    assert secret not in str(exc.value)


def test_anthropic_paginates(monkeypatch):
    calls = _patch_get_seq(monkeypatch, [
        FakeResp(200, {"data": [{"id": "a", "display_name": "A"}],
                       "has_more": True, "last_id": "a"}),
        FakeResp(200, {"data": [{"id": "b", "display_name": "B"}],
                       "has_more": False}),
    ])
    ids = [m["id"] for m in list_chat_models("anthropic", "k")]
    assert ids == ["a", "b"]
    assert any("after_id=a" in u for u in calls["urls"])
    assert calls["urls"][0].startswith("https://api.anthropic.com/v1/models")


def test_gemini_paginates(monkeypatch):
    calls = _patch_get_seq(monkeypatch, [
        FakeResp(200, {"models": [{"name": "models/g1", "displayName": "G1",
                       "supportedGenerationMethods": ["generateContent"]}],
                       "nextPageToken": "tok"}),
        FakeResp(200, {"models": [{"name": "models/g2", "displayName": "G2",
                       "supportedGenerationMethods": ["generateContent"]}]}),
    ])
    ids = [m["id"] for m in list_chat_models("gemini", "k")]
    assert ids == ["g1", "g2"]
    assert any("pageToken=tok" in u for u in calls["urls"])


def test_dedup_by_id(monkeypatch):
    _patch_get(monkeypatch, FakeResp(200, {"data": [
        {"id": "gpt-4o"}, {"id": "gpt-4o"},
    ]}))
    assert [m["id"] for m in list_chat_models("openai", "k")] == ["gpt-4o"]


def test_label_falls_back_to_id(monkeypatch):
    _patch_get(monkeypatch, FakeResp(200, {"data": [{"id": "claude-x"}]}))
    assert list_chat_models("anthropic", "k") == [{"id": "claude-x", "label": "claude-x"}]


def test_exaone_lists_served_models_without_key(monkeypatch):
    cap = {}
    monkeypatch.setenv("LOCAL_LLM_URL", "http://local-host:8000/v1")
    _patch_get(monkeypatch, FakeResp(200, {"data": [
        {"id": "LGAI-EXAONE/EXAONE-4.5-33B-AWQ"},
    ]}), cap)
    # empty api_key is fine for exaone (must NOT raise auth)
    models = list_chat_models("exaone", "")
    assert models == [{"id": "LGAI-EXAONE/EXAONE-4.5-33B-AWQ",
                       "label": "LGAI-EXAONE/EXAONE-4.5-33B-AWQ"}]
    assert cap["url"] == "http://local-host:8000/v1/models"
    assert "Authorization" not in cap["headers"]
    assert "x-api-key" not in cap["headers"]


def test_local_alias_maps_to_exaone(monkeypatch):
    monkeypatch.delenv("LOCAL_LLM_URL", raising=False)
    _patch_get(monkeypatch, FakeResp(200, {"data": [{"id": "m1"}]}))
    assert list_chat_models("local", "")[0]["id"] == "m1"


def test_keyed_provider_still_requires_key(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("should not call network")
    monkeypatch.setattr(cp.requests, "get", boom)
    with pytest.raises(ChatProviderError) as exc:
        list_chat_models("openai", "")
    assert exc.value.kind == "auth"
