"""yt-dlp adapter: listing channel tabs and downloading videos."""

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import yt_dlp
from yt_dlp.postprocessor import PostProcessor
from yt_dlp.utils import DownloadError, match_filter_func

from .core import MEMBERS_ONLY, TabUnavailable
from .models import TABS, Entry, Link

# YouTube auto-translates titles to the request language; always ask for Spanish.
TITLE_LANGUAGE = "es"
OUTPUT_TEMPLATE = "%(upload_date>%Y-%m-%d)s - %(title).150B [%(id)s].%(ext)s"


@dataclass
class Progress:
    percent: float | None
    speed: float | None
    eta: int | None
    phase: str  # "video", "audio", "merging", "converting"


def published_timestamp(info: dict) -> float | None:
    """When the video was published (or the stream aired), as a Unix timestamp."""
    for key in ("timestamp", "release_timestamp"):
        if info.get(key):
            return float(info[key])
    if upload_date := info.get("upload_date"):
        return datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc).timestamp()
    return None


class SetPublishedMtimePP(PostProcessor):
    """Sets the final file's modification time to the publish date, so sorting by date shows video age."""

    def run(self, info: dict):
        path = info.get("filepath")
        ts = published_timestamp(info)
        if path and ts is not None and os.path.exists(path):
            os.utime(path, (ts, ts))
        return [], info


def base_options() -> dict:
    """Options shared by every yt-dlp request."""
    return {"quiet": True, "extractor_args": {"youtube": {"lang": [TITLE_LANGUAGE]}}}


def download_options(dest_dir: Path, archive: Path, audio_only: bool) -> dict:
    """yt-dlp options: best video+audio as MKV, or best audio as MP3 with cover art for podcasts."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    archive.parent.mkdir(parents=True, exist_ok=True)
    # Embed date, channel, description and chapters into the file itself.
    metadata = {"key": "FFmpegMetadata", "add_metadata": True, "add_chapters": True}
    opts: dict = {
        **base_options(),
        "outtmpl": str(dest_dir / OUTPUT_TEMPLATE),
        "download_archive": str(archive),
        # Never record a stream that is still live or scheduled; it is retried later.
        "match_filter": match_filter_func("!is_live & live_status != is_upcoming & live_status != post_live"),
        "concurrent_fragment_downloads": 4,
        "retries": 10,
        "fragment_retries": 10,
        "noprogress": True,
        "updatetime": False,  # yt-dlp would use the HTTP Last-Modified header, not the publish date
    }
    if audio_only:
        opts.update(
            format="bestaudio/best",
            writethumbnail=True,
            postprocessors=[
                {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"},
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"},
                metadata,
                {"key": "EmbedThumbnail"},
            ],
        )
    else:
        opts.update(format="bestvideo*+bestaudio/best", merge_output_format="mkv", postprocessors=[metadata])
    return opts


class _Logger:
    def __init__(self, on_log: Callable[[str], None] | None = None):
        self.on_log = on_log

    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        if self.on_log:
            self.on_log(msg)


def _to_entry(raw: dict, tab: str) -> Entry:
    return Entry(
        id=raw["id"],
        title=raw.get("title") or raw["id"],
        url=raw.get("url") or f"https://www.youtube.com/watch?v={raw['id']}",
        tab=tab,
        live_status=raw.get("live_status"),
        duration=raw.get("duration"),
        availability=raw.get("availability"),
    )


def link_from_info(info: dict) -> Link:
    """Turn yt-dlp's info for a video or playlist URL into a Link."""
    channel_urls = [u for u in (info.get("uploader_url"), info.get("channel_url")) if u]
    channel = info.get("channel") or info.get("uploader")
    if info.get("_type") == "playlist":
        entries = [
            _to_entry(raw, "videos")
            for raw in info.get("entries") or []
            if raw and raw.get("id") and raw.get("availability") != MEMBERS_ONLY
        ]
        return Link(channel, channel_urls, info.get("title") or info.get("id"), entries)
    # A processed video's "url" is the media stream, so point at the watch page instead.
    url = info.get("webpage_url") or f"https://www.youtube.com/watch?v={info['id']}"
    return Link(channel, channel_urls, None, [_to_entry({**info, "url": url}, "videos")])


