from __future__ import annotations

import argparse
import io
import json
import os
import shutil
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.parse import unquote
from urllib.parse import urlencode
import zipfile

from .storage import new_run_id
from .storage import resolve_run_path
from .auth import AuthError
from .auth import load_auth_manager
from .auth import safe_run_prefix
from .oidc import claims_from_oidc_token_data
from .oidc import claims_to_user
from .oidc import account_console_url
from .oidc import exchange_oidc_code
from .oidc import get_oidc_discovery
from .oidc import load_oidc_settings
from .oidc import verify_oidc_token
from .runpod_metrics import ensure_runpod_metrics_collector
from .session_auth import load_session_manager
from .pat_store import load_pat_store
from .pat_store import looks_like_pat
from .tools import ToolDispatcher


_DISPATCHER: ToolDispatcher | None = None
_AUTH = None
_OIDC = None
_SESSIONS = None
_PAT_KEYS = None
_ALLOW_ALL_ORIGINS = True
_ALLOWED_ORIGINS: set[str] = set()
_ADMIN_ONLY_TOOLS = {
    "pipeline.cath_get_batch_overview",
    "pipeline.cath_launch_batch",
    "pipeline.cath_launch_training",
    "pipeline.cath_list_jobs",
    "pipeline.cath_get_job",
    "pipeline.cath_read_job_log",
    "pipeline.cath_stop_job",
    "pipeline.cath_delete_job",
    "pipeline.runpod_list_endpoints",
    "pipeline.runpod_get_endpoint",
    "pipeline.runpod_update_endpoint",
    "pipeline.runpod_list_billing",
    "pipeline.runpod_get_history",
}

_USER_PROVIDER_SCOPED_TOOLS = {
    "pipeline.run",
    "pipeline.preflight",
    "pipeline.af2_predict",
    "pipeline.run_af2",
    "pipeline.run_diffdock",
    "pipeline.run_from_prompt",
}

# Management / web-UI tools hidden from the MCP `tools/list` so the core
# run + monitor + analyze tools always fit under MCP clients' tool-count
# limits (some clients silently drop tools when the list is large). These
# remain fully callable — they are just not advertised in the MCP list.
# Set PIPELINE_MCP_FULL_TOOL_LIST=1 to advertise everything.
_MCP_LIST_HIDDEN_TOOLS = {
    "pipeline.save_project",
    "pipeline.list_projects",
    "pipeline.get_project",
    "pipeline.archive_project",
    "pipeline.delete_project",
    "pipeline.restore_project",
    "pipeline.save_round",
    "pipeline.list_rounds",
    "pipeline.get_round",
    "pipeline.archive_round",
    "pipeline.restore_round",
    "pipeline.delete_round",
    "pipeline.save_workflow_session",
    "pipeline.get_workflow_session",
    "pipeline.save_report",
    "pipeline.get_report",
    "pipeline.submit_feedback",
    "pipeline.list_feedback",
    "pipeline.submit_experiment",
    "pipeline.list_experiments",
    "pipeline.list_agent_events",
    "pipeline.agent_chat",
    "pipeline.analyze_paper_for_masking",
    "pipeline.model_provider_list",
    "pipeline.model_provider_update",
    "pipeline.model_provider_health",
    "chat.list_models",
    "chat.send",
    "chat.list_attachments",
}

_RUN_SCOPED_TOOLS = {
    "pipeline.status",
    "pipeline.list_artifacts",
    "pipeline.read_artifact",
    "pipeline.export_results_package",
    "pipeline.save_workflow_session",
    "pipeline.get_workflow_session",
    "pipeline.delete_run",
    "pipeline.cancel_run",
    "pipeline.submit_feedback",
    "pipeline.list_feedback",
    "pipeline.submit_experiment",
    "pipeline.list_experiments",
    "pipeline.generate_report",
    "pipeline.save_report",
    "pipeline.get_report",
}


