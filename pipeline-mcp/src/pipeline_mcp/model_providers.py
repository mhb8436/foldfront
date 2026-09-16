from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any

import requests
from cryptography.fernet import Fernet
from cryptography.fernet import InvalidToken

from .clients.runpod import RunPodClient


PROVIDER_TYPES = {"runpod", "http_api", "disabled"}


@dataclass(frozen=True)
class ModelSpec:
    key: str
    label: str
    runpod_env: tuple[str, ...] = ()
    http_env: tuple[str, ...] = ()
    token_env: tuple[str, ...] = ()
    timeout_env: tuple[str, ...] = ()


MODEL_SPECS: tuple[ModelSpec, ...] = (
    ModelSpec("mmseqs", "MMseqs2", ("MMSEQS_ENDPOINT_ID",), ("MMSEQS_HTTP_URL", "MMSEQS_GPU_URL"), ("MMSEQS_HTTP_TOKEN",), ("MMSEQS_HTTP_TIMEOUT_S",)),
    ModelSpec("proteinmpnn", "ProteinMPNN", ("PROTEINMPNN_ENDPOINT_ID",), ("PROTEINMPNN_GPU_URL", "PROTEINMPNN_HTTP_URL"), ("PROTEINMPNN_GPU_TOKEN", "PROTEINMPNN_HTTP_TOKEN"), ("PROTEINMPNN_GPU_TIMEOUT_S", "PROTEINMPNN_HTTP_TIMEOUT_S")),
    ModelSpec("colabfold", "ColabFold", ("COLABFOLD_ENDPOINT_ID", "COLABFOLD_RUNPOD_ENDPOINT_ID"), ("COLABFOLD_URL", "COLABFOLD_HTTP_URL", "COLABFOLD_GPU_URL"), ("COLABFOLD_HTTP_TOKEN",), ("COLABFOLD_HTTP_TIMEOUT_S",)),
    ModelSpec("alphafold2", "AlphaFold2", ("ALPHAFOLD2_ENDPOINT_ID", "AF2_ENDPOINT_ID", "ALPHAFOLD2_RUNPOD_ENDPOINT_ID"), ("AF2_URL", "ALPHAFOLD2_HTTP_URL"), ("AF2_HTTP_TOKEN", "ALPHAFOLD2_HTTP_TOKEN"), ("AF2_HTTP_TIMEOUT_S",)),
    ModelSpec("esmfold", "ESMFold", ("ESMFOLD_ENDPOINT_ID",), ("ESMFOLD_HTTP_URL", "ESMFOLD_URL"), ("ESMFOLD_HTTP_TOKEN",), ("ESMFOLD_HTTP_TIMEOUT_S",)),
    ModelSpec("esm_embedding", "ESM Embedding", ("ESM_EMBEDDING_ENDPOINT_ID", "ESM2_ENDPOINT_ID"), ("ESM_EMBEDDING_URL", "ESM_EMBEDDING_HTTP_URL", "ESM2_HTTP_URL"), ("ESM_EMBEDDING_TOKEN", "ESM_EMBEDDING_HTTP_TOKEN"), ("ESM_EMBEDDING_TIMEOUT_S", "ESM_EMBEDDING_HTTP_TIMEOUT_S")),
    ModelSpec("rfd3", "RFD3", ("RFD3_ENDPOINT_ID",), ("RFD3_HTTP_URL", "RFD3_GPU_URL"), ("RFD3_HTTP_TOKEN",), ("RFD3_HTTP_TIMEOUT_S",)),
    ModelSpec("bioemu", "BioEmu", ("BIOEMU_ENDPOINT_ID",), ("BIOEMU_HTTP_URL", "BIOEMU_GPU_URL"), ("BIOEMU_HTTP_TOKEN",), ("BIOEMU_HTTP_TIMEOUT_S",)),
    ModelSpec("diffdock", "DiffDock", ("DIFFDOCK_ENDPOINT_ID",), ("DIFFDOCK_HTTP_URL", "DIFFDOCK_GPU_URL"), ("DIFFDOCK_HTTP_TOKEN",), ("DIFFDOCK_HTTP_TIMEOUT_S",)),
    ModelSpec("rosetta_relax", "Rosetta Relax", ("RUNPOD_RELAX_ENDPOINT_ID",), ("ROSETTA_RELAX_HTTP_URL", "RELAX_HTTP_URL"), ("ROSETTA_RELAX_HTTP_TOKEN",), ("ROSETTA_RELAX_HTTP_TIMEOUT_S",)),
)


