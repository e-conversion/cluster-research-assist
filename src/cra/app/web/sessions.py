"""Browser sessions: a random cookie, its hash as the database key."""

import hashlib
import secrets
from datetime import datetime, timedelta

from cra.app.auth.principal import SessionState
from cra.app.history.repository import Repository, utcnow

COOKIE_NAME = "cra_session"
# last_seen_at is written at most this often to keep reads cheap
TOUCH_INTERVAL = timedelta(minutes=5)


def hash_cookie(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class SessionStore:
    def __init__(self, repo: Repository, max_age: timedelta) -> None:
        self._repo = repo
        self.max_age = max_age

    def _expiry(self) -> datetime:
        return utcnow() + self.max_age

    async def load(self, cookie: str | None) -> SessionState | None:
        if not cookie:
            return None
        row = await self._repo.get_session(hash_cookie(cookie))
        if row is None:
            return None
        if utcnow() - row.last_seen_at > TOUCH_INTERVAL:
            await self._repo.update_session(row.id, expires_at=self._expiry())
        return SessionState(id=row.id, user_id=row.user_id, data=dict(row.data))

    async def create(self, user_id: str | None = None) -> tuple[str, SessionState]:
        cookie = secrets.token_urlsafe(32)
        row = await self._repo.create_session(
            hash_cookie(cookie), self._expiry(), user_id
        )
        return cookie, SessionState(id=row.id, user_id=row.user_id, data={})

    async def save(self, state: SessionState) -> None:
        await self._repo.update_session(
            state.id, user_id=state.user_id, data=state.data
        )

    async def rotate(
        self, state: SessionState, user_id: str | None
    ) -> tuple[str, SessionState]:
        """A fresh cookie for the same browser, as required after login."""
        await self._repo.delete_session(state.id)
        cookie, fresh = await self.create(user_id)
        fresh.data = dict(state.data)
        await self.save(fresh)
        return cookie, fresh

    async def delete(self, state: SessionState) -> None:
        await self._repo.delete_session(state.id)
