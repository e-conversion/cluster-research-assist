"""Per-paper method summaries: what is asked of the model, what is kept of its
answer, and the file they are stored in.

Nothing here calls a model. The build step (``cra.assistant.summarise``) sends
the text chosen here and hands the answer back to ``validate``, so what ends up
in ``summaries.json`` is fixed in one place: a closed set for the kind of work,
a cap on every field, and an input hash that says whether a paper needs asking
again.

The file keeps a record for every paper that has one worth keeping: the summary,
or ``no_text`` for a paper with neither an abstract nor a usable full text, so
that the paper is listed and a rebuild does not look for it again. A paper the
model failed on has no record and is tried again on the next run.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cra.core.library.records import Summaries, Summary, normalise_doi

# Raised whenever the question or the fields change, so old answers are asked again.
PROMPT_VERSION = 1

APPROACHES = ("experimental", "computational", "theoretical", "mixed", "review")
_APPROACH_WORDS = {
    "experiment": "experimental",
    "experiments": "experimental",
    "simulation": "computational",
    "simulations": "computational",
    "computation": "computational",
    "theory": "theoretical",
    "both": "mixed",
    "survey": "review",
}

# A full text shorter than this is a scan, a landing page or a stub, not a paper.
MIN_FULLTEXT_CHARS = 3000
# About 25k tokens: the whole of nine papers in ten, and a window every model
# the cluster uses has room for.
MAX_TEXT_CHARS = 100_000
HEAD_CHARS = 30_000

# Caps per field, in characters. Longer is cut at a word; the file stays small
# because of them (about 600 characters a record, 1,200 at the very most).
LIMITS = {
    "summary": 200,
    "material_system": 60,
    "processing_method": 100,
    "results": 220,
    "novelty": 120,
    "limitations": 120,
}
LIST_FIELDS = ("characterization_methods", "properties_investigated")
LIST_ITEMS, LIST_ITEM_CHARS = 6, 32

_METHODS_HEADING = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]*|\*\*)?(?:\d+(?:\.\d+)*\.?[ \t]*)?"
    r"(?:methods?|materials and methods|methodology|experimental"
    r"(?: (?:section|details|methods|procedures?))?|computational (?:methods|details))"
    r"\b",
    re.IGNORECASE | re.MULTILINE,
)
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_OBJECT = re.compile(r"\{.*\}", re.DOTALL)
_SPLIT = re.compile(r"[;,\n]")


def clip(text: str) -> str:
    """The text a model is shown: all of it up to the limit, else the opening
    and the methods section, which is what a method summary is made from."""
    if len(text) <= MAX_TEXT_CHARS:
        return text
    head = text[:HEAD_CHARS]
    found = _METHODS_HEADING.search(text, HEAD_CHARS)
    if found is None:
        return text[:MAX_TEXT_CHARS]
    room = MAX_TEXT_CHARS - HEAD_CHARS
    return head + "\n\n[...]\n\n" + text[found.start() : found.start() + room]


def select_text(abstract: str, fulltext: str) -> tuple[str, str]:
    """``(source, text)`` for a paper, or ``("", "")`` when there is nothing to read."""
    body = fulltext.strip()
    if len(body) >= MIN_FULLTEXT_CHARS:
        return "fulltext", clip(body)
    if abstract.strip():
        return "abstract", abstract.strip()
    return "", ""


def input_hash(title: str, source: str, text: str) -> str:
    """Names what a record was made from: the same text, title and question."""
    if not source:
        return ""
    material = f"{PROMPT_VERSION}\n{source}\n{title}\n{text}"
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def cut(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:.") + "…"


def parse(raw: str) -> dict[str, Any] | None:
    """The JSON object in a model's answer, whatever it wrote around it."""
    match = _OBJECT.search(_THINK.sub("", raw or ""))
    try:
        answer = json.loads(match.group(0)) if match else None
    except ValueError:
        return None
    return answer if isinstance(answer, dict) else None


def _items(value: Any) -> list[str]:
    if isinstance(value, str):
        value = _SPLIT.split(value)
    if not isinstance(value, list):
        return []
    seen: dict[str, str] = {}
    for item in value:
        text = cut(str(item), LIST_ITEM_CHARS) if item is not None else ""
        if text:
            seen.setdefault(text.casefold(), text)
    return list(seen.values())[:LIST_ITEMS]


def approach_of(value: Any) -> str:
    word = str(value or "").strip().casefold()
    word = _APPROACH_WORDS.get(word, word)
    return word if word in APPROACHES else ""


