"""A complete run over a library: safety checks, recording, report."""

import concurrent.futures
import datetime
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from .config import CONFIG, Config, ConfigError, load_config, write_config
from .library import find_titles
from .probe import Probe, ffprobe
from .record import (
    MANIFEST,
    RUNS_DIR,
    Context,
    ManifestError,
    TitleResult,
    load_manifest,
    mark_missing,
    record_title,
)

# A run refuses to mark more titles as missing than this share of the known titles
# (but always allows MISSING_MIN), so an unmounted or half-readable share does not
# get recorded as a library that has lost most of its content.
MISSING_SHARE = 0.05
MISSING_MIN = 3


log = logging.getLogger(__name__)


class Refusal(Exception):
    """The run cannot start; the message says why and what to do."""


def find_manifests(target: Path) -> list[Path]:
    """Return the title folders recorded in target, relative to it."""
    if not target.is_dir():
        return []
    return sorted(
        p.parent.relative_to(target)
        for p in target.rglob(MANIFEST)
        if p.relative_to(target).parts[0] != RUNS_DIR
    )


@dataclass
class RunResult:
    titles: list[TitleResult]
    started: datetime.datetime

    def having(self, status: str) -> list[TitleResult]:
        return [t for t in self.titles if t.status == status]

    @property
    def errors(self) -> list[TitleResult]:
        return [t for t in self.titles if t.errors]

    @property
    def has_changes(self) -> bool:
        return any(t.status != "unchanged" for t in self.titles)


def check_paths(source: Path, target: Path) -> None:
    if not source.is_dir():
        raise Refusal(f"{source}: no such directory, check that the share is mounted")
    if not target.exists() and not target.parent.is_dir():
        raise Refusal(f"{target}: its parent directory does not exist, create it first")
    src, dst = source.resolve(), target.resolve()
    if dst.is_relative_to(src) or src.is_relative_to(dst):
        raise Refusal("SOURCE and TARGET must not contain each other")


def check_missing(
    found: list[Path], known: list[Path], newly_missing: list[Path]
) -> None:
    if not newly_missing:
        return
    limit = max(MISSING_MIN, int(len(known) * MISSING_SHARE))
    if not found or len(newly_missing) > limit:
        raise Refusal(
            f"{len(newly_missing)} of {len(known)} recorded titles are gone from the "
            f"source (at most {limit} are accepted per run); check that the share is "
            "complete, or pass --allow-missing if they were really removed",
        )


def newly_missing_titles(target: Path, gone: list[Path]) -> list[Path]:
    result: list[Path] = []
    for rel in gone:
        try:
            manifest = load_manifest(target / rel / MANIFEST)
        except ManifestError:
            continue
        if manifest is not None and not manifest.get("missing_since"):
            result.append(rel)
    return result


def record_all(ctx: Context, titles: list[Path], jobs: int) -> list[TitleResult]:
    results: dict[Path, TitleResult] = {}
    last_report = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(record_title, ctx, rel): rel for rel in titles}
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            results[futures[future]] = res
            for err in res.errors:
                log.warning("%s: %s", res.path, err)
            if res.status in ("new", "changed"):
                log.info("%s: %s", res.status, res.path)
            if time.monotonic() - last_report > 10:
                log.info("recording titles: %d of %d done", len(results), len(titles))
                last_report = time.monotonic()
    return [results[rel] for rel in titles]


def resolve_exclude(
    source: Path,
    target: Path,
    exclude: frozenset[Path],
    dry_run: bool,
) -> frozenset[Path]:
    """Combine --exclude with the target's configuration.

    A target without a configuration gets one holding the folders of --exclude, so
    later runs need no options.
    """
    for rel in sorted(exclude):
        if not (source / rel).is_dir():
            raise Refusal(f"--exclude {rel}: no such folder in the source")
    try:
        config = load_config(target)
    except ConfigError as e:
        raise Refusal(str(e)) from e
    if config is None:
        if not dry_run:
            target.mkdir(exist_ok=True)
            write_config(target, Config(exclude))
            log.info("saved the settings of this run to %s", target / CONFIG)
    else:
        for rel in sorted(config.exclude):
            if not (source / rel).is_dir():
                raise Refusal(
                    f"{rel}: no such folder in the source, "
                    f"remove it from exclude in {target / CONFIG}",
                )
        exclude = exclude | config.exclude
    if exclude:
        log.info("skipping %s", ", ".join(p.as_posix() for p in sorted(exclude)))
    return exclude


def run(
    source: Path,
    target: Path,
    *,
    probe: Probe = ffprobe,
    jobs: int = 4,
    dry_run: bool = False,
    allow_missing: bool = False,
    exclude: frozenset[Path] = frozenset(),
) -> RunResult:
    """Bring the records in target up to date with source."""
    started = datetime.datetime.now()
    check_paths(source, target)
    exclude = resolve_exclude(source, target, exclude, dry_run)
    ctx = Context(source, target, probe, started.date().isoformat(), dry_run)

    log.info("step 1 of 2: looking for movie and series folders in %s", source)
    discovery = find_titles(source, exclude)
    found = discovery.titles
    unseen = [*discovery.unreadable, *exclude]
    for rel, reason in discovery.unreadable.items():
        log.warning(
            "%s: cannot read this folder (%s), its titles are left as they are",
            rel,
            reason,
        )
    known = find_manifests(target)
    found_set = set(found)
    gone = [
        rel
        for rel in known
        if rel not in found_set and not any(rel.is_relative_to(u) for u in unseen)
    ]
    newly_missing = newly_missing_titles(target, gone)
    if not allow_missing:
        check_missing(found, known, newly_missing)

    log.info("step 2 of 2: recording %s in %s", titles_count(len(found)), target)
    if not known:
        log.info(
            "no earlier records in the target: every video gets probed, which takes a while"
        )
    titles = record_all(ctx, found, jobs)
    titles += [mark_missing(ctx, rel) for rel in newly_missing]
    titles += [
        TitleResult(
            rel.as_posix(), "error", errors=[f"cannot read this folder: {reason}"]
        )
        for rel, reason in discovery.unreadable.items()
    ]
    return RunResult(titles, started)


def render_report(result: RunResult, source: Path) -> str:
    lines = [
        f"# cenotaph run {result.started:%Y-%m-%d %H:%M}",
        "",
        f"Source: `{source}`",
        "",
    ]
    sections = [
        ("New titles", result.having("new"), False),
        ("Changed titles", result.having("changed"), True),
        ("Titles missing from the source", result.having("missing"), False),
    ]
    for heading, titles, with_notes in sections:
        if not titles:
            continue
        lines += [f"## {heading} ({len(titles)})", ""]
        for t in titles:
            lines.append(f"- {t.path}")
            if with_notes:
                lines += [f"  - {n}" for n in t.notes]
        lines.append("")
    if errors := result.errors:
        lines += [f"## Errors ({len(errors)})", ""]
        for t in errors:
            lines.append(f"- {t.path}")
            lines += [f"  - {e}" for e in t.errors]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def titles_count(n: int) -> str:
    return f"{n:,} title" if n == 1 else f"{n:,} titles"


def verdict(result: RunResult) -> str:
    counts = [
        f"{titles_count(len(result.titles) - len(result.having('missing')) - len(result.having('error')))} recorded",
        f"{len(result.having('new'))} new",
        f"{len(result.having('changed'))} changed",
        f"{len(result.having('missing'))} newly missing",
    ]
    text = ", ".join(counts)
    if result.errors:
        text += f"; {len(result.errors)} with errors, see the report"
    return text
