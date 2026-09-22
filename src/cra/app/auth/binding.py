"""First login binds an identity to a pre-registered email; afterwards only
``(issuer, sub)`` counts. This is the decision table from the SSO handover."""

from dataclasses import dataclass

from cra.app.auth.principal import LoginDenied, LoginOutcome
from cra.app.history.repository import Repository, normalise_email


@dataclass(frozen=True)
class Claims:
    issuer: str
    sub: str
    email: str = ""
    given_name: str = ""
    family_name: str = ""
    home_organization: str = ""

    @property
    def display_name(self) -> str:
        name = f"{self.given_name} {self.family_name}".strip()
        return name or self.email or self.sub


async def resolve_login(repo: Repository, claims: Claims) -> LoginOutcome:
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
    outcome.user_id = user.id
    return outcome
