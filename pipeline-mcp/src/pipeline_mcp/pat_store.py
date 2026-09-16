"""Personal access tokens (long-lived MCP API keys).

A PAT lets a user connect an MCP client without the short-lived SSO access
token: it is a high-entropy random secret (``kbfpat_...``) that maps to a user
identity and does not expire until revoked (or until an optional TTL elapses).

Security model:
- Only the SHA-256 **hash** of the token is stored, never the raw token.
- The raw token is shown to the user exactly once, at creation.
- Each key carries the issuing user's ``username``/``role`` so run-scoping and
  admin checks behave exactly as they do for a session-authenticated user.
- Keys are revocable by id and can carry an optional expiry.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Any


TOKEN_PREFIX = "kbfpat_"
_LAST_USED_PERSIST_INTERVAL_S = 300


def _now_ts() -> int:
    return int(time.time())


def looks_like_pat(token: str) -> bool:
    return str(token or "").startswith(TOKEN_PREFIX)


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(str(raw_token or "").encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PatConfig:
    store_path: Path
    default_ttl_days: int


class PatStore:
    def __init__(self, config: PatConfig) -> None:
        self.config = config
        self._lock = threading.Lock()
        self._keys: dict[str, dict[str, Any]] = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        path = self.config.store_path
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): v for k, v in data.items() if isinstance(v, dict)}

    def _save_locked(self) -> None:
        path = self.config.store_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._keys, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def _ttl_to_expiry(self, now_ts: int, ttl_days: int | None) -> int:
        days = self.config.default_ttl_days if ttl_days is None else int(ttl_days)
        if days <= 0:
            return 0  # never expires
        return now_ts + days * 86400

    def create_key(
        self, user: dict[str, Any], *, label: str = "", ttl_days: int | None = None
    ) -> dict[str, Any]:
        """Create a new PAT. Returns metadata plus the raw ``token`` (shown once)."""
        now_ts = _now_ts()
        key_id = secrets.token_hex(8)
        raw_token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        record = {
            "token_hash": _hash_token(raw_token),
            "username": str(user.get("username") or ""),
            "role": str(user.get("role") or "user"),
            "label": str(label or "").strip()[:120],
            "created_at": now_ts,
            "expires_at": self._ttl_to_expiry(now_ts, ttl_days),
            "last_used": 0,
        }
        with self._lock:
            self._keys[key_id] = record
            self._save_locked()
        public = self.public_record(key_id, record)
        public["token"] = raw_token
        return public

    def verify(self, raw_token: str) -> dict[str, Any] | None:
        """Return the {username, role} user for a valid, unexpired token, else None."""
        if not looks_like_pat(raw_token):
            return None
        token_hash = _hash_token(raw_token)
        now_ts = _now_ts()
        with self._lock:
            match_id = None
            match = None
            for key_id, record in self._keys.items():
                if secrets.compare_digest(str(record.get("token_hash") or ""), token_hash):
                    match_id = key_id
                    match = record
                    break
            if match is None:
                return None
            expires_at = int(match.get("expires_at") or 0)
            if expires_at and expires_at <= now_ts:
                return None
            last_used = int(match.get("last_used") or 0)
            if now_ts - last_used >= _LAST_USED_PERSIST_INTERVAL_S:
                match["last_used"] = now_ts
                self._keys[match_id] = match
                self._save_locked()
            return {
                "username": str(match.get("username") or ""),
                "role": str(match.get("role") or "user"),
                "auth_type": "pat",
            }

    def public_record(self, key_id: str, record: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": key_id,
            "label": str(record.get("label") or ""),
            "created_at": int(record.get("created_at") or 0),
            "expires_at": int(record.get("expires_at") or 0),
            "last_used": int(record.get("last_used") or 0),
        }

    def list_keys(self, username: str) -> list[dict[str, Any]]:
        uname = str(username or "")
        with self._lock:
            items = [
                self.public_record(key_id, record)
                for key_id, record in self._keys.items()
                if str(record.get("username") or "") == uname
            ]
        items.sort(key=lambda item: item.get("created_at", 0), reverse=True)
        return items

    def revoke(self, username: str, key_id: str) -> bool:
        uname = str(username or "")
        with self._lock:
            record = self._keys.get(str(key_id))
            if record is None or str(record.get("username") or "") != uname:
                return False
            del self._keys[str(key_id)]
            self._save_locked()
            return True


def load_pat_store() -> PatStore:
    output_root = os.environ.get("PIPELINE_OUTPUT_ROOT", "outputs").strip() or "outputs"
    store_default = Path(output_root) / "mcp_keys.json"
    store_path = Path(os.environ.get("PIPELINE_MCP_KEYS_STORE", str(store_default))).resolve()
    try:
        default_ttl_days = int(os.environ.get("PIPELINE_MCP_KEY_TTL_DAYS", "90") or "90")
    except ValueError:
        default_ttl_days = 90
    return PatStore(PatConfig(store_path=store_path, default_ttl_days=default_ttl_days))
