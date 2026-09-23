"""Browser sessions: a random cookie, its hash as the database key.

A session ends when it goes unused for ``max_age``, and in any case
``absolute_max_age`` after it was created: a cookie that is being used is
still a cookie that may have been stolen.
"""

import hashlib
import secrets
from datetime import datetime, timedelta

from cra.app.auth.principal import SessionState
from cra.app.history.repository import Repository, utcnow

COOKIE_NAME = "cra_session"
# last_seen_at is written at most this often to keep reads cheap
TOUCH_INTERVAL = timedelta(minutes=5)
# what a login keeps from the session it replaces: the user's own choices,
# never anything that points at data (the conversation belongs to whoever
# was signed in before)
KEPT_ON_LOGIN = ("model", "params")


def hash_cookie(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class SessionStore:
    def __init__(
        self,
        repo: Repository,
        max_age: timedelta,
        absolute_max_age: timedelta | None = None,
    ) -> None:
        self._repo = repo
        self.max_age = max_age
        self.absolute_max_age = absolute_max_age or timedelta(days=7)

    def _expiry(self, created_at: datetime) -> datetime:
        return min(utcnow() + self.max_age, created_at + self.absolute_max_age)

    async def load(self, cookie: str | None) -> SessionState | None:
        if not cookie:
            return None
        row = await self._repo.get_session(hash_cookie(cookie))
        if row is None:
            return None
        if utcnow() >= row.created_at + self.absolute_max_age:
            await self._repo.delete_session(row.id)
            return None
        if utcnow() - row.last_seen_at > TOUCH_INTERVAL:
            await self._repo.update_session(
                row.id, expires_at=self._expiry(row.created_at)
            )
        return SessionState(id=row.id, user_id=row.user_id, data=dict(row.data))

    async def create(self, user_id: str | None = None) -> tuple[str, SessionState]:
        cookie = secrets.token_urlsafe(32)
        row = await self._repo.create_session(
            hash_cookie(cookie), self._expiry(utcnow()), user_id
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
        fresh.data = {k: v for k, v in state.data.items() if k in KEPT_ON_LOGIN}
        await self.save(fresh)
        return cookie, fresh

    async def delete(self, state: SessionState) -> None:
        await self._repo.delete_session(state.id)
