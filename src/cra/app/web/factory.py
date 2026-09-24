"""Quart application factory. Everything long-lived (engine, HTTP client,
library, auth provider, policy) hangs off ``app.extensions["cra"]`` and is
created once."""

import asyncio
import logging
from collections.abc import Coroutine
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
from quart import Quart, Response, g, jsonify, redirect, request, send_from_directory
from quart.wrappers import Request
from sqlalchemy.ext.asyncio import AsyncEngine

from cra.app.auth.dev import DevProvider
from cra.app.auth.oidc import OidcProvider
from cra.app.auth.principal import ANONYMOUS, AuthProvider, Principal, Role
from cra.app.history import migrate
from cra.app.history.engine import make_engine, make_session_factory
from cra.app.history.repository import Repository
from cra.app.policy import Policy
from cra.app.ratelimit import RateLimiter
from cra.app.web import (
    route_admin,
    route_auth,
    route_chat,
    route_connect,
    route_conversation,
    route_feedback,
    route_health,
    route_preferences,
    route_session,
    route_views,
)
from cra.app.web.access import required_role, satisfies
from cra.app.web.sessions import COOKIE_NAME, SessionStore
from cra.app.web.turns import TurnSlots
from cra.assistant.chat import prompt as prompt_
from cra.assistant.llm.selection import ModelCatalogue
from cra.assistant.mcpclient.host import RemoteHost
from cra.assistant.mcpclient.pool import RemotePool
from cra.config.settings import Settings
from cra.core.connectors.doi_lookup import LookupGuard
from cra.core.connectors.sources import configured as configured_sources
from cra.core.library.library import Library
from cra.core.retrieval.encoder import OnnxEncoder
from cra.core.retrieval.indexes import Indexes
from cra.core.tools.registry import Registry, ToolContext
from cra.core.tools.registry import load as load_tools
from cra.core.tools.tiers import Tier

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

# Every route but the library upload takes a small JSON body; the upload
# limit is settings.library_max_upload_mb and applies to that route alone.
JSON_BODY_LIMIT = 1024 * 1024
UPLOAD_ENDPOINT = "admin.upload_library"
STATE_CHANGING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# what an HTML form can send without a CORS preflight: an API that only ever
# takes JSON has no reason to accept these, and refusing them is what keeps a
# form on another site from acting as a signed-in user
FORM_TYPES = frozenset(
    {"application/x-www-form-urlencoded", "multipart/form-data", "text/plain"}
)
FETCH_HEADER = "X-Requested-With"
FETCH_VALUE = "cra"

# Scripts come from this package and the pinned CDN builds; nothing inline, so
# text a model produces cannot become code, and images stay on this origin so
# a rendered answer cannot carry the conversation off in an image URL.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "worker-src 'self' blob:; "
    "frame-src 'self'; "
    "frame-ancestors 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
HSTS = "max-age=31536000"


class LimitedRequest(Request):
    """The body limit is fixed when the request is built, before routing, so
    the one route that takes a bundle is told apart by its path here."""

    upload_path = ""
    upload_limit = JSON_BODY_LIMIT

    def __init__(
        self, method: str, scheme: str, path: str, *args: Any, **kwargs: Any
    ) -> None:
        is_upload = method == "POST" and path == self.upload_path
        kwargs["max_content_length"] = (
            self.upload_limit if is_upload else JSON_BODY_LIMIT
        )
        super().__init__(method, scheme, path, *args, **kwargs)


