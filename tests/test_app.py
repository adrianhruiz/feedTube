import asyncio

from rich.text import Text

from feedtube.app import BrowseScreen, FeedTubeApp
from feedtube.service import FeedTube
from feedtube.youtube import FeedCursor

from .test_service import URL, FakeGateway, entry


class BrowsableGateway(FakeGateway):
    def open_feed(self, base_url, tab):
        return FeedCursor([{"id": e.id, "title": e.title, "url": e.url, "live_status": e.live_status} for e in self.feeds.get(tab, [])], tab)


def make_service(tmp_path, feeds):
    service = FeedTube(BrowsableGateway(feeds), tmp_path / "c.json", tmp_path / "s.json", tmp_path / "a.txt")
    service.set_download_dir(str(tmp_path / "videos"))
    return service


async def wait_for(pilot, condition, timeout=5.0):
    for _ in range(int(timeout / 0.05)):
        if condition():
            return
        await pilot.pause(0.05)
    raise AssertionError("condition not met")


def test_startup_scan_downloads_new_uploads_and_records_run(tmp_path, monkeypatch):
    notified = []
    monkeypatch.setattr("feedtube.app.desktop_notify", lambda title, body: notified.append(body))
    service = make_service(tmp_path, {"streams": [entry("s1", "streams")]})
    service.add_channel(URL, name="Foo", tabs=["streams"])
    service.gateway.feeds["streams"].insert(0, entry("s2", "streams"))

    async def run():
        app = FeedTubeApp(service)
        async with app.run_test() as pilot:
            await wait_for(pilot, lambda: bool(notified))
            assert app.downloaded_ids == {"s2"}
            queue = app.query_one("#queue")
            assert queue.get_cell("s2", "progress") == "done"

    asyncio.run(run())
    assert notified == ["1 video(s) downloaded"]
    assert service.state.last_run is not None


def test_queue_drained_refreshes_jellyfin_when_configured(tmp_path, monkeypatch):
    refreshed = []
    monkeypatch.setattr("feedtube.app.desktop_notify", lambda title, body: None)
    monkeypatch.setattr("feedtube.app.refresh_jellyfin", lambda url, key: refreshed.append((url, key)))
    service = make_service(tmp_path, {"streams": [entry("s1", "streams")]})
    service.set_jellyfin("http://jf:8096", "key")
    service.add_channel(URL, name="Foo", tabs=["streams"])
    service.gateway.feeds["streams"].insert(0, entry("s2", "streams"))

    async def run():
        app = FeedTubeApp(service)
        async with app.run_test() as pilot:
            await wait_for(pilot, lambda: bool(refreshed))

    asyncio.run(run())
    assert refreshed == [("http://jf:8096", "key")]


def test_queue_drained_skips_jellyfin_when_not_configured(tmp_path, monkeypatch):
    refreshed, notified = [], []
    monkeypatch.setattr("feedtube.app.desktop_notify", lambda title, body: notified.append(body))
    monkeypatch.setattr("feedtube.app.refresh_jellyfin", lambda url, key: refreshed.append((url, key)))
    service = make_service(tmp_path, {"streams": [entry("s1", "streams")]})
    service.add_channel(URL, name="Foo", tabs=["streams"])
    service.gateway.feeds["streams"].insert(0, entry("s2", "streams"))

    async def run():
        app = FeedTubeApp(service)
        async with app.run_test() as pilot:
            await wait_for(pilot, lambda: bool(notified))

    asyncio.run(run())
    assert refreshed == []


def test_browser_strikes_downloaded_and_downloads_whole_feed(tmp_path, monkeypatch):
    monkeypatch.setattr("feedtube.app.desktop_notify", lambda title, body: None)
    service = make_service(tmp_path, {"videos": [entry("v3"), entry("v2"), entry("v1")]})
    channel, _ = service.add_channel(URL, name="Foo", tabs=[])
    service.download(service.backfill(channel, ["videos"])[0])  # v1 already downloaded

    async def run():
        app = FeedTubeApp(service)
        async with app.run_test() as pilot:
            await pilot.press("enter")
            screen = app.screen
            assert isinstance(screen, BrowseScreen)
            table = screen.query_one("#feed")
            await wait_for(pilot, lambda: table.row_count == 3)
            title = table.get_cell("v1", "title")
            assert isinstance(title, Text) and "strike" in str(title.style)
            assert "strike" not in str(table.get_cell("v3", "title").style)

            await pilot.press("D")
            await pilot.click("#yes")
            await wait_for(pilot, lambda: app.downloaded_ids >= {"v1", "v2", "v3"})

    asyncio.run(run())
    assert [d[0] for d in service.gateway.downloads] == ["v1", "v2", "v3"]


