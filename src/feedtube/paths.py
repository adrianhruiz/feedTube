import os
from pathlib import Path


def _xdg(var: str, fallback: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / fallback) / "feedtube"


def config_file() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "config.json"


def state_file() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / "state.json"


def archive_file() -> Path:
    """yt-dlp download archive: one line per downloaded video."""
    return _xdg("XDG_DATA_HOME", ".local/share") / "archive.txt"
