"""First login binds an identity to a pre-registered email; afterwards only
``(issuer, sub)`` counts. This is the decision table from the SSO handover.

The email claim is asserted by the home identity provider and verified by
nobody else, and ``sub`` is pairwise, so nothing in the token ties an address
to the institution that issued it. An invitation therefore also names the home
organisation it may be claimed from, either on its own or through the
deployment-wide list, and a matching address from anywhere else is refused.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from cra.app.auth.principal import LoginDenied, LoginOutcome
from cra.app.history.repository import Repository, normalise_email

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Claims:
    issuer: str
    sub: str
    email: str = ""
    given_name: str = ""
    family_name: str = ""
    home_organization: str = ""
    # the institution's name for people, where schacHomeOrganization is its domain
    organization_name: str = ""

    @property
    def display_name(self) -> str:
        name = f"{self.given_name} {self.family_name}".strip()
        return name or self.email or self.sub


def organization_allowed(
    claimed: str, invitation: str, deployment: Iterable[str]
) -> bool:
    """Whether an identity from ``claimed`` may take an invitation.

    The invitation's own organisation wins; without one the deployment list
    applies; without either, any organisation in the federation may claim it.
    """
    expected = [invitation] if invitation else list(deployment)
    if not expected:
        return True
    return claimed.strip().lower() in {e.strip().lower() for e in expected if e}


async def resolve_login(
    repo: Repository, claims: Claims, allowed_organizations: Iterable[str] = ()
) -> LoginOutcome:
    email = normalise_email(claims.email)
    outcome = LoginOutcome(email=email, organization=claims.home_organization)

    identity = await repo.get_identity(claims.issuer, claims.sub)
    if identity is not None:
        user = await repo.get_user(identity.user_id)
        if user is None or not user.is_active:
            outcome.denied = LoginDenied.INACTIVE
        else:
            outcome.user_id = user.id
        return outcome

    if not email:
        outcome.denied = LoginDenied.NO_EMAIL
        return outcome
    registered = await repo.get_registered_email(email)
    if registered is None:
        outcome.denied = LoginDenied.NOT_REGISTERED
        return outcome
    if not organization_allowed(
        claims.home_organization, registered.home_organization, allowed_organizations
    ):
        log.warning(
            "invitation claimed from another organisation",
            extra={
                "fields": {
                    "issuer": claims.issuer,
                    "organization": claims.home_organization,
                    "expected": registered.home_organization
                    or ",".join(allowed_organizations),
                }
            },
        )
        outcome.denied = LoginDenied.ORGANIZATION
        return outcome

    if registered.user_id is None:
        # an invitation may name the role the new account starts with
        user = await repo.create_user(claims.display_name, role=registered.role)
        await repo.link_registered_email(email, user.id)
    else:
        existing = await repo.get_user(registered.user_id)
        if existing is None or not existing.is_active:
            outcome.denied = LoginDenied.INACTIVE
            return outcome
        user = existing
    await repo.add_identity(
        claims.issuer, claims.sub, user.id, claims.home_organization
    )
    log.info(
        "identity bound",
        extra={
            "fields": {
                "user": user.id,
                "issuer": claims.issuer,
                "organization": claims.home_organization,
            }
        },
    )
    outcome.user_id = user.id
    outcome.bound = True
    return outcome
