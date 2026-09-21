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
from dataclasses import dataclass, replace
from datetime import datetime, timezone
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
    #  When the bearer token was issued. Needed to honour a sign-out: the
    #  token is stateless, so the only way to end a session is to refuse
    #  everything issued before the moment the person signed out.
    issued_at: datetime | None = None

    def has(self, *wanted: Role) -> bool:
        return any(r in self.roles for r in wanted)


def dev_identity() -> Identity:
    """Stands in while authentication is off.

    Same shape as a real identity, so the permission checks run down one path
    rather than two. DEV_ROLE sets the role the account is *first recorded*
    with; after that the users record is the word, as for any account, and
    the role is changed on the 이용자 screen.
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
    issued = claims.get("iat") if isinstance(claims, dict) else None
    return Identity(
        user_id=str(user.get("username") or user.get("subject") or ""),
        subject=str(user.get("subject") or ""),
        email=str(user.get("email") or ""),
        roles=(role,),
        authenticated=True,
        issued_at=(
            datetime.fromtimestamp(float(issued), tz=timezone.utc)
            if isinstance(issued, (int, float))
            else None
        ),
    )


async def apply_local(identity: Identity) -> Identity:
    """What an operator decided about this account, over what the provider said.

    The provider's token carries admin or user and nothing finer. The users
    collection carries the rest - that this person is a viewer here, that this
    account is a service account, that it was switched off. A first sign-in
    creates the record with the provider's roles, so the operator's screen
    lists everyone who has been here; from then on the record is the word.
    """
    from foldfront.db.repositories import Repos

    try:
        record = await Repos().users.seen(
            identity.user_id, subject=identity.subject, email=identity.email, roles=list(identity.roles),
        )
    except ValueError as exc:
        #  The name belongs to a different subject. Not this person's record.
        raise ApiError(E.AUTH_IDENTITY_MISMATCH, user_id=identity.user_id) from exc
    if not record.active:
        raise ApiError(E.AUTH_FORBIDDEN)

    #  Session termination. A token issued before the person signed out is
    #  refused even though the provider would still verify it - that is the
    #  whole of what signing out can mean for a stateless token.
    if (
        identity.issued_at is not None
        and record.signed_out_at is not None
        and identity.issued_at <= record.signed_out_at
    ):
        raise ApiError(E.AUTH_SESSION_ENDED)

    if identity.issued_at is not None:
        repos = Repos()
        if await repos.users.note_token(record.user_id, identity.issued_at):
            #  A token this account has not presented before. In a stateless
            #  scheme this is the only honest moment to call it a sign-in.
            await repos.audit.record(
                "auth.login", actor_id=record.user_id, target_type="user",
                target_id=record.user_id,
                detail={"subject": record.subject, "roles": [str(r) for r in record.roles]},
            )

    return replace(identity, user_id=record.user_id, roles=tuple(record.roles))


async def current_identity(
    authorization: Annotated[str | None, Header()] = None,
) -> Identity:
    """The identity behind a request, or the development stand-in."""
    if not oidc_enabled():
        return await apply_local(dev_identity())

    if not authorization or not authorization.lower().startswith("bearer "):
        raise ApiError(E.AUTH_TOKEN_REQUIRED)

    return await apply_local(identity_from_token(authorization.split(" ", 1)[1].strip()))


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


#  `has` is any-of, not a ladder: an admin does not carry the viewer role, so
#  a read path guarded with require(Role.VIEWER) alone would refuse the
#  operator. Reads name every role instead.
ALL_ROLES: tuple[Role, ...] = (Role.VIEWER, Role.RESEARCHER, Role.SERVICE, Role.ADMIN)


def require_signed_in():
    """A dependency for read paths: any known role, but not nobody.

    Read paths carried no dependency at all, which meant that with OIDC on,
    run data and artifact downloads were served to anyone who could reach the
    port. A reader needs no particular role, but they do need to be someone -
    both so the refusal exists and so the audit trail has a name to record.
    """
    return require(*ALL_ROLES)


async def may_see_run(identity: Identity, run_id: str) -> bool:
    """Whether this caller may read that run.

    An operator or a service account sees every run. Anyone else sees a run
    they own, and a run with no owner - runs made before ownership was
    recorded, and the seeded ones - because refusing those would hide the
    only data a fresh installation has.

    A run that does not exist reads as visible, so the caller is told it does
    not exist rather than that they may not see it. Which of the two is
    kinder here: this platform's run ids are issued, not guessed, and "you
    may not see it" about a run nobody has is a worse answer to debug.
    """
    from foldfront.db.repositories import Repos

    if identity.has(Role.ADMIN, Role.SERVICE):
        return True
    run = await Repos().runs.get(run_id)
    if run is None:
        return True
    return run.owner_id is None or run.owner_id == identity.user_id


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