def test_adding_podcast_channel_asks_for_podcast_folder_first(tmp_path, monkeypatch):
    from feedtube.app import ChannelScreen, DirScreen

    monkeypatch.setattr("feedtube.app.desktop_notify", lambda title, body: None)
    service = make_service(tmp_path, {"videos": []})

    async def run():
        app = FeedTubeApp(service)
        async with app.run_test() as pilot:
            await pilot.press("a")
            assert isinstance(app.screen, ChannelScreen)
            app.screen.query_one("#url").value = URL
            app.screen.query_one("#name").value = "Pod"
            app.screen.query_one("#podcast").value = True
            await pilot.click("#save")
            await pilot.pause()
            assert isinstance(app.screen, DirScreen)
            app.screen.query_one("#dir").value = str(tmp_path / "pods")
            await pilot.click("#save")
            await wait_for(pilot, lambda: bool(service.config.channels))
            assert app.query_one("#channels").get_cell(URL, "format") == "podcast (mp3)"

    asyncio.run(run())
    assert service.config.podcast_dir == str(tmp_path / "pods")
    assert service.config.channels[0].podcast is True



def test_queue_downloads_one_at_a_time_in_order(tmp_path, monkeypatch):
    import threading
    import time

    monkeypatch.setattr("feedtube.app.desktop_notify", lambda title, body: None)
    order, active, peak = [], [0], [0]
    lock = threading.Lock()

    class TrackingGateway(BrowsableGateway):
        def download(self, entry, dest_dir, archive, on_progress=None, on_log=None, audio_only=False):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
                order.append(entry.id)
            time.sleep(0.1)
            with lock:
                active[0] -= 1
            super().download(entry, dest_dir, archive)

    feeds = {"videos": [entry("v3"), entry("v2"), entry("v1")]}
    service = FeedTube(TrackingGateway(feeds), tmp_path / "c.json", tmp_path / "s.json", tmp_path / "a.txt")
    service.set_download_dir(str(tmp_path / "videos"))
    channel, _ = service.add_channel(URL, name="Foo", tabs=[])

    async def run():
        app = FeedTubeApp(service)
        async with app.run_test() as pilot:
            entries = service.backfill(channel, ["videos"])
            app.enqueue(entries[:2])
            await pilot.pause(0.05)
            app.enqueue(entries[2:] + entries[:1])  # duplicate of a queued video is ignored
            await wait_for(pilot, lambda: len(app.downloaded_ids) == 3)

    asyncio.run(run())
    assert order == ["v1", "v2", "v3"] and peak[0] == 1


def test_jellyfin_key_sets_server_from_tui(tmp_path, monkeypatch):
    from feedtube.app import JellyfinScreen

    monkeypatch.setattr("feedtube.app.desktop_notify", lambda title, body: None)
    service = make_service(tmp_path, {"videos": []})

    async def run():
        app = FeedTubeApp(service)
        async with app.run_test() as pilot:
            await pilot.press("j")
            assert isinstance(app.screen, JellyfinScreen)
            assert app.screen.query_one("#jf_url").value == "http://localhost:8096"
            assert app.screen.query_one("#jf_key").password is True
            app.screen.query_one("#jf_url").value = "http://jf:8096"
            app.screen.query_one("#jf_key").value = "secret"
            await pilot.click("#save")
            await wait_for(pilot, lambda: service.config.jellyfin_api_key is not None)

    asyncio.run(run())
    assert (service.config.jellyfin_url, service.config.jellyfin_api_key) == ("http://jf:8096", "secret")


def test_jellyfin_screen_requires_both_fields_and_prefills_current(tmp_path, monkeypatch):
    from feedtube.app import JellyfinScreen

    monkeypatch.setattr("feedtube.app.desktop_notify", lambda title, body: None)
    service = make_service(tmp_path, {"videos": []})
    service.set_jellyfin("http://old:8096", "oldkey")

    async def run():
        app = FeedTubeApp(service)
        async with app.run_test() as pilot:
            await pilot.press("j")
            assert app.screen.query_one("#jf_url").value == "http://old:8096"
            assert app.screen.query_one("#jf_key").value == "oldkey"
            app.screen.query_one("#jf_key").value = ""
            await pilot.click("#save")
            await pilot.pause()
            assert isinstance(app.screen, JellyfinScreen)  # not dismissed without a key
            await pilot.click("#cancel")
            await pilot.pause()
            assert not isinstance(app.screen, JellyfinScreen)

    asyncio.run(run())
    assert (service.config.jellyfin_url, service.config.jellyfin_api_key) == ("http://old:8096", "oldkey")


def test_link_key_queues_the_link_into_its_own_folder(tmp_path, monkeypatch):
    from feedtube.models import Link

    monkeypatch.setattr("feedtube.app.desktop_notify", lambda title, body: None)
    service = make_service(tmp_path, {})
    service.gateway.links["https://youtu.be/v1"] = Link("Bar", [], "Mix", [entry("v1")])

    async def run():
        app = FeedTubeApp(service)
        async with app.run_test() as pilot:
            await pilot.press("l")
            await pilot.pause()
            app.screen.query_one("#link").value = "https://youtu.be/v1"
            await pilot.press("enter")
            await wait_for(pilot, lambda: "v1" in app.downloaded_ids)
            assert app.query_one("#queue").get_cell("v1", "channel") == "Bar/Mix"

    asyncio.run(run())
    assert service.gateway.downloads == [("v1", tmp_path / "videos" / "Bar" / "Mix")]
