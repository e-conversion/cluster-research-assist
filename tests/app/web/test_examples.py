"""The example questions on the landing page."""


async def test_the_landing_page_is_offered_questions_worth_asking(client):
    from cra.app.web.examples import QUESTIONS, SHOWN

    config = await (await client.get("/api/config")).get_json()
    assert len(config["examples"]) == SHOWN
    assert set(config["examples"]) <= set(QUESTIONS)
    # a different few each time, so the page does not always suggest the same
    seen = {
        tuple(sorted((await (await client.get("/api/config")).get_json())["examples"]))
        for _ in range(12)
    }
    assert len(seen) > 1
