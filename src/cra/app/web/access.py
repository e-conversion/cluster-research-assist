"""Who may reach which route.

The site is public: anyone may read the corpus, use the public tools and,
within a daily budget, ask a question. Signing in raises the data tier and
attaches history; the console needs an admin. Routes declare what they need
with the decorators below, and a test enumerates every route so that adding
one is a deliberate decision rather than an oversight.
"""

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from cra.app.auth.principal import Principal, Role

REQUIREMENT = "_cra_requires"

View = TypeVar("View", bound=Callable[..., Awaitable[Any]])


def requires_user(view: View) -> View:
    view._cra_requires = Role.USER  # type: ignore[attr-defined]
    return view


def requires_admin(view: View) -> View:
    view._cra_requires = Role.ADMIN  # type: ignore[attr-defined]
    return view


def required_role(view: Callable[..., Any] | None) -> Role:
    return getattr(view, REQUIREMENT, Role.ANONYMOUS)


def satisfies(principal: Principal, required: Role) -> bool:
    if required is Role.ANONYMOUS:
        return True
    if required is Role.USER:
        return principal.signed_in
    return principal.is_admin
