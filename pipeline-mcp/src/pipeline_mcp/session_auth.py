from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Any

from .oidc import OIDCSettings
from .oidc import claims_from_oidc_token_data
from .oidc import claims_to_user
from .oidc import refresh_oidc_tokens


def _now_ts() -> int:
    return int(time.time())


def _coerce_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _ttl_to_epoch(now_ts: int, ttl_value: Any) -> int:
    ttl = _coerce_int(ttl_value, default=0)
    if ttl <= 0:
        return 0
    return now_ts + ttl


@dataclass(frozen=True)
class SessionConfig:
    store_path: Path
    cookie_name: str
    local_ttl_s: int
    oidc_refresh_leeway_s: int
    oidc_fallback_ttl_s: int


class SessionManager:
    def __init__(self, config: SessionConfig) -> None:
        self.config = config
        self._lock = threading.Lock()
        self._sessions = self._load_sessions()
        with self._lock:
            self._prune_expired_locked()
            self._save_locked()

    def create_local_session(self, user: dict[str, Any]) -> str:
        session_id = secrets.token_urlsafe(32)
        now_ts = _now_ts()
        payload = {
            "auth_type": "local",
            "user": dict(user),
            "created_at": now_ts,
            "updated_at": now_ts,
            "expires_at": now_ts + max(1, int(self.config.local_ttl_s)),
        }
        with self._lock:
            self._prune_expired_locked(now_ts)
            self._sessions[session_id] = payload
            self._save_locked()
        return session_id

    def create_oidc_session(
        self,
        settings: OIDCSettings,
        token_data: dict[str, Any],
        *,
        user: dict[str, Any] | None = None,
    ) -> str:
        session_id = secrets.token_urlsafe(32)
        now_ts = _now_ts()
        oidc_tokens = self._normalize_oidc_tokens(token_data)
        if user is None:
            claims = claims_from_oidc_token_data(settings, token_data)
            user = claims_to_user(claims, client_id=settings.client_id)
        payload = {
            "auth_type": "oidc",
            "user": dict(user),
            "created_at": now_ts,
            "updated_at": now_ts,
            "expires_at": self._session_expiry_for_oidc(oidc_tokens, now_ts),
            "oidc": oidc_tokens,
        }
        with self._lock:
            self._prune_expired_locked(now_ts)
            self._sessions[session_id] = payload
            self._save_locked()
        return session_id

    def get_user(self, session_id: str, *, oidc_settings: OIDCSettings | None = None) -> dict[str, Any] | None:
        session = self.get_session(session_id, oidc_settings=oidc_settings)
        if not isinstance(session, dict):
            return None
        user = session.get("user")
        if not isinstance(user, dict):
            return None
        return dict(user)

    def get_session(self, session_id: str, *, oidc_settings: OIDCSettings | None = None) -> dict[str, Any] | None:
        session_key = str(session_id or "").strip()
        if not session_key:
            return None
        now_ts = _now_ts()
        with self._lock:
            self._prune_expired_locked(now_ts)
            session = self._sessions.get(session_key)
            if not isinstance(session, dict):
                return None
            snapshot = json.loads(json.dumps(session))
        if str(snapshot.get("auth_type") or "") != "oidc":
            return snapshot
        return self._maybe_refresh_oidc_session(session_key, snapshot, oidc_settings=oidc_settings)

    def cookie_max_age(self, session_id: str) -> int | None:
        session = self.get_session(session_id)
        if not isinstance(session, dict):
            return None
        expires_at = _coerce_int(session.get("expires_at"))
        if expires_at <= 0:
            return None
        return max(0, expires_at - _now_ts())

    def destroy_session(self, session_id: str) -> None:
        session_key = str(session_id or "").strip()
        if not session_key:
            return
        with self._lock:
            removed = self._sessions.pop(session_key, None)
            if removed is not None:
                self._save_locked()

    def get_oidc_id_token(self, session_id: str) -> str:
        session = self.get_session(session_id)
        if not isinstance(session, dict):
            return ""
        oidc = session.get("oidc")
        if not isinstance(oidc, dict):
            return ""
        return str(oidc.get("id_token") or "").strip()

    def get_oidc_access_token(
        self, session_id: str, *, oidc_settings: "OIDCSettings | None" = None
    ) -> tuple[str, int]:
        """Return (access_token, access_expires_at_epoch) for an OIDC session.

        Refreshes the session first when oidc_settings is provided so the
        returned access token is as fresh as the stored refresh token allows.
        Returns ("", 0) for non-OIDC sessions or when no access token exists.
        """
        session = self.get_session(session_id, oidc_settings=oidc_settings)
        if not isinstance(session, dict):
            return "", 0
        if str(session.get("auth_type") or "") != "oidc":
            return "", 0
        oidc = session.get("oidc")
        if not isinstance(oidc, dict):
            return "", 0
        token = str(oidc.get("access_token") or "").strip()
        expires_at = _coerce_int(oidc.get("access_expires_at"))
        return token, expires_at

    def _maybe_refresh_oidc_session(
        self,
        session_id: str,
        session: dict[str, Any],
        *,
        oidc_settings: OIDCSettings | None,
    ) -> dict[str, Any] | None:
        oidc_state = session.get("oidc")
        if not isinstance(oidc_state, dict):
            self.destroy_session(session_id)
            return None
        now_ts = _now_ts()
        access_expires_at = _coerce_int(oidc_state.get("access_expires_at"))
        refresh_expires_at = _coerce_int(oidc_state.get("refresh_expires_at"))
        leeway = max(0, int(self.config.oidc_refresh_leeway_s))

        if refresh_expires_at and refresh_expires_at <= now_ts:
            self.destroy_session(session_id)
            return None
        if access_expires_at and access_expires_at > now_ts + leeway:
            return session

        refresh_token = str(oidc_state.get("refresh_token") or "").strip()
        if not refresh_token:
            if access_expires_at and access_expires_at > now_ts:
                return session
            self.destroy_session(session_id)
            return None
        if oidc_settings is None:
            if access_expires_at and access_expires_at > now_ts:
                return session
            self.destroy_session(session_id)
            return None

        try:
            refreshed = refresh_oidc_tokens(oidc_settings, refresh_token=refresh_token)
        except Exception:
            self.destroy_session(session_id)
            return None

        next_oidc = self._normalize_oidc_tokens(refreshed, previous=oidc_state)
        next_user = dict(session.get("user") or {})
        try:
            claims = claims_from_oidc_token_data(oidc_settings, next_oidc)
            next_user = claims_to_user(claims, client_id=oidc_settings.client_id)
        except Exception:
            # Keep the previous user payload if refreshed token claims are temporarily unavailable.
            pass

        updated = dict(session)
        updated["user"] = next_user
        updated["oidc"] = next_oidc
        updated["updated_at"] = now_ts
        updated["expires_at"] = self._session_expiry_for_oidc(next_oidc, now_ts)

        with self._lock:
            current = self._sessions.get(session_id)
            if not isinstance(current, dict):
                return None
            self._sessions[session_id] = updated
            self._save_locked()
        return json.loads(json.dumps(updated))

    def _normalize_oidc_tokens(
        self,
        token_data: dict[str, Any],
        *,
        previous: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        previous = previous if isinstance(previous, dict) else {}
        now_ts = _now_ts()
        access_token = str(token_data.get("access_token") or previous.get("access_token") or "").strip()
        refresh_token = str(token_data.get("refresh_token") or previous.get("refresh_token") or "").strip()
        id_token = str(token_data.get("id_token") or previous.get("id_token") or "").strip()
        token_type = str(token_data.get("token_type") or previous.get("token_type") or "Bearer").strip() or "Bearer"
        access_expires_at = _ttl_to_epoch(now_ts, token_data.get("expires_in"))
        if access_expires_at <= 0:
            access_expires_at = _coerce_int(previous.get("access_expires_at"))
        refresh_expires_at = _ttl_to_epoch(now_ts, token_data.get("refresh_expires_in"))
        if refresh_expires_at <= 0:
            refresh_expires_at = _coerce_int(previous.get("refresh_expires_at"))
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "id_token": id_token,
            "token_type": token_type,
            "access_expires_at": access_expires_at,
            "refresh_expires_at": refresh_expires_at,
        }

    def _session_expiry_for_oidc(self, oidc_tokens: dict[str, Any], now_ts: int | None = None) -> int:
        now_value = int(now_ts if now_ts is not None else _now_ts())
        refresh_expires_at = _coerce_int(oidc_tokens.get("refresh_expires_at"))
        if refresh_expires_at > 0:
            return refresh_expires_at
        access_expires_at = _coerce_int(oidc_tokens.get("access_expires_at"))
        if access_expires_at > 0:
            return access_expires_at
        return now_value + max(1, int(self.config.oidc_fallback_ttl_s))

    def _load_sessions(self) -> dict[str, dict[str, Any]]:
        path = self.config.store_path
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        sessions = payload.get("sessions") if isinstance(payload, dict) else None
        if not isinstance(sessions, dict):
            return {}
        out: dict[str, dict[str, Any]] = {}
        for session_id, session in sessions.items():
            if not isinstance(session, dict):
                continue
            out[str(session_id)] = dict(session)
        return out

    def _save_locked(self) -> None:
        payload = {"sessions": self._sessions}
        self.config.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.config.store_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _prune_expired_locked(self, now_ts: int | None = None) -> None:
        current_ts = int(now_ts if now_ts is not None else _now_ts())
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if _coerce_int(session.get("expires_at")) <= current_ts
        ]
        for session_id in expired:
            self._sessions.pop(session_id, None)


