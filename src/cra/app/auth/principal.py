"""Who is calling, and what that entitles them to.

Someone who has not signed in is a principal too, at the public tier, which is
what the landing page and the outward MCP endpoint serve. Signing in raises the
tier to internal, where the full texts and the proposal are, and attaches the
conversation history; an admin additionally reaches the console.
"""

from dataclasses import dataclass, field
from enum import Enum, StrEnum
from typing import Any

from cra.core.tools.tiers import Tier


class Role(StrEnum):
    ANONYMOUS = "anonymous"
    USER = "user"
    ADMIN = "admin"


@dataclass(frozen=True)
class Principal:
    user_id: str | None = None
    display: str = ""
    role: Role = Role.ANONYMOUS

    @property
    def tier(self) -> Tier:
        return Tier.PUBLIC if self.role is Role.ANONYMOUS else Tier.INTERNAL

    @property
    def signed_in(self) -> bool:
        return self.role is not Role.ANONYMOUS

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN


ANONYMOUS = Principal()


class LoginDenied(Enum):
    NOT_REGISTERED = "not_registered"
    NO_EMAIL = "no_email"
    # the address is invited, but not from this identity's home organisation
    ORGANIZATION = "organization"
    INACTIVE = "inactive"
    FAILED = "failed"


@dataclass
class LoginOutcome:
    user_id: str | None = None
    denied: LoginDenied | None = None
    email: str = ""
    organization: str = ""
    # this login bound a new identity to the account
    bound: bool = False
    # promote the account: only ever set on the login that bound the identity,
    # so an email claim on a later login cannot hand out admin
    grants_admin: bool = False
    # the verified claims of an identity that has no account, kept so the
    # person can ask for one without signing in again
    identity: dict[str, str] | None = None


@dataclass
class SessionState:
    """The mutable part of a browser session a provider may use, framework-free."""

    id: str
    user_id: str | None
    data: dict[str, Any] = field(default_factory=dict)
