import sys
from types import SimpleNamespace

from pipeline_mcp import oidc
from pipeline_mcp.oidc import OIDCSettings
from pipeline_mcp.oidc import _claims_match_expected_client
from pipeline_mcp.oidc import account_console_url
from pipeline_mcp.oidc import claims_to_user
from pipeline_mcp.oidc import is_google_issuer
from pipeline_mcp.oidc import refresh_oidc_tokens
from pipeline_mcp.oidc import verify_oidc_token


def test_claims_to_user_maps_pipeline_roles_and_identity():
    claims = {
        "sub": "user-123",
        "email": "tester@kribb.re.kr",
        "preferred_username": "tester@kribb.re.kr",
        "resource_access": {
            "protein-pipeline": {"roles": ["pipeline-user"]},
        },
    }

    user = claims_to_user(claims)

    assert user["username"] == "tester@kribb.re.kr"
    assert user["role"] == "user"
    assert user["status"] == "approved"
    assert user["run_prefix"] == "tester_kribb.re.kr"


def test_claims_to_user_maps_realm_admin_to_admin():
    claims = {
        "sub": "user-123",
        "email": "sso-admin@kribb.re.kr",
        "preferred_username": "sso-admin@kribb.re.kr",
        "resource_access": {
            "realm-management": {"roles": ["realm-admin"]},
        },
    }

    user = claims_to_user(claims)

    assert user["role"] == "admin"


def test_claims_to_user_maps_model_manager_role():
    claims = {
        "sub": "user-123",
        "email": "manager@example.org",
        "preferred_username": "manager@example.org",
        "resource_access": {
            "protein-pipeline": {"roles": ["pipeline-model-manager"]},
        },
    }

    user = claims_to_user(claims, client_id="protein-pipeline")

    assert user["role"] == "model_manager"


def test_google_issuer_shorthand_is_detected(monkeypatch):
    monkeypatch.setenv("PIPELINE_OIDC_ISSUER", "google")
    monkeypatch.setenv("PIPELINE_OIDC_CLIENT_ID", "client-id.apps.googleusercontent.com")
    settings = oidc.load_oidc_settings()

    assert settings is not None
    assert settings.issuer == "https://accounts.google.com"
    assert settings.provider_name == "Google"
    assert is_google_issuer(settings.issuer) is True


def test_account_console_url_uses_realm_issuer():
    settings = OIDCSettings(
        issuer="https://sso.k-biofoundrycopilot.duckdns.org/realms/kbf",
        client_id="protein-pipeline",
        audience="protein-pipeline",
        scopes="openid profile email",
        provider_name="KBF SSO",
        jwks_url=None,
        algorithms=("RS256",),
    )

    assert account_console_url(settings) == "https://sso.k-biofoundrycopilot.duckdns.org/realms/kbf/account/"


def test_claims_match_expected_client_accepts_keycloak_azp_fallback():
    settings = OIDCSettings(
        issuer="https://sso.k-biofoundrycopilot.duckdns.org/realms/kbf",
        client_id="protein-pipeline",
        audience="protein-pipeline",
        scopes="openid profile email",
        provider_name="KBF SSO",
        jwks_url=None,
        algorithms=("RS256",),
    )

    claims = {
        "iss": settings.issuer,
        "sub": "user-123",
        "aud": "account",
        "azp": "protein-pipeline",
    }

    assert _claims_match_expected_client(claims, settings) is True


def test_claims_match_expected_client_rejects_other_client():
    settings = OIDCSettings(
        issuer="https://sso.k-biofoundrycopilot.duckdns.org/realms/kbf",
        client_id="protein-pipeline",
        audience="protein-pipeline",
        scopes="openid profile email",
        provider_name="KBF SSO",
        jwks_url=None,
        algorithms=("RS256",),
    )

    claims = {
        "iss": settings.issuer,
        "sub": "user-123",
        "aud": "account",
        "azp": "unrelated-client",
    }

    assert _claims_match_expected_client(claims, settings) is False


def test_verify_oidc_token_uses_cached_metadata_when_upstream_fetch_temporarily_fails(monkeypatch):
    settings = OIDCSettings(
        issuer="https://sso.k-biofoundrycopilot.duckdns.org/realms/kbf",
        client_id="protein-pipeline",
        audience="protein-pipeline",
        scopes="openid profile email",
        provider_name="KBF SSO",
        jwks_url=None,
        algorithms=("RS256",),
    )
    claims = {
        "iss": settings.issuer,
        "sub": "user-123",
        "aud": "account",
        "azp": settings.client_id,
    }
    discovery_url = f"{settings.issuer}/.well-known/openid-configuration"
    jwks_url = f"{settings.issuer}/protocol/openid-connect/certs"
    successful_responses = {
        discovery_url: {
            "issuer": settings.issuer,
            "jwks_uri": jwks_url,
        },
        jwks_url: {
            "keys": [{"kid": "kid-1", "kty": "RSA", "n": "abc", "e": "AQAB"}],
        },
    }
    request_attempts = []

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeJWT:
        @staticmethod
        def get_unverified_header(_token):
            return {"kid": "kid-1"}

        @staticmethod
        def decode(_token, _signing_key, algorithms, options):
            assert algorithms == ["RS256"]
            assert options["verify_signature"] is True
            return dict(claims)

    def fake_get(url, timeout=10):
        assert timeout == 10
        request_attempts.append(url)
        if len(request_attempts) <= 2:
            return FakeResponse(successful_responses[url])
        raise RuntimeError("temporary network failure")

    monkeypatch.setattr(oidc.requests, "get", fake_get)
    monkeypatch.setitem(sys.modules, "jose", SimpleNamespace(jwt=FakeJWT))
    monkeypatch.setattr(oidc, "_OIDC_DISCOVERY_CACHE", {}, raising=False)
    monkeypatch.setattr(oidc, "_OIDC_JWKS_CACHE", {}, raising=False)

    assert verify_oidc_token("header.payload.sig", settings) == claims

    # The second verification should keep using cached discovery/JWKS data.
    assert verify_oidc_token("header.payload.sig", settings) == claims
    assert request_attempts == [discovery_url, jwks_url]


def test_refresh_oidc_tokens_uses_refresh_token_grant(monkeypatch):
    settings = OIDCSettings(
        issuer="https://sso.k-biofoundrycopilot.duckdns.org/realms/kbf",
        client_id="protein-pipeline",
        audience="protein-pipeline",
        scopes="openid profile email",
        provider_name="KBF SSO",
        jwks_url=None,
        algorithms=("RS256",),
    )
    expected = {
        "access_token": "next-access",
        "refresh_token": "next-refresh",
        "id_token": "next-id",
        "expires_in": 300,
        "refresh_expires_in": 1800,
    }
    calls = []

    class FakeResponse:
        status_code = 200
        content = b"{}"

        def json(self):
            return dict(expected)

    def fake_post(url, data=None, timeout=10):
        calls.append((url, dict(data or {}), timeout))
        return FakeResponse()

    monkeypatch.setattr(oidc, "get_oidc_discovery", lambda settings, force_refresh=False: {
        "token_endpoint": f"{settings.issuer}/protocol/openid-connect/token"
    })
    monkeypatch.setattr(oidc.requests, "post", fake_post)

    assert refresh_oidc_tokens(settings, refresh_token="refresh-123") == expected
    assert calls == [
        (
            f"{settings.issuer}/protocol/openid-connect/token",
            {
                "grant_type": "refresh_token",
                "refresh_token": "refresh-123",
                "client_id": "protein-pipeline",
            },
            10,
        )
    ]
