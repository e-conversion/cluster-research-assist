"""The admin console: accounts, the sign-in allow-list, and policy."""

import asyncio
import logging
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from quart import Blueprint, current_app, g, request

from cra.app.auth.principal import Role
from cra.app.policy import KEYS, PolicyError
from cra.app.web.access import requires_admin
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

    people = []
    for user in await repo.list_users():
        identities = await repo.list_identities(user.id)
        people.append(
            {
                "kind": "account",
                "id": user.id,
                "name": user.display_name,
                "email": email_of.get(user.id),
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
    if "is_active" in body:
        if user_id == g.principal.user_id and not body["is_active"]:
            return _bad("an admin cannot deactivate their own account")
        await repo.set_user_active(user_id, bool(body["is_active"]))
    return {"ok": True}


@bp.delete("/api/admin/users/<user_id>")
@requires_admin
async def delete_user(user_id: str) -> Any:
    if user_id == g.principal.user_id:
        return _bad("an admin cannot delete their own account")
    if not await _ctx().repo.delete_user(user_id):
        return _bad("no such account", 404)
    return {"ok": True}


@bp.post("/api/admin/emails")
@requires_admin
async def invite() -> Any:
    repo = _ctx().repo
    body = await request.get_json(silent=True) or {}
    email = str(body.get("email", "")).strip()
    role = str(body.get("role", Role.USER))
    if "@" not in email:
        return _bad("that is not an email address")
    if role not in (Role.USER, Role.ADMIN):
        return _bad(f"role must be {Role.USER} or {Role.ADMIN}")
    if await repo.get_registered_email(email) is not None:
        return _bad("already invited")
    await repo.add_registered_email(email, g.principal.display or "admin", role)
    return {"email": email, "role": role}


@bp.put("/api/admin/emails/<path:email>")
@requires_admin
async def update_invitation(email: str) -> Any:
    body = await request.get_json(silent=True) or {}
    role = str(body.get("role", ""))
    if role not in (Role.USER, Role.ADMIN):
        return _bad(f"role must be {Role.USER} or {Role.ADMIN}")
    if not await _ctx().repo.set_registered_email_role(email, role):
        return _bad("not invited", 404)
    return {"email": email, "role": role}


@bp.delete("/api/admin/emails/<path:email>")
@requires_admin
async def remove_email(email: str) -> Any:
    if not await _ctx().repo.remove_registered_email(email):
        return _bad("not on the list", 404)
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
