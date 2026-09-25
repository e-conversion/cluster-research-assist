"""The pooled client for the external MCP servers.

A toy server on memory streams stands in for the proxies: it speaks the real
protocol through the real ``ClientSession``, so these tests exercise the same
code paths as a deployment without needing a socket.
"""

import asyncio
import contextlib
import json

import pytest
from conftest import make_settings, sign_in
from cryptography.fernet import Fernet
from mcp.server.mcpserver import MCPServer
from mcp.shared.memory import create_client_server_memory_streams
from mcp.types import ToolAnnotations

from cra.app.web import route_chat
from cra.app.web.factory import create_app
from cra.assistant.mcpclient.host import RemoteHost
from cra.assistant.mcpclient.pool import RemotePool
from cra.core.connectors.sources import Source
from cra.core.tools.tiers import Tier

SESSION = "session-1"
ELAB = Source(
    kind="elab",
    label="eLabFTW",
    key_label="eLabFTW API key",
    url="https://proxy.invalid/el/mcp",
    register_url="https://proxy.invalid/el/register",
    default_base_url="https://eln.invalid",
)


class Toy:
    """An MCP server behind a transport that checks the token in the URL."""

    def __init__(self, token: str = "good") -> None:
        self.token = token
        self.down = False
        self.connections = 0
        self.server = MCPServer("toy")

        @self.server.tool(annotations=ToolAnnotations(read_only_hint=True))
        def echo(text: str) -> str:
            """Say it back."""
            return f"echo: {text}"

        @self.server.tool()
        def count_items() -> int:
            """How many items there are."""
            return 7

        # what the proxies really look like: writes with no annotation, told
        # apart by the verb, and one that declares itself despite its name
        @self.server.tool()
        def create_item(title: str) -> str:
            """Add an item."""
            return f"created {title}"

        @self.server.tool(annotations=ToolAnnotations(read_only_hint=False))
        def lookup_and_mark(item: int) -> str:
            """Marks an item as seen."""
            return f"marked {item}"

    @contextlib.asynccontextmanager
    async def transport(self, url: str):
        self.connections += 1
        if self.down:
            raise ConnectionError("connection refused")
        if url.partition("token=")[2] != self.token:
            raise RuntimeError("HTTP 401 Unauthorized")
        async with create_client_server_memory_streams() as (client, server):
            low = self.server._lowlevel_server
            task = asyncio.create_task(
                low.run(server[0], server[1], low.create_initialization_options())
            )
            try:
                yield client
            finally:
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task


@pytest.fixture
def toy():
    return Toy()


def make_host(toy: Toy, idle_s: float = 600.0, allow_write: bool = False) -> RemoteHost:
    return RemoteHost(
        {"elab": ELAB}, RemotePool(idle_s, toy.transport), allow_write=allow_write
    )


@pytest.fixture
async def host(toy):
    host = make_host(toy)
    yield host
    await host.aclose()


async def test_a_connected_source_offers_its_tools(host, toy):
    assert await host.connect(SESSION, "elab", "good") == {
        "kind": "elab",
        "active": True,
        "tools": 2,
        "error": None,
    }
    assert host.status(SESSION)["elab"] == {"active": True, "tools": 2}
    names = [s["function"]["name"] for s in await host.schemas(SESSION)]
    assert names == ["elab_echo", "elab_count_items"]


async def test_a_tool_call_reaches_the_source(host):
    await host.connect(SESSION, "elab", "good")
    result = await host.call(SESSION, "elab_echo", {"text": "hi"})
    assert "echo: hi" in result


async def test_one_session_serves_every_call(host, toy):
    await host.connect(SESSION, "elab", "good")
    for _ in range(3):
        await host.call(SESSION, "elab_echo", {"text": "hi"})
    assert toy.connections == 1


async def test_two_browser_sessions_do_not_share_a_connection(host, toy):
    await host.connect(SESSION, "elab", "good")
    await host.connect("session-2", "elab", "good")
    assert toy.connections == 2
    assert host.status("session-2")["elab"]["active"] is True


async def test_a_refused_token_is_not_kept(host):
    result = await host.connect(SESSION, "elab", "wrong")
    assert result["active"] is False
    assert "401" in result["error"]
    assert host.status(SESSION)["elab"] == {"active": False, "tools": 0}
    unknown = await host.call(SESSION, "elab_echo", {"text": "hi"})
    assert json.loads(unknown)["error"].startswith("Unknown tool")


