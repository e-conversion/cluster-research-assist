"""Signing in and out: with a password, or at one's institution through the
DFN-AAI OIDC proxy when the deployment has a client for it. This module owns
the cookie and the error pages; ``cra.app.auth`` decides who someone is."""

import logging
import time
from typing import Any

import httpx
from quart import (
    Blueprint,
    Response,
    current_app,
    g,
    jsonify,
    redirect,
    render_template,
    request,
)

from cra.app.auth import local
from cra.app.auth.principal import LoginDenied, LoginOutcome, Role, SessionState
from cra.app.web.access import public
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
    LoginDenied.ORGANIZATION: (
        "The address {email} is registered, but not for accounts at {organization}. "
        "Sign in with the institution that issued it, or contact {contact}."
    ),
    LoginDenied.INACTIVE: "Access to this account is disabled.",
    LoginDenied.FAILED: "Sign-in failed. Please try again or contact {contact}.",
}
STATUS = {LoginDenied.FAILED: 400}
# callbacks per address per minute: a forged token costs a signature check
# and possibly a JWKS fetch, and nobody signs in that often
CALLBACKS_PER_MINUTE = 30
# Password guesses. Per address, so one client cannot hammer the hasher; per
# username, so a botnet cannot work through one account's password. Only
# failures count against a username, or anyone could keep a known one locked
# out. A person who mistypes a few times is never near either.
PASSWORD_TRIES_PER_ADDRESS = (10, 60)
PASSWORD_TRIES_PER_USERNAME = (10, 15 * 60)
LINK_TRIES_PER_ADDRESS = (10, 60)
WRONG_PASSWORD = "The username or password is not right."

# where the verified claims of an identity without an account wait while the
# person fills in the request form
PENDING_KEY = "pending_identity"
PENDING_LIFETIME_S = 30 * 60
VIA_KEY = "via"


def _ctx():
    return current_app.extensions["cra"]


