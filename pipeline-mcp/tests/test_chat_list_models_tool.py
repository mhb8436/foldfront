from pipeline_mcp import tools
from pipeline_mcp.chat_providers import ChatProviderError


def test_tool_returns_models(monkeypatch):
    monkeypatch.setattr(
        tools, "list_chat_models",
        lambda provider, api_key: [{"id": "gpt-4o", "label": "gpt-4o"}],
    )
    out = tools._chat_list_models_tool(None, {"provider": "openai", "api_key": "sk"})
    assert out == {"provider": "openai", "models": [{"id": "gpt-4o", "label": "gpt-4o"}]}


def test_tool_returns_error_shape(monkeypatch):
    def boom(provider, api_key):
        raise ChatProviderError("auth", "provider rejected the API key")
    monkeypatch.setattr(tools, "list_chat_models", boom)
    out = tools._chat_list_models_tool(None, {"provider": "anthropic", "api_key": "bad"})
    assert out == {"error": {"kind": "auth", "message": "provider rejected the API key"},
                   "provider": "anthropic"}


def test_exaone_lists_models_with_empty_key(monkeypatch):
    seen = {}
    def fake(provider, api_key):
        seen["provider"] = provider
        seen["api_key"] = api_key
        return [{"id": "LGAI-EXAONE/EXAONE-4.5-33B-AWQ", "label": "LGAI-EXAONE/EXAONE-4.5-33B-AWQ"}]
    monkeypatch.setattr(tools, "list_chat_models", fake)
    out = tools._chat_list_models_tool(None, {"provider": "exaone", "api_key": ""})
    assert out["provider"] == "exaone"
    assert out["models"][0]["id"] == "LGAI-EXAONE/EXAONE-4.5-33B-AWQ"
    assert seen == {"provider": "exaone", "api_key": ""}  # empty key allowed
