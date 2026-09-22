"""Sending feedback, and reading it in the console."""

import pytest
from conftest import make_settings

from cra.app.web.factory import create_app
from cra.app.web.route_feedback import CATEGORIES, MAX_CHARS, MAX_MESSAGES


@pytest.fixture
async def admin_app(tmp_path):
    app = create_app(
        make_settings(tmp_path, auth_dev_user="root", auth_admins=["root"])
    )
    async with app.test_app():
        yield app


@pytest.fixture
async def admin(admin_app):
    client = admin_app.test_client()
    await client.get("/auth/login")
    return client


async def json_of(response):
    return await response.get_json()


async def send(client, **body):
    payload = {"category": "Bug report", "text": "it broke", **body}
    return await client.post("/api/feedback", json=payload)


async def test_the_categories_are_offered_to_the_form(admin):
    assert (await json_of(await admin.get("/api/feedback")))["categories"] == list(
        CATEGORIES
    )


async def test_feedback_is_stored_with_its_conversation(admin):
    response = await send(
        admin,
        text="  the answer cited a paper that does not exist  ",
        model="qwen3.8-27b",
        messages=[
            {"role": "user", "content": "which papers cover perovskites?"},
            {"role": "assistant", "content": "these three…"},
        ],
    )
    assert response.status_code == 200

    (entry,) = (await json_of(await admin.get("/api/admin/feedback")))["feedback"]
    assert entry["text"] == "the answer cited a paper that does not exist"
    assert entry["from"] == "root"
    assert entry["category"] == "Bug report"
    assert entry["model"] == "qwen3.8-27b"
    assert [m["role"] for m in entry["messages"]] == ["user", "assistant"]


async def test_a_long_conversation_is_trimmed_to_the_recent_part(admin):
    messages = [{"role": "user", "content": str(n)} for n in range(MAX_MESSAGES + 10)]
    await send(admin, messages=messages)
    (entry,) = (await json_of(await admin.get("/api/admin/feedback")))["feedback"]
    assert len(entry["messages"]) == MAX_MESSAGES
    assert entry["messages"][-1]["content"] == str(MAX_MESSAGES + 9)


@pytest.mark.parametrize(
    "body",
    [
        {"category": "Praise"},
        {"text": "   "},
        {"text": "x" * (MAX_CHARS + 1)},
    ],
    ids=["unknown category", "empty note", "too long"],
)
async def test_bad_submissions_are_refused(admin, body):
    assert (await send(admin, **body)).status_code == 400
    assert (await json_of(await admin.get("/api/admin/feedback")))["feedback"] == []


async def test_feedback_is_listed_newest_first_and_can_be_deleted(admin):
    for n in range(3):
        await send(admin, text=f"note {n}")
    listed = (await json_of(await admin.get("/api/admin/feedback")))["feedback"]
    assert [f["text"] for f in listed] == ["note 2", "note 1", "note 0"]

    assert (
        await admin.delete(f"/api/admin/feedback/{listed[1]['id']}")
    ).status_code == 200
    remaining = (await json_of(await admin.get("/api/admin/feedback")))["feedback"]
    assert [f["text"] for f in remaining] == ["note 2", "note 0"]
    assert (
        await admin.delete(f"/api/admin/feedback/{listed[1]['id']}")
    ).status_code == 404


async def test_feedback_goes_when_the_account_goes(admin, admin_app):
    """Deleting an account promises the history goes with it."""
    repo = admin_app.extensions["cra"].repo
    other = await repo.create_user("ada")
    await repo.add_feedback(other.id, "Bug report", "from ada")
    await send(admin, text="from root")
    assert await repo.count_feedback() == 2

    await admin.delete(f"/api/admin/users/{other.id}")
    remaining = (await json_of(await admin.get("/api/admin/feedback")))["feedback"]
    assert [f["text"] for f in remaining] == ["from root"]


async def test_sending_feedback_needs_an_account(admin_app):
    anonymous = admin_app.test_client()
    assert (await send(anonymous)).status_code == 401
