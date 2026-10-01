"""The outward MCP endpoint.

A real ``ClientSession`` talks to the dispatcher over an ASGI transport, so
these are the same code paths a deployment serves, minus the socket.
"""

import asyncio
import contextlib
import json

import httpx2
import pytest
from conftest import make_settings
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from cra.app.auth import tokens
from cra.app.mcpserver.dispatcher import Dispatcher, wrap
from cra.app.web.factory import create_app

BASE = "http://cra.test"


@contextlib.asynccontextmanager
async def lifespan(app):
    """Drive the ASGI lifespan by hand: both halves have to start."""
    to_app: asyncio.Queue = asyncio.Queue()
    from_app: asyncio.Queue = asyncio.Queue()
    task = asyncio.create_task(
        app({"type": "lifespan", "asgi": {"version": "3.0"}}, to_app.get, from_app.put)
    )
    await to_app.put({"type": "lifespan.startup"})
    started = await from_app.get()
    assert started["type"] == "lifespan.startup.complete", started
    try:
        yield app
    finally:
        await to_app.put({"type": "lifespan.shutdown"})
        assert (await from_app.get())["type"] == "lifespan.shutdown.complete"
        await task


def make_endpoint(tmp_path, **overrides):
    values = {
        "mcp_server_enabled": True,
        "mcp_server_require_token": False,
        **overrides,
    }
    return wrap(create_app(make_settings(tmp_path, **values)))


async def mint(app, owner: str = "a-colleague"):
    """A token row and its value, for an account made up on the spot; the
    schema exists only once the lifespan has started."""
    repo = app._ctx.repo
    user = await repo.create_user(owner)
    return await tokens.mint(repo, user.id, "test client")


@pytest.fixture
async def endpoint(tmp_path):
    async with lifespan(make_endpoint(tmp_path)) as app:
        yield app


@contextlib.asynccontextmanager
async def connect(app, token: str = ""):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with (
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url=BASE, headers=headers
        ) as http,
        streamable_http_client(f"{BASE}/mcp", http_client=http) as (
            read,
            write,
        ),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


def text_of(result) -> str:
    return "\n".join(block.text for block in result.content if hasattr(block, "text"))


async def test_the_endpoint_offers_the_public_tools_only(endpoint):
    async with connect(endpoint) as session:
        names = {tool.name for tool in (await session.list_tools()).tools}
    assert "search_papers" in names
    assert "get_paper_fulltext" not in names
    assert "get_proposal_fulltext" not in names


async def test_a_public_tool_answers(endpoint):
    async with connect(endpoint) as session:
        result = await session.call_tool("search_papers", {"query": "machine learning"})
    assert result.is_error is False
    payload = json.loads(text_of(result))
    assert payload["count"] == len(payload["results"]) > 0


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("get_proposal_fulltext", {}),
        ("get_paper_fulltext", {"doi": "10.1000/x"}),
        ("search_fulltext", {"query": "electrostatic"}),
    ],
)
async def test_an_internal_tool_is_refused_by_name(endpoint, name, arguments):
    """Not offered and not reachable: the registry gives the same answer to
    both. Passage search is in the list because passages around a chosen
    query reconstruct a paper, one query at a time."""
    async with connect(endpoint) as session:
        result = await session.call_tool(name, arguments)
    assert result.is_error is True
    assert "Unknown tool" in text_of(result)


async def test_the_web_app_still_answers_every_other_path(endpoint):
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=endpoint), base_url=BASE
    ) as http:
        health = await http.get("/api/health")
        page = await http.get("/")
    assert health.status_code == 200
    assert health.json()["ok"] is True
    assert page.status_code == 200


async def test_the_endpoint_is_absent_until_it_is_switched_on(tmp_path):
    settings = make_settings(tmp_path, mcp_server_enabled=False)
    app = create_app(settings)
    assert wrap(app) is app


async def test_a_token_is_required_when_the_gate_is_on(tmp_path):
    app = make_endpoint(tmp_path, mcp_server_require_token=True)
    async with lifespan(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url=BASE
        ) as http:
            refused = await http.post("/mcp", json={"jsonrpc": "2.0", "method": "ping"})
        assert refused.status_code == 401
        assert refused.headers["www-authenticate"].startswith("Bearer")

        _, token = await mint(app)
        async with connect(app, token) as session:
            assert (await session.list_tools()).tools


