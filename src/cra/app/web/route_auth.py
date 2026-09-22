"""Login, callback and logout. The provider decides how; this module owns the
cookie and the error pages."""

import logging

from quart import (
    Blueprint,
    Response,
    current_app,
    g,
    redirect,
    render_template_string,
    request,
)

from cra.app.auth.principal import LoginDenied, LoginOutcome, Role, SessionState
from cra.app.web.sessions import COOKIE_NAME

log = logging.getLogger(__name__)

bp = Blueprint("auth", __name__)

MESSAGES = {
    LoginDenied.NOT_REGISTERED: (
        "Your account at {organization} is not yet registered for this service. "
        "Please contact {contact} and give them this address: {email}."
    ),
    LoginDenied.NO_EMAIL: (
        "Your university did not transmit an email address. Please ask your IT "
        "department to release attributes to the identity provider, or contact {contact}."
    ),
    LoginDenied.INACTIVE: "Access to this account is disabled.",
    LoginDenied.FAILED: "Sign-in failed. Please try again or contact {contact}.",
}
STATUS = {LoginDenied.FAILED: 400}

ERROR_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }}</title>
<link rel="stylesheet" href="{{ home }}static/css/app.css"></head>
<body><main class="view"><div class="page"><h1>Sign-in not possible</h1>
<p class="lede">{{ message }}</p><p><a class="btn" href="{{ home }}">Back</a></p>
</div></main></body></html>"""


def _ctx():
    return current_app.extensions["cra"]


def _set_cookie(response: Response, cookie: str) -> Response:
    ctx = _ctx()
    response.set_cookie(
        COOKIE_NAME,
        cookie,
        max_age=int(ctx.sessions.max_age.total_seconds()),
        httponly=True,
        secure=ctx.settings.cookie_secure,
        samesite="Lax",
        path=ctx.settings.base_path or "/",
    )
    return response


def _clear_cookie(response: Response) -> Response:
    response.delete_cookie(COOKIE_NAME, path=_ctx().settings.base_path or "/")
    return response


async def _finish(state: SessionState, outcome: LoginOutcome) -> Response:
    ctx = _ctx()
    if outcome.denied is not None or outcome.user_id is None:
        denied = outcome.denied or LoginDenied.FAILED
        log.info(
            "login denied",
            extra={
                "fields": {"reason": denied.value, "organization": outcome.organization}
            },
        )
        page = await render_template_string(
            ERROR_PAGE,
            title=ctx.settings.cluster_display_name,
            home=ctx.home,
            message=MESSAGES[denied].format(
                organization=outcome.organization or "your institution",
                contact=ctx.settings.auth_admin_contact or "the administrators",
                email=outcome.email,
            ),
        )
        return Response(page, status=STATUS.get(denied, 403), content_type="text/html")
    if outcome.grants_admin:
        user = await ctx.repo.get_user(outcome.user_id)
        if user is not None and user.role != Role.ADMIN:
            await ctx.repo.set_user_role(outcome.user_id, Role.ADMIN)
            log.info(
                "granted admin from configuration", extra={"fields": {"user": user.id}}
            )
    cookie, _ = await ctx.sessions.rotate(state, outcome.user_id)
    await ctx.repo.touch_login(outcome.user_id)
    log.info("login", extra={"fields": {"user": outcome.user_id}})
    return _set_cookie(redirect(ctx.home), cookie)


@bp.get("/auth/login")
async def login() -> Response:
    ctx = _ctx()
    cookie: str | None = None
    state = g.session
    if state is None:
        cookie, state = await ctx.sessions.create()
    headers = {k.lower(): v for k, v in request.headers.items()}
    result = await ctx.provider.login(state, headers)
    if isinstance(result, str):
        await ctx.sessions.save(state)
        response = redirect(result)
        return _set_cookie(response, cookie) if cookie else response
    return await _finish(state, result)


@bp.get("/auth/callback")
async def callback() -> Response:
    ctx = _ctx()
    if g.session is None:
        return await _finish(
            SessionState(id="", user_id=None), LoginOutcome(denied=LoginDenied.FAILED)
        )
    outcome = await ctx.provider.callback(g.session, dict(request.args))
    await ctx.sessions.save(g.session)
    return await _finish(g.session, outcome)


@bp.post("/auth/logout")
async def logout() -> Response:
    ctx = _ctx()
    if g.session is not None:
        await ctx.sessions.delete(g.session)
    home = request.host_url.rstrip("/") + ctx.home
    response = current_app.response_class(
        response=current_app.json.dumps(
            {"ok": True, "redirect": ctx.provider.logout_url(home) or ctx.home}
        ),
        content_type="application/json",
    )
    return _clear_cookie(response)
