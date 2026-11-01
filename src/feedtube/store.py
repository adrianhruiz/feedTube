import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from yt_dlp.utils import sanitize_filename

from .models import Channel


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(path)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


@dataclass
class Config:
    download_dir: str | None = None
    podcast_dir: str | None = None
    scan_limit: int = 30
    notify: bool = True
    # When both are set, Jellyfin rescans its libraries after a queue downloads something.
    jellyfin_url: str | None = None
    jellyfin_api_key: str | None = None
    channels: list[Channel] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "Config":
        data = _read_json(path)
        channels = [Channel(**c) for c in data.pop("channels", [])]
        return cls(**data, channels=channels)

    def save(self, path: Path) -> None:
        _write_json(path, asdict(self))

    def channel_dir(self, channel: Channel) -> Path:
        base = Path((self.podcast_dir if channel.podcast else self.download_dir) or ".").expanduser()
        return base / sanitize_filename(channel.name)

    def find(self, url: str) -> Channel | None:
        return next((c for c in self.channels if c.url == url), None)


@dataclass
class State:
    last_run: str | None = None
    last_scan: dict[str, str] = field(default_factory=dict)
    # Videos that already existed when a channel was added without backfill.
    ignored: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "State":
        return cls(**_read_json(path))

    def save(self, path: Path) -> None:
        _write_json(path, asdict(self))

    def ignore(self, channel_url: str, ids) -> None:
        current = self.ignored.setdefault(channel_url, [])
        current.extend(i for i in ids if i not in current)

    def ignored_ids(self, channel_url: str) -> set[str]:
        return set(self.ignored.get(channel_url, []))

    def mark_scanned(self, channel_url: str, when: str) -> None:
        self.last_scan[channel_url] = when

    def forget(self, channel_url: str) -> None:
        self.ignored.pop(channel_url, None)
        self.last_scan.pop(channel_url, None)


def read_archive(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {parts[1] for line in path.read_text().splitlines() if len(parts := line.split()) == 2}
