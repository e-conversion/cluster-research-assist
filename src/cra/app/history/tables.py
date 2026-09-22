"""Database schema. Migrations under ``migrations/`` MUST match this module;
``tests/test_migrations.py`` checks that."""

import secrets
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    return secrets.token_urlsafe(12)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar = {dict[str, Any]: JSON}


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
