"""The ``cra`` command. Subcommands register themselves here per module."""

import argparse
import sys
from collections.abc import Sequence

from cra import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cra", description="Cluster Research Assistant."
    )
    parser.add_argument("--version", action="version", version=f"cra {__version__}")
    parser.add_subparsers(dest="command", metavar="<command>")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
