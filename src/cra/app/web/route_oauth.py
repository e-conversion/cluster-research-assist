"""The consent page of the MCP sign-in (``cra.app.mcpserver.oauth``): where a
person, signed in here, lets a client search the library as them.

The page names the client and, more to the point, where it will send the
person back to: a client's name is whatever it registered with, its return
address is what it cannot fake.
"""

from typing import Any

from quart import (
    Blueprint,
    Response,
    current_app,
    g,
    redirect,
    render_template,
    request,
)

from cra.app.mcpserver import oauth
from cra.app.web import auditlog
from cra.app.web.access import public
from cra.app.web.route_auth import RETURN_KEY, body_of, set_cookie

bp = Blueprint("oauth", __name__)

EXPIRED = "This sign-in request has expired or was already answered. Start again from your app."


def _ctx():
    return current_app.extensions["cra"]


@bp.before_request
async def enabled() -> Any:
    if not _ctx().settings.mcp_oauth_enabled:
        return {"error": "this deployment offers no sign-in for MCP clients"}, 404
    return None


@bp.get("/oauth/consent")
@public
async def consent() -> Response:
    ctx = _ctx()
    handle = request.args.get("request", "")
    found = await oauth.pending(ctx.repo, handle) if handle else None
    if found is None:
        page = await render_template(
            "signin_error.html", **ctx.page_context(), message=EXPIRED
        )
        return Response(page, status=400, content_type="text/html")
    if not g.principal.signed_in:
        return await _sign_in_first()
    response = Response(
        await render_template(
            "oauth_consent.html",
            **ctx.page_context(),
            client=found.client_name,
            return_host=found.return_host,
            user=g.principal.display,
            cluster=ctx.settings.cluster_display_name,
            handle=handle,
        )
    )
    response.headers["Cache-Control"] = "no-store"
    return response


async def _sign_in_first() -> Response:
    """To the landing page, which is where every way of signing in starts;
    the sign-in sends the person back here."""
    ctx = _ctx()
    cookie: str | None = None
    state = g.session
    if state is None:
        cookie, state = await ctx.sessions.create()
    state.data[RETURN_KEY] = request.full_path
    await ctx.sessions.save(state)
    response = redirect(ctx.home)
    return set_cookie(response, cookie) if cookie else response


@bp.post("/oauth/consent/answer")
async def decide() -> Any:
    body = await body_of()
    approve = body.get("approve") is True
    where = await oauth.answer(
        _ctx().repo, str(body.get("request", "")), g.principal.user_id, approve
    )
    if where is None:
        return {"error": EXPIRED}, 400
    auditlog.record("mcp_sign_in", approved=approve)
    return {"redirect": where}
