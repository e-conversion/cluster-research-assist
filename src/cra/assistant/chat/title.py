"""A short name for a conversation.

The first question truncated is serviceable but reads badly in a list, so the
model is asked for a name once the first answer exists. It is a small, bounded
call with no tools, and a failure simply leaves the fallback in place: nobody
should lose an answer over a title.
"""

import logging

from cra.assistant.llm.client import make_client
from cra.config.settings import Settings

log = logging.getLogger(__name__)

INSTRUCTION = (
    "Name this exchange in at most six words, as a title in the language of the question. "
    "Name the subject, not the act of asking. No quotation marks, no final full stop."
)
MAX_TOKENS = 32
TIMEOUT_S = 20.0
MAX_CHARS = 80
FALLBACK_CHARS = 120


def fallback(question: str) -> str:
    return question.strip()[:FALLBACK_CHARS]


def tidy(raw: str) -> str:
    """Models like to answer with a sentence; keep the first line of it."""
    title = raw.strip().splitlines()[0] if raw.strip() else ""
    title = title.strip().strip("\"'").rstrip(".").strip()
    return title[:MAX_CHARS]


async def suggest(settings: Settings, model: str, question: str, answer: str) -> str:
    """A title, or the empty string if the model would not give one."""
    if not settings.llm_api_key.get_secret_value():
        return ""
    client = make_client(settings)
    try:
        response = await client.chat.completions.create(
            model=model,
            max_tokens=MAX_TOKENS,
            timeout=TIMEOUT_S,
            messages=[
                {"role": "system", "content": INSTRUCTION},
                {
                    "role": "user",
                    "content": f"Question: {question[:1000]}\n\nAnswer: {answer[:1000]}",
                },
            ],
        )
    except Exception:
        log.warning("no title from the model", exc_info=True)
        return ""
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""
    return tidy(getattr(choices[0].message, "content", "") or "")
