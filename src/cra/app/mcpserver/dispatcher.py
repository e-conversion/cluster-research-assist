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
from quart import Quart

from cra.app.auth import tokens
from cra.app.mcpserver import audit, facade

log = logging.getLogger(__name__)

SUBJECT_HEADER = b"x-cra-subject"
WINDOW_S = 60


class Dispatcher:
    def __init__(self, app: Quart, ctx: Any) -> None:
        self._app = app
        self._ctx = ctx
        self._path = ctx.settings.base_path + ctx.settings.mcp_server_path
        # stateless: every request stands on its own, so nothing has to be
        # remembered between them and a restart costs a caller nothing
        self._manager = StreamableHTTPSessionManager(
            facade.build(ctx), stateless=True, json_response=True
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
        subject = ""
        if settings.mcp_server_require_token:
            claims = tokens.verify(
                settings.mcp_token_secret.get_secret_value(),
                tokens.bearer(headers.get("authorization", b"").decode("latin-1")),
            )
            if claims is None:
                audit.refused(caller=address, reason="no valid token")
                await _refuse(
                    send,
                    401,
                    "This endpoint needs a bearer token.",
                    [(b"www-authenticate", b'Bearer realm="cra"')],
                )
                return
            subject = claims.subject
        caller = subject or _address(scope, headers)
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
        await self._manager.handle_request(_stamped(scope, subject), receive, send)


def _stamped(scope: dict, subject: str) -> dict:
    """The scope with the verified subject attached, and any claim the caller
    made about it removed: the audit line must say who the token says they are."""
    headers = [(k, v) for k, v in scope["headers"] if k.lower() != SUBJECT_HEADER]
    if subject:
        headers.append((SUBJECT_HEADER, subject.encode("latin-1", "replace")))
    return {**scope, "headers": headers}


def _address(scope: dict, headers: dict[str, bytes]) -> str:
    """Who to count an untokened request against.

    Behind the cluster's proxy every socket comes from the same address, so the
    forwarded one is the only thing that separates callers. Trusting it is fine
    here: the limit is cost control, and a deployment that needs a boundary
    turns the token gate on, where the bucket is the token's subject instead.
    """
    forwarded = headers.get("x-forwarded-for", b"").decode("latin-1")
    if first := forwarded.split(",")[0].strip():
        return first
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
