"""The ``cra`` command. Each subcommand is one ``cmd_*`` function."""

import argparse
import asyncio
import shutil
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
