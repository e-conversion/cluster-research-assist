"""Quart application factory. Everything long-lived (engine, HTTP client,
library, auth provider, policy) hangs off ``app.extensions["cra"]`` and is
created once."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import httpx
from quart import Quart, Response, g, jsonify, redirect, request, send_from_directory
from sqlalchemy.ext.asyncio import AsyncEngine

from cra.app.auth.dev import DevProvider
from cra.app.auth.oidc import OidcProvider
from cra.app.auth.principal import ANONYMOUS, AuthProvider, Principal, Role
from cra.app.history import migrate
from cra.app.history.engine import make_engine, make_session_factory
from cra.app.history.repository import Repository
from cra.app.policy import Policy
from cra.app.ratelimit import RateLimiter
from cra.app.web import route_admin, route_auth, route_health, route_session
from cra.app.web.access import required_role, satisfies
from cra.app.web.sessions import COOKIE_NAME, SessionStore
from cra.config.settings import Settings
from cra.core.library.library import Library

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


@dataclass
class AppContext:
    settings: Settings
    engine: AsyncEngine
    repo: Repository
    sessions: SessionStore
    provider: AuthProvider
    http: httpx.AsyncClient
    policy: Policy
    limiter: RateLimiter = field(default_factory=RateLimiter)
    library: Library | None = None

    @property
    def home(self) -> str:
        return self.settings.base_path + "/"


def make_provider(
    settings: Settings, repo: Repository, http: httpx.AsyncClient
) -> AuthProvider:
    if settings.auth_provider == "oidc":
        return OidcProvider(settings, repo, http)
    return DevProvider(settings, repo)


def create_app(settings: Settings, engine: AsyncEngine | None = None) -> Quart:
    base = settings.base_path
    app = Quart("cra", static_folder=str(STATIC_DIR), static_url_path=f"{base}/static")
    app.config["MAX_CONTENT_LENGTH"] = settings.library_max_upload_mb * 1024 * 1024
    engine = engine or make_engine(settings.history_url)
    repo = Repository(make_session_factory(engine))
    http = httpx.AsyncClient(timeout=HTTP_TIMEOUT)
    ctx = AppContext(
        settings=settings,
        engine=engine,
        repo=repo,
        sessions=SessionStore(repo, timedelta(hours=settings.session_max_age_hours)),
        provider=make_provider(settings, repo, http),
        http=http,
        policy=Policy(settings),
    )
    app.extensions["cra"] = ctx

    for blueprint in (route_health.bp, route_session.bp, route_auth.bp, route_admin.bp):
        app.register_blueprint(blueprint, url_prefix=base or None)

    @app.get(f"{base}/")
    async def index() -> Response:
        return await _page("index.html")

    # the landing page renders for anyone: it is where signing in starts
    index._cra_requires = Role.ANONYMOUS  # type: ignore[attr-defined]

    @app.get(f"{base}/admin")
    async def admin_console() -> Response:
        return await _page("admin.html")

    # the page itself is a shell; every call it makes is checked on its own
    admin_console._cra_requires = Role.ADMIN  # type: ignore[attr-defined]

    @app.before_serving
    async def start() -> None:
        try:
            ctx.library = await asyncio.to_thread(
                Library.load,
                ctx.settings.library_path,
                required_schema=ctx.settings.library_require_schema,
            )
            await _check_schema(ctx)
            ctx.policy = await Policy.load(ctx.settings, ctx.repo)
            await ctx.provider.start()
        except Exception:
            # after_serving does not run when startup fails: release the pool here
            await _close(ctx)
            raise

    @app.after_serving
    async def stop() -> None:
        await _close(ctx)

    @app.before_request
    async def guard() -> Response | None:
        g.session = await ctx.sessions.load(request.cookies.get(COOKIE_NAME))
        g.principal = await _principal(ctx)
        if request.endpoint == "static":
            return None
        required = required_role(app.view_functions.get(request.endpoint or ""))
        if satisfies(g.principal, required):
            return None
        return _refuse(ctx, required)

    return app


async def _page(name: str) -> Response:
    response = await send_from_directory(STATIC_DIR, name)
    response.headers["Cache-Control"] = "no-cache"
    return response


def _refuse(ctx: AppContext, required: Role) -> Response:
    login_url = f"{ctx.settings.base_path}/auth/login"
    if required is Role.ADMIN and g.principal.signed_in:
        response = jsonify(error="admin_required")
        response.status_code = 403
        return response
    # a browser asking for a page is sent to sign in; an API caller gets JSON
    if not g.principal.signed_in and _wants_html():
        return redirect(login_url)
    response = jsonify(error="login_required", login_url=login_url)
    response.status_code = 401
    return response


def _wants_html() -> bool:
    accept = request.headers.get("Accept", "")
    return "text/html" in accept and "application/json" not in accept


async def _close(ctx: AppContext) -> None:
    await ctx.http.aclose()
    await ctx.engine.dispose()


async def _principal(ctx: AppContext) -> Principal:
    if g.session is None or g.session.user_id is None:
        return ANONYMOUS
    user = await ctx.repo.get_user(g.session.user_id)
    if user is None or not user.is_active:
        return ANONYMOUS
    return Principal(user_id=user.id, display=user.display_name, role=Role(user.role))


async def _check_schema(ctx: AppContext) -> None:
    if ctx.settings.history_auto_migrate:
        await migrate.upgrade(ctx.engine)
    current, head = await migrate.current_revision(ctx.engine), migrate.head_revision()
    if current != head:
        raise RuntimeError(
            f"database schema is at {current}, the code needs {head}: run `cra db upgrade`"
        )
    log.info("database ready", extra={"fields": {"revision": current}})
