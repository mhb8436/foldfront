from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from pipeline_mcp.model_providers import ModelProviderStore
from pipeline_mcp.model_providers import build_provider_summary
from pipeline_mcp.tools import ToolDispatcher


def test_model_provider_store_lists_env_fallbacks_and_masks_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("PROTEINMPNN_PROVIDER", "gpu_http")
    monkeypatch.setenv("PROTEINMPNN_GPU_URL", "http://gpu.example:18101")
    monkeypatch.setenv("PROTEINMPNN_GPU_TOKEN", "secret-token")
    monkeypatch.setenv("BIOEMU_ENDPOINT_ID", "bioemu-runpod")

    store = ModelProviderStore(tmp_path)
    providers = {item["model_key"]: item for item in store.list_effective()}

    assert providers["proteinmpnn"]["provider_type"] == "http_api"
    assert providers["proteinmpnn"]["base_url"] == "http://gpu.example:18101"
    assert providers["proteinmpnn"]["token_masked"] == "********oken"
    assert "token" not in providers["proteinmpnn"]
    assert providers["bioemu"]["provider_type"] == "runpod"
    assert providers["bioemu"]["endpoint_id"] == "bioemu-runpod"
    assert "esm_embedding" in providers


def test_colabfold_url_env_alias_configures_http_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("COLABFOLD_URL", "http://gpu.example:18160/")

    store = ModelProviderStore(tmp_path)
    provider = store.get_effective("colabfold")

    assert provider["provider_type"] == "http_api"
    assert provider["base_url"] == "http://gpu.example:18160"


def test_esm_embedding_provider_env_can_prefer_runpod_when_url_also_exists(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ESM_EMBEDDING_PROVIDER", "runpod")
    monkeypatch.setenv("ESM_EMBEDDING_ENDPOINT_ID", "esm-runpod")
    monkeypatch.setenv("ESM_EMBEDDING_URL", "http://gpu.example:18170/")

    store = ModelProviderStore(tmp_path)
    provider = store.get_effective("esm_embedding")

    assert provider["provider_type"] == "runpod"
    assert provider["endpoint_id"] == "esm-runpod"


def test_model_provider_store_persists_http_provider_with_encrypted_token(tmp_path):
    store = ModelProviderStore(tmp_path)
    saved = store.upsert(
        "esmfold",
        {
            "provider_type": "http_api",
            "base_url": "http://gpu.example:18162/",
            "token": "worker-token",
            "timeout_s": 123,
            "enabled": True,
        },
        actor="admin",
    )

    assert saved["model_key"] == "esmfold"
    assert saved["token_masked"] == "********oken"
    raw_text = (Path(tmp_path) / "_model_providers" / "providers.json").read_text(encoding="utf-8")
    assert "worker-token" not in raw_text

    reloaded = ModelProviderStore(tmp_path)
    effective = reloaded.get_effective("esmfold", include_secret=True)
    assert effective["provider_type"] == "http_api"
    assert effective["base_url"] == "http://gpu.example:18162"
    assert effective["token"] == "worker-token"


def test_user_override_does_not_change_global_default(tmp_path):
    store = ModelProviderStore(tmp_path)
    store.upsert(
        "rfd3",
        {
            "provider_type": "runpod",
            "endpoint_id": "global-rfd3",
            "enabled": True,
        },
        actor="admin",
        scope="global",
    )
    store.upsert(
        "rfd3",
        {
            "provider_type": "http_api",
            "base_url": "http://alice-gpu.example:18104",
            "enabled": True,
        },
        actor="alice",
        scope="user",
        user_id="alice",
    )

    global_provider = store.get_effective("rfd3")
    alice_provider = store.get_effective("rfd3", user_id="alice")
    bob_provider = store.get_effective("rfd3", user_id="bob")

    assert global_provider["provider_type"] == "runpod"
    assert global_provider["endpoint_id"] == "global-rfd3"
    assert alice_provider["provider_type"] == "http_api"
    assert alice_provider["base_url"] == "http://alice-gpu.example:18104"
    assert alice_provider["scope"] == "user"
    assert alice_provider["scope_user"] == "alice"
    assert bob_provider["provider_type"] == "runpod"
    assert bob_provider["endpoint_id"] == "global-rfd3"


def test_provider_tools_list_and_update_model_providers(tmp_path):
    runner = SimpleNamespace(output_root=str(tmp_path))
    dispatcher = ToolDispatcher(runner)  # type: ignore[arg-type]

    listed = dispatcher.call_tool("pipeline.model_provider_list", {})
    assert any(item["model_key"] == "esmfold" for item in listed["providers"])
    assert any(item["model_key"] == "esm_embedding" for item in listed["providers"])

    updated = dispatcher.call_tool(
        "pipeline.model_provider_update",
        {
            "model_key": "esmfold",
            "provider": {
                "provider_type": "http_api",
                "base_url": "http://gpu.example:18162",
                "timeout_s": 600,
            },
            "user": {"username": "admin", "role": "admin"},
        },
    )
    assert updated["provider"]["model_key"] == "esmfold"
    assert updated["provider"]["provider_type"] == "http_api"


def test_provider_health_can_check_unsaved_http_provider(tmp_path, monkeypatch):
    store = ModelProviderStore(tmp_path)
    store.upsert(
        "alphafold2",
        {
            "provider_type": "runpod",
            "endpoint_id": "saved-runpod",
            "enabled": True,
        },
        actor="test",
    )

    calls = []

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True, "ready": True, "model": "colabfold"}

    def fake_get(url, headers=None, timeout=None):  # type: ignore[no-untyped-def]
        calls.append({"url": url, "headers": headers, "timeout": timeout})
        return _Response()

    monkeypatch.setattr("pipeline_mcp.model_providers.requests.get", fake_get)

    result = store.health(
        "alphafold2",
        {
            "provider_type": "http_api",
            "base_url": "http://gpu.example:18161/",
            "timeout_s": 30,
            "enabled": True,
        },
    )

    assert result["ok"] is True
    assert result["ready"] is True
    assert result["provider"]["provider_type"] == "http_api"
    assert result["provider"]["base_url"] == "http://gpu.example:18161"
    assert calls == [{"url": "http://gpu.example:18161/healthz", "headers": {}, "timeout": 30.0}]
    assert store.get_effective("alphafold2")["provider_type"] == "runpod"


