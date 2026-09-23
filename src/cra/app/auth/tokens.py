"""Bearer tokens for the outward MCP endpoint.

A token is self-contained and signed, so the endpoint verifies one with the
deployment secret alone: no table, no lookup on the hot path. The trade is that
a single token cannot be revoked on its own -- rotating ``CRA_MCP_TOKEN_SECRET``
revokes all of them at once, which is the right blunt instrument for a surface
that only ever serves public data.
"""

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

VERSION = "cra1"
DEFAULT_DAYS = 365
DAY_S = 24 * 60 * 60


@dataclass(frozen=True)
class Claims:
    subject: str
    issued_at: int
    expires_at: int


def issue(
    secret: str, subject: str, *, days: int = DEFAULT_DAYS, now: float | None = None
) -> str:
    """A token for ``subject``, valid for ``days``."""
    subject = subject.strip()
    if not secret:
        raise ValueError("no token secret is configured")
    if not subject:
        raise ValueError("a token needs a subject")
    if days < 1:
        raise ValueError("a token must be valid for at least one day")
    issued = int(now if now is not None else time.time())
    payload = _encode({"sub": subject, "iat": issued, "exp": issued + days * DAY_S})
    return f"{VERSION}.{payload}.{_sign(secret, payload)}"


def verify(secret: str, token: str, *, now: float | None = None) -> Claims | None:
    """The claims, or None for anything that is not a live, signed token."""
    if not secret or not token:
        return None
    version, _, rest = token.partition(".")
    payload, _, signature = rest.partition(".")
    if version != VERSION or not payload or not signature:
        return None
    if not hmac.compare_digest(signature, _sign(secret, payload)):
        return None
    try:
        claims = json.loads(_decode(payload))
        expires = int(claims["exp"])
        subject = str(claims["sub"])
        issued = int(claims["iat"])
    except (ValueError, KeyError, TypeError):
        return None
    if expires <= (now if now is not None else time.time()):
        return None
    return Claims(subject=subject, issued_at=issued, expires_at=expires)


def bearer(header: str) -> str:
    """The token out of an Authorization header, or an empty string."""
    scheme, _, value = header.strip().partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


def _sign(secret: str, payload: str) -> str:
    digest = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _encode(claims: dict[str, object]) -> str:
    raw = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(payload: str) -> bytes:
    return base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
