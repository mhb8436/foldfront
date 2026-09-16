"""Authentication and authorisation.

Token verification, JWKS rotation and issuer normalisation are called straight
out of the original oidc.py. That code has run in the field; reimplementing it
would only be a chance to get it wrong. Two things are left for this module:

  1. Map the roles the original emits - admin, model_manager, user - onto the
     four this platform uses.
  2. Wrap the result as a FastAPI dependency so a path can state a minimum role.

No account is defined in code. Who someone is comes entirely from the OIDC
provider.

**Development mode.** With no OIDC configured, authentication is off, which is
what lets the console and the tests run locally. It does not switch off
quietly: startup logs a warning and /healthz reports auth as "disabled", so a
deployment that reaches production in that state says so.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated, Any, Iterable

from fastapi import Depends, Header

from foldfront.core.config import get_settings
from foldfront.core.errors import ApiError, E
from foldfront.db.models import Role

log = logging.getLogger(__name__)

#  The original's names on the left. It has no read-only role and no machine
#  account, so those are additions rather than translations.
ROLE_MAP: dict[str, Role] = {
    "admin": Role.ADMIN,
    "model_manager": Role.ADMIN,
    "user": Role.RESEARCHER,
}


@dataclass(frozen=True)
class Identity:
    """A verified identity. Screens and the audit trail see only this."""

    user_id: str
    subject: str
    email: str
    roles: tuple[Role, ...]
    authenticated: bool

    def has(self, *wanted: Role) -> bool:
        return any(r in self.roles for r in wanted)


def dev_identity() -> Identity:
    """Stands in while authentication is off.

    Same shape as a real identity, so the permission checks run down one path
    rather than two. Its role comes from DEV_ROLE, which is how the console can
    be seen as a viewer without standing up an identity provider.
    """
    try:
        role = Role(get_settings().dev_role)
    except ValueError:
        role = Role.ADMIN
    return Identity(user_id="dev", subject="dev", email="", roles=(role,), authenticated=False)


def oidc_enabled() -> bool:
    """Whether OIDC is configured, by the original's environment convention."""
    try:
        from pipeline_mcp.oidc import load_oidc_settings
    except Exception:  # pragma: no cover - running without the original
        return False
    return load_oidc_settings() is not None


def identity_from_token(token: str) -> Identity:
    """Turn a bearer token into an identity. The original verifies it."""
    from pipeline_mcp.oidc import claims_to_user, load_oidc_settings, verify_oidc_token

    settings = load_oidc_settings()
    if settings is None:
        raise ApiError(E.AUTH_NOT_CONFIGURED)

    try:
        claims = verify_oidc_token(token, settings)
        user = claims_to_user(claims, settings.client_id)
    except ApiError:
        raise
    except Exception as exc:
        #  The reason stays in the log. Telling a caller which part of their
        #  token failed tells a prober which part to change.
        log.warning("token verification failed: %s", exc)
        raise ApiError(E.AUTH_TOKEN_INVALID) from exc

    role = ROLE_MAP.get(str(user.get("role")), Role.VIEWER)
    return Identity(
        user_id=str(user.get("username") or user.get("subject") or ""),
        subject=str(user.get("subject") or ""),
        email=str(user.get("email") or ""),
        roles=(role,),
        authenticated=True,
    )


async def current_identity(
    authorization: Annotated[str | None, Header()] = None,
) -> Identity:
    """The identity behind a request, or the development stand-in."""
    if not oidc_enabled():
        return dev_identity()

    if not authorization or not authorization.lower().startswith("bearer "):
        raise ApiError(E.AUTH_TOKEN_REQUIRED)

    return identity_from_token(authorization.split(" ", 1)[1].strip())


CurrentIdentity = Annotated[Identity, Depends(current_identity)]


def require(*roles: Role):
    """Build a dependency that enforces a minimum role.

        @router.post(..., dependencies=[Depends(require(Role.ADMIN))])
    """

    async def guard(identity: CurrentIdentity) -> Identity:
        if not identity.has(*roles):
            raise ApiError(E.AUTH_FORBIDDEN)
        return identity

    return guard


def describe(roles: Iterable[Role]) -> dict[str, Any]:
    """The shape the audit trail records."""
    return {"roles": [str(r) for r in roles]}


def auth_mode() -> str:
    """What /healthz reports, so an unauthenticated deployment is visible."""
    return "oidc" if oidc_enabled() else "disabled"


def warn_if_open() -> None:
    if not oidc_enabled():
        log.warning(
            "authentication is off: no OIDC environment. development only."
        )
    _ = get_settings()
