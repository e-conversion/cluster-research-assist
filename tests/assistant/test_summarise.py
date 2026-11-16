"""Summarising the library's papers at build time."""

import asyncio
import json

from conftest import make_settings

from cra.assistant import summarise
from cra.core.library import summaries as store


class Answering:
    """A client that answers each call with the next of its contents."""

    def __init__(self, *contents):
        self.contents = list(contents)
        self.calls: list[dict] = []

    async def complete(self, request, timeout_s=None):
        self.calls.append(request)
        content = self.contents.pop(0)
        if isinstance(content, Exception):
            raise content
        return {"choices": [{"message": {"content": content}}]}


GOOD = json.dumps(
    {
        "summary": "Platinum films were grown by ALD.",
        "approach": "experimental",
        "material_system": "Pt thin films",
        "characterization_methods": ["XRD"],
    }
)


def job(doi="10.1/a", source="fulltext", text="The paper's text."):
    return store.Job(
        doi, "A title", source, text, store.input_hash("A title", source, text)
    )


def run(coro):
    return asyncio.run(coro)


def test_the_request_names_the_paper_and_says_what_text_it_is(tmp_path):
    settings = make_settings(tmp_path)
    request = summarise.request_for(settings, "m", job())
    system, user = (m["content"] for m in request["messages"])
    assert request["model"] == "m"
    assert "JSON" in system
    assert '"approach"' in system
    assert "Title: A title" in user
    assert "Text: full text" in user
    assert summarise.ABSTRACT_ONLY not in user


def test_an_abstract_alone_is_said_to_be_one(tmp_path):
    settings = make_settings(tmp_path)
    request = summarise.request_for(settings, "m", job(source="abstract"))
    user = request["messages"][1]["content"]
    assert "Text: abstract only" in user
    assert summarise.ABSTRACT_ONLY in user


def test_the_question_states_the_same_limits_the_answers_are_held_to():
    for limit in store.LIMITS.values():
        assert f"at most {limit} characters" in summarise.INSTRUCTION
    assert f"up to {store.LIST_ITEMS}" in summarise.INSTRUCTION
    for approach in store.APPROACHES:
        assert f'"{approach}"' in summarise.INSTRUCTION


def test_openrouter_is_asked_to_think_little(tmp_path):
    plain = make_settings(tmp_path)
    routed = make_settings(tmp_path, llm_base_url="https://openrouter.ai/api/v1")
    assert "reasoning" not in summarise.request_for(plain, "m", job())
    assert summarise.request_for(routed, "m", job())["reasoning"] == {
        "effort": "minimal"
    }


def test_a_good_answer_gives_the_fields(tmp_path):
    client = Answering("Sure! <think>{hmm}</think> " + GOOD)
    fields = run(summarise.summarise_one(client, make_settings(tmp_path), "m", job()))
    assert fields["approach"] == "experimental"
    assert fields["characterization_methods"] == ["XRD"]
    assert len(client.calls) == 1


def test_an_unusable_answer_is_tried_once_more(tmp_path):
    client = Answering("I cannot do that.", GOOD)
    fields = run(summarise.summarise_one(client, make_settings(tmp_path), "m", job()))
    assert fields["summary"].startswith("Platinum")
    assert len(client.calls) == 2


def test_an_answer_with_no_known_kind_of_work_is_not_kept(tmp_path):
    bad = json.dumps({"summary": "Something.", "approach": "alchemy"})
    client = Answering(bad, bad)
    assert (
        run(summarise.summarise_one(client, make_settings(tmp_path), "m", job()))
        is None
    )
    assert len(client.calls) == summarise.ATTEMPTS


def test_a_failing_endpoint_gives_nothing_rather_than_an_error(tmp_path):
    client = Answering(RuntimeError("500"), RuntimeError("500"))
    assert (
        run(summarise.summarise_one(client, make_settings(tmp_path), "m", job()))
        is None
    )


def test_every_paper_is_reported_as_it_ends_and_failures_are_counted(tmp_path):
    jobs = [job("10.1/a"), job("10.1/b"), job("10.1/c")]
    # one worker at a time, so the answers fall in order: ok, unusable twice, ok
    client = Answering(GOOD, "no", "no", GOOD)
    reported = []
    outcome = run(
        summarise.summarise(
            client,
            make_settings(tmp_path),
            "m",
            jobs,
            lambda j, fields: reported.append((j.doi, fields is not None)),
            concurrency=1,
        )
    )
    assert reported == [("10.1/a", True), ("10.1/b", False), ("10.1/c", True)]
    assert (outcome.summarised, outcome.failed) == (2, 1)
