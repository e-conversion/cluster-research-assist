"""The admin console: accounts, the sign-in allow-list, and policy."""

import asyncio
import logging
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from quart import Blueprint, current_app, g, request

from cra.app.auth import local
from cra.app.auth.principal import Role
from cra.app.history.repository import valid_email
from cra.app.policy import KEYS, PolicyError
from cra.app.web import auditlog
from cra.app.web.access import requires_admin
from cra.app.web.route_auth import body_of
from cra.core.library import versions as versioning
from cra.core.library.library import Library, LibraryError

log = logging.getLogger(__name__)

bp = Blueprint("admin", __name__)


def _ctx():
    return current_app.extensions["cra"]


def _bad(message: str, status: int = 400) -> tuple[dict[str, str], int]:
    return {"error": message}, status


@bp.get("/api/admin/people")
@requires_admin
async def list_people() -> dict[str, Any]:
    """Accounts, invitations not yet used, and the admins named in the
    configuration, in one list so that no source of access is hidden."""
    ctx = _ctx()
    repo = ctx.repo
    invitations = await repo.list_registered_emails()
    email_of = {i.user_id: i.email for i in invitations if i.user_id}
    usernames = await repo.usernames()

    people = []
    for user in await repo.list_users():
        identities = await repo.list_identities(user.id)
        sign_in = (["password"] if user.id in usernames else []) + (
            ["institution"] if identities else []
        )
        people.append(
            {
                "kind": "account",
                "id": user.id,
                "name": user.display_name,
                "username": usernames.get(user.id),
                "sign_in": sign_in,
                "email": user.email or email_of.get(user.id),
                "role": user.role,
                "active": user.is_active,
                "last_login_at": user.last_login_at.isoformat()
                if user.last_login_at
                else None,
                "organizations": sorted(
                    {i.home_organization or i.issuer for i in identities}
                ),
                "self": user.id == g.principal.user_id,
            }
        )
    people += [
        {
            "kind": "invitation",
            "email": row.email,
            "role": row.role,
            "home_organization": row.home_organization,
            "invited_by": row.created_by,
            "invited_at": row.created_at.isoformat(),
        }
        for row in invitations
        if row.user_id is None
    ]
    known = {p.get("email") for p in people} | {p.get("name") for p in people}
    people += [
        {"kind": "configured", "email": address, "role": Role.ADMIN}
        for address in ctx.settings.auth_admins
        if address.strip().lower() not in {k.lower() for k in known if k}
    ]
    return {"people": people}


@bp.put("/api/admin/users/<user_id>")
@requires_admin
async def update_user(user_id: str) -> Any:
    repo = _ctx().repo
    body = await request.get_json(silent=True) or {}
    user = await repo.get_user(user_id)
    if user is None:
        return _bad("no such account", 404)
    if "role" in body:
        role = str(body["role"])
        if role not in (Role.USER, Role.ADMIN):
            return _bad(f"role must be {Role.USER} or {Role.ADMIN}")
        if user_id == g.principal.user_id and role != Role.ADMIN:
            return _bad("an admin cannot take their own admin rights away")
        await repo.set_user_role(user_id, role)
        auditlog.record("set_role", user=user_id, role=role, was=user.role)
    if "is_active" in body:
        if user_id == g.principal.user_id and not body["is_active"]:
            return _bad("an admin cannot deactivate their own account")
        active = bool(body["is_active"])
        await repo.set_user_active(user_id, active)
        if not active:
            # a disabled account keeps no live connection to anyone's eLN, and
            # no token that would work again if it were reactivated
            for session in await repo.sessions_of(user_id):
                await _ctx().remote.forget(session.id)
            await repo.delete_sessions_of(user_id)
            await repo.revoke_tokens_of(user_id)
        auditlog.record("set_active", user=user_id, active=active)
    return {"ok": True}


