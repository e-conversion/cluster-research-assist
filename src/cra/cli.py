"""The ``cra`` command. Each subcommand is one ``cmd_*`` function."""

import argparse
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


def cmd_check_config(args: argparse.Namespace) -> int:
    settings = load_settings(args)
    for key, value in settings.dump().items():
        sys.stdout.write(f"{key}={value}\n")
    return 0


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
