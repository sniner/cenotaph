# cenotaph

> [!NOTE]
> This is a private tool and does not get the same polish as my other projects.
> You are welcome to use it, but it is written for my own requirements first.

Records what is needed to rebuild a movie and series library after losing it,
without copying the videos themselves: the file list of every title, the track
layout of every video, and every small file that took work to make.

The record is meant for the day a disk fails and there is no backup of the
videos: it shows which files a title had, which disc release they came from
(MakeMKV version, resolution, HDR), which audio and subtitle tracks were muxed in
with which names, languages and flags, and it keeps the working material in
`source/` that went into them.

## Installation

```
uv tool install /path/to/cenotaph
```

cenotaph needs Python 3.11 or later and runs on Linux and macOS. `ffprobe` must
be on the PATH (`pacman -S ffmpeg`, `apt install ffmpeg`, `brew install ffmpeg`).

## Usage

```
cenotaph [-n] [-j N] [-x DIR]... [--allow-missing] [-v | -q] SOURCE TARGET
```

- `-n`, `--dry-run`: print the report of what would change, write nothing
- `-j N`, `--jobs N`: titles processed in parallel (default 4)
- `-x DIR`, `--exclude DIR`: skip this folder of SOURCE, given relative to it;
  may be repeated (see [Settings](#settings))
- `--allow-missing`: accept any number of titles that are gone from SOURCE
- `-v` logs every probed file, `-q` only problems

SOURCE is only read, never written to. Example:

```
cenotaph -x Jellyfin -x MakeMKV /mnt/nas/filmothek /mnt/backup/cenotaph
```

## Settings

The first run into a TARGET creates `TARGET/cenotaph.toml` with the folders given
by `--exclude`:

```toml
# Folders of the source that are not recorded, relative to the source.
exclude = ["Jellyfin", "MakeMKV"]
```

Later runs read it, so `cenotaph /mnt/nas/filmothek /mnt/backup/cenotaph` is
enough. To change the list for good, edit the file. `--exclude` on a later run
adds folders for that run only.

A run stops if the file has an unknown setting or names a folder that does not
exist in SOURCE.

Exit code 0 means everything was recorded, 1 that some files or folders could not
be read or copied (listed in the report), 2 that the run did not start.

## Which folders are titles

cenotaph searches SOURCE at any depth, so SOURCE can be a single library or a
share holding several. A folder is a title if

- its name has the form `Title (Year) ...`,
- it has season folders (`Season 1`, `Season 01`, `Staffel 1`, `Specials`), or
- it holds a video file directly.

The search does not descend into a title. A title with season folders is
recorded as a series, any other as a movie.

Skipped are hidden files, NAS housekeeping folders (`@eaDir`, `#recycle`, ...)
and the folders given with `--exclude`. Use `--exclude` for folders that are not
part of the library, for example a mirror made by jellyplex-sync or a folder of
unfinished rips.

## What is recorded

For each title cenotaph writes to the same relative path below TARGET:

| File | Content |
|------|---------|
| `cenotaph.json` | every file with its path, size in bytes and modification time; for each video the complete ffprobe output (streams, chapters, container tags) |
| `cenotaph.md` | the same as a readable overview: file table, one track table per video, chapter names |
| `source/` | a complete copy, regardless of size |
| other files | a copy of every file smaller than 50 MB: notes, subtitles, posters, short bonus clips |

The track table in `cenotaph.md` shows for each track the codec, resolution,
frame rate, bit depth, HDR format, interlacing, channel layout, sample rate,
bit rate, language, track name and the flags default, forced, commentary and
cover image. For subtitles it shows the number of events, which tells a forced
track (a few dozen) from a full one (a few thousand). The line above the table
names the program that wrote the file, for example the MakeMKV version.

The 50 MB limit is `COPY_LIMIT` in `src/cenotaph/record.py`.

## Repeated runs

A run compares each file's size and modification time with the record. Only new
or changed videos are probed again and only new or changed files are copied, so a
run over an unchanged library reads no video data.

If anything has changed, the run writes a report to `TARGET/runs/` listing new
titles, changed titles with each added, changed or missing file, titles that are
gone, and everything that could not be read.

Nothing is deleted from TARGET:

- A file that is gone from a title stays in `cenotaph.json` with `missing_since`;
  its copy stays in TARGET.
- A title folder that is gone stays as it is and is marked with `missing_since`.
- Renaming a title folder records a new title and marks the old one as missing.
  Delete the old folder in TARGET by hand once you are sure.

Titles below a folder that cannot be read, or below a folder given with
`--exclude`, are left as they are and are not marked as missing.

If more than 5% of the recorded titles (at least 3) disappear at once, the run
stops before writing anything. That is usually an unmounted or incomplete share.
Pass `--allow-missing` if the titles were removed on purpose.

An interrupted run can be started again: files copied completely are not copied
a second time.

## Development

```
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
```