async def test_a_source_that_stops_answering_becomes_one_entry(toy):
    # idle 0: the next use closes the pooled session, so the failure is a
    # reconnect against a server that is now down
    host = make_host(toy, idle_s=0.0)
    await host.connect(SESSION, "elab", "good")
    toy.down = True
    schemas = await host.schemas(SESSION)
    assert [s["function"]["name"] for s in schemas] == ["elab___unavailable__"]
    assert host.status(SESSION)["elab"] == {"active": True, "tools": 0}
    failed = await host.call(SESSION, "elab_echo", {"text": "hi"})
    assert "eLabFTW" in json.loads(failed)["error"]
    await host.aclose()


async def test_an_unused_session_is_closed(toy):
    host = make_host(toy, idle_s=0.0)
    await host.connect(SESSION, "elab", "good")
    await host.schemas(SESSION)
    assert toy.connections == 2
    assert len(host._pool) == 1
    await host.aclose()


async def test_disconnect_and_forget_close_the_session(host, toy):
    await host.connect(SESSION, "elab", "good")
    await host.disconnect(SESSION, "elab")
    assert len(host._pool) == 0
    assert host.status(SESSION)["elab"] == {"active": False, "tools": 0}

    await host.connect(SESSION, "elab", "good")
    await host.forget(SESSION)
    assert len(host._pool) == 0
    assert await host.schemas(SESSION) == []


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("elab_echo", "elab"),
        ("elab___unavailable__", "elab"),
        ("search_papers", None),
        ("elab", None),
        ("dt_upload", None),
    ],
)
def test_a_tool_name_says_where_it_runs(host, name, expected):
    assert host.kind_of(name) == expected


@pytest.fixture
async def connected_app(tmp_path, toy):
    """An app whose one source is the toy server."""
    settings = make_settings(
        tmp_path,
        mcp_elab_url=ELAB.url,
        mcp_elab_register_url=ELAB.register_url,
        mcp_elab_base_url=ELAB.default_base_url,
    )
    app = create_app(settings)
    async with app.test_app():
        app.extensions["cra"].remote = make_host(toy)
        yield app
        await app.extensions["cra"].remote.aclose()


@pytest.fixture
async def client(connected_app):
    client = connected_app.test_client()
    await sign_in(connected_app, client)
    return client


async def test_the_session_reports_what_is_connected(connected_app, client):
    config = await (await client.get("/api/config")).get_json()
    assert config["sources"]["elab"]["label"] == "eLabFTW"
    assert config["sources"]["elab"]["base_url"] == ELAB.default_base_url

    before = await (await client.get("/api/session")).get_json()
    assert before["connected"] == {"elab": {"active": False, "tools": 0}}

    connected = await client.post("/api/session/connect/elab", json={"token": "good"})
    assert (await connected.get_json())["tools"] == 2

    after = await (await client.get("/api/session")).get_json()
    assert after["connected"] == {"elab": {"active": True, "tools": 2}}
    assert after["tools"]["elab"] == 2
    assert after["tools"]["total"] == after["tools"]["local"] + 2

    await client.delete("/api/session/connect/elab")
    gone = await (await client.get("/api/session")).get_json()
    assert gone["connected"] == {"elab": {"active": False, "tools": 0}}


async def test_a_bad_token_is_answered_with_a_message(client):
    response = await client.post("/api/session/connect/elab", json={"token": "wrong"})
    assert response.status_code == 400
    assert "401" in (await response.get_json())["error"]


async def test_an_unknown_source_is_not_found(client):
    response = await client.post("/api/session/connect/nope", json={"token": "x"})
    assert response.status_code == 404


async def test_signing_out_takes_the_tokens(connected_app, client):
    await client.post("/api/session/connect/elab", json={"token": "good"})
    remote = connected_app.extensions["cra"].remote
    await client.post("/auth/logout")
    assert len(remote._pool) == 0


async def test_a_turn_offers_both_tool_sets_and_routes_by_name(connected_app):
    """The model sees one list; the name decides which side runs the call."""
    ctx = connected_app.extensions["cra"]
    await ctx.remote.connect(SESSION, "elab", "good")
    turn = route_chat._Turn(
        ctx=ctx,
        session_id=SESSION,
        conversation_id="c1",
        chosen={"model": "m", "params": {"max_tool_rounds": "1"}},
        history=[],
        question="q",
        tier=Tier.INTERNAL,
        cancel=None,
    )
    tool_ctx = ctx.tool_context(Tier.INTERNAL)
    assert "echo: hi" in await turn.call("elab_echo", {"text": "hi"}, tool_ctx)
    local = await turn.call("library_status", {}, tool_ctx)
    assert local["counts"]["papers"] == 19


