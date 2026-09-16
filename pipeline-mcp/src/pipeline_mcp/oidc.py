from __future__ import annotations

from dataclasses import dataclass
import os
import time
from typing import Any

import requests

from .auth import safe_run_prefix


_OIDC_CACHE_TTL_S = 300.0
_OIDC_DISCOVERY_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_OIDC_JWKS_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}


@dataclass(frozen=True)
class OIDCSettings:
    issuer: str
    client_id: str
    audience: str | None
    scopes: str
    provider_name: str
    jwks_url: str | None
    algorithms: tuple[str, ...]


def _env(key: str) -> str | None:
    value = os.environ.get(key)
    if not value:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_issuer(issuer: str) -> str:
    normalized = issuer.strip().rstrip("/")
    if normalized.lower() in {"google", "google-oauth2", "google_oidc"}:
        return "https://accounts.google.com"
    suffix = "/.well-known/openid-configuration"
    if normalized.endswith(suffix):
        normalized = normalized[: -len(suffix)].rstrip("/")
    if "://" not in normalized:
        normalized = f"https://{normalized}"
    return normalized


def is_google_issuer(issuer: str) -> bool:
    return _normalize_issuer(issuer) == "https://accounts.google.com"


def _extract_audiences(claims: dict[str, Any]) -> set[str]:
    raw = claims.get("aud")
    if isinstance(raw, str):
        normalized = raw.strip()
        return {normalized} if normalized else set()
    if isinstance(raw, list):
        audiences: set[str] = set()
        for item in raw:
            if item is None:
                continue
            normalized = str(item).strip()
            if normalized:
                audiences.add(normalized)
        return audiences
    return set()


def _claims_match_expected_client(claims: dict[str, Any], settings: OIDCSettings) -> bool:
    if settings.audience:
        audiences = _extract_audiences(claims)
        if settings.audience in audiences:
            return True

    azp = claims.get("azp")
    if isinstance(azp, str) and azp.strip() == settings.client_id:
        return True

    return not settings.audience


def load_oidc_settings() -> OIDCSettings | None:
    issuer = _env("PIPELINE_OIDC_ISSUER") or _env("OIDC_ISSUER")
    client_id = _env("PIPELINE_OIDC_CLIENT_ID") or _env("OIDC_CLIENT_ID")
    if not issuer or not client_id:
        return None
    audience = _env("PIPELINE_OIDC_AUDIENCE") or client_id
    scopes = _env("PIPELINE_OIDC_SCOPES") or "openid profile email"
    normalized_issuer = _normalize_issuer(issuer)
    provider_name = _env("PIPELINE_OIDC_PROVIDER_NAME") or ("Google" if is_google_issuer(normalized_issuer) else "KBF SSO")
    jwks_url = _env("PIPELINE_OIDC_JWKS_URL")
    algorithms_raw = _env("PIPELINE_OIDC_ALGORITHMS") or "RS256"
    algorithms = tuple(item.strip() for item in algorithms_raw.split(",") if item.strip())
    return OIDCSettings(
        issuer=normalized_issuer,
        client_id=client_id,
        audience=audience,
        scopes=scopes,
        provider_name=provider_name,
        jwks_url=jwks_url,
        algorithms=algorithms,
    )


def _cache_lookup[T](cache: dict[str, tuple[float, T]], key: str) -> tuple[T | None, bool]:
    cached = cache.get(key)
    if cached is None:
        return None, False
    expires_at, payload = cached
    return payload, expires_at > time.time()


def _cache_store[T](cache: dict[str, tuple[float, T]], key: str, payload: T) -> T:
    cache[key] = (time.time() + _OIDC_CACHE_TTL_S, payload)
    return payload


