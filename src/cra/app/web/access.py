"""Who may reach which route.

Signing in is the rule: a route serves anonymous callers only when it says so,
so a new route is closed until someone opens it deliberately. What stays open
is the landing page, the static files, health, the public configuration the
landing page needs, and the sign-in flow itself.

The outward MCP endpoint is gated separately, by a token its owner mints while
signed in, and serves the public tier only.
"""

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from cra.app.auth.principal import Principal, Role

REQUIREMENT = "_cra_requires"

View = TypeVar("View", bound=Callable[..., Awaitable[Any]])


def public(view: View) -> View:
    view._cra_requires = Role.ANONYMOUS  # type: ignore[attr-defined]
    return view


def requires_admin(view: View) -> View:
    view._cra_requires = Role.ADMIN  # type: ignore[attr-defined]
    return view


def required_role(view: Callable[..., Any] | None) -> Role:
    return getattr(view, REQUIREMENT, Role.USER)


def satisfies(principal: Principal, required: Role) -> bool:
    if required is Role.ANONYMOUS:
        return True
    if required is Role.USER:
        return principal.signed_in
    return principal.is_admin
