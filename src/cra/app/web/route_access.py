"""Asking for an account, and admins deciding.

Only someone who has just signed in at their institution without having an
account can ask: the request carries that sign-in's verified claims (name,
address, organisation), and the person adds the group they belong to and a
page at their institution that shows it. Approving binds the identity to a new
account, so the next institutional sign-in goes straight in.
"""

import logging
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from quart import Blueprint, current_app, g

from cra.app.auth.principal import Role
from cra.app.history.tables import AccessRequest
from cra.app.web import auditlog
from cra.app.web.access import public, requires_admin
from cra.app.web.route_auth import body_of, client_address, pending_identity

log = logging.getLogger(__name__)

bp = Blueprint("access", __name__)

PROFILE_URL_MAX = 500
GROUP_MAX = 300
MESSAGE_MAX = 1000
NOTE_MAX = 1000
CLAIM_MAX = 500
# a person asks once; this bounds a script that keeps signing in to ask again
REQUESTS_PER_ADDRESS = (5, 60 * 60)
# what browsers accept as a host; Python's urlsplit is laxer, and a link the
# admin console cannot parse must not get into it
HOSTNAME = re.compile(
    r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+"
)


def _ctx():
    return current_app.extensions["cra"]


def _bad(message: str, status: int = 400) -> tuple[dict[str, str], int]:
    return {"error": message}, status


def check_profile_url(value: str) -> str:
    url = value.strip()
    if not url:
        raise ValueError("give a link to your page at your institution")
    if len(url) > PROFILE_URL_MAX:
        raise ValueError(f"a link has at most {PROFILE_URL_MAX} characters")
    if any(c.isspace() or ord(c) < 32 for c in url):
        raise ValueError("the link cannot contain spaces")
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 -- raises on a port out of range
        host = (parts.hostname or "").encode("idna").decode("ascii")
    except (ValueError, UnicodeError):
        raise ValueError("that is not a link to a website") from None
    if parts.scheme != "https" or not HOSTNAME.fullmatch(host):
        raise ValueError("the link has to start with https:// and name a website")
    # user:password@host is how a link hides where it really goes
    if parts.username is not None or parts.password is not None:
        raise ValueError("the link cannot contain a user name or password")
    return urlunsplit(parts)


async def _request_of(ctx: Any, pending: dict[str, Any]) -> AccessRequest | None:
    """This identity's request, if it still means something: one approved for
    an account that has since been deleted is dropped, so the person can ask
    again rather than being told to sign in forever."""
    row = await ctx.repo.access_request_of(pending["issuer"], pending["sub"])
    if row is not None and row.status == "approved":
        await ctx.repo.delete_access_request(row.id)
        return None
    return row


def _groups() -> list[dict[str, str]]:
    library = _ctx().library
    if library is None:
        return []
    groups = []
    for pi in library.pis:
        label = pi.name
        if pi.group:
            label += f" — {pi.group}"
        if pi.institution:
            label += f" ({pi.institution})"
        groups.append({"smid": pi.smid, "label": label})
    return sorted(groups, key=lambda group: group["label"].lower())


def _institution(organization_name: str, home_organization: str) -> str:
    return organization_name or home_organization


def _status(row: AccessRequest) -> dict[str, Any]:
    return {
        "status": row.status,
        "created_at": row.created_at.isoformat(),
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "note": row.decision_note if row.status == "rejected" else "",
    }


@bp.get("/api/access-requests/me")
@public
async def my_request() -> Any:
    ctx = _ctx()
    pending = pending_identity(g.session)
    if pending is None:
        return _bad("sign in with your institution first", 403)
    existing = await _request_of(ctx, pending)
    return {
        "identity": {
            "name": " ".join(
                p for p in (pending.get("given_name"), pending.get("family_name")) if p
            ),
            "email": pending.get("email", ""),
            "institution": _institution(
                pending.get("organization_name", ""),
                pending.get("home_organization", ""),
            ),
        },
        "request": _status(existing) if existing else None,
        "groups": _groups() if existing is None else [],
        "contact": ctx.settings.auth_admin_contact,
    }