class FeedCursor:
    """Lazily pages through a channel tab, newest first, without refetching earlier pages.

    Members-only videos are skipped, so pages stay full.
    """

    def __init__(self, raw_entries, tab: str):
        self._iter = iter(raw_entries)
        self.tab = tab
        self.exhausted = False

    def next_page(self, size: int) -> list[Entry]:
        page: list[Entry] = []
        while len(page) < size:
            try:
                raw = next(self._iter)
            except StopIteration:
                self.exhausted = True
                break
            except DownloadError as exc:
                if "does not have a" in str(exc):
                    raise TabUnavailable(self.tab) from exc
                raise
            if raw and raw.get("id") and raw.get("availability") != MEMBERS_ONLY:
                page.append(_to_entry(raw, self.tab))
        return page


class YtDlpGateway:
    def open_feed(self, base_url: str, tab: str) -> FeedCursor:
        opts = {**base_options(), "extract_flat": "in_playlist", "logger": _Logger()}
        try:
            info = yt_dlp.YoutubeDL(opts).extract_info(f"{base_url}/{tab}", download=False, process=False)
        except DownloadError as exc:
            if "does not have a" in str(exc):
                raise TabUnavailable(tab) from exc
            raise
        return FeedCursor(info.get("entries") or [], tab)

    def list_tab(self, base_url: str, tab: str, limit: int | None) -> list[Entry]:
        cursor = self.open_feed(base_url, tab)
        if limit:
            return cursor.next_page(limit)
        entries: list[Entry] = []
        while not cursor.exhausted:
            entries.extend(cursor.next_page(200))
        return entries

    def channel_name(self, base_url: str) -> str:
        opts = {**base_options(), "extract_flat": "in_playlist", "logger": _Logger()}
        last_error: Exception | None = None
        for tab in TABS:
            try:
                info = yt_dlp.YoutubeDL(opts).extract_info(f"{base_url}/{tab}", download=False, process=False)
                return info.get("channel") or info.get("uploader") or base_url.rsplit("/", 1)[-1]
            except DownloadError as exc:
                last_error = exc
        raise last_error or ValueError(base_url)

    def resolve_link(self, url: str) -> Link:
        # noplaylist: a watch URL opened from inside a playlist means just that video.
        opts = {**base_options(), "extract_flat": "in_playlist", "noplaylist": True, "logger": _Logger()}
        info = yt_dlp.YoutubeDL(opts).extract_info(url, download=False)
        return link_from_info(info)

    def download(
        self,
        entry: Entry,
        dest_dir: Path,
        archive: Path,
        on_progress: Callable[[Progress], None] | None = None,
        on_log: Callable[[str], None] | None = None,
        audio_only: bool = False,
    ) -> None:
        last_emit = 0.0

        def progress_hook(d: dict) -> None:
            nonlocal last_emit
            if not on_progress or d["status"] != "downloading":
                return
            now = time.monotonic()
            if now - last_emit < 0.3:
                return
            last_emit = now
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes") or 0
            vcodec = (d.get("info_dict") or {}).get("vcodec")
            on_progress(
                Progress(
                    percent=done * 100 / total if total else None,
                    speed=d.get("speed"),
                    eta=d.get("eta"),
                    phase="audio" if vcodec == "none" else "video",
                )
            )

        def postprocessor_hook(d: dict) -> None:
            phase = {"Merger": "merging", "ExtractAudio": "converting"}.get(d.get("postprocessor", ""))
            if on_progress and phase and d["status"] == "started":
                on_progress(Progress(None, None, None, phase))

        opts = download_options(dest_dir, archive, audio_only)
        opts.update(
            logger=_Logger(on_log),
            progress_hooks=[progress_hook],
            postprocessor_hooks=[postprocessor_hook],
        )
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.add_post_processor(SetPublishedMtimePP(ydl), when="after_move")
            ydl.download([entry.url])
