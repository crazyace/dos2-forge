"""``dos2forge`` command-line entry point.

The CLI is intentionally thin: every subcommand is a few lines of glue
around the library so that other projects can do the same things by
importing :mod:`dos2forge` directly.
"""

from __future__ import annotations

import argparse
import fnmatch
import sys
from pathlib import Path

from .. import __version__
from ..locate import find_data_dir, find_game
from ..pak.extractor import Extractor
from ..pak.reader import PakReader, file_is_lspk


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dos2forge",
        description="Extract, parse, and convert Divinity: Original Sin 2 game data.",
    )
    parser.add_argument("--version", action="version", version=f"dos2forge {__version__}")
    parser.add_argument(
        "--game-path",
        type=Path,
        help="game install root (default: auto-detect / $DOS2_PATH)",
    )
    parser.add_argument(
        "--data-dir", type=Path, help="directory containing .pak archives directly"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("list", help="list the contents of a .pak archive")
    listing.add_argument("pak", type=Path)

    cat = sub.add_parser("cat", help="print one archived file to stdout")
    cat.add_argument("pak", type=Path)
    cat.add_argument("name", help="archived path, e.g. Public/Shared/Stats/…/Weapon.txt")

    unpack = sub.add_parser("unpack", help="extract .pak archives incrementally")
    unpack.add_argument("pak", type=Path, nargs="?", help="a single .pak (default: all game paks)")
    unpack.add_argument("-o", "--output", type=Path, default=Path("extracted"))
    unpack.add_argument("-p", "--pattern", action="append", help="glob filter, repeatable")
    unpack.add_argument("--force", action="store_true", help="rewrite unchanged files too")

    search = sub.add_parser(
        "search", help="search archived file paths across all game paks (index-only, fast)"
    )
    search.add_argument(
        "pattern",
        help="case-insensitive substring, or a glob when it contains * or ? "
        '(e.g. "*/RootTemplates/*.lsf")',
    )
    search.add_argument("--limit", type=int, default=50, help="matches to print (default: 50)")

    convert = sub.add_parser("convert", help="convert between .lsf and .lsx resources")
    convert.add_argument("input", type=Path, help="source resource (.lsf, .lsx, or .lsj)")
    convert.add_argument("output", type=Path, help="target file; extension picks the format")
    convert.add_argument(
        "--lsf-version", type=int, default=3, choices=(1, 2, 3, 5, 6, 7),
        help="LSF version to write (default: 3, DOS2 DE native)",
    )

    sub.add_parser("doctor", help="diagnose the installation and environment")
    return parser


def _data_dir(args) -> Path | None:
    if args.data_dir is not None:
        return args.data_dir
    game = find_game(args.game_path)
    return find_data_dir(game) if game else None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _dispatch(args)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _dispatch(args) -> int:
    if args.command == "list":
        with PakReader(args.pak) as pak:
            for entry in pak:
                print(f"{entry.size:>12}  {entry.name}")
        return 0

    if args.command == "cat":
        with PakReader(args.pak) as pak:
            sys.stdout.buffer.write(pak.read(args.name))
        return 0

    if args.command == "unpack":
        extractor = Extractor(args.output)
        if args.pak is not None:
            paks = [args.pak]
        else:
            data_dir = _data_dir(args)
            if data_dir is None:
                print("error: unpack needs a game install or --data-dir", file=sys.stderr)
                return 1
            paks = sorted(data_dir.rglob("*.pak"))
        total_extracted = total_skipped = failures = 0
        for pak_path in paks:
            try:
                result = extractor.extract(pak_path, patterns=args.pattern, force=args.force)
            except ValueError as exc:
                # Files without the LSPK signature are secondary archive
                # parts or foreign files — skipping them is routine.  A
                # real archive that fails (unsafe entry path, truncated
                # data) must be reported, not silently dropped.
                if file_is_lspk(pak_path):
                    failures += 1
                    print(f"error: {pak_path.name}: {exc}", file=sys.stderr)
                continue
            total_extracted += len(result.extracted)
            total_skipped += len(result.skipped)
            if result.total:  # keep quiet about paks with nothing matching
                print(f"{pak_path.name}: {len(result.extracted)} extracted, "
                      f"{len(result.skipped)} unchanged")
        print(f"done: {total_extracted} extracted, {total_skipped} unchanged")
        return 1 if failures else 0

    if args.command == "search":
        data_dir = _data_dir(args)
        if data_dir is None:
            print("error: search needs a game install or --data-dir", file=sys.stderr)
            return 1
        pattern = args.pattern.lower()
        is_glob = any(ch in pattern for ch in "*?[")
        matches: list[tuple[str, str]] = []
        for pak_path in sorted(data_dir.rglob("*.pak")):
            if not file_is_lspk(pak_path):
                continue
            try:
                with PakReader(pak_path) as pak:
                    for name in pak.names():
                        lowered = name.lower()
                        hit = (
                            fnmatch.fnmatch(lowered, pattern)
                            if is_glob else pattern in lowered
                        )
                        if hit:
                            matches.append((pak_path.name, name))
            except ValueError as exc:
                print(f"warning: {pak_path.name}: {exc}", file=sys.stderr)
        for source, name in matches[: args.limit]:
            print(f"{source}  {name}")
        shown = min(len(matches), args.limit)
        summary = f"{len(matches)} match(es)"
        if len(matches) > shown:
            summary += f", first {shown} shown"
        print(summary)
        return 0

    if args.command == "convert":
        from ..parsers.lsf import write_lsf
        from ..parsers.lsx import write_lsx
        from ..parsers.resource import load_resource

        document = load_resource(args.input)
        suffix = args.output.suffix.lower()
        if suffix == ".lsx":
            args.output.write_text(write_lsx(document), "utf-8")
        elif suffix == ".lsf":
            args.output.write_bytes(write_lsf(document, version=args.lsf_version))
        else:
            print(f"error: unsupported output format {suffix!r}", file=sys.stderr)
            return 1
        print(f"converted {args.input} -> {args.output}")
        return 0

    if args.command == "doctor":
        from ..doctor import format_report, run_doctor

        report = run_doctor(path=args.game_path, data_dir=args.data_dir)
        print(format_report(report))
        return 0 if report.ok else 1

    raise AssertionError(f"unhandled command {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