def get_oidc_discovery(settings: OIDCSettings, *, force_refresh: bool = False) -> dict[str, Any]:
    cache_key = settings.issuer
    cached_payload, is_fresh = _cache_lookup(_OIDC_DISCOVERY_CACHE, cache_key)
    if cached_payload is not None and is_fresh and not force_refresh:
        return cached_payload

    try:
        response = requests.get(
            f"{settings.issuer}/.well-known/openid-configuration",
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("invalid OIDC discovery payload")
        return _cache_store(_OIDC_DISCOVERY_CACHE, cache_key, payload)
    except Exception:
        if cached_payload is not None:
            return cached_payload
        raise


def account_console_url(settings: OIDCSettings) -> str:
    issuer = settings.issuer.rstrip("/")
    if not issuer:
        return ""
    return f"{issuer}/account/"


def get_client_roles(claims: dict[str, Any], client_id: str) -> set[str]:
    resource_access = claims.get("resource_access")
    if not isinstance(resource_access, dict):
        return set()
    client_block = resource_access.get(client_id)
    if not isinstance(client_block, dict):
        return set()
    roles = client_block.get("roles")
    if not isinstance(roles, list):
        return set()
    return {str(role) for role in roles}


def get_realm_roles(claims: dict[str, Any]) -> set[str]:
    roles: set[str] = set()
    realm_access = claims.get("realm_access")
    if isinstance(realm_access, dict):
        realm_roles = realm_access.get("roles")
        if isinstance(realm_roles, list):
            roles.update(str(role) for role in realm_roles)
    roles.update(get_client_roles(claims, "realm-management"))
    return roles


def _get_jwks_keys(settings: OIDCSettings, *, force_refresh: bool = False) -> list[dict[str, Any]]:
    jwks_uri = settings.jwks_url
    cache_key = jwks_uri or settings.issuer
    cached_keys, is_fresh = _cache_lookup(_OIDC_JWKS_CACHE, cache_key)
    if cached_keys is not None and is_fresh and not force_refresh:
        return cached_keys

    if not jwks_uri:
        discovery = get_oidc_discovery(settings, force_refresh=force_refresh)
        jwks_uri = str(discovery.get("jwks_uri") or "")
    if not jwks_uri:
        raise ValueError("OIDC JWKS URI is not configured")

    try:
        jwks_response = requests.get(jwks_uri, timeout=10)
        jwks_response.raise_for_status()
        jwks_payload = jwks_response.json()
        keys = jwks_payload.get("keys") if isinstance(jwks_payload, dict) else None
        if not isinstance(keys, list) or not keys:
            raise ValueError("OIDC JWKS payload is invalid")
        normalized = [key for key in keys if isinstance(key, dict)]
        if not normalized:
            raise ValueError("OIDC JWKS payload is invalid")
        return _cache_store(_OIDC_JWKS_CACHE, cache_key, normalized)
    except Exception:
        if cached_keys is not None:
            return cached_keys
        raise


def claims_to_user(claims: dict[str, Any], client_id: str = "protein-pipeline") -> dict[str, Any]:
    username = (
        str(claims.get("preferred_username") or "").strip()
        or str(claims.get("email") or "").strip()
        or str(claims.get("sub") or "").strip()
    )
    if not username:
        raise ValueError("missing subject")
    roles = get_client_roles(claims, client_id)
    realm_roles = get_realm_roles(claims)
    if "pipeline-admin" in roles or "realm-admin" in realm_roles:
        role = "admin"
    elif "pipeline-model-manager" in roles or "model-manager" in roles:
        role = "model_manager"
    else:
        role = "user"
    return {
        "username": username,
        "role": role,
        "status": "approved",
        "created_at": "",
        "run_prefix": safe_run_prefix(username),
        "subject": str(claims.get("sub") or ""),
        "email": str(claims.get("email") or ""),
    }


def _select_signing_key(keys: list[dict[str, Any]], kid: Any) -> dict[str, Any] | None:
    for key in keys:
        if key.get("kid") == kid:
            return key
    if len(keys) == 1:
        return keys[0]
    return None


def verify_oidc_token(token: str, settings: OIDCSettings) -> dict[str, Any]:
    try:
        from jose import jwt
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"python-jose is required for OIDC: {exc}") from exc

    header = jwt.get_unverified_header(token)
    kid = header.get("kid")

    keys = _get_jwks_keys(settings)
    signing_key = _select_signing_key(keys, kid)
    if signing_key is None:
        keys = _get_jwks_keys(settings, force_refresh=True)
        signing_key = _select_signing_key(keys, kid)
    if signing_key is None:
        raise ValueError("unable to select OIDC signing key")

    claims = jwt.decode(
        token,
        signing_key,
        algorithms=list(settings.algorithms),
        options={
            "verify_signature": True,
            "verify_exp": True,
            "verify_aud": False,
            "verify_iss": False,
            "verify_at_hash": False,
        },
    )
    if _normalize_issuer(str(claims.get("iss") or "")) != settings.issuer:
        raise ValueError("invalid OIDC issuer")
    if not _claims_match_expected_client(claims, settings):
        raise ValueError("invalid OIDC audience")
    if not isinstance(claims, dict):
        raise ValueError("invalid OIDC claims")
    return claims


def claims_from_oidc_token_data(settings: OIDCSettings, token_data: dict[str, Any]) -> dict[str, Any]:
    access_token = str(token_data.get("access_token") or "").strip()
    id_token = str(token_data.get("id_token") or "").strip()
    errors: list[str] = []
    for token in (access_token, id_token):
        if not token:
            continue
        try:
            return verify_oidc_token(token, settings)
        except Exception as exc:
            errors.append(str(exc))
    if errors:
        raise ValueError("; ".join(errors))
    raise ValueError("OIDC token response did not include a usable access_token or id_token")


def _post_token_request(token_endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(token_endpoint, data=payload, timeout=10)
    token_data = response.json() if response.content else {}
    if response.status_code >= 400:
        detail = token_data.get("error_description") or token_data.get("error") or f"HTTP {response.status_code}"
        raise ValueError(str(detail))
    if not isinstance(token_data, dict):
        raise ValueError("invalid OIDC token response")
    return token_data


def exchange_oidc_code(
    settings: OIDCSettings,
    *,
    code: str,
    redirect_uri: str,
    code_verifier: str | None = None,
) -> dict[str, Any]:
    discovery = get_oidc_discovery(settings)
    token_endpoint = str(discovery.get("token_endpoint") or "")
    if not token_endpoint:
        raise ValueError("OIDC token endpoint is unavailable")

    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": settings.client_id,
    }
    if code_verifier:
        payload["code_verifier"] = code_verifier

    return _post_token_request(token_endpoint, payload)


def refresh_oidc_tokens(settings: OIDCSettings, *, refresh_token: str) -> dict[str, Any]:
    discovery = get_oidc_discovery(settings)
    token_endpoint = str(discovery.get("token_endpoint") or "")
    if not token_endpoint:
        raise ValueError("OIDC token endpoint is unavailable")
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": str(refresh_token or "").strip(),
        "client_id": settings.client_id,
    }
    if not payload["refresh_token"]:
        raise ValueError("refresh_token is required")
    return _post_token_request(token_endpoint, payload)
