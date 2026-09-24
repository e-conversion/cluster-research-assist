"""Bearer tokens for the outward MCP endpoint.

A token belongs to one account and is stored as the sha256 of its value, so
each one can be listed, revoked and seen to be in use on its own. Verifying
one is a single indexed read; ``last_used_at`` is written at most once per
``TOUCH_INTERVAL`` so that a busy client does not turn every call into a write.
"""

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Any

from cra.app.history.repository import Repository, utcnow
from cra.app.history.tables import McpToken

PREFIX = "cra1_"
DEFAULT_DAYS = 90
MAX_DAYS = 365
# what the web interface offers; the CLI takes any number of days up to MAX_DAYS
EXPIRY_CHOICES = (30, 90, 365)
LABEL_MAX = 100
TOUCH_INTERVAL = timedelta(minutes=1)


def hash_value(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def mint(
    repo: Repository, user_id: str, label: str, days: int = DEFAULT_DAYS
) -> tuple[McpToken, str]:
    """A new token for ``user_id``, and its value, which is never seen again."""
    label = label.strip()
    if not label:
        raise ValueError("a token needs a label")
    if len(label) > LABEL_MAX:
        raise ValueError(f"a label is at most {LABEL_MAX} characters")
    if not 1 <= days <= MAX_DAYS:
        raise ValueError(f"a token is valid for 1 to {MAX_DAYS} days")
    value = PREFIX + secrets.token_urlsafe(32)
    row = await repo.create_token(
        user_id, label, hash_value(value), utcnow() + timedelta(days=days)
    )
    return row, value


async def verify(
    repo: Repository, value: str, *, now: datetime | None = None
) -> McpToken | None:
    """The token, or None for anything that is not live: unknown, revoked,
    expired, or owned by an account that has been deactivated."""
    if not value.startswith(PREFIX):
        return None
    found = await repo.get_token_by_hash(hash_value(value))
    if found is None:
        return None
    row, owner_active = found
    now = now or utcnow()
    if row.revoked_at is not None or row.expires_at <= now or not owner_active:
        return None
    if row.last_used_at is None or now - row.last_used_at >= TOUCH_INTERVAL:
        await repo.touch_token(row.id, now)
    return row


def state(row: McpToken, now: datetime | None = None) -> str:
    if row.revoked_at is not None:
        return "revoked"
    return "expired" if row.expires_at <= (now or utcnow()) else "active"


def describe(row: McpToken) -> dict[str, Any]:
    """What a listing shows about a token: everything but the value."""

    def iso(when: datetime | None) -> str | None:
        return when.isoformat() if when else None

    return {
        "id": row.id,
        "label": row.label,
        "created_at": iso(row.created_at),
        "expires_at": iso(row.expires_at),
        "last_used_at": iso(row.last_used_at),
        "revoked_at": iso(row.revoked_at),
        "state": state(row),
    }


def bearer(header: str) -> str:
    """The token out of an Authorization header, or an empty string."""
    scheme, _, value = header.strip().partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""
