"""One ASGI application in front of two: the web app and the MCP endpoint.

Quart owns every path but ``CRA_MCP_SERVER_PATH``, which goes to the MCP
session manager instead. Both need their lifespan run, and there is only one
lifespan to run it in, so the manager's context wraps Quart's: it starts before
the app does and stops after it, which is what the session manager's task group
needs.

The endpoint is gated here rather than inside the protocol: a caller without a
token is turned away before a session is set up at all.
"""

import json
import logging
from typing import Any

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from quart import Quart

from cra.app.auth import tokens
from cra.app.mcpserver import audit, facade

log = logging.getLogger(__name__)

CALLER_HEADER = b"x-cra-caller"
WINDOW_S = 60


class Dispatcher:
    def __init__(self, app: Quart, ctx: Any) -> None:
        self._app = app
        self._ctx = ctx
        self._path = ctx.settings.base_path + ctx.settings.mcp_server_path
        # stateless: every request stands on its own, so nothing has to be
        # remembered between them and a restart costs a caller nothing
        self._manager = StreamableHTTPSessionManager(
            facade.build(ctx),
            stateless=True,
            json_response=True,
            security_settings=_security(ctx.settings.mcp_server_allowed_hosts),
        )

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            async with self._manager.run():
                log.info(
                    "outward mcp endpoint ready", extra={"fields": {"path": self._path}}
                )
                await self._app(scope, receive, send)
            return
        if scope["type"] == "http" and self._mine(scope.get("path", "")):
            await self._serve(scope, receive, send)
            return
        await self._app(scope, receive, send)

    def _mine(self, path: str) -> bool:
        return path == self._path or path.startswith(f"{self._path}/")

    async def _serve(self, scope: dict, receive: Any, send: Any) -> None:
        settings = self._ctx.settings
        headers = {k.decode("latin-1").lower(): v for k, v in scope["headers"]}
        address = _address(scope, headers)
        token_id = ""
        if settings.mcp_server_require_token:
            token = await tokens.verify(
                self._ctx.repo,
                tokens.bearer(headers.get("authorization", b"").decode("latin-1")),
            )
            if token is None:
                audit.refused(caller=address, reason="no valid token")
                await _refuse(
                    send,
                    401,
                    "This endpoint needs a bearer token.",
                    [(b"www-authenticate", b'Bearer realm="cra"')],
                )
                return
            # the token, not its owner: two tokens of one person are two
            # clients, each with its own budget and its own audit trail
            token_id = token.id
        caller = token_id or address
        allowance = self._ctx.limiter.check(
            f"mcp:{caller}", settings.mcp_server_rate_limit, WINDOW_S
        )
        if not allowance.allowed:
            audit.refused(caller=caller, reason="rate limit")
            await _refuse(
                send,
                429,
                "Too many requests.",
                [(b"retry-after", str(allowance.retry_after).encode())],
            )
            return
        await self._manager.handle_request(_stamped(scope, token_id), receive, send)


def _stamped(scope: dict, token_id: str) -> dict:
    """The scope with the verified token id attached, and any claim the caller
    made about it removed: the audit line must name the token that was shown.

    A header is the one thing that reaches the tool handler through the
    session manager; ``ToolContext.caller`` is not filled from it yet.
    """
    headers = [(k, v) for k, v in scope["headers"] if k.lower() != CALLER_HEADER]
    if token_id:
        headers.append((CALLER_HEADER, token_id.encode("ascii")))
    return {**scope, "headers": headers}


def _security(allowed_hosts: list[str]) -> TransportSecuritySettings | None:
    """Host and Origin checks against DNS rebinding, when the deployment says
    what it is called; a browser page on another site must not be able to
    reach this endpoint through the caller's own resolver."""
    if not allowed_hosts:
        return None
    hosts = [h.strip() for h in allowed_hosts if h.strip()]
    origins = [f"{scheme}://{h}" for h in hosts for scheme in ("https", "http")]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


def _address(scope: dict, headers: dict[str, bytes]) -> str:
    """Who to count an untokened request against.

    Behind the cluster's proxy every socket comes from the same address, so the
    forwarded one is the only thing that separates callers. The proxy appends
    the address it saw, so the last entry is its word and the earlier ones are
    the caller's; only the last is taken. The limit is cost control, and a
    deployment that needs a boundary turns the token gate on, where the bucket
    is the token instead.
    """
    forwarded = headers.get("x-forwarded-for", b"").decode("latin-1")
    if last := forwarded.rpartition(",")[2].strip():
        return last
    client = scope.get("client")
    return client[0] if client else "unknown"


async def _refuse(
    send: Any, status: int, message: str, headers: list[tuple[bytes, bytes]]
) -> None:
    body = json.dumps({"error": message}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                *headers,
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def wrap(app: Quart) -> Any:
    """The application to serve: the dispatcher when the endpoint is on."""
    ctx = app.extensions["cra"]
    if not ctx.settings.mcp_server_enabled:
        return app
    return Dispatcher(app, ctx)
