"""The connected sources of one browser session.

Tokens are bearer credentials for somebody else's account on somebody else's
server, so they live in memory and nowhere else: a restart asks the user to
connect again, which is the trade we want over writing them to the database.

Remote tools are namespaced (``elab_*``, ``dt_*``) before the model ever sees
them, both so they cannot collide with the library's own tools and so a call
can be routed back to the server it came from by name alone.
"""

import json
import logging
import re
from typing import Any

from cra.assistant.mcpclient.degraded import (
    friendly_error,
    unavailable_name,
    unavailable_schema,
)
from cra.assistant.mcpclient.pool import RemotePool
from cra.core.connectors.sources import Source

log = logging.getLogger(__name__)


class RemoteHost:
    def __init__(
        self, sources: dict[str, Source], pool: RemotePool, allow_write: bool = False
    ) -> None:
        self.sources = sources
        self._pool = pool
        self._allow_write = allow_write
        self._tokens: dict[str, dict[str, str]] = {}
        self._counts: dict[str, dict[str, int]] = {}

    def offered(self, tools: list[Any]) -> list[Any]:
        """The tools the model may see. A tool that changes data on the user's
        own account is out unless the deployment opted in: what a tool returns
        can steer the model, and a steered model must not be able to write."""
        if self._allow_write:
            return list(tools)
        return [tool for tool in tools if is_read_only(tool)]

    def kind_of(self, tool_name: str) -> str | None:
        """Which source a tool name belongs to, or None for a local tool."""
        kind, _, rest = tool_name.partition("_")
        return kind if rest and kind in self.sources else None

    def status(self, session_id: str) -> dict[str, dict[str, Any]]:
        tokens = self._tokens.get(session_id, {})
        counts = self._counts.get(session_id, {})
        return {
            kind: {"active": kind in tokens, "tools": counts.get(kind, 0)}
            for kind in self.sources
        }

    def counts(self, session_id: str) -> dict[str, int]:
        return dict(self._counts.get(session_id, {}))

    async def connect(self, session_id: str, kind: str, token: str) -> dict[str, Any]:
        """Hold a token for this session, after proving it actually works."""
        source = self.sources[kind]
        token = token.strip()
        if not token:
            cleared = await self.disconnect(session_id, kind)
            return cleared | {"error": "Paste a token first."}
        self._tokens.setdefault(session_id, {})[kind] = token
        try:
            connection = await self._pool.acquire(
                (session_id, kind), source.authorised(token)
            )
        except Exception as exc:  # noqa: BLE001 -- a remote may fail any way
            log.info(
                "connect refused",
                extra={"fields": {"source": kind, "error": type(exc).__name__}},
            )
            await self.disconnect(session_id, kind)
            return {"kind": kind, "active": False, "tools": 0, "error": _refused(exc)}
        usable = self.offered(connection.tools)
        found = len(usable)
        if len(connection.tools) != found:
            log.info(
                "write tools withheld",
                extra={
                    "fields": {
                        "source": kind,
                        "withheld": sorted(
                            t.name for t in connection.tools if t not in usable
                        ),
                    }
                },
            )
        self._counts.setdefault(session_id, {})[kind] = found
        if not found:
            # a token the proxy accepts but that unlocks nothing is not a
            # connection worth keeping: the model would see an empty source
            await self.disconnect(session_id, kind)
            return {
                "kind": kind,
                "active": False,
                "tools": 0,
                "error": "That token unlocks no tools — register a new one.",
            }
        return {"kind": kind, "active": True, "tools": found, "error": None}

    async def disconnect(self, session_id: str, kind: str) -> dict[str, Any]:
        self._tokens.get(session_id, {}).pop(kind, None)
        self._counts.get(session_id, {}).pop(kind, None)
        await self._pool.release((session_id, kind))
        return {"kind": kind, "active": False, "tools": 0, "error": None}

    async def forget(self, session_id: str) -> None:
        """Signing out takes the tokens with it."""
        for kind in list(self._tokens.get(session_id, {})):
            await self.disconnect(session_id, kind)
        self._tokens.pop(session_id, None)
        self._counts.pop(session_id, None)

    async def schemas(self, session_id: str) -> list[dict[str, Any]]:
        """Every connected source's tools, as the model-facing schemas."""
        out: list[dict[str, Any]] = []
        for kind, token in list(self._tokens.get(session_id, {}).items()):
            source = self.sources[kind]
            try:
                connection = await self._pool.acquire(
                    (session_id, kind), source.authorised(token)
                )
            except Exception as exc:  # noqa: BLE001 -- a remote may fail any way
                # no traceback: the URL in an httpx message carries the token
                log.warning(
                    "source unavailable",
                    extra={"fields": {"source": kind, "error": type(exc).__name__}},
                )
                self._counts.setdefault(session_id, {})[kind] = 0
                out.append(
                    unavailable_schema(source.prefix, source.label, friendly_error(exc))
                )
                continue
            usable = self.offered(connection.tools)
            self._counts.setdefault(session_id, {})[kind] = len(usable)
            out.extend(_schema(source.prefix, tool) for tool in usable)
        return out

    async def call(
        self, session_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> str:
        kind = self.kind_of(tool_name)
        token = self._tokens.get(session_id, {}).get(kind or "")
        if kind is None or not token:
            return _error(f"Unknown tool: {tool_name}")
        source = self.sources[kind]
        if tool_name == unavailable_name(source.prefix):
            return _error(f"{source.label} is not answering right now.")
        name = tool_name[len(source.prefix) :]
        try:
            connection = await self._pool.acquire(
                (session_id, kind), source.authorised(token)
            )
            if name not in {t.name for t in self.offered(connection.tools)}:
                # not offered, so not callable by name either
                return _error(f"Unknown tool: {tool_name}")
            result = await connection.call(name, arguments)
        except Exception as exc:  # noqa: BLE001 -- a remote may fail any way
            # no traceback for the same reason as above
            log.warning(
                "remote tool failed",
                extra={
                    "fields": {
                        "source": kind,
                        "tool": name,
                        "error": type(exc).__name__,
                    }
                },
            )
            return _error(f"{source.label}: {friendly_error(exc)}")
        return _content(result)

    async def aclose(self) -> None:
        await self._pool.aclose()


# a server that declares nothing gets judged by the verb it chose
MUTATING_VERBS = re.compile(
    r"^(create|add|new|insert|update|edit|patch|set|put|post|write|save|upload|"
    r"delete|remove|destroy|drop|purge|clear|rename|move|copy|duplicate|archive|"
    r"restore|lock|unlock|assign|attach|detach|link|unlink|tag|untag|share|"
    r"publish|submit|send|execute|run)(_|$)",
    re.IGNORECASE,
)


def is_read_only(tool: Any) -> bool:
    """MCP's ``readOnlyHint`` when the server sets it; otherwise the name."""
    annotations = getattr(tool, "annotations", None)
    hint = getattr(annotations, "read_only_hint", None)
    if hint is not None:
        return bool(hint)
    return MUTATING_VERBS.match(str(tool.name)) is None


def _schema(prefix: str, tool: Any) -> dict[str, Any]:
    parameters = dict(getattr(tool, "input_schema", None) or {})
    parameters.setdefault("type", "object")
    parameters.setdefault("properties", {})
    return {
        "type": "function",
        "function": {
            "name": f"{prefix}{tool.name}",
            "description": tool.description or tool.name,
            "parameters": parameters,
        },
    }


def _content(result: Any) -> str:
    """One MCP result as the single string a tool message carries."""
    parts = [
        str(text)
        for block in getattr(result, "content", None) or []
        if (text := getattr(block, "text", None)) is not None
    ]
    structured = getattr(result, "structured_content", None)
    if structured:
        parts.append(json.dumps(structured, ensure_ascii=False, default=str))
    if getattr(result, "is_error", False):
        return _error("\n".join(parts) or "the tool reported an error")
    return "\n".join(parts) or json.dumps({"ok": True})


def _error(message: str) -> str:
    return json.dumps({"error": message})


def _refused(exc: Exception) -> str:
    return f"Could not connect: {friendly_error(exc)}"
