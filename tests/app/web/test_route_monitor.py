"""The admin console's Server info and Logs tabs."""

import json

import httpx
import pytest
import respx
from conftest import make_settings, sign_in

from cra.app.web.factory import create_app

OPENROUTER = "https://openrouter.ai/api/v1"


@pytest.fixture
async def app(tmp_path):
    app = create_app(make_settings(tmp_path, llm_base_url=OPENROUTER, llm_api_key="k"))
    async with app.test_app():
        yield app


@pytest.fixture
async def admin(app):
    return await sign_in(app, app.test_client(), "ops", role="admin")


@pytest.fixture
def credit():
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(f"{OPENROUTER}/key").mock(
            return_value=httpx.Response(
                200, json={"data": {"limit": 20, "limit_remaining": 19.2, "usage": 0.8}}
            )
        )
        yield route


async def server(admin):
    return await (await admin.get("/api/admin/server")).get_json()


async def test_an_answers_tokens_land_in_the_hour_it_was_given(app, admin, credit):
    repo = app.extensions["cra"].repo
    conversation = await repo.create_conversation(
        (await repo.get_credential_by_username("ops")).user_id
    )
    await repo.add_message(
        conversation.id,
        "assistant",
        "an answer",
        {"usage": {"prompt": 900, "completion": 100, "total": 1000}},
    )
    usage = (await server(admin))["usage"]
    assert usage["hours"][-1]["prompt"] == 900
    assert usage["totals"]["avg_tokens_per_answer"] == 1000


async def test_a_failed_answer_is_counted_by_kind(app, admin, credit):
    repo = app.extensions["cra"].repo
    conversation = await repo.create_conversation(
        (await repo.get_credential_by_username("ops")).user_id
    )
    for error in ("llm_out_of_credit", "cancelled"):
        await repo.add_message(conversation.id, "assistant", "", {"error": error})
    assert (await server(admin))["usage"]["failures"] == {"llm_out_of_credit": 1}


async def test_the_credit_is_asked_for_once_a_minute(admin, credit):
    first = await server(admin)
    await server(admin)
    assert first["llm"]["credit"]["limit_remaining"] == 19.2
    assert credit.call_count == 1


async def test_the_signed_in_admin_counts_as_active(admin, credit):
    assert (await server(admin))["app"]["active_users"]["5m"] == 1


async def test_the_logs_are_filtered_by_level(app, admin):
    log_dir = app.extensions["cra"].settings.log_dir
    log_dir.mkdir(exist_ok=True)
    (log_dir / "app.log").write_text(
        json.dumps({"ts": "2026-10-07T10:00:00", "level": "INFO", "msg": "login"})
        + "\n"
        + json.dumps({"ts": "2026-10-07T10:00:01", "level": "ERROR", "msg": "boom"})
        + "\n"
    )
    page = await (await admin.get("/api/admin/logs?levels=error,warning")).get_json()
    assert [e["msg"] for e in page["entries"]] == ["boom"]