def test_provider_health_can_check_unsaved_runpod_provider_with_draft_key(tmp_path, monkeypatch):
    store = ModelProviderStore(tmp_path)
    calls = []

    class _RunPod:
        def __init__(self, *, api_key, ca_bundle=None, skip_verify=False, timeout_s=60.0, poll_interval_s=2.0):
            calls.append({"api_key": api_key, "timeout_s": timeout_s})

        def health(self, endpoint_id):
            calls[-1]["endpoint_id"] = endpoint_id
            return {"ok": True, "workers": {"idle": 1}}

    monkeypatch.setattr("pipeline_mcp.model_providers.RunPodClient", _RunPod)

    result = store.health(
        "rfd3",
        {
            "provider_type": "runpod",
            "endpoint_id": "draft-rfd3",
            "token": "draft-runpod-key",
            "timeout_s": 45,
            "enabled": True,
        },
    )

    assert result["ok"] is True
    assert result["ready"] is True
    assert result["provider"]["provider_type"] == "runpod"
    assert result["provider"]["endpoint_id"] == "draft-rfd3"
    assert result["provider"]["token_configured"] is True
    assert calls == [{"api_key": "draft-runpod-key", "timeout_s": 30.0, "endpoint_id": "draft-rfd3"}]


def test_build_provider_summary_marks_missing_required_fields(tmp_path):
    store = ModelProviderStore(tmp_path)
    store.upsert("rfd3", {"provider_type": "http_api", "enabled": True}, actor="admin")

    summary = build_provider_summary(store)
    rfd3 = next(item for item in summary if item["model_key"] == "rfd3")

    assert rfd3["configured"] is False
    assert rfd3["missing"] == ["base_url"]


def test_custom_model_provider_can_be_added_and_listed(tmp_path):
    store = ModelProviderStore(tmp_path)

    created = store.upsert(
        "esmfold_large",
        {
            "custom": True,
            "label": "ESMFold Large",
            "provider_type": "http_api",
            "base_url": "http://gpu.example:18162",
            "enabled": True,
        },
        actor="admin",
    )

    assert created["model_key"] == "esmfold_large"
    assert created["label"] == "ESMFold Large"
    assert created["custom"] is True

    listed = store.list_effective()
    custom = next(row for row in listed if row["model_key"] == "esmfold_large")
    assert custom["base_url"] == "http://gpu.example:18162"
    assert custom["provider_type"] == "http_api"
