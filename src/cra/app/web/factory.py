"""Quart application factory. Everything long-lived (engine, HTTP client,
auth provider) hangs off ``app.extensions["cra"]`` and is created once."""

import logging
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import httpx
from quart import Quart, Response, g, jsonify, request, send_from_directory
from sqlalchemy.ext.asyncio import AsyncEngine

from cra.app.auth.dev import DevProvider
from cra.app.auth.oidc import OidcProvider
from cra.app.auth.principal import AuthProvider, Principal
from cra.app.history import migrate
from cra.app.history.engine import make_engine, make_session_factory
from cra.app.history.repository import Repository
from cra.app.web import route_auth, route_health, route_session
from cra.app.web.access import is_public
from cra.app.web.sessions import COOKIE_NAME, SessionStore
from cra.config.settings import Settings

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
    )
    app.extensions["cra"] = ctx

    for blueprint in (route_health.bp, route_session.bp, route_auth.bp):
        app.register_blueprint(blueprint, url_prefix=base or None)

    @app.get(f"{base}/")
    async def index() -> Response:
        response = await send_from_directory(STATIC_DIR, "index.html")
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.before_serving
    async def start() -> None:
        try:
            await ctx.provider.start()
            await _check_schema(ctx)
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
        if is_public(request.path, base):
            return None
        if g.principal is None or not g.principal.active:
            response = jsonify(error="login_required", login_url=f"{base}/auth/login")
            response.status_code = 401
            return response
        return None

    return app


async def _close(ctx: AppContext) -> None:
    await ctx.http.aclose()
    await ctx.engine.dispose()


async def _principal(ctx: AppContext) -> Principal | None:
    if g.session is None or g.session.user_id is None:
        return None
    user = await ctx.repo.get_user(g.session.user_id)
    if user is None:
        return None
    return Principal(user_id=user.id, display=user.display_name, active=user.is_active)


async def _check_schema(ctx: AppContext) -> None:
    if ctx.settings.history_auto_migrate:
        await migrate.upgrade(ctx.engine)
    current, head = await migrate.current_revision(ctx.engine), migrate.head_revision()
    if current != head:
        raise RuntimeError(
            f"database schema is at {current}, the code needs {head}: run `cra db upgrade`"
        )
    log.info("database ready", extra={"fields": {"revision": current}})
