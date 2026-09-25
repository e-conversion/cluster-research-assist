"""The only place that talks to the database. Everything above it works with
the ORM rows as plain objects and never imports SQLAlchemy."""

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cra.app.history.tables import (
    AccessRequest,
    Conversation,
    Feedback,
    Identity,
    LocalCredential,
    McpToken,
    Message,
    PasswordToken,
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

    async def delete_user(self, user_id: str, *, keep_invitation: bool = True) -> bool:
        """Removes the account and, by cascade, its identities, sessions,
        conversations, feedback and tokens.

        With ``keep_invitation`` the address stays on the allow-list, unlinked,
        so a fresh sign-in creates a new account; without it the address goes
        too, which is what deleting one's own account promises.
        """
        owned = RegisteredEmail.user_id == user_id
        async with self._sessions() as s, s.begin():
            if keep_invitation:
                await s.execute(
                    update(RegisteredEmail).where(owned).values(user_id=None)
                )
            else:
                await s.execute(delete(RegisteredEmail).where(owned))
            result = await s.execute(delete(User).where(User.id == user_id))
            return result.rowcount == 1

    async def count_active_admins(self) -> int:
        async with self._sessions() as s:
            rows = await s.scalars(
                select(User.id).where(User.role == "admin", User.is_active)
            )
            return len(list(rows))

    async def set_user_active(self, user_id: str, active: bool) -> bool:
        async with self._sessions() as s, s.begin():
            result = await s.execute(
                update(User).where(User.id == user_id).values(is_active=active)
            )
            return result.rowcount == 1

    async def set_user_email(self, user_id: str, email: str) -> bool:
        async with self._sessions() as s, s.begin():
            result = await s.execute(
                update(User)
                .where(User.id == user_id)
                .values(email=normalise_email(email))
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

    async def emails_of(self, user_id: str) -> list[str]:
        async with self._sessions() as s:
            rows = await s.scalars(
                select(RegisteredEmail.email)
                .where(RegisteredEmail.user_id == user_id)
                .order_by(RegisteredEmail.email)
            )
            return list(rows)

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

    # password accounts

    async def create_local_user(
        self, username: str, display_name: str, email: str = "", role: str = "user"
    ) -> User:
        user = User(
            display_name=display_name,
            role=role,
            email=normalise_email(email),
            created_at=utcnow(),
        )
        async with self._sessions() as s, s.begin():
            s.add(user)
            await s.flush()
            s.add(LocalCredential(user_id=user.id, username=username))
        return user

    async def get_credential(self, user_id: str) -> LocalCredential | None:
        async with self._sessions() as s:
            return await s.get(LocalCredential, user_id)

    async def get_credential_by_username(self, username: str) -> LocalCredential | None:
        async with self._sessions() as s:
            return await s.scalar(
                select(LocalCredential).where(LocalCredential.username == username)
            )

    async def usernames(self) -> dict[str, str]:
        async with self._sessions() as s:
            rows = await s.execute(
                select(LocalCredential.user_id, LocalCredential.username)
            )
            return {row[0]: row[1] for row in rows}

    async def set_password_hash(self, user_id: str, password_hash: str) -> bool:
        # an empty hash matches no password: the account waits for its link
        async with self._sessions() as s, s.begin():
            result = await s.execute(
                update(LocalCredential)
                .where(LocalCredential.user_id == user_id)
                .values(
                    password_hash=password_hash,
                    password_changed_at=utcnow() if password_hash else None,
                )
            )
            return result.rowcount == 1

    async def add_password_token(
        self,
        user_id: str,
        token_hash: str,
        purpose: str,
        created_by: str,
        expires_at: datetime,
    ) -> PasswordToken:
        # a new link replaces any the account has not used yet
        row = PasswordToken(
            token_hash=token_hash,
            user_id=user_id,
            purpose=purpose,
            created_by=created_by,
            created_at=utcnow(),
            expires_at=expires_at,
        )
        async with self._sessions() as s, s.begin():
            await s.execute(
                delete(PasswordToken).where(
                    PasswordToken.user_id == user_id, PasswordToken.used_at.is_(None)
                )
            )
            s.add(row)
        return row

    async def get_password_token(self, token_hash: str) -> PasswordToken | None:
        async with self._sessions() as s:
            return await s.get(PasswordToken, token_hash)

    async def redeem_password_token(
        self, token_hash: str, password_hash: str
    ) -> str | None:
        """Spends a live link and stores the password it sets; returns the
        account, or None.

        Both happen in one transaction, and the spend is a conditional UPDATE:
        two requests racing with one link cannot both get through, and an admin
        reset running at the same moment cannot be undone by the old link.
        """
        now = utcnow()
        async with self._sessions() as s, s.begin():
            result = await s.execute(
                update(PasswordToken)
                .where(
                    PasswordToken.token_hash == token_hash,
                    PasswordToken.used_at.is_(None),
                    PasswordToken.expires_at > now,
                )
                .values(used_at=now)
            )
            if result.rowcount != 1:
                return None
            user_id = await s.scalar(
                select(PasswordToken.user_id).where(
                    PasswordToken.token_hash == token_hash
                )
            )
            await s.execute(
                update(LocalCredential)
                .where(LocalCredential.user_id == user_id)
                .values(password_hash=password_hash, password_changed_at=now)
            )
            return user_id

    # access requests

    async def add_access_request(self, **values: Any) -> AccessRequest | None:
        # None when this identity has already asked
        row = AccessRequest(created_at=utcnow(), status="pending", **values)
        try:
            async with self._sessions() as s, s.begin():
                s.add(row)
        except IntegrityError:
            return None
        return row

    async def get_access_request(self, request_id: str) -> AccessRequest | None:
        async with self._sessions() as s:
            return await s.get(AccessRequest, request_id)

    async def access_request_of(self, issuer: str, sub: str) -> AccessRequest | None:
        async with self._sessions() as s:
            return await s.scalar(
                select(AccessRequest).where(
                    AccessRequest.issuer == issuer, AccessRequest.sub == sub
                )
            )

    async def list_access_requests(self) -> list[AccessRequest]:
        async with self._sessions() as s:
            rows = await s.scalars(
                select(AccessRequest).order_by(AccessRequest.created_at.desc())
            )
            return list(rows)

    async def count_pending_access_requests(self) -> int:
        async with self._sessions() as s:
            return int(
                await s.scalar(
                    select(func.count())
                    .select_from(AccessRequest)
                    .where(AccessRequest.status == "pending")
                )
            )

    async def approve_access_request(
        self, request_id: str, role: str, decided_by: str
    ) -> User | None:
        """None when there is no such pending request; ValueError when the
        identity was bound some other way meanwhile.

        The address also goes on the allow-list, bound to the account: ``sub``
        is pairwise and changes with the client registration (test proxy to
        production), and the address is what re-binds the person then.
        """
        try:
            return await self._approve(request_id, role, decided_by)
        except IntegrityError:
            # another admin approved the same request a moment earlier
            return None

    async def _approve(
        self, request_id: str, role: str, decided_by: str
    ) -> User | None:
        async with self._sessions() as s, s.begin():
            request = await s.get(AccessRequest, request_id, with_for_update=True)
            if request is None or request.status != "pending":
                return None
            bound = await s.scalar(
                select(Identity.id).where(
                    Identity.issuer == request.issuer, Identity.sub == request.sub
                )
            )
            if bound is not None:
                # signed in through an invitation after asking
                raise ValueError("this identity already belongs to an account")
            now = utcnow()
            user = User(
                display_name=request.display_name or request.email or "unnamed",
                role=role,
                email=request.email,
                created_at=now,
            )
            s.add(user)
            await s.flush()
            s.add(
                Identity(
                    issuer=request.issuer,
                    sub=request.sub,
                    user_id=user.id,
                    home_organization=request.home_organization,
                    bound_at=now,
                )
            )
            # without a home organisation the entry could be claimed from any
            # identity provider in the federation that asserts the address
            if (
                request.email
                and request.home_organization
                and await s.get(RegisteredEmail, request.email) is None
            ):
                s.add(
                    RegisteredEmail(
                        email=request.email,
                        user_id=user.id,
                        role=role,
                        home_organization=request.home_organization,
                        created_by=decided_by,
                        created_at=now,
                    )
                )
            request.status = "approved"
            request.decided_by = decided_by
            request.decided_at = now
        return user

    async def reject_access_request(
        self, request_id: str, decided_by: str, note: str
    ) -> bool:
        async with self._sessions() as s, s.begin():
            result = await s.execute(
                update(AccessRequest)
                .where(
                    AccessRequest.id == request_id, AccessRequest.status == "pending"
                )
                .values(
                    status="rejected",
                    decided_by=decided_by,
                    decided_at=utcnow(),
                    decision_note=note,
                )
            )
            return result.rowcount == 1

    async def delete_access_request(self, request_id: str) -> bool:
        async with self._sessions() as s, s.begin():
            result = await s.execute(
                delete(AccessRequest).where(AccessRequest.id == request_id)
            )
            return result.rowcount == 1

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
        self, user_id: str, limit: int | None = 100
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

    async def answer_meta(self) -> list[tuple[dict[str, Any], str]]:
        """Every stored answer's metadata, with the account it was given to."""
        async with self._sessions() as s:
            rows = await s.execute(
                select(Message.meta, Conversation.user_id)
                .join(Conversation, Conversation.id == Message.conversation_id)
                .where(Message.role == "assistant")
            )
            return [(row[0] or {}, row[1]) for row in rows]

    async def count_conversations(self) -> int:
        async with self._sessions() as s:
            return int(await s.scalar(select(func.count()).select_from(Conversation)))

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

    async def feedback_of(self, user_id: str) -> list[Feedback]:
        async with self._sessions() as s:
            rows = await s.scalars(
                select(Feedback)
                .where(Feedback.user_id == user_id)
                .order_by(Feedback.created_at, Feedback.id)
            )
            return list(rows)

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

    async def delete_sessions_of(self, user_id: str, keep: str | None = None) -> None:
        condition = WebSession.user_id == user_id
        if keep is not None:
            condition = condition & (WebSession.id != keep)
        async with self._sessions() as s, s.begin():
            await s.execute(delete(WebSession).where(condition))

    async def purge_expired_sessions(self) -> int:
        async with self._sessions() as s, s.begin():
            result = await s.execute(
                delete(WebSession).where(WebSession.expires_at <= utcnow())
            )
            return int(result.rowcount)
