"""The ``cra`` command. Each subcommand is one ``cmd_*`` function."""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from cra import __version__
from cra.config.settings import Settings, unknown_keys

EXIT_CONFIG = 2


def load_settings(args: argparse.Namespace) -> Settings:
    """Settings for a subcommand; validation problems end the process with exit 2."""
    unknown = unknown_keys(args.env_file)
    if unknown:
        sys.stderr.write("unknown configuration keys: " + ", ".join(unknown) + "\n")
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

    from cra.app.web.factory import create_app
    from cra.logsetup import configure_logging

    settings = load_settings(args)
    configure_logging(settings.log_dir)
    config = Config()
    config.bind = [f"{settings.host}:{settings.port}"]
    config.accesslog = None
    asyncio.run(serve(create_app(settings), config))  # type: ignore[arg-type]
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
                f"email  {email.email:40} user={email.user_id or '-'}  by {email.created_by}"
            )
        for user in await repo.list_users():
            state = "active" if user.is_active else "disabled"
            identities = ", ".join(
                f"{i.issuer}:{i.sub[:12]}" for i in await repo.list_identities(user.id)
            )
            _out(f"user   {user.id:20} {user.display_name:30} {state:8} {identities}")
        await engine.dispose()

    asyncio.run(run())
    return 0


def cmd_users_add_email(args: argparse.Namespace) -> int:
    engine, repo = _repo(load_settings(args))

    async def run() -> int:
        if await repo.get_registered_email(args.email) is not None:
            sys.stderr.write("already registered\n")
            return 1
        await repo.add_registered_email(args.email, args.by)
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


def cmd_users_set_active(args: argparse.Namespace, active: bool) -> int:
    engine, repo = _repo(load_settings(args))

    async def run() -> int:
        changed = await repo.set_user_active(args.user_id, active)
        await engine.dispose()
        if not changed:
            sys.stderr.write("no such user\n")
        return 0 if changed else 1

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

    users = sub.add_parser("users", help="users and the registered-email allow-list")
    users_sub = users.add_subparsers(
        dest="users_command", metavar="<command>", required=True
    )
    users_sub.add_parser(
        "list", help="registered emails, users and identities"
    ).set_defaults(func=cmd_users_list)
    add = users_sub.add_parser("add-email", help="allow an email address to sign in")
    add.add_argument("email")
    add.add_argument("--by", default="cli", help="who registered it (for the record)")
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
