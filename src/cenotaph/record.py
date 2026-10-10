"""Recording one title folder: file list, tracks and copies."""

import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .library import TMP_SUFFIX, is_series, list_files
from .probe import VIDEO_SUFFIXES, Json, Probe, ProbeError
from .summary import render_summary, stream_flags, tag

# Files below this size are copied to TARGET, everything in source/ regardless of size.
COPY_LIMIT = 50 * 1024 * 1024


FORMAT = 1
MANIFEST = "cenotaph.json"
SUMMARY = "cenotaph.md"
RUNS_DIR = "runs"
SOURCE_DIR = "source"


log = logging.getLogger(__name__)


class ManifestError(Exception):
    """A manifest in TARGET cannot be used."""


def load_manifest(path: Path) -> Json | None:
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except ValueError as e:
        raise ManifestError(
            f"{path}: unreadable record ({e}), move it away to rebuild it"
        ) from e
    if not isinstance(data, dict) or data.get("format", 0) > FORMAT:
        raise ManifestError(f"{path}: written by a newer cenotaph, upgrade this script")
    return data


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + TMP_SUFFIX)
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def write_if_changed(path: Path, text: str) -> None:
    """Write text unless the file already holds it; a newer cenotaph may render the
    overview differently from the same record."""
    try:
        if path.read_text(encoding="utf-8") == text:
            return
    except FileNotFoundError:
        pass
    write_text(path, text)


def dump_manifest(manifest: Json) -> str:
    return json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


@dataclass
class Context:
    source: Path
    target: Path
    probe: Probe
    today: str
    dry_run: bool = False


@dataclass
class TitleResult:
    path: str
    status: str = "unchanged"  # new, changed, unchanged, missing, error
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def wants_copy(rel_file: Path, size: int) -> bool:
    if len(rel_file.parts) == 1 and rel_file.name in (MANIFEST, SUMMARY):
        return False
    return SOURCE_DIR in rel_file.parts[:-1] or size < COPY_LIMIT


def is_current_copy(src_size: int, src_mtime_ns: int, dst: Path) -> bool:
    try:
        st = dst.stat()
    except FileNotFoundError:
        return False
    # Some target filesystems (FAT, SMB) store modification times coarser than 1 ns.
    return st.st_size == src_size and abs(st.st_mtime_ns - src_mtime_ns) < 2_000_000_000


def copy_file(
    ctx: Context, src: Path, dst: Path, entry: Json, result: TitleResult
) -> bool:
    if not wants_copy(Path(entry["path"]), entry["size"]):
        return False
    if ctx.dry_run or is_current_copy(entry["size"], entry["mtime_ns"], dst):
        return True
    tmp = dst.with_name(dst.name + TMP_SUFFIX)
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, tmp)
        tmp.replace(dst)
    except OSError as e:
        result.errors.append(f"{entry['path']}: cannot copy: {e}")
        return False
    return True


def track_list(probe: Json | None) -> list[tuple[str, ...]]:
    """Reduce a probe to what identifies its tracks, for change detection."""
    if not probe:
        return []
    return [
        (
            str(s.get("codec_type", "")),
            str(s.get("codec_name", "")),
            str(tag(s, "language")),
            str(tag(s, "title")),
            ",".join(stream_flags(s)),
        )
        for s in probe.get("streams", [])
    ]


def describe_change(old: Json, new: Json) -> str:
    parts: list[str] = []
    if old["size"] != new["size"]:
        parts.append(f"size {old['size']:,} -> {new['size']:,} bytes")
    old_tracks, new_tracks = track_list(old.get("probe")), track_list(new.get("probe"))
    if old_tracks != new_tracks:
        parts.append(f"tracks changed ({len(old_tracks)} -> {len(new_tracks)})")
    if not parts:
        parts.append("modified, same size and tracks")
    return f"changed: {new['path']}: {', '.join(parts)}"


def record_file(
    ctx: Context,
    title_dir: Path,
    rel_file: Path,
    old: Json | None,
    result: TitleResult,
) -> Json:
    src = title_dir / rel_file
    st = src.stat()
    entry: Json = {
        "path": rel_file.as_posix(),
        "size": st.st_size,
        "mtime_ns": st.st_mtime_ns,
    }
    same = old is not None and (old["size"], old["mtime_ns"]) == (
        st.st_size,
        st.st_mtime_ns,
    )
    if src.suffix.lower() in VIDEO_SUFFIXES:
        if same and old is not None and old.get("probe") is not None:
            entry["probe"] = old["probe"]
        else:
            log.debug("reading the tracks of %s", src)
            try:
                entry["probe"] = ctx.probe(src)
            except ProbeError as e:
                entry["probe_error"] = str(e)
                result.errors.append(f"{entry['path']}: cannot read the tracks: {e}")
    dst = ctx.target / Path(result.path) / rel_file
    entry["copied"] = copy_file(ctx, src, dst, entry, result)

    if old is None:
        result.notes.append(f"added: {entry['path']}")
    elif old.get("missing_since"):
        result.notes.append(f"back: {entry['path']}")
    elif not same:
        result.notes.append(describe_change(old, entry))
    return entry


def keep_missing(
    old_files: dict[str, Json], present: set[str], ctx: Context, result: TitleResult
) -> list[Json]:
    """Carry forward the entries of files that are gone, marked as missing."""
    kept: list[Json] = []
    for path, old in old_files.items():
        if path in present:
            continue
        if not old.get("missing_since"):
            old = {**old, "missing_since": ctx.today}
            result.notes.append(f"missing: {path}")
        kept.append(old)
    return kept


def record_title(ctx: Context, rel: Path) -> TitleResult:
    """Bring the record of one title folder in TARGET up to date."""
    result = TitleResult(rel.as_posix())
    src_dir, dst_dir = ctx.source / rel, ctx.target / rel
    try:
        old = load_manifest(dst_dir / MANIFEST)
        kind = "series" if is_series(src_dir) else "movie"
        old_files = {f["path"]: f for f in old["files"]} if old else {}
        entries = [
            record_file(ctx, src_dir, f, old_files.get(f.as_posix()), result)
            for f in list_files(src_dir)
        ]
    except (OSError, ManifestError) as e:
        result.status = "error"
        result.errors.append(str(e))
        return result
    entries += keep_missing(old_files, {e["path"] for e in entries}, ctx, result)
    manifest: Json = {
        "format": FORMAT,
        "path": rel.as_posix(),
        "kind": kind,
        "files": sorted(entries, key=lambda e: e["path"]),
    }
    if old is None:
        result.status = "new"
        result.notes.clear()
    elif manifest != old:
        result.status = "changed"
        if old.get("missing_since"):
            result.notes.insert(0, "back in the source")
    if not ctx.dry_run:
        if old != manifest:
            write_text(dst_dir / MANIFEST, dump_manifest(manifest))
        write_if_changed(dst_dir / SUMMARY, render_summary(manifest))
    return result


def mark_missing(ctx: Context, rel: Path) -> TitleResult:
    result = TitleResult(rel.as_posix(), status="missing")
    dst_dir = ctx.target / rel
    try:
        manifest = load_manifest(dst_dir / MANIFEST)
    except ManifestError as e:
        result.status = "error"
        result.errors.append(str(e))
        return result
    if manifest is None or manifest.get("missing_since"):
        return result
    manifest["missing_since"] = ctx.today
    if not ctx.dry_run:
        write_text(dst_dir / MANIFEST, dump_manifest(manifest))
        write_text(dst_dir / SUMMARY, render_summary(manifest))
    return result
