"""Naming a conversation."""

import pytest
from conftest import make_settings

from cra.assistant.chat import title as title_


class Answering:
    """A client that returns one non-streamed completion."""

    def __init__(self, content):
        from types import SimpleNamespace

        self.content = content
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        from types import SimpleNamespace

        self.calls.append(kwargs)
        if isinstance(self.content, Exception):
            raise self.content
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path, llm_api_key="a-key")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Perovskite stability", "Perovskite stability"),
        ('  "Battery ageing models."  ', "Battery ageing models"),
        (
            "Collaborators for battery testing\nAnother line",
            "Collaborators for battery testing",
        ),
        ("x" * 200, "x" * title_.MAX_CHARS),
        ("   ", ""),
    ],
)
def test_a_model_answer_becomes_a_title(raw, expected):
    assert title_.tidy(raw) == expected


async def test_the_model_is_asked_briefly(settings, monkeypatch):
    client = Answering("Perovskite stability under light")
    monkeypatch.setattr(title_, "make_client", lambda _settings: client)
    suggested = await title_.suggest(settings, "m", "which papers?", "These two.")
    assert suggested == "Perovskite stability under light"
    sent = client.calls[0]
    assert sent["max_tokens"] == title_.MAX_TOKENS
    assert "tools" not in sent, "naming a conversation needs no tools"
    assert "which papers?" in sent["messages"][1]["content"]


async def test_a_failure_leaves_the_fallback(settings, monkeypatch):
    monkeypatch.setattr(
        title_, "make_client", lambda _s: Answering(RuntimeError("down"))
    )
    assert await title_.suggest(settings, "m", "q", "a") == ""


async def test_without_an_api_key_nothing_is_asked(tmp_path):
    assert await title_.suggest(make_settings(tmp_path), "m", "q", "a") == ""


def test_the_fallback_is_the_question_itself():
    assert (
        title_.fallback("  Which papers cover perovskites?  ")
        == "Which papers cover perovskites?"
    )
    assert len(title_.fallback("x" * 500)) == title_.FALLBACK_CHARS
