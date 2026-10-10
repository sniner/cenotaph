"""Command line interface."""

import argparse
import logging
import shutil
import sys
from pathlib import Path

from . import __version__
from .record import write_text
from .run import RUNS_DIR, Refusal, render_report, run, verdict

log = logging.getLogger("cenotaph")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="cenotaph",
        description=(
            "Record file lists, track layouts and small files of every movie and series "
            "folder in SOURCE to TARGET, so a lost title can be ripped and muxed again. "
            "Repeated runs only process what has changed and keep everything that has "
            "disappeared from SOURCE."
        ),
    )
    parser.add_argument("source", type=Path, metavar="SOURCE", help="library to record")
    parser.add_argument(
        "target", type=Path, metavar="TARGET", help="where the records go"
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="print the report of what would change, write nothing",
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=4,
        help="titles processed in parallel (default: 4)",
    )
    parser.add_argument(
        "-x",
        "--exclude",
        type=Path,
        action="append",
        default=[],
        metavar="DIR",
        help=(
            "skip this folder of SOURCE (relative to it); may be repeated; "
            "the first run into TARGET saves these in TARGET/cenotaph.toml"
        ),
    )
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="accept any number of titles that are gone from SOURCE",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="log every probed file"
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="log only problems")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    level = (
        logging.DEBUG if args.verbose else logging.WARNING if args.quiet else logging.INFO
    )
    logging.basicConfig(level=level, format="%(message)s", stream=sys.stderr)
    if shutil.which("ffprobe") is None:
        log.error("ffprobe not found: install ffmpeg (apt/pacman/brew install ffmpeg)")
        return 2
    try:
        result = run(
            args.source,
            args.target,
            jobs=max(1, args.jobs),
            dry_run=args.dry_run,
            allow_missing=args.allow_missing,
            exclude=frozenset(args.exclude),
        )
    except Refusal as e:
        log.error("%s", e)
        return 2

    report = render_report(result, args.source)
    if args.dry_run:
        print(report, end="")
        print(f"dry run, nothing written: {verdict(result)}")
    elif result.has_changes or result.errors:
        path = args.target / RUNS_DIR / f"{result.started:%Y-%m-%dT%H%M%S}.md"
        write_text(path, report)
        print(f"{verdict(result)}; report in {path}")
    else:
        print(f"{verdict(result)}; nothing has changed")
    return 1 if result.errors else 0
