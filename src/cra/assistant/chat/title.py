"""A short name for a conversation.

The first question truncated is serviceable but reads badly in a list, so the
model is asked for a name once the first answer exists. It is a small, bounded
call with no tools, and a failure simply leaves the fallback in place: nobody
should lose an answer over a title.
"""

import logging

from cra.assistant.llm.client import make_client
from cra.assistant.llm.params import is_openrouter
from cra.config.settings import Settings

log = logging.getLogger(__name__)

INSTRUCTION = (
    "Name this exchange in at most six words, as a title in the language of the question. "
    "Name the subject, not the act of asking. No quotation marks, no final full stop."
)
# A reasoning model thinks before it answers, and a budget that only covers the
# thinking returns nothing at all: measured, 32 tokens gave an empty answer and
# 512 a title. Asking for the least thinking took it from 14 seconds to under
# one, where the gateway allows that to be asked.
MAX_TOKENS = 512
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
    extra_body = (
        {"reasoning": {"effort": "minimal"}} if is_openrouter(settings) else None
    )
    try:
        response = await client.chat.completions.create(
            model=model,
            max_tokens=MAX_TOKENS,
            timeout=TIMEOUT_S,
            extra_body=extra_body,
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