def validate(answer: dict[str, Any]) -> dict[str, Any] | None:
    """The fields to keep, or None when the answer cannot serve as a summary.

    A summary and a known kind of work are required; everything else may be
    empty, which is what an abstract-only paper often has to say about its
    characterisation.
    """
    approach = approach_of(answer.get("approach"))
    summary = cut(str(answer.get("summary") or ""), LIMITS["summary"])
    if not summary or not approach:
        return None
    fields: dict[str, Any] = {"summary": summary, "approach": approach}
    for key, limit in LIMITS.items():
        if key != "summary":
            fields[key] = cut(str(answer.get(key) or ""), limit)
    for key in LIST_FIELDS:
        fields[key] = _items(answer.get(key))
    return fields


@dataclass(frozen=True)
class Job:
    doi: str
    title: str
    source: str
    text: str
    digest: str


@dataclass
class Plan:
    jobs: list[Job]
    no_text: list[str]  # papers with nothing to read, to be recorded as such
    unchanged: int  # papers whose record is already current


def plan(
    papers: dict[str, Any],
    fulltexts: dict[str, Any],
    records: dict[str, dict[str, Any]],
    *,
    force: bool = False,
    only: set[str] | None = None,
) -> Plan:
    """What a run has to do: ask for papers new or changed since their record."""
    result = Plan(jobs=[], no_text=[], unchanged=0)
    for doi, paper in sorted(papers.items()):
        if only is not None and doi not in only:
            continue
        full = fulltexts.get(doi)
        source, text = select_text(paper.abstract, full.text if full else "")
        digest = input_hash(paper.title, source, text)
        known = records.get(doi)
        current = known is not None and (
            known.get("input_hash") == digest
            and known.get("status") == ("ok" if source else "no_text")
        )
        if current and not force:
            result.unchanged += 1
        elif not source:
            result.no_text.append(doi)
        else:
            result.jobs.append(Job(doi, paper.title, source, text, digest))
    return result


def record(fields: dict[str, Any], job: Job, model: str) -> dict[str, Any]:
    return {
        "status": "ok",
        "model": model,
        "source": job.source,
        "input_hash": job.digest,
        **fields,
    }


def no_text_record() -> dict[str, Any]:
    return {"status": "no_text", "input_hash": ""}


def read(path: Path) -> dict[str, Any]:
    """The file as written, or an empty one; the build keeps what it holds."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"records": {}}
    return (
        raw
        if isinstance(raw, dict) and isinstance(raw.get("records"), dict)
        else {"records": {}}
    )


def write(path: Path, records: dict[str, dict[str, Any]], model: str) -> dict[str, Any]:
    """Replace the file in one step, so a run stopped halfway leaves a whole file."""
    header = {
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    document = {**header, "records": dict(sorted(records.items()))}
    temporary = path.with_name(path.name + ".part")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)
    return header


def load(raw: Any) -> Summaries:
    """The summaries in a parsed ``summaries.json``; TypeError or ValueError when malformed."""
    if not isinstance(raw, dict) or not isinstance(raw.get("records"), dict):
        raise TypeError("an object with a 'records' object expected")
    records = {}
    for doi, entry in raw["records"].items():
        if not isinstance(entry, dict):
            raise TypeError(f"the record for {doi} is not an object")
        if entry.get("status") != "ok":
            continue
        try:
            records[normalise_doi(doi)] = Summary(
                doi=normalise_doi(doi),
                summary=str(entry["summary"]),
                approach=str(entry["approach"]),
                material_system=str(entry.get("material_system") or ""),
                processing_method=str(entry.get("processing_method") or ""),
                characterization_methods=tuple(
                    str(m) for m in entry.get("characterization_methods") or ()
                ),
                properties_investigated=tuple(
                    str(m) for m in entry.get("properties_investigated") or ()
                ),
                results=str(entry.get("results") or ""),
                novelty=str(entry.get("novelty") or ""),
                limitations=str(entry.get("limitations") or ""),
                source=str(entry.get("source") or ""),
                model=str(entry.get("model") or raw.get("model") or ""),
            )
        except KeyError as exc:
            raise ValueError(f"the record for {doi} lacks {exc.args[0]!r}") from exc
    return Summaries(
        records=records,
        model=str(raw.get("model") or ""),
        prompt_version=int(raw.get("prompt_version") or 0),
        built_at=str(raw.get("built_at") or ""),
    )
