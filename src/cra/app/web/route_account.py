"""One's own account: what is stored about it, its password, a copy of all
of it, and deleting it.

Deleting removes the account, everything that cascades from it, and the
invitation that let it in: signing in again needs a new invitation. Two
accounts cannot delete themselves. One named in ``CRA_AUTH_ADMINS`` would be
promoted again at its next sign-in, so it is removed from the configuration
first. The last active admin would leave nobody to run the console.
"""

from datetime import datetime
from typing import Any

from quart import Blueprint, current_app, g

from cra.app.auth import local, tokens
from cra.app.history.repository import utcnow
from cra.app.web import auditlog
from cra.app.web.route_auth import body_of, clear_cookie

bp = Blueprint("account", __name__)

# a stolen session must not be a way to guess the current password
PASSWORD_CHANGES_PER_ACCOUNT = (10, 15 * 60)


def _ctx():
    return current_app.extensions["cra"]


def _iso(when: datetime | None) -> str | None:
    return when.isoformat() if when else None


async def configured_admin(ctx: Any, user_id: str) -> bool:
    """Whether ``CRA_AUTH_ADMINS`` names an address this account claimed."""
    configured = {a.strip().lower() for a in ctx.settings.auth_admins if a.strip()}
    if not configured:
        return False
    return bool(set(await ctx.repo.emails_of(user_id)) & configured)


async def deletion_blocked(ctx: Any, principal: Any) -> tuple[int, str] | None:
    """The status and reason this account cannot delete itself, or None."""
    if await configured_admin(ctx, principal.user_id):
        return 403, (
            "This account is named in the deployment's configuration "
            "(CRA_AUTH_ADMINS). Ask whoever runs the service to remove it there "
            "first, or it comes back as an admin the next time you sign in."
        )
    if principal.is_admin and await ctx.repo.count_active_admins() <= 1:
        return 409, "You are the only admin. Make someone else an admin first."
    return None


@bp.get("/api/me")
async def me() -> dict[str, Any]:
    ctx = _ctx()
    principal = g.principal
    user = await ctx.repo.get_user(principal.user_id)
    blocked = await deletion_blocked(ctx, principal)
    credential = await ctx.repo.get_credential(user.id)
    emails = await ctx.repo.emails_of(user.id)
    if user.email and user.email not in emails:
        emails.insert(0, user.email)
    return {
        "id": user.id,
        "name": user.display_name,
        "username": credential.username if credential else None,
        "emails": emails,
        "role": user.role,
        "created_at": _iso(user.created_at),
        "delete_blocked": blocked[1] if blocked else "",
    }


@bp.put("/api/me/password")
async def change_password() -> Any:
    # whoever may have known the old password is signed out
    ctx = _ctx()
    allowance = ctx.limiter.check(
        f"pw-change:{g.principal.user_id}", *PASSWORD_CHANGES_PER_ACCOUNT
    )
    if not allowance.allowed:
        return {
            "error": "Too many attempts.",
            "retry_after": allowance.retry_after,
        }, 429
    body = await body_of()
    try:
        await local.change_password(
            ctx.repo,
            g.principal.user_id,
            str(body.get("current", "")),
            str(body.get("new", "")),
        )
    except local.CredentialError as exc:
        return {"error": str(exc)}, 400
    for session in await ctx.repo.sessions_of(g.principal.user_id):
        if session.id != g.session.id:
            await ctx.remote.forget(session.id)
    await ctx.repo.delete_sessions_of(g.principal.user_id, keep=g.session.id)
    auditlog.record("change_password")
    return {"ok": True}


@bp.get("/api/me/export")
async def export() -> Any:
    """Everything stored about the account, as one JSON file. Identities are
    listed by issuer only: the pairwise subject is an identifier for this
    service, not something the person ever saw."""
    ctx = _ctx()
    repo = ctx.repo
    user = await repo.get_user(g.principal.user_id)
    credential = await repo.get_credential(user.id)
    conversations = []
    for conversation in await repo.list_conversations(user.id, limit=None):
        conversations.append(
            {
                "id": conversation.id,
                "title": conversation.title,
                "created_at": _iso(conversation.created_at),
                "updated_at": _iso(conversation.updated_at),
                "messages": [
                    {
                        "role": m.role,
                        "content": m.content,
                        "meta": m.meta,
                        "created_at": _iso(m.created_at),
                    }
                    for m in await repo.messages(conversation.id)
                ],
            }
        )
    body = {
        "exported_at": _iso(utcnow()),
        "account": {
            "id": user.id,
            "name": user.display_name,
            "role": user.role,
            "active": user.is_active,
            "created_at": _iso(user.created_at),
            "last_login_at": _iso(user.last_login_at),
            "email": user.email,
            "username": credential.username if credential else None,
        },
        "emails": await repo.emails_of(user.id),
        "identities": [
            {
                "issuer": i.issuer,
                "home_organization": i.home_organization,
                "bound_at": _iso(i.bound_at),
            }
            for i in await repo.list_identities(user.id)
        ],
        "conversations": conversations,
        "feedback": [
            {
                "category": f.category,
                "text": f.text,
                "model": f.model,
                "messages": f.messages,
                "created_at": _iso(f.created_at),
            }
            for f in await repo.feedback_of(user.id)
        ],
        "mcp_tokens": [tokens.describe(t) for t in await repo.list_tokens(user.id)],
    }
    auditlog.record("export_account")
    response = current_app.response_class(
        current_app.json.dumps(body, indent=2), content_type="application/json"
    )
    response.headers["Content-Disposition"] = (
        f'attachment; filename="account-{utcnow():%Y-%m-%d}.json"'
    )
    return response


@bp.delete("/api/me")
async def delete_account() -> Any:
    ctx = _ctx()
    principal = g.principal
    if blocked := await deletion_blocked(ctx, principal):
        status, reason = blocked
        return {"error": reason}, status
    for session in await ctx.repo.sessions_of(principal.user_id):
        await ctx.remote.forget(session.id)
    await ctx.repo.delete_user(principal.user_id, keep_invitation=False)
    auditlog.record("delete_own_account")
    return clear_cookie(current_app.response_class(status=204))
