"""What a session reports about itself and its conversation."""

from conftest import sign_in

from cra.app.web.sessions import COOKIE_NAME


async def test_the_session_reports_the_tools_the_caller_may_use(app, client):
    await sign_in(app, client)
    body = await (await client.get("/api/session")).get_json()
    # every tool but semantic search, which needs an encoder this test has not
    # configured; the library's vectors alone still answer "papers like this one"
    assert body["tools"]["local"] == 19
    assert body["tools"]["total"] == 19
    health = await (await client.get("/api/health")).get_json()
    assert health["tools"] == 19


async def test_the_session_hands_back_the_conversation_it_is_in(client, app):
    ctx = app.extensions["cra"]
    await sign_in(app, client)
    body = await (await client.get("/api/session")).get_json()
    assert (body["messages"], body["turns"], body["conversation"]) == ([], 0, None)

    # what a finished turn leaves behind
    principal_id = (await ctx.repo.list_users())[0].id
    conversation = await ctx.repo.create_conversation(principal_id, "About perovskites")
    await ctx.repo.add_message(conversation.id, "user", "which papers?")
    await ctx.repo.add_message(
        conversation.id,
        "assistant",
        "These two.",
        {"model": "m", "tools": [{"name": "t"}]},
    )
    from cra.app.web.sessions import COOKIE_NAME

    cookie = next(c for c in client.cookie_jar if c.name == COOKIE_NAME)
    state = await ctx.sessions.load(cookie.value)
    state.data = {"conversation": conversation.id}
    await ctx.sessions.save(state)

    body = await (await client.get("/api/session")).get_json()
    assert body["turns"] == 1
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["messages"][1]["meta"]["model"] == "m"


async def test_signing_in_as_someone_else_does_not_continue_their_conversation(
    app, client
):
    """Shared lab machine: A walks away signed in, B signs in on top."""
    ctx = app.extensions["cra"]
    await sign_in(app, client, "ada")
    ada = (await ctx.repo.list_users())[0]
    conversation = await ctx.repo.create_conversation(ada.id, "Ada's")
    await ctx.repo.add_message(conversation.id, "user", "my secret question")
    await client.post(f"/api/conversations/{conversation.id}/open")
    assert (await (await client.get("/api/session")).get_json())["turns"] == 1

    await sign_in(app, client, "bob")
    session = await (await client.get("/api/session")).get_json()
    assert session["user"] == "bob"
    assert (session["conversation"], session["messages"]) == (None, [])


async def test_a_session_naming_someone_elses_conversation_shows_nothing(app, client):
    ctx = app.extensions["cra"]
    await sign_in(app, client)
    bob = await ctx.repo.create_user("bob")
    theirs = await ctx.repo.create_conversation(bob.id, "Private")
    await ctx.repo.add_message(theirs.id, "user", "secret")
    cookie = next(c for c in client.cookie_jar if c.name == COOKIE_NAME)
    state = await ctx.sessions.load(cookie.value)
    state.data = {"conversation": theirs.id}
    await ctx.sessions.save(state)

    body = await (await client.get("/api/session")).get_json()
    assert (body["conversation"], body["messages"]) == (None, [])
    # and a question would start a fresh one rather than append to theirs
    from cra.app.web import route_chat

    principal = type("P", (), {"user_id": (await ctx.repo.list_users())[0].id})()
    started = await route_chat._conversation(ctx, state, principal)
    assert started != theirs.id
