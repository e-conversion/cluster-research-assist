"""The only place that talks to the database. Everything above it works with
the ORM rows as plain objects and never imports SQLAlchemy."""

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cra.app.history.tables import (
    Conversation,
    Feedback,
    Identity,
    McpToken,
    Message,
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


# one local part, one ASCII domain with a dot; no comments, quotes or unicode,
# which is all an invitation could ever be matched against anyway
_EMAIL = re.compile(r"^[a-z0-9!#$%&'*+/=?^_`{|}~.-]+@[a-z0-9-]+(\.[a-z0-9-]+)+$")


def valid_email(email: str) -> bool:
    address = normalise_email(email)
    return (
        len(address) <= 320 and bool(_EMAIL.fullmatch(address)) and ".." not in address
    )


class Repository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    # users

    async def create_user(self, display_name: str, role: str = "user") -> User:
        user = User(display_name=display_name, role=role, created_at=utcnow())
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
        self,
        email: str,
        created_by: str,
        role: str = "user",
        home_organization: str = "",
    ) -> RegisteredEmail:
        row = RegisteredEmail(
            email=normalise_email(email),
            created_by=created_by,
            role=role,
            home_organization=home_organization.strip().lower(),
            created_at=utcnow(),
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

    async def set_registered_email_role(self, email: str, role: str) -> bool:
        async with self._sessions() as s, s.begin():
            result = await s.execute(
                update(RegisteredEmail)
                .where(RegisteredEmail.email == normalise_email(email))
                .values(role=role)
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

    # conversations

    async def create_conversation(self, user_id: str, title: str = "") -> Conversation:
        now = utcnow()
        row = Conversation(user_id=user_id, title=title, created_at=now, updated_at=now)
        async with self._sessions() as s, s.begin():
            s.add(row)
        return row

    async def get_conversation(self, conversation_id: str) -> Conversation | None:
        async with self._sessions() as s:
            return await s.get(Conversation, conversation_id)

    async def list_conversations(
        self, user_id: str, limit: int = 100
    ) -> list[Conversation]:
        async with self._sessions() as s:
            rows = await s.scalars(
                select(Conversation)
                .where(Conversation.user_id == user_id)
                .order_by(Conversation.updated_at.desc())
                .limit(limit)
            )
            return list(rows)

    async def rename_conversation(self, conversation_id: str, title: str) -> bool:
        async with self._sessions() as s, s.begin():
            result = await s.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(title=title[:200], updated_at=utcnow())
            )
            return result.rowcount == 1

    async def delete_conversation(self, conversation_id: str) -> bool:
        async with self._sessions() as s, s.begin():
            result = await s.execute(
                delete(Conversation).where(Conversation.id == conversation_id)
            )
            return result.rowcount == 1

    async def messages(self, conversation_id: str) -> list[Message]:
        async with self._sessions() as s:
            rows = await s.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.position)
            )
            return list(rows)

    async def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        meta: dict[str, Any] | None = None,
    ) -> Message:
        async with self._sessions() as s, s.begin():
            position = len(
                list(
                    await s.scalars(
                        select(Message.id).where(
                            Message.conversation_id == conversation_id
                        )
                    )
                )
            )
            row = Message(
                conversation_id=conversation_id,
                position=position,
                role=role,
                content=content,
                meta=meta or {},
                created_at=utcnow(),
            )
            s.add(row)
            await s.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(updated_at=utcnow())
            )
        return row

    async def purge_conversations(self, older_than_days: int) -> int:
        cutoff = utcnow() - timedelta(days=older_than_days)
        async with self._sessions() as s, s.begin():
            result = await s.execute(
                delete(Conversation).where(Conversation.updated_at < cutoff)
            )
            return int(result.rowcount)

    # feedback

    async def add_feedback(
        self,
        user_id: str,
        category: str,
        text: str,
        model: str = "",
        messages: list[Any] | None = None,
    ) -> Feedback:
        row = Feedback(
            user_id=user_id,
            created_at=utcnow(),
            category=category,
            text=text,
            model=model,
            messages=messages or [],
        )
        async with self._sessions() as s, s.begin():
            s.add(row)
        return row

    async def list_feedback(
        self, limit: int = 200
    ) -> list[tuple[Feedback, str | None]]:
        """Newest first, each with the name of whoever sent it."""
        async with self._sessions() as s:
            rows = await s.execute(
                select(Feedback, User.display_name)
                .join(User, User.id == Feedback.user_id, isouter=True)
                .order_by(Feedback.created_at.desc(), Feedback.id.desc())
                .limit(limit)
            )
            return [(row[0], row[1]) for row in rows]

    async def delete_feedback(self, feedback_id: int) -> bool:
        async with self._sessions() as s, s.begin():
            result = await s.execute(delete(Feedback).where(Feedback.id == feedback_id))
            return result.rowcount == 1

    async def count_feedback(self) -> int:
        async with self._sessions() as s:
            return len(list(await s.scalars(select(Feedback.id))))

    # MCP tokens

    async def create_token(
        self, user_id: str, label: str, token_hash: str, expires_at: datetime
    ) -> McpToken:
        row = McpToken(
            user_id=user_id,
            label=label,
            token_hash=token_hash,
            created_at=utcnow(),
            expires_at=expires_at,
        )
        async with self._sessions() as s, s.begin():
            s.add(row)
        return row

    async def get_token_by_hash(self, token_hash: str) -> tuple[McpToken, bool] | None:
        """The token and whether its owner's account is active."""
        async with self._sessions() as s:
            row = (
                await s.execute(
                    select(McpToken, User.is_active)
                    .join(User, User.id == McpToken.user_id)
                    .where(McpToken.token_hash == token_hash)
                )
            ).first()
            return (row[0], bool(row[1])) if row else None

    async def get_token(self, token_id: str) -> McpToken | None:
        async with self._sessions() as s:
            return await s.get(McpToken, token_id)

    async def list_tokens(self, user_id: str) -> list[McpToken]:
        async with self._sessions() as s:
            rows = await s.scalars(
                select(McpToken)
                .where(McpToken.user_id == user_id)
                .order_by(McpToken.created_at.desc())
            )
            return list(rows)

    async def list_all_tokens(self) -> list[tuple[McpToken, str]]:
        """Newest first, each with its owner's name."""
        async with self._sessions() as s:
            rows = await s.execute(
                select(McpToken, User.display_name)
                .join(User, User.id == McpToken.user_id)
                .order_by(McpToken.created_at.desc())
            )
            return [(row[0], row[1]) for row in rows]

    async def revoke_token(self, token_id: str, user_id: str | None = None) -> bool:
        """Ends a token; ``user_id`` restricts it to that owner's. Revoking a
        token that is already revoked succeeds and keeps the first time."""
        async with self._sessions() as s, s.begin():
            row = await s.get(McpToken, token_id)
            if row is None or (user_id is not None and row.user_id != user_id):
                return False
            if row.revoked_at is None:
                row.revoked_at = utcnow()
            return True

    async def revoke_tokens_of(self, user_id: str) -> int:
        async with self._sessions() as s, s.begin():
            result = await s.execute(
                update(McpToken)
                .where(McpToken.user_id == user_id, McpToken.revoked_at.is_(None))
                .values(revoked_at=utcnow())
            )
            return int(result.rowcount)

    async def touch_token(self, token_id: str, when: datetime) -> None:
        async with self._sessions() as s, s.begin():
            await s.execute(
                update(McpToken)
                .where(McpToken.id == token_id)
                .values(last_used_at=when)
            )

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

    async def sessions_of(self, user_id: str) -> list[WebSession]:
        async with self._sessions() as s:
            rows = await s.scalars(
                select(WebSession).where(WebSession.user_id == user_id)
            )
            return list(rows)

    async def delete_session(self, session_id: str) -> None:
        async with self._sessions() as s, s.begin():
            await s.execute(delete(WebSession).where(WebSession.id == session_id))

    async def purge_expired_sessions(self) -> int:
        async with self._sessions() as s, s.begin():
            result = await s.execute(
                delete(WebSession).where(WebSession.expires_at <= utcnow())
            )
            return int(result.rowcount)
