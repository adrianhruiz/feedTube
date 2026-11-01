"""Pure scanning logic: decides which videos are new for a channel."""

import re
from typing import Iterable, Protocol

from .models import TABS, Channel, Entry

# Streams that are live, scheduled or still being processed are retried on a later scan.
_NOT_READY = {"is_live", "is_upcoming", "post_live"}
MEMBERS_ONLY = "subscriber_only"

_CHANNEL_RE = re.compile(
    r"^(?:https?://)?(?:www\.|m\.)?youtube\.com/(?P<id>@[^/?#]+|channel/[^/?#]+|c/[^/?#]+|user/[^/?#]+)(?:/(?P<tab>[^/?#]+))?/?(?:[?#].*)?$"
)


class TabUnavailable(Exception):
    """The channel has no such tab (e.g. no shorts)."""


class Lister(Protocol):
    def list_tab(self, base_url: str, tab: str, limit: int | None) -> list[Entry]: ...


def parse_channel_url(url: str) -> tuple[str, str | None]:
    """Normalize a channel URL and return (base_url, tab) where tab may be None."""
    url = url.strip()
    if url.startswith("@"):
        url = f"https://www.youtube.com/{url}"
    match = _CHANNEL_RE.match(url)
    if not match:
        raise ValueError(f"Not a YouTube channel URL: {url}")
    tab = match["tab"]
    return f"https://www.youtube.com/{match['id']}", tab if tab in TABS else None


def select_new(entries: Iterable[Entry], downloaded: set[str], ignored: set[str]) -> list[Entry]:
    """Filter a newest-first listing down to pending videos, oldest first."""
    seen: set[str] = set()
    pending = []
    for e in entries:
        if (
            e.id in seen
            or e.id in downloaded
            or e.id in ignored
            or e.live_status in _NOT_READY
            or e.availability == MEMBERS_ONLY
        ):
            continue
        seen.add(e.id)
        pending.append(e)
    pending.reverse()
    return pending


def find_new(
    channel: Channel, lister: Lister, downloaded: set[str], ignored: set[str], limit: int | None
) -> tuple[list[Entry], list[str]]:
    """Scan every selected tab of a channel. Returns (new entries, error messages)."""
    new: list[Entry] = []
    errors: list[str] = []
    for tab in channel.tabs:
        try:
            listing = lister.list_tab(channel.url, tab, limit)
        except TabUnavailable:
            errors.append(f"{channel.name}: no '{tab}' tab")
            continue
        except Exception as exc:  # network errors, removed channels...
            errors.append(f"{channel.name} [{tab}]: {exc}")
            continue
        new.extend(select_new(listing, downloaded | {e.id for e in new}, ignored))
    return new, errors