MODEL_SPEC_BY_KEY = {spec.key: spec for spec in MODEL_SPECS}


def _first_env(names: tuple[str, ...]) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _mask_secret(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 4:
        return "****"
    return "*" * 8 + text[-4:]


def _normalize_base_url(value: object | None) -> str:
    return str(value or "").strip().rstrip("/")


def _normalize_provider_type(value: object | None) -> str:
    raw = str(value or "").strip().lower().replace("-", "_")
    if raw in {"http", "gpu", "gpu_http", "local_http", "http_api"}:
        return "http_api"
    if raw in {"runpod", "serverless"}:
        return "runpod"
    if raw in {"disabled", "off", "none"}:
        return "disabled"
    if raw:
        raise ValueError("provider_type must be one of: runpod, http_api, disabled")
    return "disabled"


def _read_timeout(value: object | None, default: float = 21600.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    return max(1.0, parsed)


def _env_true(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _normalize_scope(value: object | None) -> str:
    raw = str(value or "global").strip().lower().replace("-", "_")
    if raw in {"global", "default", "admin"}:
        return "global"
    if raw in {"user", "personal", "mine"}:
        return "user"
    raise ValueError("scope must be one of: global, user")


def _normalize_scope_user(value: object | None) -> str:
    raw = str(value or "").strip().lower()
    key = "".join(ch if ch.isalnum() else "_" for ch in raw).strip("_")
    while "__" in key:
        key = key.replace("__", "_")
    if not key:
        raise ValueError("user_id is required for user-scoped model providers")
    return key[:96]


class ModelProviderStore:
    def __init__(self, output_root: str | Path) -> None:
        self.output_root = Path(output_root)
        self.root = self.output_root / "_model_providers"
        self.path = self.root / "providers.json"
        self.secret_path = self.root / "secret.key"

    def list_effective(self, *, include_secret: bool = False, user_id: str | None = None) -> list[dict[str, Any]]:
        data = self._load()
        user_data = self._load_user(user_id) if user_id else {}
        providers = [
            self.get_effective(spec.key, include_secret=include_secret, user_id=user_id)
            for spec in MODEL_SPECS
        ]
        custom_keys = sorted(
            {
                key
                for source in (data, user_data)
                for key, record in source.items()
                if key not in MODEL_SPEC_BY_KEY and isinstance(record, dict) and bool(record.get("custom"))
            }
        )
        providers.extend(
            self.get_effective(key, include_secret=include_secret, user_id=user_id)
            for key in custom_keys
        )
        return providers

    def get_effective(
        self,
        model_key: str,
        *,
        include_secret: bool = False,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        key = self._normalize_model_key(model_key, allow_custom=True)
        data = self._load()
        stored = data.get(key)
        user_key = _normalize_scope_user(user_id) if user_id else ""
        user_data = self._load_user(user_key) if user_key else {}
        user_stored = user_data.get(key)
        if key in MODEL_SPEC_BY_KEY:
            provider = self._env_provider(key)
        elif isinstance(stored, dict) and bool(stored.get("custom")):
            provider = {
                "model_key": key,
                "label": str(stored.get("label") or key).strip() or key,
                "custom": True,
                "provider_type": "disabled",
                "enabled": False,
                "endpoint_id": "",
                "base_url": "",
                "timeout_s": 21600.0,
                "source": "registry",
            }
        elif isinstance(user_stored, dict) and bool(user_stored.get("custom")):
            provider = {
                "model_key": key,
                "label": str(user_stored.get("label") or key).strip() or key,
                "custom": True,
                "provider_type": "disabled",
                "enabled": False,
                "endpoint_id": "",
                "base_url": "",
                "timeout_s": 21600.0,
                "source": "user",
            }
        else:
            raise ValueError(f"unknown model_key: {model_key}")
        if isinstance(stored, dict):
            provider.update(stored)
            provider["source"] = "registry"
            provider["scope"] = "global"
        else:
            provider["scope"] = "global"
        if isinstance(user_stored, dict):
            provider.update(user_stored)
            provider["source"] = "user"
            provider["scope"] = "user"
            provider["scope_user"] = user_key
        provider = self._normalize_record(key, provider)
        return self._public_record(provider, include_secret=include_secret)

    def upsert(
        self,
        model_key: str,
        payload: dict[str, Any],
        *,
        actor: str = "",
        scope: str = "global",
        user_id: str | None = None,
    ) -> dict[str, Any]:
        target_scope = _normalize_scope(scope)
        user_key = _normalize_scope_user(user_id) if target_scope == "user" else ""
        data = self._load_for_scope(target_scope, user_key)
        key = self._normalize_model_key(model_key, allow_custom=True)
        custom_requested = bool(payload.get("custom")) or "label" in payload
        stored = data.get(key)
        global_stored = self._load().get(key)
        if key not in MODEL_SPEC_BY_KEY and not (
            custom_requested
            or (isinstance(stored, dict) and bool(stored.get("custom")))
            or (isinstance(global_stored, dict) and bool(global_stored.get("custom")))
        ):
            raise ValueError(f"unknown model_key: {model_key}")
        if key in MODEL_SPEC_BY_KEY or isinstance(stored, dict) or isinstance(global_stored, dict):
            current = self.get_effective(
                key,
                include_secret=True,
                user_id=user_key if target_scope == "user" else None,
            )
        else:
            current = self._public_record(
                self._normalize_record(
                    key,
                    {
                        "model_key": key,
                        "label": payload.get("label") or key,
                        "custom": True,
                        "provider_type": "disabled",
                        "enabled": False,
                        "source": "user" if target_scope == "user" else "registry",
                        "scope": target_scope,
                        "scope_user": user_key,
                    },
                ),
                include_secret=True,
            )
        requested_provider_type = payload.get("provider_type", current.get("provider_type"))
        default_enabled = current.get("enabled", True)
        if "provider_type" in payload:
            default_enabled = _normalize_provider_type(requested_provider_type) != "disabled"
        next_record = {
            "model_key": key,
            "label": payload.get("label", current.get("label", key)),
            "custom": bool(current.get("custom")) or key not in MODEL_SPEC_BY_KEY or bool(payload.get("custom")),
            "provider_type": requested_provider_type,
            "enabled": payload.get("enabled", default_enabled),
            "endpoint_id": payload.get("endpoint_id", current.get("endpoint_id", "")),
            "base_url": payload.get("base_url", current.get("base_url", "")),
            "timeout_s": payload.get("timeout_s", current.get("timeout_s", 21600.0)),
            "source": "user" if target_scope == "user" else "registry",
            "scope": target_scope,
            "scope_user": user_key,
            "updated_by": str(actor or ""),
        }
        token = str(payload.get("token") if "token" in payload else current.get("token") or "").strip()
        normalized = self._normalize_record(key, next_record)
        data = self._load_for_scope(target_scope, user_key)
        stored = dict(normalized)
        if token:
            stored["token_encrypted"] = self._encrypt(token)
        else:
            stored.pop("token_encrypted", None)
        if not stored.get("custom"):
            stored.pop("label", None)
        stored.pop("token", None)
        data[key] = stored
        self._save_for_scope(target_scope, user_key, data)
        return self.get_effective(key, user_id=user_key if target_scope == "user" else None)

    def health(
        self,
        model_key: str,
        provider_override: dict[str, Any] | None = None,
        *,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        provider = self.get_effective(model_key, include_secret=True, user_id=user_id)
        if isinstance(provider_override, dict):
            key = self._normalize_model_key(model_key, allow_custom=True)
            draft = dict(provider)
            draft.update(provider_override)
            draft["model_key"] = key
            draft["source"] = "draft"
            provider = self._public_record(self._normalize_record(key, draft), include_secret=True)
        provider_type = provider.get("provider_type")
        if provider_type == "runpod":
            endpoint_id = str(provider.get("endpoint_id") or "").strip()
            if not endpoint_id:
                return {
                    "ok": False,
                    "ready": False,
                    "error": "endpoint_id is required",
                    "provider": self._public_record(provider),
                }
            api_key = str(provider.get("token") or os.environ.get("RUNPOD_API_KEY", "")).strip()
            if not api_key:
                return {
                    "ok": False,
                    "ready": False,
                    "error": "RunPod API key is required",
                    "provider": self._public_record(provider),
                }
            client = RunPodClient(
                api_key=api_key,
                ca_bundle=os.environ.get("RUNPOD_CA_BUNDLE") or None,
                skip_verify=_env_true("RUNPOD_INSECURE"),
                timeout_s=min(float(provider.get("timeout_s") or 30), 30.0),
            )
            try:
                payload = client.health(endpoint_id)
                if not isinstance(payload, dict):
                    payload = {"raw": payload}
                return {
                    "ok": True,
                    "ready": True,
                    "health": payload,
                    "provider": self._public_record(provider),
                }
            except Exception as exc:
                return {"ok": False, "ready": False, "error": str(exc), "provider": self._public_record(provider)}
        if provider_type != "http_api":
            return {"ok": True, "ready": bool(provider.get("configured")), "provider": self._public_record(provider)}
        base_url = _normalize_base_url(provider.get("base_url"))
        if not base_url:
            return {"ok": False, "ready": False, "error": "base_url is required", "provider": self._public_record(provider)}
        headers = {}
        token = str(provider.get("token") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = requests.get(f"{base_url}/healthz", headers=headers, timeout=min(float(provider.get("timeout_s") or 30), 30.0))
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                payload = {"raw": payload}
            return {"ok": True, "ready": bool(payload.get("ready", payload.get("ok", True))), "health": payload, "provider": self._public_record(provider)}
        except Exception as exc:
            return {"ok": False, "ready": False, "error": str(exc), "provider": self._public_record(provider)}

    def _normalize_model_key(self, model_key: str, *, allow_custom: bool = False) -> str:
        key = str(model_key or "").strip().lower().replace("-", "_")
        aliases = {"relax": "rosetta_relax", "af2": "alphafold2", "rfdiffusion": "rfd3"}
        key = aliases.get(key, key)
        if key in MODEL_SPEC_BY_KEY:
            return key
        if not allow_custom:
            raise ValueError(f"unknown model_key: {model_key}")
        if not key or len(key) > 80 or not all(ch.isalnum() or ch == "_" for ch in key):
            raise ValueError("model_key must use letters, numbers, underscores, or hyphens")
        return key

    def _env_provider(self, model_key: str) -> dict[str, Any]:
        spec = MODEL_SPEC_BY_KEY[model_key]
        runpod_id = _first_env(spec.runpod_env)
        http_url = _first_env(spec.http_env)
        token = _first_env(spec.token_env)
        timeout = _first_env(spec.timeout_env)
        provider_type = "disabled"
        if http_url:
            provider_type = "http_api"
        elif runpod_id:
            provider_type = "runpod"
        provider_env_name = {
            "proteinmpnn": "PROTEINMPNN_PROVIDER",
            "esm_embedding": "ESM_EMBEDDING_PROVIDER",
        }.get(model_key, "")
        if provider_env_name:
            requested = os.environ.get(provider_env_name, "").strip().lower().replace("-", "_")
            if requested in {"gpu", "http", "gpu_http", "http_api"} and http_url:
                provider_type = "http_api"
            elif requested == "runpod" and runpod_id:
                provider_type = "runpod"
        return self._normalize_record(
            model_key,
            {
                "model_key": model_key,
                "provider_type": provider_type,
                "endpoint_id": runpod_id,
                "base_url": http_url,
                "token": token,
                "timeout_s": timeout or 21600,
                "enabled": provider_type != "disabled",
                "source": "env",
            },
        )

    def _normalize_record(self, model_key: str, record: dict[str, Any]) -> dict[str, Any]:
        spec = MODEL_SPEC_BY_KEY.get(model_key)
        custom = bool(record.get("custom")) or spec is None
        label = spec.label if spec is not None else str(record.get("label") or model_key).strip() or model_key
        provider_type = _normalize_provider_type(record.get("provider_type"))
        enabled = bool(record.get("enabled", provider_type != "disabled"))
        if not enabled:
            provider_type = "disabled"
        token = str(record.get("token") or "").strip()
        encrypted = str(record.get("token_encrypted") or "").strip()
        if not token and encrypted:
            token = self._decrypt(encrypted)
        out = {
            "model_key": model_key,
            "label": label,
            "custom": custom,
            "provider_type": provider_type,
            "enabled": enabled,
            "endpoint_id": str(record.get("endpoint_id") or "").strip(),
            "base_url": _normalize_base_url(record.get("base_url")),
            "timeout_s": _read_timeout(record.get("timeout_s"), 21600.0),
            "source": str(record.get("source") or "registry"),
            "scope": _normalize_scope(record.get("scope")),
            "scope_user": str(record.get("scope_user") or "").strip(),
            "updated_by": str(record.get("updated_by") or ""),
            "token": token,
        }
        out["configured"] = self._configured(out)
        return out

    def _configured(self, record: dict[str, Any]) -> bool:
        if record["provider_type"] == "runpod":
            return bool(record["endpoint_id"])
        if record["provider_type"] == "http_api":
            return bool(record["base_url"])
        return False

    def _public_record(self, record: dict[str, Any], *, include_secret: bool = False) -> dict[str, Any]:
        out = dict(record)
        token = str(out.pop("token", "") or "")
        out["token_configured"] = bool(token)
        out["token_masked"] = _mask_secret(token)
        if include_secret:
            out["token"] = token
        return out

    def _load(self) -> dict[str, dict[str, Any]]:
        return self._load_path(self.path)

    def _load_user(self, user_id: str | None) -> dict[str, dict[str, Any]]:
        if not user_id:
            return {}
        return self._load_path(self._user_path(_normalize_scope_user(user_id)))

    def _load_for_scope(self, scope: str, user_key: str = "") -> dict[str, dict[str, Any]]:
        target_scope = _normalize_scope(scope)
        if target_scope == "user":
            return self._load_user(user_key)
        return self._load()

    def _load_path(self, path: Path) -> dict[str, dict[str, Any]]:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        providers = payload.get("providers") if isinstance(payload, dict) else None
        if not isinstance(providers, dict):
            return {}
        return {str(k): v for k, v in providers.items() if isinstance(v, dict)}

    def _save(self, providers: dict[str, dict[str, Any]]) -> None:
        self._save_path(self.path, providers)

    def _save_for_scope(self, scope: str, user_key: str, providers: dict[str, dict[str, Any]]) -> None:
        target_scope = _normalize_scope(scope)
        if target_scope == "user":
            self._save_path(self._user_path(user_key), providers)
            return
        self._save(providers)

    def _save_path(self, path: Path, providers: dict[str, dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"providers": providers}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            path.chmod(0o600)
        except Exception:
            pass

    def _user_path(self, user_key: str) -> Path:
        return self.root / "users" / _normalize_scope_user(user_key) / "providers.json"

    def _fernet(self) -> Fernet:
        self.root.mkdir(parents=True, exist_ok=True)
        if self.secret_path.exists():
            raw = self.secret_path.read_bytes().strip()
            if raw:
                return Fernet(raw)
        key = Fernet.generate_key()
        self.secret_path.write_bytes(key)
        try:
            self.secret_path.chmod(0o600)
        except Exception:
            pass
        return Fernet(key)

    def _encrypt(self, value: str) -> str:
        return self._fernet().encrypt(value.encode("utf-8")).decode("ascii")

    def _decrypt(self, value: str) -> str:
        try:
            return self._fernet().decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError):
            return ""


def build_provider_summary(store: ModelProviderStore, *, user_id: str | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for provider in store.list_effective(user_id=user_id):
        missing: list[str] = []
        if provider["provider_type"] == "runpod" and not provider.get("endpoint_id"):
            missing.append("endpoint_id")
        if provider["provider_type"] == "http_api" and not provider.get("base_url"):
            missing.append("base_url")
        rows.append({**provider, "missing": missing, "configured": not missing and bool(provider.get("configured"))})
    return rows


def model_provider_store_from_env(output_root: str | Path | None = None) -> ModelProviderStore:
    root = output_root or os.environ.get("PIPELINE_OUTPUT_ROOT", "outputs").strip() or "outputs"
    return ModelProviderStore(root)
