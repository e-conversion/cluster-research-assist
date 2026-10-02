"""Feedback as a file, for reading it outside the console after an event.

Each row names the account by its username as well as its display name: a
workshop's accounts are named after the attendees' addresses, and the
analysis needs to tell two people with the same name apart.
"""

import csv
import io
import json
from datetime import datetime
from typing import Any

from cra.app.history.repository import Repository

CSV_COLUMNS = (
    "id",
    "created_at",
    "username",
    "name",
    "category",
    "model",
    "text",
    "question",
    "answer",
    "conversation",
)


async def records(repo: Repository, since: datetime | None = None) -> list[dict]:
    """Oldest first, every one of them."""
    usernames = await repo.usernames()
    rows = await repo.list_feedback(limit=None, since=since)
    return [
        {
            "id": row.id,
            "created_at": row.created_at.isoformat(),
            "username": usernames.get(row.user_id, ""),
            "name": name or "a deleted account",
            "category": row.category,
            "model": row.model,
            "text": row.text,
            "messages": row.messages,
        }
        for row, name in reversed(rows)
    ]


def _last(messages: list[Any], role: str) -> str:
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == role:
            return str(message.get("content", ""))
    return ""


def to_csv(entries: list[dict]) -> str:
    """One row per note; the question and answer it was sent about get
    columns of their own, the whole conversation stays as JSON."""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for entry in entries:
        messages = entry["messages"]
        writer.writerow(
            {
                **{k: entry[k] for k in CSV_COLUMNS[:7]},
                "question": _last(messages, "user"),
                "answer": _last(messages, "assistant"),
                "conversation": json.dumps(messages, ensure_ascii=False),
            }
        )
    return out.getvalue()


def to_json(entries: list[dict]) -> str:
    return json.dumps(entries, ensure_ascii=False, indent=1)
