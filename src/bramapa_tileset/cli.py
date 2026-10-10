"""``bramapa-tileset`` command line: ``generate`` and ``check``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bramapa-tileset",
        description="Placeholder atlas generator and tiles.toml/atlas.png checker for Bramapa.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="render the placeholder atlas from tiles.toml")
    gen.add_argument("--tiles", default=None, help="path to tiles.toml (default: ./tiles.toml or the repo root)")
    gen.add_argument("--out", default=None, help="output PNG (default: the atlas path declared in tiles.toml)")

    chk = sub.add_parser("check", help="verify that tiles.toml and atlas.png are consistent")
    chk.add_argument("--tiles", default=None, help="path to tiles.toml (default: ./tiles.toml or the repo root)")
    chk.add_argument("--atlas", default=None, help="atlas PNG (default: the one declared in tiles.toml)")
    chk.add_argument("-q", "--quiet", action="store_true", help="only print problems")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "generate":
        from .generate import generate

        out = generate(args.tiles, args.out)
        print(f"wrote {out}")
        return 0

    if args.command == "check":
        from .check import check, format_table

        result = check(args.tiles, args.atlas)
        if args.quiet:
            for w in result.warnings:
                print(f"- WARN {w}")
            for p in result.problems:
                print(f"- {p}")
        else:
            print(format_table(result))
        return 0 if result.ok else 1

    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