@bp.post("/api/admin/local-users")
@requires_admin
async def create_local_user() -> Any:
    ctx = _ctx()
    body = await body_of()
    role = str(body.get("role", Role.USER))
    if role not in (Role.USER, Role.ADMIN):
        return _bad(f"role must be {Role.USER} or {Role.ADMIN}")
    try:
        user = await local.create_user(
            ctx.repo,
            str(body.get("username", "")),
            str(body.get("name", "")),
            str(body.get("email", "") or ""),
            role,
        )
    except local.CredentialError as exc:
        return _bad(str(exc))
    value = await local.issue_link(
        ctx.repo, user.id, local.Purpose.SETUP, g.principal.display or "admin"
    )
    auditlog.record("create_local_user", user=user.id, role=role)
    return {
        "id": user.id,
        "link": local.link_path(ctx.settings.base_path, value),
        "expires_in_hours": int(local.LINK_LIFETIME.total_seconds() // 3600),
    }, 201


@bp.post("/api/admin/users/<user_id>/password-reset")
@requires_admin
async def reset_password(user_id: str) -> Any:
    ctx = _ctx()
    if await ctx.repo.get_user(user_id) is None:
        return _bad("no such account", 404)
    try:
        value = await local.reset(ctx.repo, user_id, g.principal.display or "admin")
    except local.CredentialError as exc:
        return _bad(str(exc))
    for session in await ctx.repo.sessions_of(user_id):
        await ctx.remote.forget(session.id)
    await ctx.repo.delete_sessions_of(user_id)
    # whoever the reset is meant to lock out may have minted some
    await ctx.repo.revoke_tokens_of(user_id)
    auditlog.record("reset_password", user=user_id)
    return {
        "link": local.link_path(ctx.settings.base_path, value),
        "expires_in_hours": int(local.LINK_LIFETIME.total_seconds() // 3600),
    }


@bp.delete("/api/admin/users/<user_id>")
@requires_admin
async def delete_user(user_id: str) -> Any:
    ctx = _ctx()
    if user_id == g.principal.user_id:
        return _bad("an admin cannot delete their own account")
    for session in await ctx.repo.sessions_of(user_id):
        await ctx.remote.forget(session.id)
    if not await ctx.repo.delete_user(user_id):
        return _bad("no such account", 404)
    auditlog.record("delete_user", user=user_id)
    return {"ok": True}


@bp.post("/api/admin/emails")
@requires_admin
async def invite() -> Any:
    repo = _ctx().repo
    body = await request.get_json(silent=True) or {}
    email = str(body.get("email", "")).strip()
    role = str(body.get("role", Role.USER))
    organization = str(body.get("home_organization", "") or "").strip()
    if not valid_email(email):
        return _bad("that is not an email address")
    if role not in (Role.USER, Role.ADMIN):
        return _bad(f"role must be {Role.USER} or {Role.ADMIN}")
    if await repo.get_registered_email(email) is not None:
        return _bad("already invited")
    await repo.add_registered_email(
        email, g.principal.display or "admin", role, home_organization=organization
    )
    auditlog.record("invite", email=email, role=role, home_organization=organization)
    return {"email": email, "role": role, "home_organization": organization}


@bp.put("/api/admin/emails/<path:email>")
@requires_admin
async def update_invitation(email: str) -> Any:
    body = await request.get_json(silent=True) or {}
    role = str(body.get("role", ""))
    if role not in (Role.USER, Role.ADMIN):
        return _bad(f"role must be {Role.USER} or {Role.ADMIN}")
    if not await _ctx().repo.set_registered_email_role(email, role):
        return _bad("not invited", 404)
    auditlog.record("set_invitation_role", email=email, role=role)
    return {"email": email, "role": role}


@bp.delete("/api/admin/emails/<path:email>")
@requires_admin
async def remove_email(email: str) -> Any:
    if not await _ctx().repo.remove_registered_email(email):
        return _bad("not on the list", 404)
    auditlog.record("remove_email", email=email)
    return {"ok": True}


@bp.get("/api/admin/feedback")
@requires_admin
async def feedback() -> dict[str, Any]:
    rows = await _ctx().repo.list_feedback()
    return {
        "feedback": [
            {
                "id": row.id,
                "from": name or "a deleted account",
                "created_at": row.created_at.isoformat(),
                "category": row.category,
                "text": row.text,
                "model": row.model,
                "messages": row.messages,
            }
            for row, name in rows
        ]
    }


@bp.delete("/api/admin/feedback/<int:feedback_id>")
@requires_admin
async def delete_feedback(feedback_id: int) -> Any:
    if not await _ctx().repo.delete_feedback(feedback_id):
        return _bad("no such feedback", 404)
    return {"ok": True}


@bp.get("/api/admin/policy")
@requires_admin
async def get_policy() -> dict[str, Any]:
    return {"settings": _ctx().policy.describe()}


@bp.put("/api/admin/policy/<key>")
@requires_admin
async def set_policy(key: str) -> Any:
    ctx = _ctx()
    if key not in KEYS:
        return _bad("no such setting", 404)
    body = await request.get_json(silent=True) or {}
    try:
        if body.get("reset"):
            ctx.policy.clear(key)
            await ctx.repo.clear_policy(key)
        else:
            value = ctx.policy.set(key, body.get("value"))
            await ctx.repo.set_policy(key, value, g.principal.display or "admin")
    except (PolicyError, TypeError, ValueError) as exc:
        return _bad(str(exc))
    return {"key": key, "value": ctx.policy[key], "source": ctx.policy.source(key)}


@bp.get("/api/admin/library")
@requires_admin
async def library() -> dict[str, Any]:
    from cra.core.library import manifest as manifest_

    ctx = _ctx()
    loaded = ctx.library
    root = ctx.settings.library_path
    return {
        "path": str(root),
        "active": str(loaded.path) if loaded else None,
        "counts": loaded.counts if loaded else {},
        "available": loaded.available if loaded else {},
        "manifest": manifest_.read(loaded.path) if loaded else None,
        "updatable": versioning.writable(root),
        "max_upload_mb": ctx.settings.library_max_upload_mb,
        "versions": [
            {"name": v.name, "active": v.active, "bytes": v.bytes}
            for v in versioning.versions(root)
        ],
    }


@bp.post("/api/admin/library")
@requires_admin
async def upload_library() -> Any:
    """Install an uploaded bundle beside the live one and switch to it.

    The new version is verified in full before anything moves, so a bad upload
    leaves the running service untouched.
    """
    ctx = _ctx()
    root = Path(ctx.settings.library_path)
    if not versioning.writable(root):
        return _bad(
            "this deployment points at a fixed bundle; "
            "run `cra library init-root` to make it updatable",
            409,
        )
    files = await request.files
    upload = files.get("bundle")
    if upload is None:
        return _bad("attach the bundle as the form field 'bundle'")

    with tempfile.TemporaryDirectory() as staging:
        archive = Path(staging) / "bundle.tar.gz"
        await upload.save(str(archive))
        try:
            installed = await asyncio.to_thread(versioning.unpack, archive, root)
        except (versioning.LibraryLayoutError, tarfile.TarError, OSError) as exc:
            log.warning(
                "library upload rejected", extra={"fields": {"error": str(exc)}}
            )
            return _bad(f"the archive could not be unpacked: {exc}")

    try:
        library = await asyncio.to_thread(
            Library.load, installed, required_schema=ctx.settings.library_require_schema
        )
    except LibraryError as exc:
        await asyncio.to_thread(_discard, installed)
        return _bad(str(exc))

    await asyncio.to_thread(versioning.activate, root, installed.name)
    ctx.library = library
    await asyncio.to_thread(versioning.prune, root, ctx.settings.library_keep_versions)
    log.info(
        "library replaced",
        extra={
            "fields": {
                "version": installed.name,
                "by": g.principal.user_id,
                **library.counts,
            }
        },
    )
    return {"version": installed.name, "counts": library.counts}


@bp.post("/api/admin/library/<version>/activate")
@requires_admin
async def activate_library(version: str) -> Any:
    ctx = _ctx()
    root = Path(ctx.settings.library_path)
    if not versioning.writable(root):
        return _bad("this deployment points at a fixed bundle", 409)
    target = root / versioning.VERSIONS / version
    try:
        library = await asyncio.to_thread(
            Library.load, target, required_schema=ctx.settings.library_require_schema
        )
        await asyncio.to_thread(versioning.activate, root, version)
    except (versioning.LibraryLayoutError, LibraryError) as exc:
        return _bad(str(exc), 404 if not target.exists() else 400)
    ctx.library = library
    log.info(
        "library version activated",
        extra={"fields": {"version": version, "by": g.principal.user_id}},
    )
    return {"version": version, "counts": library.counts}


def _discard(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)