def _env_true(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def _init_cors() -> None:
    raw = os.environ.get("PIPELINE_CORS_ORIGINS", "*")
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    global _ALLOW_ALL_ORIGINS, _ALLOWED_ORIGINS
    if not parts or "*" in parts:
        _ALLOW_ALL_ORIGINS = True
        _ALLOWED_ORIGINS = set()
    else:
        _ALLOW_ALL_ORIGINS = False
        _ALLOWED_ORIGINS = set(parts)


class Handler(BaseHTTPRequestHandler):
    # Speak HTTP/1.1 so the server answers `Expect: 100-continue`. curl (and the
    # Caddy reverse proxy) send `Expect: 100-continue` before uploading a larger
    # request body (e.g. a raw PDB as `target_pdb`); an HTTP/1.0 server never
    # sends the "100 Continue" interim response, so the proxy/client waits for it
    # while the server waits for the body — a deadlock that hangs large MCP
    # requests (small ones don't trigger Expect, so they worked). All responses
    # set Content-Length, so HTTP/1.1 keep-alive is safe; `timeout` closes idle
    # keep-alive connections so handler threads don't accumulate.
    protocol_version = "HTTP/1.1"
    timeout = 65
    _MAX_BODY_BYTES = 50 * 1024 * 1024
    _MAX_CHUNK_LINE_BYTES = 1024

    @property
    def dispatcher(self) -> ToolDispatcher:
        if _DISPATCHER is None:
            raise RuntimeError("Server not initialized")
        return _DISPATCHER

    @property
    def auth(self):
        return _AUTH

    @property
    def oidc(self):
        return _OIDC

    @property
    def sessions(self):
        return _SESSIONS

    @property
    def pat_keys(self):
        return _PAT_KEYS

    def _auth_enabled(self) -> bool:
        auth = self.auth
        if auth is not None and getattr(auth, "enabled", False):
            return True
        return self.oidc is not None

    def _set_cors_headers(self) -> None:
        origin = self.headers.get("Origin")
        allow_origin = bool(origin and (_ALLOW_ALL_ORIGINS or origin in _ALLOWED_ORIGINS))
        if allow_origin:
            self.send_header("Access-Control-Allow-Origin", str(origin))
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Credentials", "true")
        elif _ALLOW_ALL_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Max-Age", "600")

    def _json(
        self,
        code: int,
        payload: dict[str, Any],
        *,
        extra_headers: list[tuple[str, str]] | None = None,
    ) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._set_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for key, value in extra_headers or []:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _binary(
        self,
        code: int,
        data: bytes,
        content_type: str,
        *,
        extra_headers: list[tuple[str, str]] | None = None,
    ) -> None:
        self.send_response(code)
        self._set_cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for key, value in extra_headers or []:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _file_download(
        self,
        path: Path,
        content_type: str,
        *,
        filename: str,
        extra_headers: list[tuple[str, str]] | None = None,
    ) -> None:
        size = path.stat().st_size
        self.send_response(200)
        self._set_cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        for key, value in extra_headers or []:
            self.send_header(key, value)
        self.end_headers()
        with path.open("rb") as source:
            shutil.copyfileobj(source, self.wfile, length=1024 * 1024)

    def _read_chunked(self) -> bytes:
        body = bytearray()
        while True:
            line = self.rfile.readline(self._MAX_CHUNK_LINE_BYTES + 2)
            if not line:
                raise ValueError("Unexpected EOF while reading chunked request body")
            if len(line) > self._MAX_CHUNK_LINE_BYTES + 1 and not line.endswith(b"\n"):
                raise ValueError("Chunk size line too long")

            size_token = line.strip().split(b";", 1)[0].strip()
            try:
                chunk_size = int(size_token, 16)
            except ValueError as exc:
                raise ValueError("Invalid chunk size") from exc

            if chunk_size == 0:
                while True:
                    trailer = self.rfile.readline(self._MAX_CHUNK_LINE_BYTES + 2)
                    if not trailer or trailer in (b"\r\n", b"\n"):
                        break
                break

            if len(body) + chunk_size > self._MAX_BODY_BYTES:
                raise ValueError("Request body too large")

            chunk = self.rfile.read(chunk_size)
            if len(chunk) != chunk_size:
                raise ValueError("Unexpected EOF while reading chunk data")
            body.extend(chunk)

            terminator = self.rfile.readline(2)
            if terminator not in (b"\r\n", b"\n"):
                raise ValueError("Invalid chunk terminator")

        return bytes(body)

    def _read_body(self) -> bytes:
        transfer_encoding = str(self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in transfer_encoding:
            return self._read_chunked()

        length_raw = self.headers.get("Content-Length")
        if not length_raw:
            return b""
        try:
            length = int(length_raw)
        except ValueError as exc:
            raise ValueError("Invalid Content-Length header") from exc
        if length < 0:
            raise ValueError("Invalid Content-Length header")
        if length > self._MAX_BODY_BYTES:
            raise ValueError("Request body too large")
        return self.rfile.read(length) if length else b""

    def _read_json(self) -> dict[str, Any]:
        raw = self._read_body() or b"{}"
        data = json.loads(raw.decode("utf-8", errors="replace"))
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    def _route_path(self) -> str:
        raw = str(self.path or "").split("?", 1)[0].strip() or "/"
        normalized = raw.rstrip("/") or "/"
        if normalized == "/api":
            return "/"
        if normalized.startswith("/api/"):
            stripped = normalized[4:]
            return stripped or "/"
        return normalized

    def _request_is_secure(self) -> bool:
        forced = str(os.environ.get("PIPELINE_SESSION_COOKIE_SECURE") or "").strip().lower()
        if forced in {"1", "true", "yes", "y", "on"}:
            return True
        if forced in {"0", "false", "no", "n", "off"}:
            return False
        forwarded_proto = str(self.headers.get("X-Forwarded-Proto") or "").split(",", 1)[0].strip().lower()
        if forwarded_proto:
            return forwarded_proto == "https"
        origin = str(self.headers.get("Origin") or "").strip().lower()
        if origin.startswith("https://"):
            return True
        referer = str(self.headers.get("Referer") or "").strip().lower()
        return referer.startswith("https://")

    def _session_cookie_name(self) -> str:
        manager = self.sessions
        if manager is None:
            return "kbf_session"
        return str(manager.config.cookie_name or "kbf_session")

    def _session_cookie_samesite(self) -> str:
        raw = str(os.environ.get("PIPELINE_SESSION_COOKIE_SAMESITE", "Lax") or "Lax").strip()
        normalized = raw[:1].upper() + raw[1:].lower() if raw else "Lax"
        return normalized if normalized in {"Lax", "Strict", "None"} else "Lax"

    def _cookie_value(self, name: str) -> str:
        raw = str(self.headers.get("Cookie") or "").strip()
        if not raw:
            return ""
        cookie = SimpleCookie()
        try:
            cookie.load(raw)
        except Exception:
            return ""
        morsel = cookie.get(str(name or ""))
        if morsel is None:
            return ""
        return str(morsel.value or "").strip()

    def _session_id_from_cookie(self) -> str:
        return self._cookie_value(self._session_cookie_name())

    def _public_session_info(self) -> dict[str, str]:
        manager = self.sessions
        session_id = self._session_id_from_cookie()
        if manager is not None and session_id:
            session = manager.get_session(session_id, oidc_settings=self.oidc)
            if isinstance(session, dict):
                auth_type = str(session.get("auth_type") or "").strip()
                if auth_type:
                    return {"auth_type": auth_type}
        header = self.headers.get("Authorization") or ""
        if header.startswith("Bearer "):
            return {"auth_type": "token"}
        return {"auth_type": ""}

    def _session_cookie_headers(self, session_id: str, *, max_age: int | None = None, clear: bool = False) -> list[tuple[str, str]]:
        cookie = SimpleCookie()
        name = self._session_cookie_name()
        cookie[name] = "" if clear else str(session_id or "")
        morsel = cookie[name]
        morsel["path"] = "/"
        morsel["httponly"] = True
        morsel["samesite"] = self._session_cookie_samesite()
        if self._request_is_secure():
            morsel["secure"] = True
        if clear:
            morsel["expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
            morsel["max-age"] = "0"
        elif max_age is not None:
            morsel["max-age"] = str(max(0, int(max_age)))
        return [("Set-Cookie", morsel.OutputString())]

    def _expire_session_cookie_headers(self) -> list[tuple[str, str]]:
        return self._session_cookie_headers("", clear=True)

    def _build_oidc_logout_url(self, *, id_token_hint: str = "", redirect_uri: str = "") -> str:
        if self.oidc is None:
            return ""
        try:
            discovery = get_oidc_discovery(self.oidc)
        except Exception:
            return ""
        end_session_endpoint = str(discovery.get("end_session_endpoint") or "").strip()
        if not end_session_endpoint:
            return ""
        params: dict[str, str] = {}
        if id_token_hint:
            params["id_token_hint"] = id_token_hint
        if redirect_uri:
            params["post_logout_redirect_uri"] = redirect_uri
        if self.oidc.client_id:
            params["client_id"] = self.oidc.client_id
        if not params:
            return end_session_endpoint
        return f"{end_session_endpoint}?{urlencode(params)}"

    def _require_user(self) -> dict[str, Any] | None:
        header = self.headers.get("Authorization") or ""
        token = ""
        if header.startswith("Bearer "):
            token = header.removeprefix("Bearer ").strip()
        if token:
            pat_store = self.pat_keys
            if pat_store is not None and looks_like_pat(token):
                pat_user = pat_store.verify(token)
                if pat_user is not None:
                    return pat_user
            auth = self.auth
            if auth is not None and getattr(auth, "enabled", False):
                user = auth.verify_token(token)
                if user is not None:
                    return user
            if self.oidc is not None:
                try:
                    claims = verify_oidc_token(token, self.oidc)
                except Exception:
                    claims = None
                if claims is not None:
                    return self._apply_external_user_policy(claims_to_user(claims, client_id=self.oidc.client_id))
        manager = self.sessions
        session_id = self._session_id_from_cookie()
        if manager is not None and session_id:
            user = manager.get_user(session_id, oidc_settings=self.oidc)
            if user is not None:
                if str(user.get("auth_type") or "") == "oidc":
                    return self._apply_external_user_policy(user)
                return user
        return None

    def _approval_required(self) -> bool:
        return _env_true("PIPELINE_OIDC_APPROVAL_REQUIRED") or _env_true("PIPELINE_USER_APPROVAL_REQUIRED")

    def _apply_external_user_policy(self, user: dict[str, Any]) -> dict[str, Any]:
        auth = self.auth
        if auth is not None and hasattr(auth, "resolve_external_user"):
            default_status = "pending" if self._approval_required() else "approved"
            return auth.resolve_external_user(user, default_status=default_status)
        if self._approval_required() and str(user.get("role") or "") != "admin":
            return {**user, "status": "pending"}
        return {**user, "status": user.get("status") or "approved"}

    def _is_admin(self, user: dict[str, Any] | None) -> bool:
        return bool(user and str(user.get("role") or "") == "admin")

    def _can_manage_models(self, user: dict[str, Any] | None) -> bool:
        role = str((user or {}).get("role") or "")
        return role in {"admin", "model_manager"}

    def _provider_scope_from_arguments(self, arguments: dict[str, Any], user: dict[str, Any] | None = None) -> str:
        provider = arguments.get("provider") if isinstance(arguments.get("provider"), dict) else {}
        raw = arguments.get("scope", provider.get("scope"))
        if raw is None:
            return "global" if self._can_manage_models(user) else "user"
        scope = str(raw or "global").strip().lower().replace("-", "_")
        if scope in {"global", "default", "admin"}:
            return "global"
        if scope in {"user", "personal", "mine"}:
            return "user"
        raise AuthError("scope must be one of: global, user")

    def _user_context(self, user: dict[str, Any] | None) -> dict[str, str]:
        return {
            "username": str((user or {}).get("username") or ""),
            "role": str((user or {}).get("role") or ""),
            "run_prefix": safe_run_prefix(str((user or {}).get("username") or "user")),
        }

    def _dispatcher_for_tool(self, user: dict[str, Any] | None, name: str) -> ToolDispatcher:
        dispatcher = self.dispatcher
        if name in _USER_PROVIDER_SCOPED_TOOLS and user is not None and isinstance(dispatcher, ToolDispatcher):
            from .app import build_runner

            return ToolDispatcher(build_runner(provider_user=str(user.get("username") or "")))
        return dispatcher

    def _user_is_approved(self, user: dict[str, Any] | None) -> bool:
        if user is None:
            return True
        if self._is_admin(user):
            return True
        status = str(user.get("status") or "approved").strip().lower()
        return status in {"", "approved", "active"}

    def _require_auth(self) -> dict[str, Any] | None:
        if not self._auth_enabled():
            return None
        user = self._require_user()
        if user is None:
            extra_headers = self._expire_session_cookie_headers() if self._session_id_from_cookie() else None
            self._json(401, {"ok": False, "error": "unauthorized"}, extra_headers=extra_headers)
        elif not self._user_is_approved(user):
            self._json(403, {"ok": False, "error": "approval required"})
            return None
        elif _env_true("PIPELINE_REQUIRE_ADMIN") and not self._is_admin(user):
            self._json(403, {"ok": False, "error": "admin required"})
            return None
        return user

    def _enforce_run_access(self, user: dict[str, Any] | None, run_id: str) -> None:
        if user is None or self._is_admin(user):
            return
        prefix = safe_run_prefix(str(user.get("username") or "user")) + "_"
        if not str(run_id or "").startswith(prefix):
            raise AuthError("run_id not allowed for this user")

    def _normalize_scoped_run_id(self, user: dict[str, Any] | None, run_id: str) -> str:
        raw = str(run_id or "").strip()
        if not raw or user is None or self._is_admin(user):
            return raw
        prefix = safe_run_prefix(str(user.get("username") or "user"))
        if raw.startswith(f"{prefix}_"):
            return raw
        normalized = safe_run_prefix(raw)
        if not normalized:
            return raw
        if normalized.startswith(f"{prefix}_"):
            return normalized
        return f"{prefix}_{normalized}"

    def _list_tools_for_user(self, user: dict[str, Any] | None) -> dict[str, Any]:
        tools = self.dispatcher.list_tools()
        entries = tools.get("tools") if isinstance(tools, dict) else None
        if not isinstance(entries, list):
            return tools
        full_list = _env_true("PIPELINE_MCP_FULL_TOOL_LIST")
        is_admin = self._is_admin(user)

        def _visible(name: str) -> bool:
            if not full_list and name in _MCP_LIST_HIDDEN_TOOLS:
                return False
            if user is not None and not is_admin and name in _ADMIN_ONLY_TOOLS:
                return False
            return True

        tools["tools"] = [
            item
            for item in entries
            if isinstance(item, dict) and _visible(str(item.get("name") or ""))
        ]
        return tools

    def _call_tool_for_user(
        self,
        user: dict[str, Any] | None,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if name in _ADMIN_ONLY_TOOLS and user is not None and not self._is_admin(user):
            raise AuthError("admin required")
        if (
            name == "pipeline.model_provider_update"
            and user is not None
            and self._provider_scope_from_arguments(arguments, user) == "global"
            and not self._can_manage_models(user)
        ):
            raise AuthError("model manager required")
        if name in _RUN_SCOPED_TOOLS:
            run_id = str(arguments.get("run_id") or "")
            if run_id:
                self._enforce_run_access(user, run_id)
        if name in {"pipeline.run", "pipeline.run_from_prompt"} and user is not None and not self._is_admin(user):
            prefix = safe_run_prefix(str(user.get("username") or "user"))
            run_id = arguments.get("run_id")
            if run_id:
                normalized_run_id = self._normalize_scoped_run_id(user, str(run_id))
                arguments["run_id"] = normalized_run_id
                self._enforce_run_access(user, normalized_run_id)
            else:
                arguments["run_id"] = new_run_id(prefix)
        if name in {
            "pipeline.run",
            "pipeline.preflight",
            "pipeline.run_from_prompt",
            "pipeline.submit_feedback",
            "pipeline.submit_experiment",
            "pipeline.save_report",
            "pipeline.save_project",
            "pipeline.list_projects",
            "pipeline.get_project",
            "pipeline.archive_project",
            "pipeline.restore_project",
            "pipeline.delete_project",
            "pipeline.save_round",
            "pipeline.list_rounds",
            "pipeline.get_round",
            "pipeline.archive_round",
            "pipeline.restore_round",
            "pipeline.delete_round",
            "pipeline.model_provider_list",
            "pipeline.model_provider_update",
            "pipeline.model_provider_health",
        } and user is not None:
            arguments.setdefault("user", self._user_context(user))
        out = self._dispatcher_for_tool(user, name).call_tool(name, arguments)
        if name == "pipeline.list_runs" and user is not None and not self._is_admin(user):
            prefix = safe_run_prefix(str(user.get("username") or "user")) + "_"
            runs = out.get("runs") if isinstance(out, dict) else None
            if isinstance(runs, list):
                out["runs"] = [r for r in runs if str(r).startswith(prefix)]
        self._attach_run_ui_url(name, out)
        return out

    def _public_base_url(self) -> str:
        """Best-effort public origin of this server, env-aware.

        Honours an explicit ``PIPELINE_PUBLIC_BASE_URL`` override, else derives
        scheme + host from the request (respecting reverse-proxy forwarding
        headers) so dev/staging/prod each emit their own correct origin.
        """
        override = str(os.environ.get("PIPELINE_PUBLIC_BASE_URL") or "").strip()
        if override:
            return override.rstrip("/")
        host = str(
            self.headers.get("X-Forwarded-Host")
            or self.headers.get("Host")
            or ""
        ).split(",", 1)[0].strip()
        if not host:
            return ""
        proto = "https" if self._request_is_secure() else "http"
        return f"{proto}://{host}"

    def _run_ui_url(self, run_id: str) -> str | None:
        run_id = str(run_id or "").strip()
        if not run_id:
            return None
        base = self._public_base_url()
        if not base:
            return None
        return f"{base}/?run={quote(run_id, safe='')}"

    def _attach_run_ui_url(self, name: str, out: Any) -> None:
        """Add a ``ui_url`` deep-link to run-producing tool results so an MCP
        client can point the user at the live web view of the run."""
        if not isinstance(out, dict) or "ui_url" in out:
            return
        if name not in {
            "pipeline.run",
            "pipeline.run_from_prompt",
            "pipeline.preflight",
            "pipeline.status",
            "pipeline.cancel_run",
        }:
            return
        run_id = out.get("run_id") or out.get("head_run_id")
        url = self._run_ui_url(str(run_id or ""))
        if url:
            out["ui_url"] = url

    def _mcp_success(self, request_id: str | int | None, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _mcp_error(
        self,
        request_id: str | int | None,
        code: int,
        message: str,
        *,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        error: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return {"jsonrpc": "2.0", "id": request_id, "error": error}

    def _mcp_tool_result(self, result: dict[str, Any]) -> dict[str, Any]:
        # MCP tool results must use a standard content type. The non-standard
        # {"type": "json", ...} block is rejected by strict clients (VS Code,
        # the mcp SDK) with "Unexpected response type". Return the serialized
        # JSON in a text block (accepted everywhere) plus structuredContent for
        # clients that consume machine-readable output — the spec-recommended pair.
        text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
        payload: dict[str, Any] = {
            "content": [{"type": "text", "text": text}],
            "isError": False,
        }
        if isinstance(result, dict):
            payload["structuredContent"] = result
        return payload

    def _send_mcp_token(self) -> None:
        user = None
        if self._auth_enabled():
            user = self._require_auth()
            if user is None:
                return  # 401/403 already emitted by _require_auth

        token = ""
        auth_type = ""
        expires_at = 0

        manager = self.sessions
        session_id = self._session_id_from_cookie()
        if manager is not None and session_id:
            session = manager.get_session(session_id, oidc_settings=self.oidc)
            if isinstance(session, dict):
                auth_type = str(session.get("auth_type") or "")
                if auth_type == "oidc":
                    token, expires_at = manager.get_oidc_access_token(
                        session_id, oidc_settings=self.oidc
                    )

        if not token:
            auth = self.auth
            if (
                auth is not None
                and getattr(auth, "enabled", False)
                and isinstance(user, dict)
            ):
                issued = auth.issue_token(user)
                token = str(issued.get("token") or "")
                expires_at = int(issued.get("expires_at") or 0)
                auth_type = auth_type or "local"

        if not token:
            self._json(
                409,
                {"ok": False, "error": "no MCP token available for this session"},
            )
            return

        self._json(
            200,
            {
                "ok": True,
                "token": token,
                "auth_type": auth_type or "local",
                "expires_at": int(expires_at or 0),
            },
            extra_headers=[("Cache-Control", "no-store")],
        )

    def _require_pat_user(self) -> dict[str, Any] | None:
        if not self._auth_enabled():
            self._json(400, {"ok": False, "error": "authentication required to manage API keys"})
            return None
        return self._require_auth()  # emits 401/403 and returns None if not authorized

    def _send_mcp_keys_list(self) -> None:
        user = self._require_pat_user()
        if user is None:
            return
        store = self.pat_keys
        keys = store.list_keys(str(user.get("username") or "")) if store is not None else []
        self._json(200, {"ok": True, "keys": keys}, extra_headers=[("Cache-Control", "no-store")])

    def _create_mcp_key(self) -> None:
        user = self._require_pat_user()
        if user is None:
            return
        store = self.pat_keys
        if store is None:
            self._json(503, {"ok": False, "error": "API keys unavailable"})
            return
        body = self._read_json()
        label = str(body.get("label") or "")
        raw_ttl = body.get("ttl_days")
        try:
            ttl_days = (
                int(raw_ttl) if raw_ttl is not None and str(raw_ttl).strip() != "" else None
            )
        except (TypeError, ValueError):
            ttl_days = None
        created = store.create_key(user, label=label, ttl_days=ttl_days)
        self._json(200, {"ok": True, **created}, extra_headers=[("Cache-Control", "no-store")])

    def _revoke_mcp_key(self) -> None:
        user = self._require_pat_user()
        if user is None:
            return
        store = self.pat_keys
        body = self._read_json()
        key_id = str(body.get("id") or "")
        ok = store.revoke(str(user.get("username") or ""), key_id) if store is not None else False
        self._json(200 if ok else 404, {"ok": ok})

    def _send_model_registration_skill_archive(self) -> None:
        if self._auth_enabled():
            user = self._require_auth()
            if user is None:
                return

        raw_root = os.environ.get("PIPELINE_MODEL_REGISTRATION_SKILL_DIR", "/opt/protein-model-api-registration")
        root = Path(raw_root).expanduser().resolve()
        if not root.is_dir():
            self._json(
                404,
                {
                    "ok": False,
                    "error": "model registration skill directory not found",
                    "path": str(root),
                },
            )
            return

        buffer = io.BytesIO()
        archive_root = Path("protein-model-api-registration")
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for file_path in sorted(root.rglob("*")):
                if not file_path.is_file():
                    continue
                archive.write(file_path, str(archive_root / file_path.relative_to(root)))

        self._binary(
            200,
            buffer.getvalue(),
            "application/zip",
            extra_headers=[
                ("Content-Disposition", 'attachment; filename="protein-model-api-registration.zip"'),
                ("Cache-Control", "no-store"),
            ],
        )

    def _send_pipeline_skill_archive(self) -> None:
        if self._auth_enabled():
            user = self._require_auth()
            if user is None:
                return

        default_dir = str(
            (Path(__file__).resolve().parents[3] / "skills" / "protein-pipeline-stepper")
        )
        raw_root = os.environ.get("PIPELINE_STEPPER_SKILL_DIR", default_dir)
        root = Path(raw_root).expanduser().resolve()
        if not root.is_dir():
            self._json(
                404,
                {
                    "ok": False,
                    "error": "pipeline skill directory not found",
                    "path": str(root),
                },
            )
            return

        buffer = io.BytesIO()
        archive_root = Path("protein-pipeline-stepper")
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for file_path in sorted(root.rglob("*")):
                if not file_path.is_file():
                    continue
                archive.write(file_path, str(archive_root / file_path.relative_to(root)))

        self._binary(
            200,
            buffer.getvalue(),
            "application/zip",
            extra_headers=[
                ("Content-Disposition", 'attachment; filename="protein-pipeline-stepper.zip"'),
                ("Cache-Control", "no-store"),
            ],
        )

    def _send_run_export_archive(self) -> None:
        route_path = self._route_path()
        prefix = "/runs/"
        marker = "/exports/"
        if not route_path.startswith(prefix) or marker not in route_path[len(prefix) :]:
            self._json(404, {"error": "not found"})
            return

        run_part, filename_part = route_path[len(prefix) :].split(marker, 1)
        run_id = unquote(run_part)
        filename = unquote(filename_part)
        if not run_id or not filename:
            self._json(400, {"ok": False, "error": "run_id and filename are required"})
            return
        if not all(ch.isalnum() or ch in "._-" for ch in filename):
            self._json(400, {"ok": False, "error": "invalid export filename"})
            return
        if "/" in filename or "\\" in filename or filename in {".", ".."}:
            self._json(400, {"ok": False, "error": "invalid export filename"})
            return
        if not filename.lower().endswith(".zip"):
            self._json(400, {"ok": False, "error": "export filename must be a zip"})
            return

        user = None
        if self._auth_enabled():
            user = self._require_auth()
            if user is None:
                return

        try:
            self._enforce_run_access(user, run_id)
            run_root = resolve_run_path(self.dispatcher.runner.output_root, run_id)
        except AuthError as exc:
            self._json(403, {"ok": False, "error": str(exc)})
            return
        except ValueError as exc:
            self._json(400, {"ok": False, "error": str(exc)})
            return

        if not run_root.exists():
            self._json(404, {"ok": False, "error": "run_id not found"})
            return

        export_dir = (run_root / "exports").resolve()
        target = (export_dir / filename).resolve()
        if target.parent != export_dir:
            self._json(400, {"ok": False, "error": "invalid export filename"})
            return
        if not target.is_file():
            self._json(404, {"ok": False, "error": "export package not found"})
            return

        self._file_download(
            target,
            "application/zip",
            filename=filename,
            extra_headers=[("Cache-Control", "no-store")],
        )

    def _handle_mcp_rpc(self, body: dict[str, Any], user: dict[str, Any] | None) -> dict[str, Any]:
        request_id = body.get("id")
        method = body.get("method")
        params = body.get("params") or {}
        if not isinstance(method, str) or not method.strip():
            return self._mcp_error(request_id, -32600, "Invalid request: method is required")
        if not isinstance(params, dict):
            return self._mcp_error(request_id, -32602, "params must be an object")

        try:
            if method == "initialize":
                return self._mcp_success(
                    request_id,
                    {
                        "protocolVersion": "2025-06-18",
                        "serverInfo": {"name": "protein-pipeline", "version": "0.0.0"},
                        "capabilities": {"tools": {"listChanged": False}},
                    },
                )
            if method == "ping":
                return self._mcp_success(request_id, {"status": "ok"})
            if method == "tools/list":
                return self._mcp_success(request_id, self._list_tools_for_user(user))
            if method == "tools/call":
                name = params.get("name")
                arguments = params.get("arguments") or {}
                if not isinstance(name, str) or not name.strip():
                    return self._mcp_error(request_id, -32602, "tools/call requires params.name")
                if not isinstance(arguments, dict):
                    return self._mcp_error(request_id, -32602, "tools/call requires params.arguments object")
                result = self._call_tool_for_user(user, name, arguments)
                return self._mcp_success(request_id, self._mcp_tool_result(result))
            return self._mcp_error(request_id, -32601, f"Method not found: {method}")
        except ValueError as exc:
            return self._mcp_error(request_id, -32602, str(exc))
        except AuthError as exc:
            return self._mcp_error(request_id, -32000, str(exc))
        except Exception as exc:  # noqa: BLE001
            return self._mcp_error(request_id, -32000, "Internal server error", data={"detail": str(exc)})

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._set_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        route_path = self._route_path()
        if route_path == "/auth/oidc/config":
            if self.oidc is None:
                self._json(200, {"ok": True, "enabled": False})
                return
            try:
                discovery = get_oidc_discovery(self.oidc)
                authorization_endpoint = str(discovery.get("authorization_endpoint") or "")
            except Exception:
                authorization_endpoint = ""
            self._json(
                200,
                {
                    "ok": True,
                    "enabled": True,
                    "issuer": self.oidc.issuer,
                    "client_id": self.oidc.client_id,
                    "scopes": self.oidc.scopes,
                    "provider_name": self.oidc.provider_name,
                    "authorization_endpoint": authorization_endpoint,
                    "end_session_endpoint": str(discovery.get("end_session_endpoint") or ""),
                    "account_url": account_console_url(self.oidc),
                },
            )
            return
        if route_path == "/auth/me":
            user = self._require_auth()
            if user is None:
                return
            self._json(200, {"ok": True, "user": user, "session": self._public_session_info()})
            return
        if route_path == "/auth/mcp_token":
            self._send_mcp_token()
            return
        if route_path == "/auth/mcp_keys":
            self._send_mcp_keys_list()
            return
        if route_path == "/model_provider_skill.zip":
            self._send_model_registration_skill_archive()
            return
        if route_path == "/pipeline_skill.zip":
            self._send_pipeline_skill_archive()
            return
        if route_path.startswith("/runs/") and "/exports/" in route_path:
            self._send_run_export_archive()
            return
        if route_path == "/healthz":
            self._json(200, {"ok": True})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            route_path = self._route_path()
            if route_path == "/auth/login":
                auth = self.auth
                if auth is None or not getattr(auth, "enabled", False):
                    self._json(400, {"ok": False, "error": "local auth disabled"})
                    return
                body = self._read_json()
                username = str(body.get("username") or "")
                password = str(body.get("password") or "")
                result = auth.authenticate(username, password)
                if result is None:
                    self._json(401, {"ok": False, "error": "invalid credentials"})
                    return
                extra_headers = None
                manager = self.sessions
                user = result.get("user") if isinstance(result, dict) else None
                if _env_true("PIPELINE_REQUIRE_ADMIN") and not self._is_admin(user):
                    self._json(403, {"ok": False, "error": "admin required"})
                    return
                if manager is not None and isinstance(user, dict):
                    session_id = manager.create_local_session(user)
                    extra_headers = self._session_cookie_headers(session_id, max_age=manager.cookie_max_age(session_id))
                self._json(200, {"ok": True, **result, "session": {"auth_type": "local"}}, extra_headers=extra_headers)
                return

            if route_path == "/auth/oidc/exchange":
                if self.oidc is None:
                    self._json(400, {"ok": False, "error": "oidc disabled"})
                    return
                body = self._read_json()
                code = str(body.get("code") or "")
                redirect_uri = str(body.get("redirect_uri") or "")
                code_verifier = str(body.get("code_verifier") or "") or None
                token_data = exchange_oidc_code(
                    self.oidc,
                    code=code,
                    redirect_uri=redirect_uri,
                    code_verifier=code_verifier,
                )
                claims = claims_from_oidc_token_data(self.oidc, token_data)
                user = self._apply_external_user_policy(claims_to_user(claims, client_id=self.oidc.client_id))
                if not self._user_is_approved(user):
                    self._json(403, {"ok": False, "error": "approval required", "user": user})
                    return
                if _env_true("PIPELINE_REQUIRE_ADMIN") and not self._is_admin(user):
                    self._json(403, {"ok": False, "error": "admin required"})
                    return
                extra_headers = None
                manager = self.sessions
                if manager is not None:
                    session_id = manager.create_oidc_session(self.oidc, token_data, user=user)
                    extra_headers = self._session_cookie_headers(session_id, max_age=manager.cookie_max_age(session_id))
                self._json(
                    200,
                    {
                        "ok": True,
                        "user": user,
                        "session": {"auth_type": "oidc"},
                    },
                    extra_headers=extra_headers,
                )
                return

            if route_path == "/auth/mcp_keys":
                self._create_mcp_key()
                return
            if route_path == "/auth/mcp_keys/revoke":
                self._revoke_mcp_key()
                return

            if route_path == "/auth/logout":
                body = self._read_json()
                redirect_uri = str(body.get("redirect_uri") or "").strip()
                logout_url = ""
                manager = self.sessions
                session_id = self._session_id_from_cookie()
                if manager is not None and session_id:
                    session = manager.get_session(session_id, oidc_settings=self.oidc)
                    id_token_hint = manager.get_oidc_id_token(session_id)
                    manager.destroy_session(session_id)
                    if isinstance(session, dict) and str(session.get("auth_type") or "") == "oidc":
                        logout_url = self._build_oidc_logout_url(
                            id_token_hint=id_token_hint,
                            redirect_uri=redirect_uri,
                        )
                self._json(
                    200,
                    {"ok": True, "logout_url": logout_url},
                    extra_headers=self._expire_session_cookie_headers(),
                )
                return

            if route_path == "/auth/list_users":
                auth = self.auth
                if auth is None or not getattr(auth, "enabled", False):
                    self._json(400, {"ok": False, "error": "auth disabled"})
                    return
                user = self._require_auth()
                if user is None:
                    return
                if not self._is_admin(user):
                    self._json(403, {"ok": False, "error": "admin required"})
                    return
                self._json(200, {"ok": True, "users": auth.list_users()})
                return

            if route_path == "/auth/update_user":
                auth = self.auth
                if auth is None or not getattr(auth, "enabled", False):
                    self._json(400, {"ok": False, "error": "auth disabled"})
                    return
                user = self._require_auth()
                if user is None:
                    return
                if not self._is_admin(user):
                    self._json(403, {"ok": False, "error": "admin required"})
                    return
                body = self._read_json()
                updated = auth.update_user(
                    username=str(body.get("username") or "").strip(),
                    role=str(body.get("role") or "").strip() or None,
                    status=str(body.get("status") or "").strip() or None,
                )
                self._json(200, {"ok": True, "user": updated})
                return

            if route_path == "/auth/create_user":
                auth = self.auth
                if auth is None or not getattr(auth, "enabled", False):
                    self._json(400, {"ok": False, "error": "auth disabled"})
                    return
                user = self._require_auth()
                if user is None:
                    return
                if not self._is_admin(user):
                    self._json(403, {"ok": False, "error": "admin required"})
                    return
                body = self._read_json()
                username = str(body.get("username") or "")
                password = str(body.get("password") or "")
                role = str(body.get("role") or "user")
                created = auth.create_user(username=username, password=password, role=role)
                self._json(200, {"ok": True, "user": created})
                return

            if route_path == "/mcp":
                user = self._require_auth()
                if user is None and self._auth_enabled():
                    return
                body = self._read_json()
                self._json(200, self._handle_mcp_rpc(body, user))
                return

            if route_path == "/tools/list":
                user = self._require_auth()
                if user is None and self._auth_enabled():
                    return
                self._json(200, self._list_tools_for_user(user))
                return
            if route_path == "/tools/call":
                user = self._require_auth()
                if user is None and self._auth_enabled():
                    return
                body = self._read_json()
                name = body.get("name")
                arguments = body.get("arguments") or {}
                if not isinstance(name, str) or not isinstance(arguments, dict):
                    raise ValueError("Expected {name: str, arguments: object}")
                out = self._call_tool_for_user(user, name, arguments)
                self._json(200, {"ok": True, "result": out})
                return
            self._json(404, {"error": "not found"})
        except Exception as exc:
            self.log_error("error handling %s: %s", self.path, exc)
            status = 403 if isinstance(exc, AuthError) else 400
            self._json(status, {"ok": False, "error": str(exc)})


def _bootstrap_queue_durations(runner) -> None:
    """One-time seed of the queue-ETA duration store from historical run events,
    so ETA works before any new job completes. Best-effort; never blocks startup."""
    try:
        from .config import load_config
        from .queue_stats import QueueStatsStore, bootstrap_from_events

        store = QueueStatsStore(runner.output_root)
        if not store.is_empty():
            return
        rp = load_config().runpod

        def _event_endpoint(stage: str) -> str | None:
            s = str(stage or "").lower()
            if "mmseqs" in s or "msa" in s or "novelty" in s:
                return rp.mmseqs_endpoint_id
            if "rfd3" in s:
                return rp.rfd3_endpoint_id
            if "bioemu" in s:
                return rp.bioemu_endpoint_id
            if "mpnn" in s or s == "design":
                return rp.proteinmpnn_endpoint_id
            if "af2" in s or "colabfold" in s or "alphafold" in s:
                return rp.alphafold2_endpoint_id or rp.colabfold_endpoint_id
            return None

        bootstrap_from_events(runner.output_root, _event_endpoint)
    except Exception:
        pass


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    from .app import build_runner

    runner = build_runner()
    ensure_runpod_metrics_collector(runner)
    _bootstrap_queue_durations(runner)
    global _DISPATCHER
    _DISPATCHER = ToolDispatcher(runner)
    global _AUTH
    _AUTH = load_auth_manager()
    global _OIDC
    _OIDC = load_oidc_settings()
    global _SESSIONS
    _SESSIONS = load_session_manager()
    global _PAT_KEYS
    _PAT_KEYS = load_pat_store()
    _init_cors()

    # Resume runs orphaned by a previous restart (re-attach to recorded job_ids).
    try:
        from .run_recovery import start_run_recovery

        start_run_recovery(_DISPATCHER, runner.output_root)
    except Exception as exc:  # noqa: BLE001 - recovery must never block startup
        print(f"[run-recovery] startup hook failed: {exc}", flush=True)

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"listening: http://{args.host}:{args.port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
