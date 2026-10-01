"""The pooled client for the external MCP servers.

A toy server on memory streams stands in for the proxies: it speaks the real
protocol through the real ``ClientSession``, so these tests exercise the same
code paths as a deployment without needing a socket.
"""

import json

import pytest
from fakes import Toy, make_host

SESSION = "session-1"


@pytest.fixture
def toy():
    return Toy()


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