async def test_tools_that_write_are_withheld_unless_the_deployment_opts_in(toy):
    """A tool result can steer the model; a steered model must not be able to
    change the user's lab notebook. Read-only by annotation or by name."""
    guarded = make_host(toy)
    await guarded.connect(SESSION, "elab", "good")
    names = [s["function"]["name"] for s in await guarded.schemas(SESSION)]
    assert names == ["elab_echo", "elab_count_items"]
    refused = await guarded.call(SESSION, "elab_create_item", {"title": "x"})
    assert json.loads(refused)["error"].startswith("Unknown tool")
    await guarded.aclose()

    open_host = make_host(toy, allow_write=True)
    await open_host.connect(SESSION, "elab", "good")
    names = [s["function"]["name"] for s in await open_host.schemas(SESSION)]
    assert names == [
        "elab_echo",
        "elab_count_items",
        "elab_create_item",
        "elab_lookup_and_mark",
    ]
    assert "created x" in await open_host.call(
        SESSION, "elab_create_item", {"title": "x"}
    )
    await open_host.aclose()


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("HTTP 500 for https://proxy/el/mcp?token=SECRET-TOKEN&x=1"),
        ConnectionError("cannot reach https://proxy/el/mcp?token=SECRET-TOKEN"),
    ],
)
def test_errors_never_carry_the_token(exc):
    from cra.assistant.mcpclient.degraded import friendly_error

    text = friendly_error(exc)
    assert "SECRET-TOKEN" not in text
    assert "token=***" in text or "did not answer" in text


KEY = Fernet.generate_key().decode()


@pytest.fixture
async def kept_app(tmp_path, toy):
    """The toy source, with tokens kept for the account."""
    settings = make_settings(
        tmp_path,
        mcp_elab_url=ELAB.url,
        mcp_elab_register_url=ELAB.register_url,
        mcp_elab_base_url=ELAB.default_base_url,
        source_token_key=KEY,
    )
    app = create_app(settings)
    async with app.test_app():
        app.extensions["cra"].remote = make_host(toy)
        yield app
        await app.extensions["cra"].remote.aclose()


async def elab_status(client):
    return (await (await client.get("/api/session")).get_json())["connected"]["elab"]


async def test_a_connection_follows_the_account_to_a_new_session(kept_app):
    first = await sign_in(kept_app, kept_app.test_client())
    await first.post("/api/session/connect/elab", json={"token": "good"})
    await first.post("/auth/logout")
    elsewhere = await sign_in(kept_app, kept_app.test_client())
    assert await elab_status(elsewhere) == {"active": True, "tools": 2}


async def test_the_stored_token_is_not_readable_without_the_key(kept_app):
    client = await sign_in(kept_app, kept_app.test_client())
    await client.post("/api/session/connect/elab", json={"token": "good"})
    repo = kept_app.extensions["cra"].repo
    user_id = (await repo.get_credential_by_username("alice")).user_id
    (row,) = await repo.source_connections_of(user_id)
    assert "good" not in row.sealed
    assert Fernet(KEY).decrypt(row.sealed.encode()) == b"good"


async def test_disconnecting_forgets_the_stored_token(kept_app):
    client = await sign_in(kept_app, kept_app.test_client())
    await client.post("/api/session/connect/elab", json={"token": "good"})
    await client.delete("/api/session/connect/elab")
    elsewhere = await sign_in(kept_app, kept_app.test_client())
    assert (await elab_status(elsewhere))["active"] is False


async def test_a_token_sealed_under_another_key_is_dropped(kept_app):
    client = await sign_in(kept_app, kept_app.test_client())
    repo = kept_app.extensions["cra"].repo
    user_id = (await repo.get_credential_by_username("alice")).user_id
    other = Fernet(Fernet.generate_key()).encrypt(b"good").decode()
    await repo.save_source_connection(user_id, "elab", other)
    assert (await elab_status(client))["active"] is False
    assert await repo.source_connections_of(user_id) == []


@pytest.mark.parametrize("how", ["deactivate", "password reset"])
async def test_locking_an_account_forgets_its_connections(kept_app, how):
    client = await sign_in(kept_app, kept_app.test_client())
    await client.post("/api/session/connect/elab", json={"token": "good"})
    admin = await sign_in(kept_app, kept_app.test_client(), "ops", role="admin")
    repo = kept_app.extensions["cra"].repo
    user_id = (await repo.get_credential_by_username("alice")).user_id
    if how == "deactivate":
        await admin.put(f"/api/admin/users/{user_id}", json={"is_active": False})
    else:
        await admin.post(f"/api/admin/users/{user_id}/password-reset")
    assert await repo.source_connections_of(user_id) == []


async def test_without_a_key_nothing_is_stored(connected_app, client):
    await client.post("/api/session/connect/elab", json={"token": "good"})
    repo = connected_app.extensions["cra"].repo
    user_id = (await repo.get_credential_by_username("alice")).user_id
    assert await repo.source_connections_of(user_id) == []
