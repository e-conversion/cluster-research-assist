"""Pooled sessions to the external MCP servers.

One streamable-HTTP session per (browser session, source), kept open between
calls: the handshake costs two round trips and the prototype paid them on every
single tool call, plus a whole event loop per call.

A session is owned by the task that opened it. The transport nests anyio cancel
scopes, and those may only be left in the task that entered them, so each
connection runs in a small task of its own that opens, waits, and closes; every
other task only ever borrows the session object.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 20.0
CALL_TIMEOUT_S = 120.0
CLOSE_TIMEOUT_S = 5.0

Transport = Callable[[str], AbstractAsyncContextManager[Any]]
Key = tuple[str, str]


class RemoteError(Exception):
    pass


class Connection:
    """One live MCP session, opened and closed inside its own task."""

    def __init__(self, url: str, transport: Transport) -> None:
        self._url = url
        self._transport = transport
        self._ready = asyncio.Event()
        self._release = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._failure: BaseException | None = None
        self._turn = asyncio.Lock()
        self.session: ClientSession | None = None
        self.tools: list[Any] = []
        self.used_at = time.monotonic()

    @property
    def alive(self) -> bool:
        return self.session is not None

    async def open(self) -> None:
        self._task = asyncio.create_task(self._serve())
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT_S):
                await self._ready.wait()
        except TimeoutError:
            await self.close()
            raise RemoteError("the server did not answer in time") from None
        if self._failure is not None:
            await self.close()
            raise self._failure

    async def _serve(self) -> None:
        try:
            async with (
                self._transport(self._url) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                listing = await session.list_tools()
                self.tools = list(listing.tools)
                self.session = session
                self._ready.set()
                await self._release.wait()
        except asyncio.CancelledError:
            self._failure = RemoteError("the connection was closed")
            raise
        except BaseException as exc:  # noqa: BLE001 -- anyio reports as a group
            # the opener is waiting on _ready and re-raises this; a failure
            # after that point is a dropped connection, reopened on next use
            self._failure = exc
        finally:
            self.session = None
            self._ready.set()

    async def call(self, name: str, arguments: dict[str, Any]) -> Any:
        session = self.session
        if session is None:
            raise RemoteError("the connection to this source was closed")
        # one request at a time: the turn calls tools in sequence anyway, and
        # this keeps a slow call from interleaving with a close
        async with self._turn:
            self.used_at = time.monotonic()
            return await session.call_tool(
                name, arguments, read_timeout_seconds=CALL_TIMEOUT_S
            )

    async def close(self) -> None:
        self._release.set()
        if self._task is None:
            return
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await asyncio.wait_for(self._task, CLOSE_TIMEOUT_S)


class RemotePool:
    """The open connections, one per (browser session, source)."""

    def __init__(
        self, idle_s: float, transport: Transport = streamable_http_client
    ) -> None:
        self._idle_s = idle_s
        self._transport = transport
        self._live: dict[Key, Connection] = {}
        self._locks: dict[Key, asyncio.Lock] = {}
        self._gate = asyncio.Lock()

    def __len__(self) -> int:
        return len(self._live)

    async def acquire(self, key: Key, url: str) -> Connection:
        """The open connection for this key, reconnecting if it dropped."""
        await self.sweep()
        async with await self._lock_for(key):
            found = self._live.get(key)
            if found is not None and found.alive:
                found.used_at = time.monotonic()
                return found
            if found is not None:
                await self._drop(key)
            fresh = Connection(url, self._transport)
            await fresh.open()
            self._live[key] = fresh
            log.info("mcp session opened", extra={"fields": {"source": key[1]}})
            return fresh

    async def release(self, key: Key) -> None:
        async with await self._lock_for(key):
            await self._drop(key)

    async def sweep(self) -> None:
        """Close what nobody has used for a while. A browser session that is
        simply gone leaves its connection behind otherwise."""
        cutoff = time.monotonic() - self._idle_s
        stale = [
            key
            for key, connection in list(self._live.items())
            if connection.used_at < cutoff or not connection.alive
        ]
        for key in stale:
            await self.release(key)
        async with self._gate:
            for key, lock in list(self._locks.items()):
                if key not in self._live and not lock.locked():
                    del self._locks[key]

    async def aclose(self) -> None:
        for key in list(self._live):
            await self.release(key)

    async def _lock_for(self, key: Key) -> asyncio.Lock:
        async with self._gate:
            return self._locks.setdefault(key, asyncio.Lock())

    async def _drop(self, key: Key) -> None:
        connection = self._live.pop(key, None)
        if connection is not None:
            await connection.close()
            log.info("mcp session closed", extra={"fields": {"source": key[1]}})
