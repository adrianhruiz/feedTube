import pytest

from feedtube.core import TabUnavailable, find_new, parse_channel_url, select_new
from feedtube.models import Channel, Entry


def entry(id_, live_status="not_live", tab="videos"):
    return Entry(id=id_, title=f"title {id_}", url=f"https://youtu.be/{id_}", tab=tab, live_status=live_status)


@pytest.mark.parametrize(
    ("url", "base", "tab"),
    [
        ("https://www.youtube.com/@GentlemanProgrammingVods/streams", "https://www.youtube.com/@GentlemanProgrammingVods", "streams"),
        ("https://www.youtube.com/@Foo/videos/", "https://www.youtube.com/@Foo", "videos"),
        ("https://www.youtube.com/@Foo/shorts", "https://www.youtube.com/@Foo", "shorts"),
        ("https://youtube.com/@Foo", "https://www.youtube.com/@Foo", None),
        ("@Foo", "https://www.youtube.com/@Foo", None),
        ("https://www.youtube.com/channel/UC123/featured", "https://www.youtube.com/channel/UC123", None),
    ],
)
def test_parse_channel_url(url, base, tab):
    assert parse_channel_url(url) == (base, tab)


def test_parse_channel_url_rejects_non_channel():
    with pytest.raises(ValueError):
        parse_channel_url("https://www.youtube.com/watch?v=abc")


def test_select_new_skips_known_and_returns_oldest_first():
    entries = [entry("c"), entry("b"), entry("a")]  # YouTube lists newest first
    assert [e.id for e in select_new(entries, downloaded={"b"}, ignored=set())] == ["a", "c"]
    assert [e.id for e in select_new(entries, downloaded=set(), ignored={"a", "c"})] == ["b"]


def test_select_new_skips_live_and_upcoming_streams():
    entries = [entry("live", "is_live"), entry("soon", "is_upcoming"), entry("proc", "post_live"), entry("done", "was_live")]
    assert [e.id for e in select_new(entries, set(), set())] == ["done"]


def test_select_new_dedupes():
    assert [e.id for e in select_new([entry("a"), entry("a")], set(), set())] == ["a"]


class FakeLister:
    def __init__(self, tabs):
        self.tabs = tabs
        self.calls = []

    def list_tab(self, base_url, tab, limit):
        self.calls.append((base_url, tab, limit))
        if tab not in self.tabs:
            raise TabUnavailable(tab)
        return self.tabs[tab]


def test_find_new_scans_selected_tabs_only():
    lister = FakeLister({"videos": [entry("v1")], "streams": [entry("s2", tab="streams"), entry("s1", tab="streams")]})
    channel = Channel(name="Foo", url="https://www.youtube.com/@Foo", tabs=["streams"])
    new, errors = find_new(channel, lister, downloaded=set(), ignored={"s1"}, limit=30)
    assert [e.id for e in new] == ["s2"]
    assert errors == []
    assert lister.calls == [("https://www.youtube.com/@Foo", "streams", 30)]


def test_find_new_reports_missing_tab_and_keeps_going():
    lister = FakeLister({"videos": [entry("v1")]})
    channel = Channel(name="Foo", url="https://www.youtube.com/@Foo", tabs=["shorts", "videos"])
    new, errors = find_new(channel, lister, set(), set(), limit=None)
    assert [e.id for e in new] == ["v1"]
    assert len(errors) == 1 and "shorts" in errors[0]


def test_select_new_skips_members_only_videos():
    members = Entry(id="m", title="m", url="u", tab="videos", availability="subscriber_only")
    assert [e.id for e in select_new([entry("a"), members], set(), set())] == ["a"]