@dataclass
class AppContext:
    settings: Settings
    engine: AsyncEngine
    repo: Repository
    sessions: SessionStore
    provider: AuthProvider
    http: httpx.AsyncClient
    policy: Policy
    catalogue: ModelCatalogue
    remote: RemoteHost
    limiter: RateLimiter = field(default_factory=RateLimiter)
    # Owned here rather than by the connector module so it cannot outlive the
    # app that created it, leak between tests, or survive a reload.
    lookups: LookupGuard = field(default_factory=LookupGuard)
    turns: TurnSlots = field(default_factory=TurnSlots)
    library: Library | None = None
    indexes: Indexes | None = None
    registry: Registry = field(default_factory=Registry)
    system_prompt: str = ""
    background: set[asyncio.Task[Any]] = field(default_factory=set)

    def spawn(self, work: Coroutine[Any, Any, Any], what: str) -> asyncio.Task[Any]:
        """Run ``work`` after the request that started it is gone.

        The task is held here so it is neither garbage-collected mid-way nor
        forgotten at shutdown, and a failure is logged rather than lost.
        """
        task = asyncio.create_task(work)
        self.background.add(task)

        def finished(done: asyncio.Task[Any]) -> None:
            self.background.discard(done)
            if not done.cancelled() and done.exception() is not None:
                log.error(
                    "background work failed",
                    extra={"fields": {"what": what}},
                    exc_info=done.exception(),
                )

        task.add_done_callback(finished)
        return task

    def tool_context(self, tier: Tier) -> ToolContext:
        if self.indexes is None:
            raise RuntimeError("the library is not loaded")
        return ToolContext(
            indexes=self.indexes,
            settings=self.settings,
            tier=tier,
            http=self.http,
            lookups=self.lookups,
        )

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
    app.request_class = type(
        "Request",
        (LimitedRequest,),
        {
            "upload_path": f"{base}/api/admin/library",
            "upload_limit": settings.library_max_upload_mb * 1024 * 1024,
        },
    )
    engine = engine or make_engine(settings.history_url)
    repo = Repository(make_session_factory(engine))
    http = httpx.AsyncClient(timeout=HTTP_TIMEOUT)
    ctx = AppContext(
        settings=settings,
        engine=engine,
        repo=repo,
        sessions=SessionStore(
            repo,
            timedelta(hours=settings.session_max_age_hours),
            timedelta(hours=settings.session_absolute_hours),
        ),
        provider=make_provider(settings, repo, http),
        http=http,
        policy=Policy(settings),
        catalogue=ModelCatalogue(settings),
        remote=RemoteHost(
            configured_sources(settings),
            RemotePool(settings.mcp_pool_idle_s),
            allow_write=settings.remote_write_tools,
        ),
    )
    app.extensions["cra"] = ctx

    for blueprint in (
        route_health.bp,
        route_session.bp,
        route_auth.bp,
        route_feedback.bp,
        route_preferences.bp,
        route_chat.bp,
        route_connect.bp,
        route_conversation.bp,
        route_views.bp,
        route_admin.bp,
    ):
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
            ctx.indexes = await asyncio.to_thread(
                Indexes.build, ctx.library, _encoder(ctx.settings)
            )
            ctx.registry = load_tools(ctx.settings, ctx.indexes)
            ctx.system_prompt = prompt_.build(
                ctx.settings, ctx.library, {spec.name for spec in ctx.registry}
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

    @app.after_request
    async def revalidate_static(response: Response) -> Response:
        # Without this the browser caches a script for hours and silently keeps
        # running the previous deployment's frontend. The ETag makes the
        # revalidation a 304, so the cost is one conditional request.
        if request.endpoint == "static":
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.after_request
    async def harden(response: Response) -> Response:
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if settings.cookie_secure:
            response.headers.setdefault("Strict-Transport-Security", HSTS)
        return response

    @app.before_request
    async def guard() -> Response | None:
        g.session = await ctx.sessions.load(request.cookies.get(COOKIE_NAME))
        g.principal = await _principal(ctx)
        if request.endpoint == "static":
            return None
        if (refused := _refuse_cross_site()) is not None:
            return refused
        if (
            request.endpoint != UPLOAD_ENDPOINT
            and (request.content_length or 0) > JSON_BODY_LIMIT
        ):
            return _json_error("The request body is too large.", 413)
        required = required_role(app.view_functions.get(request.endpoint or ""))
        if satisfies(g.principal, required):
            return None
        return _refuse(ctx, required)

    return app


def _refuse_cross_site() -> Response | None:
    """A state-changing request has to come from this site's own scripts.

    ``SameSite=Lax`` on the cookie is the first line; this is the second, for
    the cases Lax leaves open. Three tells, any one of which is enough: the
    browser says the request is cross-site, the Origin names another host, or
    the body is something a plain HTML form could have sent.
    """
    if request.method not in STATE_CHANGING:
        return None
    if request.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
        return _json_error("Cross-site requests are not accepted.", 403)
    origin = request.headers.get("Origin", "")
    if (
        origin
        and origin.lower() != "null"
        and _host_of(origin) != _host_of(request.host_url)
    ):
        return _json_error("Cross-site requests are not accepted.", 403)
    if request.mimetype in FORM_TYPES and not (
        request.endpoint == UPLOAD_ENDPOINT
        and request.headers.get(FETCH_HEADER) == FETCH_VALUE
    ):
        return _json_error("Send JSON, not a form.", 403)
    return None


def _host_of(url: str) -> str:
    host = url.split("//", 1)[-1].split("/", 1)[0].lower()
    return host.removesuffix(":443").removesuffix(":80")


def _json_error(message: str, status: int) -> Response:
    response = jsonify(error=message)
    response.status_code = status
    return response


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


def _encoder(settings: Settings) -> Any:
    """The query encoder, when one is configured. Without it the library still
    serves everything but meaning-based search."""
    if not settings.encoder_path:
        return None
    try:
        return OnnxEncoder.load(settings.encoder_path)
    except Exception:
        log.exception("the query encoder did not load; semantic search is off")
        return None


async def _close(ctx: AppContext) -> None:
    for task in list(ctx.background):
        task.cancel()
    if ctx.background:
        await asyncio.gather(*ctx.background, return_exceptions=True)
    await ctx.remote.aclose()
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
