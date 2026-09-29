"""Connected sources that outlast the browser session.

The live connection stays per session (``RemoteHost``); what persists is the
token, per account and source, sealed with CRA_SOURCE_TOKEN_KEY. A session
that has none of an account's sources connected yet, after signing in on
another device or after a restart, gets them back on its first request.
Without a key nothing is stored and a token lasts one session.
"""

import logging
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from cra.app.auth.principal import SessionState

log = logging.getLogger(__name__)

# (session, kind) pairs already restored or tried in this process; a source
# that is down is not retried on every request of the session
_ATTEMPTS_MAX = 10_000


class Vault:
    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key) if key else None
        self._attempted: set[tuple[str, str]] = set()

    @property
    def enabled(self) -> bool:
        return self._fernet is not None

    def seal(self, token: str) -> str:
        assert self._fernet is not None
        return self._fernet.encrypt(token.encode()).decode()

    def open(self, sealed: str) -> str | None:
        """The token, or None when it was sealed under another key."""
        assert self._fernet is not None
        try:
            return self._fernet.decrypt(sealed.encode()).decode()
        except InvalidToken:
            return None

    def first_attempt(self, session_id: str, kind: str) -> bool:
        if len(self._attempted) > _ATTEMPTS_MAX:
            self._attempted.clear()
        if (session_id, kind) in self._attempted:
            return False
        self._attempted.add((session_id, kind))
        return True


async def remember(ctx: Any, user_id: str, kind: str, token: str) -> None:
    if ctx.vault.enabled:
        await ctx.repo.save_source_connection(user_id, kind, ctx.vault.seal(token))


async def restore(ctx: Any, session: SessionState | None, user_id: str | None) -> None:
    """What this session should already be connected to.

    Two kinds come back on a first request: the account's own tokens (with
    CRA_SOURCE_TOKEN_KEY set) and, for the accounts a source names as its
    shared-key holders, the deployment's key for that source. The account's own
    token wins — a source that is already live is left alone.
    """
    if session is None:
        return
    if ctx.vault.enabled and user_id is not None:
        await _restore_own(ctx, session, user_id)
    await _connect_shared(ctx, session, user_id)


async def _restore_own(ctx: Any, session: SessionState, user_id: str) -> None:
    status = ctx.remote.status(session.id)
    for row in await ctx.repo.source_connections_of(user_id):
        live = status.get(row.kind)
        if live is None or live["active"]:
            continue
        if not ctx.vault.first_attempt(session.id, row.kind):
            continue
        token = ctx.vault.open(row.sealed)
        if token is None:
            # the key changed: the stored token is unreadable for good
            log.warning(
                "stored source token unreadable; dropped",
                extra={"fields": {"user": user_id, "source": row.kind}},
            )
            await ctx.repo.delete_source_connections(user_id, row.kind)
            continue
        result = await ctx.remote.connect(session.id, row.kind, token)
        if not result["active"]:
            # kept: a source that is down today may answer tomorrow; the
            # person sees it disconnected and may connect again or remove it
            log.info(
                "stored source did not reconnect",
                extra={"fields": {"user": user_id, "source": row.kind}},
            )


async def _connect_shared(ctx: Any, session: SessionState, user_id: str | None) -> None:
    """Connect the sources this deployment holds a key for, for the accounts
    that source names. Everybody else registers their own token.

    Read-only by construction: the host withholds every tool that is not
    declared read-only, so a shared key never writes. Nobody sees the key.
    """
    if user_id is None:
        return
    status = ctx.remote.status(session.id)
    for kind, source in ctx.remote.sources.items():
        if not source.shared_token or not source.shared_token_for:
            continue
        if not await _holds_shared_key(ctx, source, user_id):
            continue
        live = status.get(kind)
        if live is None or live["active"]:
            continue
        if not ctx.vault.first_attempt(session.id, kind):
            continue
        result = await ctx.remote.connect(session.id, kind, source.shared_token)
        if not result["active"]:
            log.info(
                "shared source did not connect",
                extra={"fields": {"source": kind, "error": result.get("error")}},
            )


async def _holds_shared_key(ctx: Any, source: Any, user_id: str) -> bool:
    """Is this account one the source names?

    A name is either a user id or a local username; an account that signs in
    through an identity provider has no username here, so a deployment that
    wants to name such an account has to use its user id.
    """
    if user_id in source.shared_token_for:
        return True
    credential = await ctx.repo.get_credential(user_id)
    return credential is not None and credential.username in source.shared_token_for
