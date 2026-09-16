from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import time
from typing import Any


_USER_RE = re.compile(r"^[a-zA-Z0-9._-]{3,32}$")


class AuthError(ValueError):
    pass


@dataclass(frozen=True)
class AuthConfig:
    enabled: bool
    store_path: Path
    secret_path: Path
    token_ttl_s: int


@dataclass
class AuthManager:
    config: AuthConfig
    users: dict[str, dict[str, Any]]
    secret: bytes

    @property
    def enabled(self) -> bool:
        return bool(self.config.enabled)

    def save(self) -> None:
        payload = {"users": self.users}
        self.config.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.config.store_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def ensure_admin(self, username: str, password: str) -> None:
        _validate_username(username)
        if not password:
            raise AuthError("admin password is required")
        if username in self.users:
            return
        self.users[username] = {
            "username": username,
            "role": "admin",
            "status": "approved",
            "password_hash": _hash_password(password),
            "created_at": _now_ts(),
        }
        self.save()

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        user = self.users.get(username)
        if not user:
            return None
        if not _verify_password(password, str(user.get("password_hash") or "")):
            return None
        token = _issue_token(self.secret, username=username, role=str(user.get("role") or "user"), ttl_s=self.config.token_ttl_s)
        return {"token": token, "user": _public_user(user)}

    def issue_token(self, user: dict[str, Any]) -> dict[str, Any]:
        """Mint a fresh bearer token for an already-authenticated user.

        Used by the one-click MCP token endpoint for local-auth sessions,
        where the original login token is not stored server-side.
        """
        username = str(user.get("username") or "")
        role = str(user.get("role") or "user")
        ttl_s = int(self.config.token_ttl_s)
        token = _issue_token(self.secret, username=username, role=role, ttl_s=ttl_s)
        return {"token": token, "expires_at": int(time.time()) + max(0, ttl_s)}

    def verify_token(self, token: str) -> dict[str, Any] | None:
        payload = _verify_token(self.secret, token)
        if payload is None:
            return None
        username = str(payload.get("sub") or "")
        user = self.users.get(username)
        if not user:
            return None
        return _public_user(user)

    def create_user(self, *, username: str, password: str, role: str = "user") -> dict[str, Any]:
        _validate_username(username)
        if not password or len(password) < 8:
            raise AuthError("password must be at least 8 characters")
        if username in self.users:
            raise AuthError("user already exists")
        role_value = _normalize_role(role)
        self.users[username] = {
            "username": username,
            "role": role_value,
            "status": "approved",
            "password_hash": _hash_password(password),
            "created_at": _now_ts(),
        }
        self.save()
        return _public_user(self.users[username])

    def list_users(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for user in self.users.values():
            public = _public_user(user)
            if user.get("email"):
                public["email"] = str(user.get("email") or "")
            if user.get("subject"):
                public["subject"] = str(user.get("subject") or "")
            public["external"] = bool(user.get("external"))
            rows.append(public)
        return sorted(rows, key=lambda item: (str(item.get("role") or "") != "admin", str(item.get("username") or "")))

    def resolve_external_user(
        self,
        user: dict[str, Any],
        *,
        default_status: str = "approved",
    ) -> dict[str, Any]:
        username = str(user.get("username") or "").strip()
        if not username:
            return user
        existing = self.users.get(username)
        if existing is None:
            status = "approved" if str(user.get("role") or "") == "admin" else str(default_status or "approved")
            existing = {
                "username": username,
                "role": _normalize_role(user.get("role")),
                "status": _normalize_status(status),
                "created_at": _now_ts(),
                "external": True,
                "email": str(user.get("email") or ""),
                "subject": str(user.get("subject") or ""),
            }
            self.users[username] = existing
            self.save()
        out = _public_user({**user, **existing})
        if user.get("email"):
            out["email"] = str(user.get("email") or "")
        if user.get("subject"):
            out["subject"] = str(user.get("subject") or "")
        return out

    def update_user(self, *, username: str, role: str | None = None, status: str | None = None) -> dict[str, Any]:
        user = self.users.get(username)
        if user is None:
            raise AuthError("user not found")
        if role is not None:
            user["role"] = _normalize_role(role)
        if status is not None:
            user["status"] = _normalize_status(status)
        self.save()
        return _public_user(user)


def _env_true(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _now_ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def _validate_username(username: str) -> None:
    if not _USER_RE.match(username or ""):
        raise AuthError("username must be 3-32 chars of [a-zA-Z0-9._-]")


def _normalize_role(role: object) -> str:
    raw = str(role or "user").strip().lower().replace("-", "_")
    if raw in {"admin", "model_manager", "user"}:
        return raw
    return "user"


def _normalize_status(status: object) -> str:
    raw = str(status or "approved").strip().lower().replace("-", "_")
    if raw in {"approved", "pending", "disabled"}:
        return raw
    return "pending"


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _hash_password(password: str) -> str:
    iterations = 200_000
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2$%d$%s$%s" % (
        iterations,
        _b64url_encode(salt),
        _b64url_encode(dk),
    )


def _verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations_raw, salt_raw, hash_raw = stored.split("$", 3)
    except ValueError:
        return False
    if algo != "pbkdf2":
        return False
    try:
        iterations = int(iterations_raw)
    except ValueError:
        return False
    try:
        salt = _b64url_decode(salt_raw)
        expected = _b64url_decode(hash_raw)
    except Exception:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(dk, expected)


def _issue_token(secret: bytes, *, username: str, role: str, ttl_s: int) -> str:
    now = int(time.time())
    payload = {
        "sub": username,
        "role": role,
        "iat": now,
        "exp": now + int(ttl_s),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = _b64url_encode(raw)
    sig = hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64url_encode(sig)}"


def _verify_token(secret: bytes, token: str) -> dict[str, Any] | None:
    parts = (token or "").split(".")
    if len(parts) != 2:
        return None
    body, sig_raw = parts
    try:
        expected = hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest()
        sig = _b64url_decode(sig_raw)
    except Exception:
        return None
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload


def _public_user(user: dict[str, Any]) -> dict[str, Any]:
    username = str(user.get("username") or "")
    return {
        "username": username,
        "role": _normalize_role(user.get("role")),
        "status": _normalize_status(user.get("status") or "approved"),
        "created_at": str(user.get("created_at") or ""),
        "run_prefix": safe_run_prefix(username),
    }


def _load_users(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    users = payload.get("users") if isinstance(payload, dict) else None
    if not isinstance(users, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, value in users.items():
        if not isinstance(value, dict):
            continue
        username = str(value.get("username") or key)
        out[username] = dict(value, username=username)
    return out


def _load_or_create_secret(path: Path) -> bytes:
    if path.exists():
        data = path.read_bytes()
        if data:
            return data
    secret = secrets.token_bytes(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(secret)
    return secret


def load_auth_manager() -> AuthManager | None:
    enabled = _env_true("PIPELINE_AUTH_ENABLED")
    if not enabled:
        return None

    output_root = os.environ.get("PIPELINE_OUTPUT_ROOT", "outputs").strip() or "outputs"
    store_default = Path(output_root).resolve() / ".auth" / "users.json"
    store_path = Path(os.environ.get("PIPELINE_AUTH_STORE", str(store_default))).resolve()
    secret_path = store_path.parent / "secret.key"
    ttl_s = int(os.environ.get("PIPELINE_AUTH_TOKEN_TTL_S", "86400"))

    users = _load_users(store_path)
    secret = _load_or_create_secret(secret_path)
    manager = AuthManager(
        config=AuthConfig(enabled=True, store_path=store_path, secret_path=secret_path, token_ttl_s=ttl_s),
        users=users,
        secret=secret,
    )

    admin_user = os.environ.get("PIPELINE_ADMIN_USERNAME", "admin").strip() or "admin"
    admin_password = os.environ.get("PIPELINE_ADMIN_PASSWORD", "").strip()
    if admin_password:
        manager.ensure_admin(admin_user, admin_password)
    else:
        has_admin = any(str(u.get("role") or "") == "admin" for u in users.values())
        if not has_admin:
            raise AuthError("PIPELINE_ADMIN_PASSWORD is required to create admin user")
    return manager


def safe_run_prefix(username: str) -> str:
    raw = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(username or "")).strip("._-")
    return raw[:32] or "user"
