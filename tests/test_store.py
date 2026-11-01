from feedtube.models import Channel
from feedtube.store import Config, State, read_archive


def test_config_roundtrip(tmp_path):
    path = tmp_path / "cfg" / "config.json"
    cfg = Config(download_dir="/videos", channels=[Channel(name="Foo", url="https://www.youtube.com/@Foo", tabs=["streams"])])
    cfg.save(path)
    loaded = Config.load(path)
    assert loaded == cfg
    assert loaded.channel_dir(loaded.channels[0]).as_posix() == "/videos/Foo"


def test_config_load_missing_returns_defaults(tmp_path):
    cfg = Config.load(tmp_path / "nope.json")
    assert cfg.download_dir is None and cfg.channels == [] and cfg.scan_limit == 30


def test_channel_dir_sanitizes_name():
    cfg = Config(download_dir="/videos")
    assert "/" not in cfg.channel_dir(Channel(name="AC/DC", url="u", tabs=["videos"])).name


def test_state_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    state = State()
    state.ignore("https://www.youtube.com/@Foo", ["a", "b"])
    state.mark_scanned("https://www.youtube.com/@Foo", "2026-10-28 10:00")
    state.last_run = "2026-10-28"
    state.save(path)
    loaded = State.load(path)
    assert loaded == state
    assert loaded.ignored_ids("https://www.youtube.com/@Foo") == {"a", "b"}


def test_state_forget_channel(tmp_path):
    state = State()
    state.ignore("u", ["a"])
    state.mark_scanned("u", "x")
    state.forget("u")
    assert state.ignored_ids("u") == set() and "u" not in state.last_scan


def test_read_archive(tmp_path):
    path = tmp_path / "archive.txt"
    assert read_archive(path) == set()
    path.write_text("youtube abc\nyoutube def\n\n")
    assert read_archive(path) == {"abc", "def"}


def test_podcast_channel_goes_to_podcast_dir_and_roundtrips(tmp_path):
    path = tmp_path / "config.json"
    cfg = Config(
        download_dir="/videos",
        podcast_dir="/podcasts",
        channels=[Channel(name="Pod", url="u", tabs=["streams"], podcast=True)],
    )
    cfg.save(path)
    loaded = Config.load(path)
    assert loaded == cfg
    assert loaded.channel_dir(loaded.channels[0]).as_posix() == "/podcasts/Pod"


def test_config_without_podcast_fields_still_loads(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"download_dir": "/v", "channels": [{"name": "A", "url": "u", "tabs": ["videos"]}]}')
    cfg = Config.load(path)
    assert cfg.podcast_dir is None and cfg.channels[0].podcast is False
