"""Username and password accounts.

Admins create them, in the console or with ``cra users create``; nobody signs
up. A new account has no password: it gets a one-time link, and so does an
account whose password an admin has reset. Only the link's sha256 is stored,
like an MCP token's, and the link carries it in the URL fragment, which
browsers do not send, so it never reaches an access log.

Passwords are hashed with argon2id at argon2-cffi's defaults (the RFC 9106
low-memory profile), and rehashed at sign-in when those defaults move on.
Hashing is CPU-bound for tens of milliseconds, so it runs off the event loop.
"""

import asyncio
import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from cra.app.history.repository import (
    Repository,
    normalise_email,
    utcnow,
    valid_email,
)
from cra.app.history.tables import LocalCredential, User

# module-level so tests can swap in cheap parameters
HASHER = PasswordHasher()
# Each hash takes 64 MiB and tens of milliseconds on the thread pool that chat
# tools and the query encoder share; a burst of sign-ins must not take it over.
HASHING_SLOTS = asyncio.Semaphore(4)

# NIST SP 800-63B: length, no composition rules. The ceiling bounds the work an
# anonymous request can make the hasher do.
PASSWORD_MIN = 12
PASSWORD_MAX = 256
# an email address is a username too, so a workshop's accounts can be named
# after the addresses people registered with; 64 is the column's width
USERNAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9._+@-]{1,63}")
# the first names anyone tries; refusing them means a guess needs the username
# as well as the password
RESERVED_USERNAMES = frozenset(
    {
        "admin",
        "administrator",
        "root",
        "superuser",
        "sysadmin",
        "system",
        "test",
        "user",
        "guest",
        "demo",
    }
)
NAME_MAX = 200
LINK_LIFETIME = timedelta(hours=72)
LINK_FRAGMENT = "#/set-password/"


class Purpose(StrEnum):
    SETUP = "setup"
    RESET = "reset"


class CredentialError(ValueError):
    """Input that cannot become an account or a password; the message is fit
    to show the person who typed it."""


@dataclass(frozen=True)
class Verified:
    """The outcome of a sign-in attempt. ``user`` is set only when the
    password matched, whether or not the account is active."""

    user: User | None = None

    @property
    def ok(self) -> bool:
        return self.user is not None and self.user.is_active


def normalise_username(username: str) -> str:
    return username.strip().lower()


def check_username(username: str) -> str:
    name = normalise_username(username)
    if not USERNAME_PATTERN.fullmatch(name):
        raise CredentialError(
            "a username is 2 to 64 lowercase letters, digits, '.', '_', '-', '+' "
            "or '@', starting with a letter or digit"
        )
    if name in RESERVED_USERNAMES:
        raise CredentialError(f"{name!r} is too easy to guess; choose another username")
    return name


def check_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise CredentialError("an account needs a name")
    if len(name) > NAME_MAX:
        raise CredentialError(f"a name has at most {NAME_MAX} characters")
    return name


def check_email(email: str) -> str:
    """An account may go without a contact address."""
    email = normalise_email(email)
    if email and not valid_email(email):
        raise CredentialError("that is not an email address")
    return email


def check_password(password: str, username: str) -> None:
    if len(password) < PASSWORD_MIN:
        raise CredentialError(f"a password needs at least {PASSWORD_MIN} characters")
    if len(password) > PASSWORD_MAX:
        raise CredentialError(f"a password has at most {PASSWORD_MAX} characters")
    if normalise_username(password) == normalise_username(username):
        raise CredentialError("the password cannot be the username")


async def _hashing(work, *args):
    async with HASHING_SLOTS:
        return await asyncio.to_thread(work, *args)


async def hash_password(password: str) -> str:
    return await _hashing(HASHER.hash, password)


_dummy_hash: str | None = None


