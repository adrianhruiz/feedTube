"""Application service shared by the TUI and the headless CLI."""

import fcntl
import threading
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from . import paths
from yt_dlp.utils import sanitize_filename

from .core import find_new, parse_channel_url
from .models import Channel, Entry
from .store import Config, State, read_archive
from .youtube import Progress, YtDlpGateway


class FeedTube:
    def __init__(
        self,
        gateway: YtDlpGateway | None = None,
        config_path: Path | None = None,
        state_path: Path | None = None,
        archive_path: Path | None = None,
    ):
        self.gateway = gateway or YtDlpGateway()
        self.config_path = config_path or paths.config_file()
        self.state_path = state_path or paths.state_file()
        self.archive_path = archive_path or paths.archive_file()
        self.config = Config.load(self.config_path)
        self.state = State.load(self.state_path)
        self._lock = threading.Lock()

    # persistence -----------------------------------------------------------

    def save(self) -> None:
        with self._lock:
            self.config.save(self.config_path)
            self.state.save(self.state_path)

    def set_download_dir(self, directory: str) -> None:
        self.config.download_dir = str(Path(directory).expanduser())
        self.save()

    def set_podcast_dir(self, directory: str) -> None:
        self.config.podcast_dir = str(Path(directory).expanduser())
        self.save()

    def set_jellyfin(self, url: str, api_key: str) -> None:
        self.config.jellyfin_url = url
        self.config.jellyfin_api_key = api_key
        self.save()

    def _require_podcast_dir(self) -> None:
        if not self.config.podcast_dir:
            raise ValueError("Set a podcast folder first")

    def downloaded(self) -> set[str]:
        return read_archive(self.archive_path)

    # channels --------------------------------------------------------------

    def add_channel(
        self,
        url: str,
        name: str | None = None,
        tabs: list[str] | None = None,
        backfill: bool = False,
        podcast: bool = False,
    ) -> tuple[Channel, list[Entry]]:
        """Register a channel. tabs=[] saves it for manual picking only; podcast=True saves MP3 audio.

        Returns the videos to enqueue (only when backfilling).
        """
        base_url, url_tab = parse_channel_url(url)
        if podcast:
            self._require_podcast_dir()
        if self.config.find(base_url):
            raise ValueError(f"Channel already added: {base_url}")
        channel = Channel(
            name=(name or "").strip() or self.gateway.channel_name(base_url),
            url=base_url,
            tabs=list(tabs) if tabs is not None else [url_tab or "videos"],
            podcast=podcast,
        )
        pending = self._prepare_tabs(channel, channel.tabs, backfill)
        self.config.channels.append(channel)
        self.save()
        return channel, pending

    def update_channel(
        self, url: str, name: str, tabs: list[str], backfill: bool = False, podcast: bool | None = None
    ) -> tuple[Channel, list[Entry]]:
        channel = self.config.find(url)
        if channel is None:
            raise ValueError(f"Unknown channel: {url}")
        if podcast:
            self._require_podcast_dir()
        if podcast is not None:
            channel.podcast = podcast
        added = [t for t in tabs if t not in channel.tabs]
        channel.name = name.strip() or channel.name
        channel.tabs = tabs
        pending = self.backfill(channel) if backfill else self._prepare_tabs(channel, added, False)
        self.save()
        return channel, pending

    def remove_channel(self, url: str) -> None:
        self.config.channels = [c for c in self.config.channels if c.url != url]
        self.state.forget(url)
        self.save()

    def _prepare_tabs(self, channel: Channel, tabs: list[str], backfill: bool) -> list[Entry]:
        """Backfill returns the whole catalog; otherwise existing videos are marked as seen."""
        if not tabs:
            return []
        scoped = replace(channel, tabs=tabs)
        if backfill:
            return self._tag(channel, find_new(scoped, self.gateway, self.downloaded(), set(), None)[0])
        existing, _ = find_new(scoped, self.gateway, set(), set(), self.config.scan_limit)
        self.state.ignore(channel.url, [e.id for e in existing])
        return []

    # scanning --------------------------------------------------------------

    def scan(self, channel: Channel) -> tuple[list[Entry], list[str]]:
        new, errors = find_new(
            channel, self.gateway, self.downloaded(), self.state.ignored_ids(channel.url), self.config.scan_limit
        )
        self.state.mark_scanned(channel.url, datetime.now().strftime("%Y-%m-%d %H:%M"))
        self.save()
        return self._tag(channel, new), errors

    def backfill(self, channel: Channel, tabs: list[str] | None = None) -> list[Entry]:
        """Every not-yet-downloaded video of the given feeds (default: subscribed ones), oldest first."""
        scoped = replace(channel, tabs=tabs) if tabs is not None else channel
        new, _ = find_new(scoped, self.gateway, self.downloaded(), set(), None)
        return self._tag(channel, new)

    @staticmethod
    def _tag(channel: Channel, entries: list[Entry]) -> list[Entry]:
        return [replace(e, channel=channel.url) for e in entries]

    # direct links ----------------------------------------------------------

    def resolve_link(self, url: str) -> list[Entry]:
        """Videos behind a direct video or playlist URL, each with its destination folder.

        Saved channels reuse their folder and format; other channels get <download folder>/<channel>.
        Playlists go one level deeper: <channel folder>/<playlist title>.
        """
        if not self.config.download_dir:
            raise ValueError("Set a download folder first")
        link = self.gateway.resolve_link(url)
        channel = self._find_saved(link.channel_urls)
        if channel:
            folder = self.config.channel_dir(channel)
        else:
            folder = Path(self.config.download_dir).expanduser()
            if link.channel:
                folder /= sanitize_filename(link.channel)
        if link.playlist:
            folder /= sanitize_filename(link.playlist)
        return [replace(e, channel=channel.url if channel else "", folder=str(folder)) for e in link.entries]

    def _find_saved(self, urls: list[str]) -> Channel | None:
        for url in urls:
            try:
                base_url, _ = parse_channel_url(url)
            except ValueError:
                continue
            if channel := self.config.find(base_url):
                return channel
        return None

    def source_label(self, entry: Entry) -> str:
        """Saved channel name, or the destination folder relative to the download folder."""
        if channel := self.config.find(entry.channel):
            return channel.name
        if entry.folder and self.config.download_dir:
            base = Path(self.config.download_dir).expanduser()
            folder = Path(entry.folder)
            return str(folder.relative_to(base)) if folder.is_relative_to(base) else folder.name
        return entry.channel

    # downloading -----------------------------------------------------------

    def download(
        self,
        entry: Entry,
        on_progress: Callable[[Progress], None] | None = None,
        on_log: Callable[[str], None] | None = None,
    ) -> bool:
        """Download one video. True when it ended up in the archive."""
        channel = self.config.find(entry.channel)
        if entry.folder:
            dest = Path(entry.folder)
        else:
            dest = self.config.channel_dir(channel) if channel else Path(self.config.download_dir or ".")
        audio_only = bool(channel and channel.podcast)
        try:
            with self._download_slot():
                if entry.id in self.downloaded():  # another instance got it while we waited
                    return True
                self.gateway.download(entry, dest, self.archive_path, on_progress, on_log, audio_only=audio_only)
        except Exception as exc:
            if on_log:
                on_log(f"{entry.title}: {exc}")
            return False
        return entry.id in self.downloaded()

    @contextmanager
    def _download_slot(self):
        """Exclusive across processes: the TUI and `feedtube sync` never run two downloads at once."""
        lock_path = self.archive_path.with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("w") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

    def finish_run(self) -> None:
        self.state.last_run = date.today().isoformat()
        self.save()

    def open_feed(self, channel: Channel, tab: str):
        return self.gateway.open_feed(channel.url, tab)

    def channel_name(self, url: str) -> str:
        channel = self.config.find(url)
        return channel.name if channel else url
