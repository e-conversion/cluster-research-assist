"""Database schema. Migrations under ``migrations/`` MUST match this module;
``tests/test_migrations.py`` checks that."""

import secrets
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    return secrets.token_urlsafe(12)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar = {dict[str, Any]: JSON, list[Any]: JSON}


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    display_name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime]
    last_login_at: Mapped[datetime | None]
    is_active: Mapped[bool] = mapped_column(default=True)
    role: Mapped[str] = mapped_column(String(20), default="user")


class RegisteredEmail(Base):
    """The allow-list. ``user_id`` is filled by the first successful login."""

    __tablename__ = "registered_emails"

    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # the role the account gets when this address first signs in
    role: Mapped[str] = mapped_column(String(20), default="user")
    # the schacHomeOrganization the claiming identity must come from; empty
    # defers to CRA_AUTH_HOME_ORGANIZATIONS
    home_organization: Mapped[str] = mapped_column(String(200), default="")
    created_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime]


class Identity(Base):
    """One ``(issuer, sub)`` pair bound to a user. ``sub`` is pairwise per
    relying party, so a user may own several."""

    __tablename__ = "identities"
    __table_args__ = (UniqueConstraint("issuer", "sub"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    issuer: Mapped[str] = mapped_column(String(500))
    sub: Mapped[str] = mapped_column(String(500))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    home_organization: Mapped[str] = mapped_column(String(200), default="")
    bound_at: Mapped[datetime]


class PolicySetting(Base):
    """An operational setting an admin changed, overriding the configured
    default. Endpoints, secrets and paths are not settings: they stay in the
    environment, because changing them needs a restart."""

    __tablename__ = "policy"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_at: Mapped[datetime]
    updated_by: Mapped[str] = mapped_column(String(200))


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class Message(Base):
    """One turn of a conversation. ``meta`` carries what the interface shows
    above and below an answer: the model, how long it took and which tools ran.
    """

    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation", "conversation_id", "position"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    position: Mapped[int]
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text, default="")
    meta: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime]


class Feedback(Base):
    """A bug report or a note about an answer.

    The conversation it was sent from is attached, so a report can be read
    without asking the person what they had asked. It goes when the account
    goes, which is what deleting an account promises.
    """

    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime]
    category: Mapped[str] = mapped_column(String(40))
    text: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(120), default="")
    messages: Mapped[list[Any]] = mapped_column(default=list)


class McpToken(Base):
    """A bearer token for the outward MCP endpoint, owned by one account.

    Only the sha256 of the value is kept: the value is shown once, when it is
    minted, and a database dump yields nothing a client could present.
    """

    __tablename__ = "mcp_tokens"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    label: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    last_used_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]


class WebSession(Base):
    """Server-side browser session. ``id`` is the hash of the cookie value, so
    a database dump does not yield usable cookies."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime]
    last_seen_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)
