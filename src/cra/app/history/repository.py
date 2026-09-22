"""The only place that talks to the database. Everything above it works with
the ORM rows as plain objects and never imports SQLAlchemy."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cra.app.history.tables import (
    Identity,
    PolicySetting,
    RegisteredEmail,
    User,
    WebSession,
)


def utcnow() -> datetime:
    """Naive UTC: SQLite has no timezone type, so every timestamp is stored naive."""
    return datetime.now(UTC).replace(tzinfo=None)


def normalise_email(email: str) -> str:
    return email.strip().lower()


class Repository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    # users

    async def create_user(self, display_name: str) -> User:
        user = User(display_name=display_name, created_at=utcnow())
        async with self._sessions() as s, s.begin():
            s.add(user)
        return user

    async def get_user(self, user_id: str) -> User | None:
        async with self._sessions() as s:
            return await s.get(User, user_id)

    async def list_users(self) -> list[User]:
        async with self._sessions() as s:
            rows = await s.scalars(select(User).order_by(User.created_at))
            return list(rows)

    async def set_user_role(self, user_id: str, role: str) -> bool:
        async with self._sessions() as s, s.begin():
            result = await s.execute(
                update(User).where(User.id == user_id).values(role=role)
            )
            return result.rowcount == 1

    async def delete_user(self, user_id: str) -> bool:
        """Removes the account and, by cascade, its identities and sessions.
        The address stays on the allow-list, unlinked, so a fresh sign-in
        creates a new account."""
        async with self._sessions() as s, s.begin():
            await s.execute(
                update(RegisteredEmail)
                .where(RegisteredEmail.user_id == user_id)
                .values(user_id=None)
            )
            result = await s.execute(delete(User).where(User.id == user_id))
            return result.rowcount == 1

    async def set_user_active(self, user_id: str, active: bool) -> bool:
        async with self._sessions() as s, s.begin():
            result = await s.execute(
                update(User).where(User.id == user_id).values(is_active=active)
            )
            return result.rowcount == 1

    async def touch_login(self, user_id: str) -> None:
        async with self._sessions() as s, s.begin():
            await s.execute(
                update(User).where(User.id == user_id).values(last_login_at=utcnow())
            )

    # registered emails (the allow-list)

    async def add_registered_email(
        self, email: str, created_by: str
    ) -> RegisteredEmail:
        row = RegisteredEmail(
            email=normalise_email(email), created_by=created_by, created_at=utcnow()
        )
        async with self._sessions() as s, s.begin():
            s.add(row)
        return row

    async def get_registered_email(self, email: str) -> RegisteredEmail | None:
        async with self._sessions() as s:
            return await s.get(RegisteredEmail, normalise_email(email))

    async def list_registered_emails(self) -> list[RegisteredEmail]:
        async with self._sessions() as s:
            rows = await s.scalars(
                select(RegisteredEmail).order_by(RegisteredEmail.email)
            )
            return list(rows)

    async def remove_registered_email(self, email: str) -> bool:
        async with self._sessions() as s, s.begin():
            result = await s.execute(
                delete(RegisteredEmail).where(
                    RegisteredEmail.email == normalise_email(email)
                )
            )
            return result.rowcount == 1

    async def link_registered_email(self, email: str, user_id: str) -> None:
        async with self._sessions() as s, s.begin():
            await s.execute(
                update(RegisteredEmail)
                .where(RegisteredEmail.email == normalise_email(email))
                .values(user_id=user_id)
            )

    # identities

    async def get_identity(self, issuer: str, sub: str) -> Identity | None:
        async with self._sessions() as s:
            return await s.scalar(
                select(Identity).where(Identity.issuer == issuer, Identity.sub == sub)
            )

    async def add_identity(
        self, issuer: str, sub: str, user_id: str, home_organization: str = ""
    ) -> Identity:
        row = Identity(
            issuer=issuer,
            sub=sub,
            user_id=user_id,
            home_organization=home_organization,
            bound_at=utcnow(),
        )
        async with self._sessions() as s, s.begin():
            s.add(row)
        return row

    async def list_identities(self, user_id: str) -> list[Identity]:
        async with self._sessions() as s:
            rows = await s.scalars(
                select(Identity)
                .where(Identity.user_id == user_id)
                .order_by(Identity.id)
            )
            return list(rows)

    # policy overrides

    async def get_policy(self) -> dict[str, Any]:
        async with self._sessions() as s:
            rows = await s.scalars(select(PolicySetting))
            return {row.key: row.value["value"] for row in rows}

    async def policy_rows(self) -> list[PolicySetting]:
        async with self._sessions() as s:
            rows = await s.scalars(select(PolicySetting).order_by(PolicySetting.key))
            return list(rows)

    async def set_policy(self, key: str, value: Any, updated_by: str) -> None:
        async with self._sessions() as s, s.begin():
            await s.merge(
                PolicySetting(
                    key=key,
                    # JSON columns need an object at the top level
                    value={"value": value},
                    updated_at=utcnow(),
                    updated_by=updated_by,
                )
            )

    async def clear_policy(self, key: str) -> bool:
        async with self._sessions() as s, s.begin():
            result = await s.execute(
                delete(PolicySetting).where(PolicySetting.key == key)
            )
            return result.rowcount == 1

    # web sessions

    async def create_session(
        self, session_id: str, expires_at: datetime, user_id: str | None = None
    ) -> WebSession:
        now = utcnow()
        row = WebSession(
            id=session_id,
            user_id=user_id,
            created_at=now,
            last_seen_at=now,
            expires_at=expires_at,
            data={},
        )
        async with self._sessions() as s, s.begin():
            s.add(row)
        return row

    async def get_session(self, session_id: str) -> WebSession | None:
        """The row, or None when missing or expired (expired rows are dropped)."""
        async with self._sessions() as s, s.begin():
            row = await s.get(WebSession, session_id)
            if row is None:
                return None
            if row.expires_at <= utcnow():
                await s.delete(row)
                return None
            return row

    async def update_session(self, session_id: str, **values: Any) -> None:
        values["last_seen_at"] = utcnow()
        async with self._sessions() as s, s.begin():
            await s.execute(
                update(WebSession).where(WebSession.id == session_id).values(**values)
            )

    async def delete_session(self, session_id: str) -> None:
        async with self._sessions() as s, s.begin():
            await s.execute(delete(WebSession).where(WebSession.id == session_id))

    async def purge_expired_sessions(self) -> int:
        async with self._sessions() as s, s.begin():
            result = await s.execute(
                delete(WebSession).where(WebSession.expires_at <= utcnow())
            )
            return int(result.rowcount)
