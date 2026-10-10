"""Settings kept in TARGET/cenotaph.toml, so every run over a target uses the same."""

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG = "cenotaph.toml"


class ConfigError(Exception):
    """TARGET/cenotaph.toml cannot be used; the message names the file and the fix."""


@dataclass(frozen=True)
class Config:
    # Folders of SOURCE that are not recorded, relative to SOURCE.
    exclude: frozenset[Path] = frozenset()


def load_config(target: Path) -> Config | None:
    """Return the configuration of target, or None if it has none yet."""
    path = target / CONFIG
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        return None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e
    unknown = sorted(set(data) - {"exclude"})
    if unknown:
        raise ConfigError(f"{path}: unknown setting {', '.join(unknown)}, remove it")
    exclude = data.get("exclude", [])
    if not isinstance(exclude, list) or not all(isinstance(e, str) for e in exclude):
        raise ConfigError(f'{path}: exclude must be a list of folders, e.g. ["Jellyfin"]')
    return Config(frozenset(Path(e) for e in exclude))


def write_config(target: Path, config: Config) -> None:
    folders = ", ".join(
        json.dumps(p.as_posix(), ensure_ascii=False) for p in sorted(config.exclude)
    )
    text = (
        "# Settings for cenotaph runs into this folder.\n"
        "\n"
        "# Folders of the source that are not recorded, relative to the source.\n"
        f"exclude = [{folders}]\n"
    )
    (target / CONFIG).write_text(text, encoding="utf-8")
