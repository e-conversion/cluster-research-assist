"""The ``cra`` command. Each subcommand is one ``cmd_*`` function."""

import argparse
import asyncio
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from cra import __version__
from cra.config.settings import RETIRED_KEYS, Settings, unknown_keys

EXIT_CONFIG = 2


def tokens_default_days() -> int:
    from cra.app.auth import tokens

    return tokens.DEFAULT_DAYS


def load_settings(args: argparse.Namespace) -> Settings:
    """Settings for a subcommand; validation problems end the process with exit 2."""
    unknown = unknown_keys(args.env_file)
    if unknown:
        sys.stderr.write("unknown configuration keys: " + ", ".join(unknown) + "\n")
        for key in unknown:
            if key in RETIRED_KEYS:
                sys.stderr.write(f"  {key} was removed: {RETIRED_KEYS[key]}\n")
        sys.exit(EXIT_CONFIG)
    try:
        return Settings.load(args.env_file)
    except ValidationError as exc:
        sys.stderr.write(f"invalid configuration:\n{exc}\n")
        sys.exit(EXIT_CONFIG)


def _out(line: str = "") -> None:
    sys.stdout.write(line + "\n")


def cmd_check_config(args: argparse.Namespace) -> int:
    settings = load_settings(args)
    for key, value in settings.dump().items():
        _out(f"{key}={value}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from hypercorn.asyncio import serve
    from hypercorn.config import Config

    from cra.app.mcpserver.dispatcher import wrap
    from cra.app.web.factory import create_app
    from cra.logsetup import configure_logging

    settings = load_settings(args)
    configure_logging(settings.log_dir)
    config = Config()
    config.bind = [f"{settings.host}:{settings.port}"]
    config.accesslog = None
    # one process on one loop: the outward endpoint and the web app share the
    # library, the registry and the per-session state
    asyncio.run(serve(wrap(create_app(settings)), config))  # type: ignore[arg-type]
    return 0


# library


def cmd_library_check(args: argparse.Namespace) -> int:
    from cra.core.library.library import Library, LibraryError

    settings = load_settings(args)
    directory = args.directory or settings.library_path
    try:
        library = Library.load(
            directory, required_schema=settings.library_require_schema
        )
    except LibraryError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    for key, value in library.counts.items():
        _out(f"{key:12} {value}")
    for key, ok in library.available.items():
        if not ok:
            _out(f"{key:12} unavailable")
    return 0


def cmd_library_build(args: argparse.Namespace) -> int:
    """Compute the derived artifacts so that serving needs no heavy work."""
    import json
    import time

    from cra.core.library import manifest
    from cra.core.library.derive import build_map
    from cra.core.library.library import FILES, Library, LibraryError

    settings = load_settings(args)
    directory = Path(args.directory or settings.library_path)
    try:
        library = Library.load(directory, verify=False)
    except LibraryError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    if library.embeddings is None:
        sys.stderr.write(f"{FILES['embeddings']} missing: the map needs embeddings\n")
        return 1

    started = time.perf_counter()
    payload = build_map(
        library.embeddings.dois,
        library.embeddings.vectors,
        {doi: paper.title for doi, paper in library.papers.items()},
        model=library.embeddings.model,
    )
    target = directory / FILES["map"]
    target.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    _out(f"wrote {target} in {time.perf_counter() - started:.1f}s")

    library = Library.load(directory, verify=False)
    manifest.write(
        directory,
        library.counts,
        embedding_model=library.embeddings.model if library.embeddings else "",
        builder=f"cra {__version__}",
    )
    _out(f"wrote {directory / manifest.MANIFEST}")
    return 0


def cmd_library_manifest(args: argparse.Namespace) -> int:
    from cra.core.library import manifest
    from cra.core.library.library import Library, LibraryError

    settings = load_settings(args)
    directory = Path(args.directory or settings.library_path)
    try:
        library = Library.load(directory, verify=False)
    except LibraryError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    model = library.embeddings.model if library.embeddings is not None else ""
    manifest.write(
        directory, library.counts, embedding_model=model, builder=f"cra {__version__}"
    )
    _out(f"wrote {directory / manifest.MANIFEST}")
    return 0


def cmd_library_versions(args: argparse.Namespace) -> int:
    from cra.core.library import versions as versioning

    settings = load_settings(args)
    root = Path(args.directory or settings.library_path)
    found = versioning.versions(root)
    if not found:
        sys.stderr.write(f"{root} holds no versions; see `cra library init-root`\n")
        return 1
    for version in found:
        marker = "*" if version.active else " "
        _out(f"{marker} {version.name:24} {version.bytes / 1e6:7.1f} MB")
    return 0


def cmd_library_activate(args: argparse.Namespace) -> int:
    from cra.core.library import versions as versioning
    from cra.core.library.library import Library, LibraryError

    settings = load_settings(args)
    root = Path(args.directory or settings.library_path)
    target = root / versioning.VERSIONS / args.version
    try:
        Library.load(target, required_schema=settings.library_require_schema)
        versioning.activate(root, args.version)
    except (versioning.LibraryLayoutError, LibraryError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    _out(f"active version is now {args.version}")
    return 0


def cmd_library_init_root(args: argparse.Namespace) -> int:
    """Turn a plain bundle into a root that can be updated while serving."""
    from cra.core.library import versions as versioning

    settings = load_settings(args)
    root = Path(args.directory or settings.library_path)
    bundle = Path(args.bundle) if args.bundle else root
    if versioning.is_root(root) and not args.bundle:
        sys.stderr.write(f"{root} is already a versioned root\n")
        return 1
    try:
        if bundle == root:
            # move the bundle aside so the root can hold it as its first version
            staging = root.parent / f".{root.name}-bundle"
            root.rename(staging)
            root.mkdir()
            active = versioning.init_root(root, staging)
            shutil.rmtree(staging)
        else:
            active = versioning.init_root(root, bundle)
    except (versioning.LibraryLayoutError, OSError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    _out(f"{root} is a versioned root; active version {active.name}")
    return 0


def cmd_encoder_fetch(args: argparse.Namespace) -> int:
    from cra.core.retrieval.encoder import fetch

    settings = load_settings(args)
    model = args.model or settings.encoder_model
    path = Path(args.directory or settings.encoder_path or "encoder")
    _out(f"downloading {model} into {path}")
    try:
        fetch(model, path, onnx_file=args.onnx_file)
    except Exception as exc:  # noqa: BLE001 -- the hub raises a family of errors
        sys.stderr.write(f"download failed: {exc}\n")
        return 1
    _out(f"set CRA_ENCODER_PATH={path}")
    return 0


# database


def _engine(settings: Settings):
    from cra.app.history.engine import make_engine

    return make_engine(settings.history_url)


def cmd_db_upgrade(args: argparse.Namespace) -> int:
    from cra.app.history import migrate

    engine = _engine(load_settings(args))

    async def run() -> None:
        await migrate.upgrade(engine)
        _out(f"database at revision {await migrate.current_revision(engine)}")
        await engine.dispose()

    asyncio.run(run())
    return 0


def cmd_db_current(args: argparse.Namespace) -> int:
    from cra.app.history import migrate

    engine = _engine(load_settings(args))

    async def run() -> int:
        current = await migrate.current_revision(engine)
        await engine.dispose()
        head = migrate.head_revision()
        _out(f"database: {current}  code: {head}")
        return 0 if current == head else 1

    return asyncio.run(run())


def cmd_db_revision(args: argparse.Namespace) -> int:
    from cra.app.history import migrate

    engine = _engine(load_settings(args))

    async def run() -> None:
        await migrate.revision(engine, args.message)
        await engine.dispose()

    asyncio.run(run())
    return 0


# users and the allow-list


def _repo(settings: Settings):
    from cra.app.history.engine import make_session_factory
    from cra.app.history.repository import Repository

    engine = _engine(settings)
    return engine, Repository(make_session_factory(engine))


def cmd_users_list(args: argparse.Namespace) -> int:
    engine, repo = _repo(load_settings(args))

    async def run() -> None:
        for email in await repo.list_registered_emails():
            _out(
                f"email  {email.email:40} user={email.user_id or '-'}  "
                f"org={email.home_organization or '*'}  by {email.created_by}"
            )
        usernames = await repo.usernames()
        for user in await repo.list_users():
            state = "active" if user.is_active else "disabled"
            sign_in = [f"password:{usernames[user.id]}"] if user.id in usernames else []
            sign_in += [
                f"{i.issuer}:{i.sub[:12]}" for i in await repo.list_identities(user.id)
            ]
            _out(
                f"user   {user.id:20} {user.display_name:30} {user.role:6} "
                f"{state:8} {', '.join(sign_in)}"
            )
        await engine.dispose()

    asyncio.run(run())
    return 0


def _read_password(from_stdin: bool) -> str | None:
    import getpass

    if from_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    first = getpass.getpass("password: ")
    if getpass.getpass("again: ") != first:
        sys.stderr.write("the two passwords differ\n")
        return None
    return first


def _link_hint(path: str) -> None:
    from cra.app.auth import local

    hours = int(local.LINK_LIFETIME.total_seconds() // 3600)
    _out(f"one-time link, valid for {hours} hours; prefix the site's address:")
    _out(f"  {path}")


def cmd_users_create(args: argparse.Namespace) -> int:
    """A password account. An admin gets its password here, so that a fresh
    instance has someone who can sign in; others get a link by default."""
    from cra.app.auth import local

    settings = load_settings(args)
    engine, repo = _repo(settings)
    role = "admin" if args.admin else "user"
    use_password = (
        args.password or args.password_stdin or (args.admin and not args.link)
    )

    async def create() -> None:
        username = local.check_username(args.username)
        password = None
        if use_password:
            password = _read_password(args.password_stdin)
            if password is None:
                raise local.CredentialError("no password given")
            local.check_password(password, username)
        user = await local.create_user(repo, username, args.name, args.email, role)
        if password is not None:
            await repo.set_password_hash(user.id, await local.hash_password(password))
            _out(f"created {role} {username} ({user.id}); it can sign in now")
        else:
            value = await local.issue_link(repo, user.id, local.Purpose.SETUP, "cli")
            _out(f"created {role} {username} ({user.id})")
            _link_hint(local.link_path(settings.base_path, value))

    async def run() -> int:
        try:
            await create()
        except local.CredentialError as exc:
            sys.stderr.write(f"{exc}\n")
            return 1
        finally:
            await engine.dispose()
        return 0

    return asyncio.run(run())


def cmd_users_password_link(args: argparse.Namespace) -> int:
    from cra.app.auth import local

    settings = load_settings(args)
    engine, repo = _repo(settings)

    async def run() -> int:
        try:
            credential = await repo.get_credential_by_username(
                local.normalise_username(args.username)
            )
            if credential is None:
                sys.stderr.write("no password account with that username\n")
                return 1
            value = await local.reset(repo, credential.user_id, "cli")
            await repo.delete_sessions_of(credential.user_id)
            await repo.revoke_tokens_of(credential.user_id)
            _link_hint(local.link_path(settings.base_path, value))
            return 0
        finally:
            await engine.dispose()

    return asyncio.run(run())


def cmd_users_add_email(args: argparse.Namespace) -> int:
    engine, repo = _repo(load_settings(args))

    async def run() -> int:
        from cra.app.history.repository import valid_email

        if not valid_email(args.email):
            sys.stderr.write("that is not an email address\n")
            return 1
        if await repo.get_registered_email(args.email) is not None:
            sys.stderr.write("already registered\n")
            return 1
        await repo.add_registered_email(args.email, args.by, home_organization=args.org)
        await engine.dispose()
        return 0

    return asyncio.run(run())


def cmd_users_remove_email(args: argparse.Namespace) -> int:
    engine, repo = _repo(load_settings(args))

    async def run() -> int:
        removed = await repo.remove_registered_email(args.email)
        await engine.dispose()
        return 0 if removed else 1

    return asyncio.run(run())


def cmd_users_set_role(args: argparse.Namespace, role: str) -> int:
    engine, repo = _repo(load_settings(args))

    async def run() -> int:
        changed = await repo.set_user_role(args.user_id, role)
        await engine.dispose()
        if not changed:
            sys.stderr.write("no such user\n")
        return 0 if changed else 1

    return asyncio.run(run())


def cmd_policy_list(args: argparse.Namespace) -> int:
    from cra.app.policy import Policy

    settings = load_settings(args)
    engine, repo = _repo(settings)

    async def run() -> int:
        policy = await Policy.load(settings, repo)
        await engine.dispose()
        for entry in policy.describe():
            marker = "*" if entry["source"] == "database" else " "
            _out(f"{marker} {entry['key']:28} {entry['value']}")
        _out("\n* changed from the configured default")
        return 0

    return asyncio.run(run())


def cmd_policy_set(args: argparse.Namespace) -> int:
    from cra.app.policy import KEYS, Policy, PolicyError

    settings = load_settings(args)
    engine, repo = _repo(settings)

    async def run() -> int:
        if args.key not in KEYS:
            sys.stderr.write(f"unknown setting {args.key!r}; try `cra policy list`\n")
            return 1
        policy = await Policy.load(settings, repo)
        try:
            if args.reset:
                await repo.clear_policy(args.key)
                policy.clear(args.key)
            else:
                value = policy.set(args.key, args.value)
                await repo.set_policy(args.key, value, "cli")
        except (PolicyError, TypeError, ValueError) as exc:
            sys.stderr.write(f"{exc}\n")
            return 1
        finally:
            pass
        _out(f"{args.key} = {policy[args.key]} ({policy.source(args.key)})")
        await engine.dispose()
        return 0

    return asyncio.run(run())


def cmd_users_set_active(args: argparse.Namespace, active: bool) -> int:
    engine, repo = _repo(load_settings(args))

    async def run() -> int:
        changed = await repo.set_user_active(args.user_id, active)
        if changed and not active:
            await repo.revoke_tokens_of(args.user_id)
        await engine.dispose()
        if not changed:
            sys.stderr.write("no such user\n")
        return 0 if changed else 1

    return asyncio.run(run())


async def _account(repo, who: str) -> str | None:
    """The user id for an id or an invited, claimed address."""
    if "@" in who:
        invitation = await repo.get_registered_email(who)
        return invitation.user_id if invitation else None
    user = await repo.get_user(who)
    return user.id if user else None


def cmd_token_issue(args: argparse.Namespace) -> int:
    from cra.app.auth import tokens

    engine, repo = _repo(load_settings(args))

    async def run() -> int:
        try:
            user_id = await _account(repo, args.user)
            if user_id is None:
                sys.stderr.write("no such account\n")
                return 1
            row, value = await tokens.mint(repo, user_id, args.label, args.days)
        except ValueError as exc:
            sys.stderr.write(f"{exc}\n")
            return 1
        finally:
            await engine.dispose()
        # the value on stdout, so it can be piped; the rest on stderr
        _out(value)
        sys.stderr.write(
            f"token {row.id} for {args.user}, valid until {row.expires_at:%Y-%m-%d}. "
            "This is the only time the value is shown.\n"
        )
        return 0

    return asyncio.run(run())


def cmd_token_list(args: argparse.Namespace) -> int:
    from cra.app.auth import tokens

    engine, repo = _repo(load_settings(args))

    async def run() -> int:
        try:
            if args.user:
                user_id = await _account(repo, args.user)
                if user_id is None:
                    sys.stderr.write("no such account\n")
                    return 1
                rows = [(row, args.user) for row in await repo.list_tokens(user_id)]
            else:
                rows = await repo.list_all_tokens()
        finally:
            await engine.dispose()
        for row, owner in rows:
            used = f"{row.last_used_at:%Y-%m-%d %H:%M}" if row.last_used_at else "never"
            _out(
                f"{row.id:18} {tokens.state(row):8} until {row.expires_at:%Y-%m-%d}  "
                f"used {used:16}  {owner:24} {row.label}"
            )
        return 0

    return asyncio.run(run())


def cmd_token_revoke(args: argparse.Namespace) -> int:
    engine, repo = _repo(load_settings(args))

    async def run() -> int:
        revoked = await repo.revoke_token(args.token_id)
        await engine.dispose()
        if not revoked:
            sys.stderr.write("no such token\n")
        return 0 if revoked else 1

    return asyncio.run(run())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cra", description="Cluster Research Assistant."
    )
    parser.add_argument("--version", action="version", version=f"cra {__version__}")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="configuration file, read below the process environment (default: .env)",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    check = sub.add_parser(
        "check-config",
        help="validate the configuration and print it with secrets redacted",
    )
    check.set_defaults(func=cmd_check_config)

    serve = sub.add_parser("serve", help="run the web application")
    serve.set_defaults(func=cmd_serve)

    library = sub.add_parser("library", help="library bundle").add_subparsers(
        dest="library_command", metavar="<command>", required=True
    )
    for name, func, help_text in (
        ("check", cmd_library_check, "verify the manifest and print the counts"),
        (
            "build",
            cmd_library_build,
            "compute the derived artifacts (needs the build extra)",
        ),
        (
            "manifest",
            cmd_library_manifest,
            "write manifest.json for a library directory",
        ),
        ("versions", cmd_library_versions, "list the installed versions"),
    ):
        p = library.add_parser(name, help=help_text)
        p.add_argument(
            "directory", nargs="?", type=Path, help="default: CRA_LIBRARY_PATH"
        )
        p.set_defaults(func=func)

    activate = library.add_parser("activate", help="switch to an installed version")
    activate.add_argument("version")
    activate.add_argument(
        "directory", nargs="?", type=Path, help="default: CRA_LIBRARY_PATH"
    )
    activate.set_defaults(func=cmd_library_activate)

    init_root = library.add_parser(
        "init-root", help="turn a bundle directory into a root that can be updated"
    )
    init_root.add_argument(
        "directory", nargs="?", type=Path, help="default: CRA_LIBRARY_PATH"
    )
    init_root.add_argument(
        "--bundle", type=Path, help="install this bundle as the first version"
    )
    init_root.set_defaults(func=cmd_library_init_root)

    db = sub.add_parser("db", help="database schema").add_subparsers(
        dest="db_command", metavar="<command>", required=True
    )
    db.add_parser("upgrade", help="migrate to the schema the code needs").set_defaults(
        func=cmd_db_upgrade
    )
    db.add_parser("current", help="compare database and code revisions").set_defaults(
        func=cmd_db_current
    )
    rev = db.add_parser("revision", help="autogenerate a migration (development)")
    rev.add_argument("-m", "--message", required=True)
    rev.set_defaults(func=cmd_db_revision)

    users = sub.add_parser("users", help="accounts and the registered-email allow-list")
    users_sub = users.add_subparsers(
        dest="users_command", metavar="<command>", required=True
    )
    users_sub.add_parser(
        "list", help="registered emails, users and how they sign in"
    ).set_defaults(func=cmd_users_list)
    create = users_sub.add_parser(
        "create",
        help="create a password account; the first admin of a new instance "
        "is made this way",
    )
    create.add_argument("username", help="what the person signs in with")
    create.add_argument("--name", required=True, help="full name, as shown")
    create.add_argument("--email", default="", help="contact address")
    create.add_argument("--admin", action="store_true", help="make it an admin")
    how = create.add_mutually_exclusive_group()
    how.add_argument(
        "--password",
        action="store_true",
        help="type the password now (the default for --admin)",
    )
    how.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the password from the first line of stdin",
    )
    how.add_argument(
        "--link",
        action="store_true",
        help="print a one-time set-password link (the default otherwise)",
    )
    create.set_defaults(func=cmd_users_create)
    reset = users_sub.add_parser(
        "password-link",
        help="clear a password account's password and print a link to set a new one",
    )
    reset.add_argument("username")
    reset.set_defaults(func=cmd_users_password_link)
    add = users_sub.add_parser("add-email", help="allow an email address to sign in")
    add.add_argument("email")
    add.add_argument("--by", default="cli", help="who registered it (for the record)")
    add.add_argument(
        "--org",
        default="",
        help="home organisation (schacHomeOrganization, e.g. tum.de) the address "
        "may be claimed from; default: CRA_AUTH_HOME_ORGANIZATIONS",
    )
    add.set_defaults(func=cmd_users_add_email)
    rm = users_sub.add_parser(
        "remove-email", help="remove an address from the allow-list"
    )
    rm.add_argument("email")
    rm.set_defaults(func=cmd_users_remove_email)
    for name, active in (("activate", True), ("deactivate", False)):
        p = users_sub.add_parser(name, help=f"{name} a user")
        p.add_argument("user_id")
        p.set_defaults(func=lambda a, active=active: cmd_users_set_active(a, active))
    for name, role in (("promote", "admin"), ("demote", "user")):
        p = users_sub.add_parser(
            name,
            help=f"make a user {'an admin' if role == 'admin' else 'an ordinary user'}",
        )
        p.add_argument("user_id")
        p.set_defaults(func=lambda a, role=role: cmd_users_set_role(a, role))

    encoder = sub.add_parser("encoder", help="the query encoder").add_subparsers(
        dest="encoder_command", metavar="<command>", required=True
    )
    fetch_parser = encoder.add_parser(
        "fetch", help="download the model and its tokenizer"
    )
    fetch_parser.add_argument(
        "directory", nargs="?", type=Path, help="default: CRA_ENCODER_PATH"
    )
    fetch_parser.add_argument("--model", help="default: CRA_ENCODER_MODEL")
    fetch_parser.add_argument(
        "--onnx-file",
        default="onnx/model.onnx",
        help="path to the ONNX file in the repository",
    )
    fetch_parser.set_defaults(func=cmd_encoder_fetch)

    token = sub.add_parser("token", help="bearer tokens for the outward MCP endpoint")
    token_sub = token.add_subparsers(
        dest="token_command", metavar="<command>", required=True
    )
    issue = token_sub.add_parser("issue", help="mint a token for an account")
    issue.add_argument("user", help="the account's user id or email address")
    issue.add_argument("--label", required=True, help="what the token is for")
    issue.add_argument(
        "--days", type=int, default=tokens_default_days(), help="how long it is valid"
    )
    issue.set_defaults(func=cmd_token_issue)
    listing = token_sub.add_parser("list", help="list tokens, never their values")
    listing.add_argument(
        "user", nargs="?", default="", help="one account's (id or email); else all"
    )
    listing.set_defaults(func=cmd_token_list)
    revoke = token_sub.add_parser("revoke", help="end a token")
    revoke.add_argument("token_id")
    revoke.set_defaults(func=cmd_token_revoke)

    policy = sub.add_parser("policy", help="operational settings admins may change")
    policy_sub = policy.add_subparsers(
        dest="policy_command", metavar="<command>", required=True
    )
    policy_sub.add_parser(
        "list", help="show every setting and where its value comes from"
    ).set_defaults(func=cmd_policy_list)
    set_ = policy_sub.add_parser(
        "set", help="change a setting, or reset it to the configured default"
    )
    set_.add_argument("key")
    set_.add_argument("value", nargs="?", default=None)
    set_.add_argument("--reset", action="store_true", help="drop the override")
    set_.set_defaults(func=cmd_policy_set)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return EXIT_CONFIG
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