@bp.post("/api/access-requests")
@public
async def submit_request() -> Any:
    ctx = _ctx()
    pending = pending_identity(g.session)
    if pending is None:
        return _bad("sign in with your institution first", 403)
    allowance = ctx.limiter.check(
        f"access:{client_address()}", REQUESTS_PER_ADDRESS[0], REQUESTS_PER_ADDRESS[1]
    )
    if not allowance.allowed:
        return {
            "error": "Too many requests.",
            "retry_after": allowance.retry_after,
        }, 429
    if await _request_of(ctx, pending):
        return _bad("you have already asked; the page shows where it stands", 409)
    if any(len(pending.get(k, "")) > CLAIM_MAX for k in ("issuer", "sub")):
        return _bad("your institution sent an identifier this service cannot store")

    body = await body_of()
    smid = str(body.get("group", "") or "").strip()
    other = str(body.get("group_other", "") or "").strip()
    if smid:
        group = next((known for known in _groups() if known["smid"] == smid), None)
        if group is None:
            return _bad("pick your group from the list, or choose Other")
        group_name = group["label"]
    elif other:
        if len(other) > GROUP_MAX:
            return _bad(f"a group name has at most {GROUP_MAX} characters")
        group_name = other
    else:
        return _bad("say which group you belong to")
    try:
        profile_url = check_profile_url(str(body.get("profile_url", "") or ""))
    except ValueError as exc:
        return _bad(str(exc))
    message = str(body.get("message", "") or "").strip()
    if len(message) > MESSAGE_MAX:
        return _bad(f"a message has at most {MESSAGE_MAX} characters")

    name = " ".join(
        p for p in (pending.get("given_name"), pending.get("family_name")) if p
    )
    row = await ctx.repo.add_access_request(
        issuer=pending["issuer"],
        sub=pending["sub"],
        email=pending.get("email", "").strip().lower()[:320],
        display_name=(name or pending.get("email", ""))[:200],
        home_organization=pending.get("home_organization", "")[:200],
        organization_name=pending.get("organization_name", "")[:200],
        group_smid=smid,
        group_name=group_name,
        profile_url=profile_url,
        message=message,
    )
    if row is None:
        return _bad("you have already asked; the page shows where it stands", 409)
    # not auditlog: nobody is signed in to attribute it to
    log.info(
        "access requested",
        extra={
            "fields": {
                "request": row.id,
                "organization": row.home_organization,
            }
        },
    )
    return {"request": _status(row)}, 201


# the admin side


def _describe(row: AccessRequest) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.display_name,
        "email": row.email,
        "institution": _institution(row.organization_name, row.home_organization),
        "home_organization": row.home_organization,
        "group": row.group_name,
        "group_in_library": bool(row.group_smid),
        "profile_url": row.profile_url,
        "message": row.message,
        "status": row.status,
        "created_at": row.created_at.isoformat(),
        "decided_by": row.decided_by,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "note": row.decision_note,
    }


@bp.get("/api/admin/access-requests")
@requires_admin
async def list_requests() -> dict[str, Any]:
    rows = await _ctx().repo.list_access_requests()
    return {
        "requests": [_describe(row) for row in rows],
        "pending": sum(1 for row in rows if row.status == "pending"),
    }


@bp.post("/api/admin/access-requests/<request_id>/approve")
@requires_admin
async def approve(request_id: str) -> Any:
    body = await body_of()
    role = str(body.get("role", Role.USER))
    if role not in (Role.USER, Role.ADMIN):
        return _bad(f"role must be {Role.USER} or {Role.ADMIN}")
    try:
        user = await _ctx().repo.approve_access_request(
            request_id, role, g.principal.display or "admin"
        )
    except ValueError as exc:
        return _bad(str(exc), 409)
    if user is None:
        return _bad("no such pending request", 404)
    auditlog.record("approve_access", request=request_id, user=user.id, role=role)
    return {"ok": True, "user": user.id}


@bp.post("/api/admin/access-requests/<request_id>/reject")
@requires_admin
async def reject(request_id: str) -> Any:
    body = await body_of()
    note = str(body.get("note", "") or "").strip()
    if len(note) > NOTE_MAX:
        return _bad(f"a note has at most {NOTE_MAX} characters")
    if not await _ctx().repo.reject_access_request(
        request_id, g.principal.display or "admin", note
    ):
        return _bad("no such pending request", 404)
    auditlog.record("reject_access", request=request_id)
    return {"ok": True}


@bp.delete("/api/admin/access-requests/<request_id>")
@requires_admin
async def delete(request_id: str) -> Any:
    if not await _ctx().repo.delete_access_request(request_id):
        return _bad("no such request", 404)
    auditlog.record("delete_access_request", request=request_id)
    return {"ok": True}
