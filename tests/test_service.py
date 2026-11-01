import pytest

from feedtube.core import TabUnavailable
from feedtube.models import Entry, Link
from feedtube.service import FeedTube

URL = "https://www.youtube.com/@Foo"


def entry(id_, tab="videos"):
    return Entry(id=id_, title=id_, url=f"https://youtu.be/{id_}", tab=tab, live_status="not_live")


class FakeGateway:
    def __init__(self, feeds):
        self.feeds = feeds  # tab -> newest-first entries
        self.downloads = []
        self.audio_only = []
        self.links = {}  # url -> Link

    def list_tab(self, base_url, tab, limit):
        if tab not in self.feeds:
            raise TabUnavailable(tab)
        return self.feeds[tab][:limit] if limit else list(self.feeds[tab])

    def channel_name(self, base_url):
        return "Foo Channel"

    def resolve_link(self, url):
        return self.links[url]

    def download(self, entry, dest_dir, archive, on_progress=None, on_log=None, audio_only=False):
        self.downloads.append((entry.id, dest_dir))
        self.audio_only.append(audio_only)
        archive.parent.mkdir(parents=True, exist_ok=True)
        with archive.open("a") as f:
            f.write(f"youtube {entry.id}\n")


@pytest.fixture
def make_app(tmp_path):
    def factory(feeds):
        app = FeedTube(FakeGateway(feeds), tmp_path / "c.json", tmp_path / "s.json", tmp_path / "a.txt")
        app.set_download_dir(str(tmp_path / "videos"))
        return app

    return factory


def test_subscribe_uses_tab_from_url_and_only_new_uploads_are_detected(make_app):
    app = make_app({"streams": [entry("s2", "streams"), entry("s1", "streams")], "videos": [entry("v1")]})
    channel, pending = app.add_channel(f"{URL}/streams")
    assert (channel.name, channel.tabs, pending) == ("Foo Channel", ["streams"], [])

    app.gateway.feeds["streams"].insert(0, entry("s3", "streams"))
    new, errors = app.scan(channel)
    assert [e.id for e in new] == ["s3"] and errors == []
    assert new[0].channel == URL


def test_backfill_on_add_enqueues_whole_catalog_oldest_first(make_app):
    app = make_app({"streams": [entry("s2", "streams"), entry("s1", "streams")]})
    _, pending = app.add_channel(URL, tabs=["streams"], backfill=True)
    assert [e.id for e in pending] == ["s1", "s2"]


def test_manual_channel_is_saved_but_never_scanned(make_app):
    app = make_app({"videos": [entry("v1")]})
    channel, _ = app.add_channel(URL, name="Mine", tabs=[])
    assert channel.tabs == [] and not channel.subscribed
    assert app.scan(channel) == ([], [])


def test_subscribing_to_a_new_feed_later_ignores_its_existing_videos(make_app):
    app = make_app({"videos": [entry("v1")], "streams": [entry("s1", "streams")]})
    channel, _ = app.add_channel(URL, tabs=["videos"])
    app.update_channel(URL, "Foo", ["videos", "streams"])
    assert app.scan(channel)[0] == []


def test_download_goes_to_channel_folder_and_is_not_detected_again(make_app, tmp_path):
    app = make_app({"videos": [entry("v1")]})
    channel, pending = app.add_channel(URL, name="Foo", backfill=True)
    assert app.download(pending[0]) is True
    assert app.gateway.downloads == [("v1", tmp_path / "videos" / "Foo")]
    assert app.scan(channel)[0] == []
    assert "v1" in app.downloaded()


def test_duplicate_channel_is_rejected(make_app):
    app = make_app({"videos": []})
    app.add_channel(URL, name="Foo")
    with pytest.raises(ValueError):
        app.add_channel(f"{URL}/streams", name="Foo")


def test_finish_run_records_today(make_app):
    from datetime import date

    app = make_app({})
    app.finish_run()
    assert FeedTube(app.gateway, app.config_path, app.state_path, app.archive_path).state.last_run == date.today().isoformat()


def test_download_whole_feed_of_manual_channel(make_app):
    app = make_app({"videos": [entry("v2"), entry("v1")], "streams": [entry("s1", "streams")]})
    channel, _ = app.add_channel(URL, name="Foo", tabs=[])
    app.download(Entry(id="v1", title="v1", url="u", tab="videos", channel=URL))
    assert [e.id for e in app.backfill(channel, ["videos"])] == ["v2"]


def test_podcast_channel_requires_podcast_folder(make_app):
    app = make_app({"videos": []})
    with pytest.raises(ValueError, match="podcast folder"):
        app.add_channel(URL, name="Pod", podcast=True)
    assert app.config.channels == []


