"""Reading the tracks of a video file with ffprobe."""

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Parsed JSON (manifests, ffprobe output) is dynamically shaped by nature.
Json = dict[str, Any]

VIDEO_SUFFIXES = frozenset(
    {".mkv", ".m4v", ".mp4", ".avi", ".ts", ".m2ts", ".mpg", ".vob", ".webm"},
)


class ProbeError(Exception):
    """A video file could not be read by ffprobe."""


Probe = Callable[[Path], Json]


def ffprobe(path: Path) -> Json:
    """Return container, stream and chapter information of a video file."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "-show_chapters",
        str(path),
    ]
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as e:
        raise ProbeError(str(e)) from e
    if res.returncode != 0:
        raise ProbeError(res.stderr.strip() or f"ffprobe exited with {res.returncode}")
    try:
        data = json.loads(res.stdout)
    except ValueError as e:
        raise ProbeError(f"unreadable ffprobe output: {e}") from e
    # The absolute path depends on where the share is mounted, not on the file.
    data.get("format", {}).pop("filename", None)
    return data
