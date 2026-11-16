"""Names for the publication map's groups, written by the model at build time.

A group's word list says what its papers share, not which field they are in: a
reader wants "Halide perovskite solar cells", not "perovskite / solar cell /
halide". The model reads the papers nearest each group's centre and names the
group once, when the library is built, so serving never calls it.

The groups come from one tree, and each split is named in one call: the model
sees the halves side by side and the name of the group they split from, so
their names say how they differ. A split the model will not name keeps its word
lists, and a split whose halves hold the same papers as in the last build keeps
the names it had, without a call.
"""

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from string import ascii_uppercase
from typing import Any

from cra.assistant.llm.client import ChatClient
from cra.assistant.llm.params import is_openrouter
from cra.config.settings import Settings

log = logging.getLogger(__name__)

INSTRUCTION = (
    "You name groups of research papers for the legend of a map of one research "
    "cluster's publications. Each name is 2 to 5 words in sentence case and names "
    "a research area, the way a researcher in the field would say it. Name what "
    "most of a group's papers share; name one compound or material only if nearly "
    "every paper in the group is about it. When several groups are given, their "
    "names must tell them apart. Answer with JSON only, one name per group letter, "
    'for example {"A": "Halide perovskite solar cells", "B": "Organic photovoltaics"}.'
)
# Room for a reasoning model to think before it answers (see chat/title.py).
MAX_TOKENS = 1024
TIMEOUT_S = 60.0
MAX_CHARS = 48
ABSTRACT_CHARS = 300
_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class Outcome:
    named: int = 0  # groups the model named in this build
    kept: int = 0  # groups that kept the name of an unchanged group
    failed: int = 0  # groups left with their word lists


def _group(
    letter: str, group: dict[str, Any], paper: Callable[[str], tuple[str, str]]
) -> str:
    lines = [f"Group {letter}, {group['size']} papers. Those nearest its centre:"]
    for doi in group["central"]:
        title, abstract = paper(doi)
        snippet = abstract[:ABSTRACT_CHARS].rsplit(" ", 1)[0] if abstract else ""
        lines.append(f"- {title}" + (f" — {snippet}…" if snippet else ""))
    if group["terms"]:
        lines.append(
            "Words common in it and rare elsewhere: " + ", ".join(group["terms"])
        )
    return "\n".join(lines)


def tidy(name: Any) -> str:
    """A usable name, or the empty string."""
    if not isinstance(name, str):
        return ""
    name = name.strip().strip("\"'").rstrip(".").strip()
    return name if 0 < len(name) <= MAX_CHARS and "\n" not in name else ""


def parse(raw: str, letters: list[str]) -> dict[str, str]:
    """The names in the model's answer, by letter; all of them or none."""
    match = _OBJECT.search(raw or "")
    try:
        answer = json.loads(match.group(0)) if match else {}
    except ValueError:
        return {}
    if not isinstance(answer, dict):
        return {}
    names = {letter: tidy(answer.get(letter)) for letter in letters}
    distinct = len({n.casefold() for n in names.values()}) == len(letters)
    return names if all(names.values()) and distinct else {}


async def name_groups(
    client: ChatClient,
    settings: Settings,
    model: str,
    payload: dict[str, Any],
    paper: Callable[[str], tuple[str, str]],
    previous: dict[str, str] | None = None,
) -> Outcome:
    """Name every group in ``payload`` in place, parents before their halves.

    ``paper`` gives a DOI's title and abstract; ``previous`` maps the
    fingerprint of a group named in an earlier build to its name.
    """
    previous = previous or {}
    outcome = Outcome()
    by_id = {g["id"]: g for g in payload["groups"]}
    splits: dict[str | None, list[dict[str, Any]]] = {}
    for group in payload["groups"]:  # built parents first, so this order holds
        splits.setdefault(group["parent"], []).append(group)
    for parent, halves in splits.items():
        if all(h["fingerprint"] in previous for h in halves):
            for h in halves:
                h["name"], h["named_by"] = previous[h["fingerprint"]], "model"
            outcome.kept += len(halves)
            continue
        letters = list(ascii_uppercase[: len(halves)])
        context = (
            f'These groups split from the group named "{by_id[parent]["name"]}".'
            if parent
            else "These groups split the whole library."
        )
        content = "\n\n".join(
            [context]
            + [
                _group(letter, h, paper)
                for letter, h in zip(letters, halves, strict=True)
            ]
        )
        request: dict[str, Any] = {
            "model": model,
            "max_tokens": MAX_TOKENS,
            "messages": [
                {"role": "system", "content": INSTRUCTION},
                {"role": "user", "content": content},
            ],
        }
        if is_openrouter(settings):
            request["reasoning"] = {"effort": "minimal"}
        try:
            response = await client.complete(request, timeout_s=TIMEOUT_S)
            choices = response.get("choices") or [{}]
            names = parse(
                (choices[0].get("message") or {}).get("content") or "", letters
            )
        except Exception:
            log.warning("no group names from the model", exc_info=True)
            names = {}
        if not names:
            outcome.failed += len(halves)
            continue
        for letter, h in zip(letters, halves, strict=True):
            h["name"], h["named_by"] = names[letter], "model"
        outcome.named += len(halves)
    return outcome
