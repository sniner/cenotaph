"""Finding movie and series folders and their files."""

import re
from dataclasses import dataclass, field
from pathlib import Path

from .probe import VIDEO_SUFFIXES

TMP_SUFFIX = ".cenotaph-tmp"

# NAS housekeeping folders (Synology, QNAP) that are not part of any title.
SKIP_NAMES = frozenset({"@eaDir", "#recycle", "#snapshot", "@Recycle", "lost+found"})

TITLE_RE = re.compile(r".+ \(\d{4}\)(?: |$)")
SEASON_RE = re.compile(r"(?:season|staffel)\s*\d+|specials", re.IGNORECASE)


def is_skipped(path: Path) -> bool:
    name = path.name
    return (
        name.startswith(".")
        or name in SKIP_NAMES
        or name.endswith(TMP_SUFFIX)
        or path.is_symlink()
    )


@dataclass
class Discovery:
    titles: list[Path] = field(default_factory=list)
    # Folders that could not be read, relative to the root, with the reason.
    unreadable: dict[Path, str] = field(default_factory=dict)


def looks_like_title(folder: Path) -> bool:
    """A title folder is named "Title (Year) ...", has season folders, or holds videos.

    The last two catch series named without a year and loose clip folders.
    """
    if TITLE_RE.match(folder.name):
        return True
    for entry in folder.iterdir():
        if is_skipped(entry):
            continue
        if entry.is_dir() and SEASON_RE.fullmatch(entry.name):
            return True
        if entry.is_file() and entry.suffix.lower() in VIDEO_SUFFIXES:
            return True
    return False


def find_titles(root: Path, exclude: frozenset[Path] = frozenset()) -> Discovery:
    """Find the title folders below root; the search does not descend into them.

    Paths in the result and in exclude are relative to root. A folder that cannot be
    read is listed in Discovery.unreadable rather than raising, so one broken folder
    does not stop the run; the caller must not treat titles below it as gone.
    """
    found = Discovery()
    pending = [root]
    while pending:
        folder = pending.pop()
        try:
            entries = sorted(folder.iterdir())
        except OSError as e:
            found.unreadable[folder.relative_to(root)] = e.strerror or str(e)
            continue
        for entry in entries:
            rel = entry.relative_to(root)
            try:
                if rel in exclude or is_skipped(entry) or not entry.is_dir():
                    continue
                is_title = looks_like_title(entry)
            except OSError as e:
                found.unreadable[rel] = e.strerror or str(e)
                continue
            if is_title:
                found.titles.append(rel)
            else:
                pending.append(entry)
    found.titles.sort()
    return found


def is_series(folder: Path) -> bool:
    return any(p.is_dir() and SEASON_RE.fullmatch(p.name) for p in folder.iterdir())


def list_files(folder: Path) -> list[Path]:
    """Return all files below folder, relative to it."""
    files: list[Path] = []
    pending = [folder]
    while pending:
        current = pending.pop()
        for entry in current.iterdir():
            if is_skipped(entry):
                continue
            if entry.is_dir():
                pending.append(entry)
            elif entry.is_file():
                files.append(entry.relative_to(folder))
    return sorted(files)