def _dummy() -> str:
    """A hash to verify against when there is nothing to verify, so that an
    unknown username takes as long to refuse as a wrong password."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = HASHER.hash(secrets.token_urlsafe(16))
    return _dummy_hash


def _matches(stored: str, password: str) -> bool:
    """Whether ``password`` matches ``stored``; an empty ``stored`` is checked
    against the dummy and never matches."""
    try:
        return HASHER.verify(stored or _dummy(), password) and bool(stored)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


async def _credential(repo: Repository, name: str) -> LocalCredential | None:
    """The credential a username, or an email address, names.

    An address is matched against usernames and the accounts' addresses
    together; naming two accounts, it names none rather than whichever comes
    first, so one person's password can never open another's account.
    """
    by_username = (
        await repo.get_credential_by_username(name)
        if USERNAME_PATTERN.fullmatch(name)
        else None
    )
    if "@" not in name:
        return by_username
    found = {c.user_id: c for c in await repo.credentials_by_email(name)}
    if by_username is not None:
        found[by_username.user_id] = by_username
    return next(iter(found.values())) if len(found) == 1 else None


async def verify(repo: Repository, username: str, password: str) -> Verified:
    """Signs in by username or by email address; refusing an unknown name
    takes as long as refusing a wrong password."""
    if len(password) > PASSWORD_MAX:
        return Verified()
    credential = await _credential(repo, normalise_username(username))
    stored = credential.password_hash if credential else ""
    # hashed before anything else is decided, unknown username or not
    matched = await _hashing(_matches, stored, password)
    if credential is None or not matched:
        return Verified()
    if HASHER.check_needs_rehash(stored):
        await repo.set_password_hash(credential.user_id, await hash_password(password))
    return Verified(await repo.get_user(credential.user_id))


async def create_user(
    repo: Repository, username: str, name: str, email: str = "", role: str = "user"
) -> User:
    username = check_username(username)
    name = check_name(name)
    email = check_email(email)
    if await repo.get_credential_by_username(username) is not None:
        raise CredentialError(f"the username {username!r} is taken")
    return await repo.create_local_user(username, name, email, role)


def hash_link(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def issue_link(
    repo: Repository, user_id: str, purpose: Purpose, created_by: str
) -> str:
    value = secrets.token_urlsafe(32)
    await repo.add_password_token(
        user_id, hash_link(value), purpose.value, created_by, utcnow() + LINK_LIFETIME
    )
    return value


def link_path(base_path: str, value: str) -> str:
    return f"{base_path}/{LINK_FRAGMENT}{value}"


async def link_username(repo: Repository, value: str) -> str:
    """The username a live link sets the password of, so the page can say whose
    account it is before anyone types a password into it."""
    token = await repo.get_password_token(hash_link(value))
    credential = await repo.get_credential(token.user_id) if token else None
    if token is None or credential is None or token.used_at is not None:
        raise CredentialError("this link is not valid; ask an admin for a new one")
    if token.expires_at <= utcnow():
        raise CredentialError("this link has expired; ask an admin for a new one")
    return credential.username


async def redeem_link(repo: Repository, value: str, password: str) -> str:
    # the password is checked before the link is spent, so a too-short one
    # does not cost the person their link
    username = await link_username(repo, value)
    check_password(password, username)
    user_id = await repo.redeem_password_token(
        hash_link(value), await hash_password(password)
    )
    if user_id is None:
        raise CredentialError(
            "this link has expired or was already used; ask an admin for a new one"
        )
    return user_id


async def change_password(
    repo: Repository, user_id: str, current: str, new: str
) -> None:
    credential = await repo.get_credential(user_id)
    if credential is None:
        raise CredentialError("this account signs in at its institution")
    if len(current) > PASSWORD_MAX or not await _hashing(
        _matches, credential.password_hash, current
    ):
        raise CredentialError("the current password is not right")
    check_password(new, credential.username)
    await repo.set_password_hash(user_id, await hash_password(new))


async def _free_for(repo: Repository, user_id: str, address: str) -> bool:
    """Whether nobody else signs in by ``address``. Two accounts sharing one
    make it name neither, so taking somebody's address would lock them out
    of signing in with it."""
    return not (await repo.accounts_named(address) - {user_id})


async def update_profile(
    repo: Repository,
    user_id: str,
    *,
    name: str | None = None,
    username: str | None = None,
    email: str | None = None,
) -> set[str]:
    """Changes what is given and differs from what is stored; returns the
    names of the fields that changed. Nothing changes when any is refused."""
    user = await repo.get_user(user_id)
    credential = await repo.get_credential(user_id)
    changes: dict[str, str] = {}
    if name is not None and (name := check_name(name)) != user.display_name:
        changes["display_name"] = name
    if username is not None:
        if credential is None:
            raise CredentialError("this account signs in at its institution")
        if (username := check_username(username)) != credential.username:
            if "@" in username and not await _free_for(repo, user_id, username):
                raise CredentialError(f"the username {username!r} is taken")
            changes["username"] = username
    if email is not None and (email := check_email(email)) != user.email:
        if email and not await _free_for(repo, user_id, email):
            raise CredentialError("another account uses that email address")
        changes["email"] = email
    if changes and not await repo.update_profile(user_id, **changes):
        raise CredentialError(f"the username {changes['username']!r} is taken")
    return {"name" if key == "display_name" else key for key in changes}


async def reset(repo: Repository, user_id: str, created_by: str) -> str:
    if await repo.get_credential(user_id) is None:
        raise CredentialError("this account signs in at its institution")
    await repo.set_password_hash(user_id, "")
    return await issue_link(repo, user_id, Purpose.RESET, created_by)
