"""Naming the publication map's groups at build time."""

import json

import pytest
from conftest import make_settings

from cra.assistant import naming


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


def group(id_, parent=None, terms=("perovskite",)):
    return {
        "id": id_,
        "parent": parent,
        "size": 10,
        "terms": list(terms),
        "coverage": 0.9,
        "central": [f"10.1000/{id_}"],
        "fingerprint": f"fp-{id_}",
        "name": " / ".join(terms),
        "named_by": "terms",
    }


@pytest.fixture
def payload():
    return {
        "groups": [
            group("g0", terms=("perovskite", "solar cell")),
            group("g1", terms=("copper", "co2")),
            group("g2", "g0", ("halide",)),
            group("g3", "g0", ("organic",)),
        ]
    }


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path, llm_api_key="a-key")


def paper(doi):
    return (f"Title of {doi}", "An abstract about it.")


async def test_each_split_is_named_in_one_call_parents_first(settings, payload):
    client = Answering(
        json.dumps({"A": "Photovoltaics", "B": "Electrocatalysis"}),
        'Sure: {"A": "Halide perovskites", "B": "Organic solar cells"}',
    )
    outcome = await naming.name_groups(client, settings, "m", payload, paper)
    assert [g["name"] for g in payload["groups"]] == [
        "Photovoltaics",
        "Electrocatalysis",
        "Halide perovskites",
        "Organic solar cells",
    ]
    assert outcome.named == 4
    assert len(client.calls) == 2
    second = client.calls[1]["messages"][1]["content"]
    assert '"Photovoltaics"' in second  # the halves are named against their parent
    assert "Title of 10.1000/g2" in second
    assert "halide" in second


async def test_an_unchanged_split_keeps_its_names_without_a_call(settings, payload):
    client = Answering(
        json.dumps({"A": "Halide perovskites", "B": "Organic solar cells"})
    )
    previous = {"fp-g0": "Photovoltaics", "fp-g1": "Electrocatalysis"}
    outcome = await naming.name_groups(client, settings, "m", payload, paper, previous)
    assert outcome.kept == 2
    assert outcome.named == 2
    assert len(client.calls) == 1
    assert payload["groups"][0]["name"] == "Photovoltaics"


@pytest.mark.parametrize(
    "answer",
    [
        "not json at all",
        json.dumps({"A": "Same name", "B": "same name"}),
        json.dumps({"A": "Only one"}),
        json.dumps({"A": "x" * 80, "B": "Fine"}),
        RuntimeError("the endpoint is down"),
    ],
    ids=["prose", "duplicates", "missing", "too long", "error"],
)
async def test_an_answer_that_cannot_be_used_leaves_the_common_words(
    settings, payload, answer
):
    client = Answering(
        answer, json.dumps({"A": "Halide perovskites", "B": "Organic solar cells"})
    )
    outcome = await naming.name_groups(client, settings, "m", payload, paper)
    assert payload["groups"][0]["name"] == "perovskite / solar cell"
    assert payload["groups"][0]["named_by"] == "terms"
    assert outcome.failed == 2
    assert outcome.named == 2


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  'Halide perovskites.' ", "Halide perovskites"),
        (None, ""),
        ("a\nb", ""),
        ("", ""),
    ],
)
def test_tidy(raw, expected):
    assert naming.tidy(raw) == expected