def test_podcast_channel_downloads_audio_into_podcast_folder(make_app, tmp_path):
    app = make_app({"videos": [entry("v1")]})
    app.set_podcast_dir(str(tmp_path / "pods"))
    channel, pending = app.add_channel(URL, name="Pod", backfill=True, podcast=True)
    assert channel.podcast is True
    assert app.download(pending[0]) is True
    assert app.gateway.downloads == [("v1", tmp_path / "pods" / "Pod")]
    assert app.gateway.audio_only == [True]


def test_video_channel_still_downloads_video(make_app):
    app = make_app({"videos": [entry("v1")]})
    _, pending = app.add_channel(URL, name="Foo", backfill=True)
    app.download(pending[0])
    assert app.gateway.audio_only == [False]


def test_edit_channel_can_switch_to_podcast(make_app, tmp_path):
    app = make_app({"videos": []})
    app.add_channel(URL, name="Foo")
    app.set_podcast_dir(str(tmp_path / "pods"))
    channel, _ = app.update_channel(URL, "Foo", ["videos"], podcast=True)
    assert channel.podcast is True



def test_only_one_download_runs_at_a_time_even_across_processes(make_app, tmp_path):
    """Two FeedTube instances (e.g. the TUI and `feedtube sync`) must never download simultaneously."""
    import threading
    import time

    active, peak, lock = [0], [0], threading.Lock()

    class SlowGateway(FakeGateway):
        def download(self, entry, dest_dir, archive, on_progress=None, on_log=None, audio_only=False):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.2)
            with lock:
                active[0] -= 1
            super().download(entry, dest_dir, archive, on_progress, on_log, audio_only)

    first = make_app({"videos": [entry("v2"), entry("v1")]})
    first.gateway = SlowGateway(first.gateway.feeds)
    _, pending = first.add_channel(URL, name="Foo", backfill=True)
    second = FeedTube(SlowGateway({}), first.config_path, first.state_path, first.archive_path)

    threads = [threading.Thread(target=first.download, args=(pending[0],)),
               threading.Thread(target=second.download, args=(pending[1],))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] == 1
    assert first.downloaded() == {"v1", "v2"}


def test_direct_video_link_of_unknown_channel_goes_to_new_channel_folder(make_app, tmp_path):
    app = make_app({})
    app.gateway.links["https://youtu.be/v1"] = Link("Bar: Live", [], None, [entry("v1")])
    pending = app.resolve_link("https://youtu.be/v1")
    assert [e.id for e in pending] == ["v1"]
    assert app.download(pending[0]) is True
    assert app.gateway.downloads == [("v1", tmp_path / "videos" / "Bar： Live")]
    assert app.gateway.audio_only == [False]
    assert app.config.channels == []  # the channel is not subscribed


def test_direct_video_link_of_saved_channel_reuses_its_folder_and_format(make_app, tmp_path):
    app = make_app({"videos": []})
    app.set_podcast_dir(str(tmp_path / "pods"))
    app.add_channel(URL, name="Pod", podcast=True)
    app.gateway.links["u"] = Link("Foo Channel", ["https://www.youtube.com/channel/UC1", f"{URL}/"], None, [entry("v1")])
    pending = app.resolve_link("u")
    assert app.source_label(pending[0]) == "Pod"
    app.download(pending[0])
    assert app.gateway.downloads == [("v1", tmp_path / "pods" / "Pod")]
    assert app.gateway.audio_only == [True]


def test_playlist_link_goes_to_channel_then_playlist_folder(make_app, tmp_path):
    app = make_app({})
    app.gateway.links["pl"] = Link("Bar", [], "Best of", [entry("v1"), entry("v2")])
    pending = app.resolve_link("pl")
    assert [e.id for e in pending] == ["v1", "v2"]
    assert app.source_label(pending[0]) == "Bar/Best of"
    for e in pending:
        app.download(e)
    folder = tmp_path / "videos" / "Bar" / "Best of"
    assert app.gateway.downloads == [("v1", folder), ("v2", folder)]


def test_playlist_link_of_saved_channel_nests_inside_its_folder(make_app, tmp_path):
    app = make_app({"videos": []})
    app.add_channel(URL, name="Foo")
    app.gateway.links["pl"] = Link("Foo Channel", [URL], "Best of", [entry("v1")])
    pending = app.resolve_link("pl")
    app.download(pending[0])
    assert app.gateway.downloads == [("v1", tmp_path / "videos" / "Foo" / "Best of")]


def test_link_requires_download_folder(tmp_path):
    app = FeedTube(FakeGateway({}), tmp_path / "c.json", tmp_path / "s.json", tmp_path / "a.txt")
    with pytest.raises(ValueError, match="download folder"):
        app.resolve_link("u")
