"""Who is calling. Providers turn a login into a ``LoginOutcome``; the web
layer turns a stored user into a ``Principal`` per request."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


@dataclass(frozen=True)
class Principal:
    user_id: str
    display: str
    active: bool


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