async def test_a_revoked_token_is_refused(tmp_path):
    app = make_endpoint(tmp_path, mcp_server_require_token=True)
    async with (
        lifespan(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url=BASE
        ) as http,
    ):
        row, token = await mint(app)
        await app._ctx.repo.revoke_token(row.id)
        response = await http.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "ping"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 401


async def test_too_many_requests_are_slowed_down(tmp_path):
    app = make_endpoint(tmp_path, mcp_server_rate_limit=1)
    async with (
        lifespan(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url=BASE
        ) as http,
    ):
        first = await http.post("/mcp", json={"jsonrpc": "2.0", "method": "ping"})
        second = await http.post("/mcp", json={"jsonrpc": "2.0", "method": "ping"})
    assert first.status_code != 429
    assert second.status_code == 429
    assert int(second.headers["retry-after"]) >= 1


async def test_callers_behind_the_proxy_are_counted_apart(tmp_path):
    """One address for every request otherwise: one crawler would block everyone."""
    app = make_endpoint(tmp_path, mcp_server_rate_limit=1)
    ping = {"jsonrpc": "2.0", "method": "ping"}
    async with (
        lifespan(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url=BASE
        ) as http,
    ):
        first = await http.post(
            "/mcp", json=ping, headers={"X-Forwarded-For": "10.0.0.1"}
        )
        other = await http.post(
            "/mcp", json=ping, headers={"X-Forwarded-For": "10.0.0.2"}
        )
        again = await http.post(
            "/mcp", json=ping, headers={"X-Forwarded-For": "10.0.0.1"}
        )
    assert (first.status_code, other.status_code) != (429, 429)
    assert again.status_code == 429


async def test_the_audit_line_names_the_token(tmp_path, caplog):
    app = make_endpoint(tmp_path, mcp_server_require_token=True)
    async with lifespan(app):
        row, token = await mint(app)
        with caplog.at_level("INFO", logger="cra.mcp.audit"):
            async with connect(app, token) as s:
                await s.call_tool("search_papers", {"query": "machine learning"})
    fields = [r.fields for r in caplog.records if r.message == "mcp call"]
    assert fields == [
        {
            "caller": row.id,
            "tool": "search_papers",
            "ok": True,
            "duration_ms": fields[0]["duration_ms"],
            "arguments": 1,
        }
    ]


async def test_a_caller_cannot_name_themselves(tmp_path, caplog):
    """The audit line comes from the token, not from a header anyone can send."""
    app = make_endpoint(tmp_path, mcp_server_require_token=True)
    async with lifespan(app):
        row, token = await mint(app)
        headers = {
            "Authorization": f"Bearer {token}",
            "X-CRA-Caller": "someone-else",
        }
        with caplog.at_level("INFO", logger="cra.mcp.audit"):
            async with (
                httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app),
                    base_url=BASE,
                    headers=headers,
                ) as http,
                streamable_http_client(f"{BASE}/mcp", http_client=http) as (
                    read,
                    write,
                ),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                await session.call_tool("search_papers", {"query": "x"})
    callers = [r.fields["caller"] for r in caplog.records if r.message == "mcp call"]
    assert callers == [row.id]


def test_the_dispatcher_only_claims_its_own_path(tmp_path):
    app = create_app(
        make_settings(tmp_path, mcp_server_enabled=True, mcp_server_require_token=False)
    )
    dispatcher = Dispatcher(app, app.extensions["cra"])
    assert dispatcher._mine("/mcp")
    assert dispatcher._mine("/mcp/messages")
    assert not dispatcher._mine("/mcp-other")
    assert not dispatcher._mine("/api/health")


async def test_only_the_proxys_own_forwarded_entry_counts(tmp_path):
    """The client may write anything into X-Forwarded-For; the proxy appends
    the address it saw. Counting by the first entry would let a caller pick
    a fresh bucket per request."""
    app = make_endpoint(tmp_path, mcp_server_rate_limit=1)
    ping = {"jsonrpc": "2.0", "method": "ping"}
    async with (
        lifespan(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url=BASE
        ) as http,
    ):
        first = await http.post(
            "/mcp", json=ping, headers={"X-Forwarded-For": "1.1.1.1, 10.0.0.1"}
        )
        again = await http.post(
            "/mcp", json=ping, headers={"X-Forwarded-For": "2.2.2.2, 10.0.0.1"}
        )
    assert first.status_code != 429
    assert again.status_code == 429
