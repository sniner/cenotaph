"""The readable overview (cenotaph.md) of a title record."""

import re
from pathlib import Path

from .probe import Json

GENERIC_CHAPTER_RE = re.compile(
    r"(?:chapter|kapitel)?\s*\d+|\d\d:\d\d:\d\d.*", re.IGNORECASE
)
BIT_DEPTH_RE = re.compile(r"p(\d+)(?:le|be)$")


def tag(stream: Json, name: str) -> str:
    """Return a tag value; mkvmerge writes statistics tags as e.g. BPS-eng."""
    tags = stream.get("tags", {})
    for key, value in tags.items():
        if key.lower() == name.lower() or key.lower().startswith(name.lower() + "-"):
            return str(value)
    return ""


def stream_flags(stream: Json) -> list[str]:
    names = {
        "default": "default",
        "forced": "forced",
        "comment": "commentary",
        "hearing_impaired": "hearing impaired",
        "visual_impaired": "visually impaired",
        "original": "original",
        "dub": "dub",
        "attached_pic": "cover image",
    }
    disp = stream.get("disposition", {})
    return [label for key, label in names.items() if disp.get(key)]


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1000:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000
    return f"{value:.1f} TB"


def timecode(seconds: str | float | None) -> str:
    try:
        total = round(float(seconds or 0))
    except ValueError:
        return "?"
    return f"{total // 3600}:{total % 3600 // 60:02}:{total % 60:02}"


def frame_rate(rate: str) -> str:
    num, _, den = rate.partition("/")
    try:
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return ""
    return f"{fps:.3f}".rstrip("0").rstrip(".") + " fps" if fps else ""


def bitrate(stream: Json) -> str:
    try:
        kbps = int(stream.get("bit_rate") or tag(stream, "BPS") or 0) // 1000
    except ValueError:
        return ""
    return f"{kbps:,} kb/s" if kbps else ""


def video_details(s: Json) -> list[str]:
    parts = [
        f"{s.get('width')}x{s.get('height')}",
        frame_rate(s.get("avg_frame_rate", "")),
    ]
    if m := BIT_DEPTH_RE.search(s.get("pix_fmt", "")):
        parts.append(f"{m.group(1)} bit")
    transfer = s.get("color_transfer", "")
    if transfer == "smpte2084":
        parts.append("HDR10 (PQ)")
    elif transfer == "arib-std-b67":
        parts.append("HLG")
    if any("DOVI" in d.get("side_data_type", "") for d in s.get("side_data_list", [])):
        parts.append("Dolby Vision")
    if s.get("field_order", "progressive") not in ("progressive", "unknown"):
        parts.append(f"interlaced ({s['field_order']})")
    return parts


def audio_details(s: Json) -> list[str]:
    layout = s.get("channel_layout") or f"{s.get('channels', '?')} ch"
    rate = s.get("sample_rate")
    return [layout, f"{int(rate) / 1000:g} kHz" if rate else ""]


def stream_details(s: Json) -> str:
    kind = s.get("codec_type")
    if kind == "attachment":
        return f"{tag(s, 'filename')} ({tag(s, 'mimetype')})"
    parts = [s.get("profile", "")] if kind in ("video", "audio") else []
    if kind == "video":
        parts += video_details(s)
    elif kind == "audio":
        parts += audio_details(s)
    parts.append(bitrate(s))
    if kind == "subtitle" and (frames := tag(s, "NUMBER_OF_FRAMES")):
        parts.append(f"{frames} events")
    return ", ".join(p for p in parts if p and p != "unknown")


def cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_probe(entry: Json) -> list[str]:
    lines = [f"### {entry['path']}", ""]
    if "probe_error" in entry:
        return [*lines, f"Tracks could not be read: {entry['probe_error']}", ""]
    probe = entry.get("probe") or {}
    fmt = probe.get("format", {})
    facts = [timecode(fmt.get("duration")), human_size(entry["size"])]
    facts += [f"written by {w}" for w in (tag(fmt, "encoder"),) if w]
    facts += [f'title tag "{t}"' for t in (tag(fmt, "title"),) if t]
    lines += [", ".join(facts), ""]
    lines += [
        "| # | Type | Codec | Details | Language | Title | Flags |",
        "|---|------|-------|---------|----------|-------|-------|",
    ]
    for s in probe.get("streams", []):
        row = [
            str(s.get("index", "")),
            s.get("codec_type", ""),
            s.get("codec_name", ""),
            stream_details(s),
            tag(s, "language"),
            tag(s, "title"),
            ", ".join(stream_flags(s)),
        ]
        lines.append("| " + " | ".join(cell(c) for c in row) + " |")
    lines.append("")
    return lines + render_chapters(probe.get("chapters", []))


def render_chapters(chapters: list[Json]) -> list[str]:
    if not chapters:
        return []
    titles = [tag(c, "title") for c in chapters]
    if all(not t or GENERIC_CHAPTER_RE.fullmatch(t) for t in titles):
        return [f"{len(chapters)} chapters without names.", ""]
    lines = [f"{len(chapters)} chapters:", ""]
    lines += [
        f"- {timecode(c.get('start_time'))} {t}"
        for c, t in zip(chapters, titles, strict=True)
    ]
    return [*lines, ""]


def render_summary(manifest: Json) -> str:
    """Return the readable overview of a title record."""
    lines = [f"# {Path(manifest['path']).name}", ""]
    lines += [
        f"{manifest['kind'].capitalize()}, recorded by cenotaph from `{manifest['path']}`.",
        "This file is rewritten on every run.",
        "",
    ]
    if manifest.get("missing_since"):
        lines += [f"**Missing from the source since {manifest['missing_since']}.**", ""]
    lines += [
        "## Files",
        "",
        "| File | Size | Bytes | Copy |",
        "|------|-----:|------:|------|",
    ]
    for e in manifest["files"]:
        name = e["path"] + (
            f" (missing since {e['missing_since']})" if e.get("missing_since") else ""
        )
        copy = "yes" if e.get("copied") else ""
        lines.append(f"| {cell(name)} | {human_size(e['size'])} | {e['size']} | {copy} |")
    lines.append("")
    videos = [e for e in manifest["files"] if "probe" in e or "probe_error" in e]
    if videos:
        lines += ["## Tracks", ""]
        for e in videos:
            lines += render_probe(e)
    return "\n".join(lines).rstrip() + "\n"
