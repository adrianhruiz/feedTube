from dataclasses import dataclass, field

TABS = ("videos", "streams", "shorts")


@dataclass
class Channel:
    name: str
    url: str
    # Feeds whose new uploads are downloaded automatically. Empty = saved for manual picking.
    tabs: list[str] = field(default_factory=lambda: ["videos"])
    # Podcast channels are saved as MP3 audio in the podcast folder.
    podcast: bool = False

    @property
    def subscribed(self) -> bool:
        return bool(self.tabs)


@dataclass(frozen=True)
class Entry:
    id: str
    title: str
    url: str
    tab: str
    live_status: str | None = None
    channel: str = ""  # channel URL, set once the entry is tied to a saved channel
    duration: float | None = None
    availability: str | None = None  # "subscriber_only" = members-only
    folder: str = ""  # destination folder for direct links; empty = the saved channel's folder


@dataclass
class Link:
    """What a direct video or playlist URL points to."""

    channel: str | None
    channel_urls: list[str]  # uploader/channel URLs, used to match a saved channel
    playlist: str | None  # playlist title, None for a single video
    entries: list[Entry]