def client_address() -> str:
    """The nearest hop's idea of who is calling.

    A proxy that sets ``X-Forwarded-For`` to the real client puts it last;
    the first entry is whatever the client itself claimed. Without a proxy the
    socket address is all there is.
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    if last := forwarded.rpartition(",")[2].strip():
        return last
    client = request.scope.get("client")
    return str(client[0]) if client else "unknown"


def pending_identity(session: SessionState | None) -> dict[str, Any] | None:
    pending = session.data.get(PENDING_KEY) if session else None
    if not isinstance(pending, dict) or pending.get("until", 0) < time.time():
        return None
    return pending


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


def clear_cookie(response: Response) -> Response:
    response.delete_cookie(COOKIE_NAME, path=_ctx().settings.base_path or "/")
    return response


async def body_of() -> dict[str, Any]:
    # a body such as [1] is a bad request, not an error
    body = await request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _too_many(
    key: str, limit: tuple[int, int], *, count: bool = True
) -> Response | None:
    limiter = _ctx().limiter
    allowance = (limiter.check if count else limiter.peek)(
        key, limit[0], window_s=limit[1]
    )
    if allowance.allowed:
        return None
    response = jsonify(
        error="Too many attempts. Wait a little and try again.",
        retry_after=allowance.retry_after,
    )
    response.status_code = 429
    response.headers["Retry-After"] = str(allowance.retry_after)
    return response


def _json_error(message: str, status: int) -> Response:
    response = jsonify(error=message)
    response.status_code = status
    return response


async def _sign_in(state: SessionState | None, user_id: str, via: str) -> str:
    ctx = _ctx()
    if state is None:
        cookie, fresh = await ctx.sessions.create(user_id)
    else:
        cookie, fresh = await ctx.sessions.rotate(state, user_id)
    fresh.data[VIA_KEY] = via
    await ctx.sessions.save(fresh)
    await ctx.repo.touch_login(user_id)
    log.info("login", extra={"fields": {"user": user_id, "via": via}})
    return cookie


async def _ask_for_access(state: SessionState, identity: dict[str, str]) -> Response:
    ctx = _ctx()
    cookie, fresh = await ctx.sessions.rotate(state, None)
    fresh.data[PENDING_KEY] = {**identity, "until": time.time() + PENDING_LIFETIME_S}
    await ctx.sessions.save(fresh)
    log.info(
        "unregistered identity offered an access request",
        extra={"fields": {"organization": identity.get("home_organization", "")}},
    )
    return _set_cookie(redirect(ctx.home + "#/request-access"), cookie)


async def _finish(state: SessionState, outcome: LoginOutcome) -> Response:
    ctx = _ctx()
    if outcome.identity is not None:
        return await _ask_for_access(state, outcome.identity)
    if outcome.denied is not None or outcome.user_id is None:
        denied = outcome.denied or LoginDenied.FAILED
        log.info(
            "login denied",
            extra={
                "fields": {"reason": denied.value, "organization": outcome.organization}
            },
        )
        page = await render_template(
            "signin_error.html",
            **ctx.page_context(),
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
    cookie = await _sign_in(state, outcome.user_id, "oidc")
    return _set_cookie(redirect(ctx.home), cookie)


@bp.post("/auth/password")
@public
async def password_login() -> Response:
    ctx = _ctx()
    body = await body_of()
    username = str(body.get("username", ""))[: local.PASSWORD_MAX]
    password = str(body.get("password", ""))
    if refused := _too_many(f"pw-ip:{client_address()}", PASSWORD_TRIES_PER_ADDRESS):
        return refused
    user_key = f"pw-user:{local.normalise_username(username)}"
    if refused := _too_many(user_key, PASSWORD_TRIES_PER_USERNAME, count=False):
        return refused
    verified = await local.verify(ctx.repo, username, password)
    if verified.user is None:
        _too_many(user_key, PASSWORD_TRIES_PER_USERNAME)
        log.info("password sign-in refused", extra={"fields": {"reason": "mismatch"}})
        return _json_error(WRONG_PASSWORD, 401)
    if not verified.ok:
        # said only to whoever knows the password
        return _json_error(MESSAGES[LoginDenied.INACTIVE], 403)
    cookie = await _sign_in(g.session, verified.user.id, "password")
    return _set_cookie(jsonify(ok=True), cookie)


@bp.post("/auth/set-password")
@public
async def set_password() -> Response:
    ctx = _ctx()
    if refused := _too_many(f"link-ip:{client_address()}", LINK_TRIES_PER_ADDRESS):
        return refused
    body = await body_of()
    try:
        user_id = await local.redeem_link(
            ctx.repo, str(body.get("token", "")), str(body.get("password", ""))
        )
    except local.CredentialError as exc:
        return _json_error(str(exc), 400)
    for session in await ctx.repo.sessions_of(user_id):
        await ctx.remote.forget(session.id)
    await ctx.repo.delete_sessions_of(user_id)
    user = await ctx.repo.get_user(user_id)
    if user is None or not user.is_active:
        return _json_error(MESSAGES[LoginDenied.INACTIVE], 403)
    cookie = await _sign_in(g.session, user_id, "password")
    return _set_cookie(jsonify(ok=True), cookie)


@bp.post("/auth/password-link")
@public
async def password_link() -> Response:
    # POST, not GET: the token stays out of URLs and access logs
    ctx = _ctx()
    if refused := _too_many(f"link-ip:{client_address()}", LINK_TRIES_PER_ADDRESS):
        return refused
    try:
        username = await local.link_username(
            ctx.repo, str((await body_of()).get("token", ""))
        )
    except local.CredentialError as exc:
        return _json_error(str(exc), 400)
    return jsonify(username=username)


@bp.get("/auth/login")
@public
async def login() -> Response:
    ctx = _ctx()
    if ctx.oidc is None:
        return _json_error("Institutional sign-in is not set up here.", 404)
    cookie: str | None = None
    state = g.session
    if state is None:
        cookie, state = await ctx.sessions.create()
    try:
        url = await ctx.oidc.login(state)
    except (httpx.HTTPError, ValueError):
        log.exception("the identity provider could not be reached")
        return await _finish(state, LoginOutcome(denied=LoginDenied.FAILED))
    await ctx.sessions.save(state)
    response = redirect(url)
    return _set_cookie(response, cookie) if cookie else response


@bp.get("/auth/callback")
@public
async def callback() -> Response:
    ctx = _ctx()
    if ctx.oidc is None:
        return _json_error("Institutional sign-in is not set up here.", 404)
    allowance = ctx.limiter.check(
        f"auth:{client_address()}", CALLBACKS_PER_MINUTE, window_s=60
    )
    if not allowance.allowed:
        response = Response("Too many sign-in attempts.", status=429)
        response.headers["Retry-After"] = str(allowance.retry_after)
        return response
    if g.session is None:
        return await _finish(
            SessionState(id="", user_id=None), LoginOutcome(denied=LoginDenied.FAILED)
        )
    outcome = await ctx.oidc.callback(g.session, dict(request.args))
    await ctx.sessions.save(g.session)
    return await _finish(g.session, outcome)


@bp.post("/auth/logout")
@public
async def logout() -> Response:
    ctx = _ctx()
    redirect_to = ctx.home
    if g.session is not None:
        # only a session that came from the institution is ended there too
        if g.session.data.get(VIA_KEY) == "oidc" and ctx.oidc is not None:
            home = request.host_url.rstrip("/") + ctx.home
            redirect_to = ctx.oidc.logout_url(home) or ctx.home
        await ctx.remote.forget(g.session.id)
        await ctx.sessions.delete(g.session)
    response = current_app.response_class(
        response=current_app.json.dumps({"ok": True, "redirect": redirect_to}),
        content_type="application/json",
    )
    return clear_cookie(response)
