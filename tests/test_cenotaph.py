import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from cenotaph import record
from cenotaph.library import find_titles
from cenotaph.probe import Json, ProbeError
from cenotaph.record import MANIFEST, SUMMARY
from cenotaph.run import Refusal, run

MOVIE = Path("Movies/Heat (1995) {imdb-tt0113277}")
SERIES = Path("Series/Firefly (2002)")


class FakeProbe:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, path: Path) -> Json:
        self.calls.append(path.name)
        size = path.stat().st_size
        streams = [{"index": 0, "codec_type": "video", "codec_name": "h264"}]
        streams += [
            {"index": i + 1, "codec_type": "audio", "codec_name": "ac3"}
            for i in range(size % 3)
        ]
        return {"format": {"duration": "60"}, "streams": streams, "chapters": []}


def write(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture
def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(record, "COPY_LIMIT", 100)
    src = tmp_path / "src"
    write(src / MOVIE / "Heat (1995) {imdb-tt0113277} [1080p].mkv", b"v" * 500)
    write(src / MOVIE / "featurettes" / "Making of.mkv", b"v" * 50)
    write(src / MOVIE / "source" / "INFO.md", b"# Heat")
    write(src / MOVIE / "source" / "german.ac3", b"a" * 1000)
    write(src / MOVIE / "notes.md", b"notes")
    write(src / MOVIE / "@eaDir" / "thumb.jpg")
    write(src / MOVIE / ".DS_Store")
    write(src / SERIES / "Season 01" / "Firefly (2002) - S01E01.mkv", b"e" * 300)
    write(src / "Movies" / "@eaDir" / "Junk (2000)" / "x.mkv")
    return src


def manifest(target: Path, rel: Path) -> Json:
    return json.loads((target / rel / MANIFEST).read_text())


def files_of(target: Path, rel: Path) -> dict[str, Json]:
    return {f["path"]: f for f in manifest(target, rel)["files"]}


def test_find_titles_stops_at_title_folders(library: Path) -> None:
    write(library / MOVIE / "other" / "Behind (2001)" / "x.mkv")
    assert find_titles(library).titles == [MOVIE, SERIES]


def test_find_titles_without_year(library: Path) -> None:
    write(library / "Series" / "Dexter" / "Season 1" / "Dexter - S01E01.mkv")
    write(library / "Clips" / "Music" / "Some Song" / "Some Song.mkv")
    write(library / "Clips" / "Music" / "Empty" / "notes.txt")
    titles = find_titles(library).titles
    assert Path("Series/Dexter") in titles
    assert Path("Clips/Music/Some Song") in titles
    assert Path("Clips/Music/Empty") not in titles


def test_excluded_and_unreadable_folders(library: Path, tmp_path: Path) -> None:
    target = tmp_path / "dst"
    run(library, target, probe=FakeProbe())
    (library / "Series").chmod(0)
    try:
        result = run(
            library,
            target,
            probe=FakeProbe(),
            exclude=frozenset({Path("Movies")}),
        )
    finally:
        (library / "Series").chmod(0o755)
    assert result.having("missing") == []
    assert [t.path for t in result.errors] == ["Series"]
    assert "missing_since" not in manifest(target, MOVIE)


def test_misspelled_exclude_is_refused(library: Path, tmp_path: Path) -> None:
    with pytest.raises(Refusal, match="--exclude Movis: no such folder"):
        run(
            library,
            tmp_path / "dst",
            probe=FakeProbe(),
            exclude=frozenset({Path("Movis")}),
        )


def test_first_run_records_and_copies(library: Path, tmp_path: Path) -> None:
    target = tmp_path / "dst"
    result = run(library, target, probe=FakeProbe())

    assert [t.status for t in result.titles] == ["new", "new"]
    files = files_of(target, MOVIE)
    assert set(files) == {
        "Heat (1995) {imdb-tt0113277} [1080p].mkv",
        "featurettes/Making of.mkv",
        "notes.md",
        "source/INFO.md",
        "source/german.ac3",
    }
    assert files["source/german.ac3"]["copied"] is True
    assert files["Heat (1995) {imdb-tt0113277} [1080p].mkv"]["copied"] is False
    assert (target / MOVIE / "source" / "german.ac3").stat().st_size == 1000
    assert (target / MOVIE / "notes.md").exists()
    assert (target / MOVIE / "featurettes" / "Making of.mkv").exists()
    assert not (target / MOVIE / "Heat (1995) {imdb-tt0113277} [1080p].mkv").exists()
    assert "probe" in files["featurettes/Making of.mkv"]
    assert "probe" not in files["notes.md"]
    assert manifest(target, SERIES)["kind"] == "series"
    assert manifest(target, MOVIE)["kind"] == "movie"
    assert (target / MOVIE / SUMMARY).exists()


def test_second_run_skips_unchanged(library: Path, tmp_path: Path) -> None:
    target = tmp_path / "dst"
    run(library, target, probe=FakeProbe())
    before = (target / MOVIE / MANIFEST).stat().st_mtime_ns

    probe = FakeProbe()
    result = run(library, target, probe=probe)

    assert probe.calls == []
    assert not result.has_changes
    assert (target / MOVIE / MANIFEST).stat().st_mtime_ns == before


def test_changed_file_is_probed_again(library: Path, tmp_path: Path) -> None:
    target = tmp_path / "dst"
    run(library, target, probe=FakeProbe())
    write(library / MOVIE / "featurettes" / "Making of.mkv", b"v" * 52)

    probe = FakeProbe()
    result = run(library, target, probe=probe)

    assert probe.calls == ["Making of.mkv"]
    movie = result.titles[0]
    assert movie.status == "changed"
    note = "changed: featurettes/Making of.mkv: size 50 -> 52 bytes, tracks changed"
    assert movie.notes == [f"{note} (3 -> 2)"]
    assert (target / MOVIE / "featurettes" / "Making of.mkv").stat().st_size == 52


def test_removed_file_is_kept_as_missing(library: Path, tmp_path: Path) -> None:
    target = tmp_path / "dst"
    run(library, target, probe=FakeProbe())
    (library / MOVIE / "source" / "german.ac3").unlink()

    result = run(library, target, probe=FakeProbe())

    assert result.titles[0].notes == ["missing: source/german.ac3"]
    entry = files_of(target, MOVIE)["source/german.ac3"]
    assert entry["missing_since"] == result.started.date().isoformat()
    assert (target / MOVIE / "source" / "german.ac3").exists()

    write(library / MOVIE / "source" / "german.ac3", b"a" * 1000)
    result = run(library, target, probe=FakeProbe())
    assert result.titles[0].notes == ["back: source/german.ac3"]
    assert "missing_since" not in files_of(target, MOVIE)["source/german.ac3"]


def test_removed_title_is_marked_missing(library: Path, tmp_path: Path) -> None:
    target = tmp_path / "dst"
    run(library, target, probe=FakeProbe())
    shutil.rmtree(library / SERIES)

    result = run(library, target, probe=FakeProbe())

    assert [t.path for t in result.having("missing")] == [SERIES.as_posix()]
    assert manifest(target, SERIES)["missing_since"]
    assert "Missing from the source" in (target / SERIES / SUMMARY).read_text()

    result = run(library, target, probe=FakeProbe())
    assert result.having("missing") == []


def test_empty_source_is_refused(library: Path, tmp_path: Path) -> None:
    target = tmp_path / "dst"
    run(library, target, probe=FakeProbe())
    shutil.rmtree(library)
    library.mkdir()

    with pytest.raises(Refusal, match="2 of 2 recorded titles are gone"):
        run(library, target, probe=FakeProbe())
    assert "missing_since" not in manifest(target, MOVIE)

    run(library, target, probe=FakeProbe(), allow_missing=True)
    assert manifest(target, MOVIE)["missing_since"]


def test_dry_run_writes_nothing(library: Path, tmp_path: Path) -> None:
    target = tmp_path / "dst"
    result = run(library, target, probe=FakeProbe(), dry_run=True)
    assert len(result.having("new")) == 2
    assert not target.exists()


def test_probe_error_is_reported(library: Path, tmp_path: Path) -> None:
    def broken(path: Path) -> Json:
        raise ProbeError("Invalid data found when processing input")

    result = run(library, tmp_path / "dst", probe=broken)

    assert len(result.errors) == 2
    assert "probe_error" in files_of(tmp_path / "dst", MOVIE)["featurettes/Making of.mkv"]


def test_target_inside_source_is_refused(library: Path) -> None:
    with pytest.raises(Refusal, match="must not contain"):
        run(library, library / "backup", probe=FakeProbe())


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_real_ffprobe(tmp_path: Path) -> None:
    folder = tmp_path / "src" / "Alien (1979)"
    folder.mkdir(parents=True)
    srt = tmp_path / "forced.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:00,500\nHallo\n")
    video = folder / "Alien (1979).mkv"
    subprocess.run(
        [
            "ffmpeg", "-v", "error",
            "-f", "lavfi", "-i", "testsrc=size=64x48:rate=24:duration=1",
            "-f", "lavfi", "-i", "sine=duration=1",
            "-i", str(srt),
            "-map", "0", "-map", "1", "-map", "2",
            "-c:v", "mpeg4", "-c:a", "ac3", "-c:s", "srt",
            "-metadata:s:a:0", "language=ger", "-metadata:s:a:0", "title=Deutsch DD",
            "-metadata:s:s:0", "language=ger", "-disposition:s:0", "forced",
            str(video),
        ],
        check=True,
    )  # fmt: skip
    os.utime(video, ns=(0, 1_000_000_000))

    run(tmp_path / "src", tmp_path / "dst")

    entry = files_of(tmp_path / "dst", Path("Alien (1979)"))["Alien (1979).mkv"]
    types = [s["codec_type"] for s in entry["probe"]["streams"]]
    assert types == ["video", "audio", "subtitle"]
    assert "filename" not in entry["probe"]["format"]
    summary = (tmp_path / "dst" / "Alien (1979)" / SUMMARY).read_text()
    assert "| 1 | audio | ac3 |" in summary
    assert "Deutsch DD" in summary
    assert "forced" in summary
    assert "64x48, 24 fps" in summary
