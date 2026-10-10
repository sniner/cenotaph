"""Record what is needed to rebuild a movie and series library after losing it.

For every movie or series folder below SOURCE, cenotaph writes to the same relative
path below TARGET:

- cenotaph.json: every file with size, modification time and the ffprobe output
  (tracks, chapters, container tags) of each video
- cenotaph.md: the same as a readable overview
- a copy of the source/ folder and of every other file smaller than COPY_LIMIT

A repeated run only probes and copies what has changed and writes a report of the
changes to TARGET/runs/. Nothing is ever deleted from TARGET: files and titles that
disappear from SOURCE are kept and marked as missing.
"""

from importlib.metadata import version

__version__ = version("cenotaph")