def load_session_manager() -> SessionManager:
    output_root = os.environ.get("PIPELINE_OUTPUT_ROOT", "outputs").strip() or "outputs"
    store_default = Path(output_root).resolve() / ".auth" / "sessions.json"
    store_path = Path(os.environ.get("PIPELINE_SESSION_STORE", str(store_default))).resolve()
    cookie_name = str(os.environ.get("PIPELINE_SESSION_COOKIE_NAME", "kbf_session") or "kbf_session").strip() or "kbf_session"
    local_ttl_s = _coerce_int(
        os.environ.get("PIPELINE_SESSION_TTL_S") or os.environ.get("PIPELINE_AUTH_TOKEN_TTL_S") or "86400",
        default=86400,
    )
    oidc_refresh_leeway_s = _coerce_int(os.environ.get("PIPELINE_OIDC_REFRESH_LEEWAY_S", "60"), default=60)
    oidc_fallback_ttl_s = _coerce_int(os.environ.get("PIPELINE_OIDC_SESSION_FALLBACK_TTL_S", "3600"), default=3600)
    return SessionManager(
        SessionConfig(
            store_path=store_path,
            cookie_name=cookie_name,
            local_ttl_s=max(1, local_ttl_s),
            oidc_refresh_leeway_s=max(0, oidc_refresh_leeway_s),
            oidc_fallback_ttl_s=max(1, oidc_fallback_ttl_s),
        )
    )
