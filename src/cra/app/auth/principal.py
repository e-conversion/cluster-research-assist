"""Who is calling, and what that entitles them to.

Anyone may use the site: an anonymous visitor is a principal too, with the
public tier and no history. Signing in raises the tier and attaches the
conversation history; an admin additionally reaches the console.
"""

from dataclasses import dataclass, field
from enum import Enum, StrEnum
from typing import Any, Protocol

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
    INACTIVE = "inactive"
    FAILED = "failed"


@dataclass
class LoginOutcome:
    user_id: str | None = None
    denied: LoginDenied | None = None
    email: str = ""
    organization: str = ""
    # the address this login came in on is listed in CRA_AUTH_ADMINS
    grants_admin: bool = False


@dataclass
class SessionState:
    """The mutable part of a browser session a provider may use, framework-free."""

    id: str
    user_id: str | None
    data: dict[str, Any] = field(default_factory=dict)


class AuthProvider(Protocol):
    name: str

    async def start(self) -> None:
        """Startup work such as fetching a discovery document."""

    async def login(
        self, session: SessionState, headers: dict[str, str]
    ) -> str | LoginOutcome:
        """Either a URL to redirect the browser to, or an immediate outcome."""

    async def callback(
        self, session: SessionState, args: dict[str, str]
    ) -> LoginOutcome:
        """Finish a login the browser was redirected back from."""

    def logout_url(self, post_logout_uri: str) -> str | None:
        """An end-session URL at the identity provider, if it advertises one."""
