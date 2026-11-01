import os
from datetime import datetime, timezone

from feedtube.youtube import SetPublishedMtimePP, published_timestamp


def test_published_timestamp_prefers_exact_timestamp():
    assert published_timestamp({"timestamp": 1700000000, "upload_date": "20200101"}) == 1700000000
    assert published_timestamp({"release_timestamp": 1600000000, "upload_date": "20200101"}) == 1600000000


def test_published_timestamp_falls_back_to_upload_date():
    expected = datetime(2005, 4, 24, tzinfo=timezone.utc).timestamp()
    assert published_timestamp({"upload_date": "20050424"}) == expected


def test_published_timestamp_unknown():
    assert published_timestamp({}) is None


def test_postprocessor_sets_file_mtime(tmp_path):
    video = tmp_path / "v.mkv"
    video.write_bytes(b"x")
    files_to_delete, info = SetPublishedMtimePP().run({"filepath": str(video), "upload_date": "20050424"})
    assert files_to_delete == [] and info["filepath"] == str(video)
    assert os.path.getmtime(video) == datetime(2005, 4, 24, tzinfo=timezone.utc).timestamp()


def test_video_options_keep_best_video_in_mkv(tmp_path):
    from feedtube.youtube import download_options

    opts = download_options(tmp_path, tmp_path / "a.txt", audio_only=False)
    assert opts["format"] == "bestvideo*+bestaudio/best"
    assert opts["merge_output_format"] == "mkv"
    assert not any(pp["key"] == "FFmpegExtractAudio" for pp in opts["postprocessors"])


def test_podcast_options_extract_best_mp3_with_cover(tmp_path):
    from feedtube.youtube import download_options

    opts = download_options(tmp_path, tmp_path / "a.txt", audio_only=True)
    keys = [pp["key"] for pp in opts["postprocessors"]]
    assert opts["format"] == "bestaudio/best"
    assert "merge_output_format" not in opts
    extract = next(pp for pp in opts["postprocessors"] if pp["key"] == "FFmpegExtractAudio")
    assert extract["preferredcodec"] == "mp3" and extract["preferredquality"] == "0"
    assert opts["writethumbnail"] is True
    # cover must be embedded after the audio exists and metadata is written
    assert keys.index("FFmpegExtractAudio") < keys.index("FFmpegMetadata") < keys.index("EmbedThumbnail")


def test_feed_cursor_hides_members_only_and_still_fills_pages():
    from feedtube.youtube import FeedCursor

    raw = [{"id": "m1", "availability": "subscriber_only"}, {"id": "a"}, {"id": "m2", "availability": "subscriber_only"},
           {"id": "b", "availability": "public"}, {"id": "c"}]
    cursor = FeedCursor(raw, "videos")
    assert [e.id for e in cursor.next_page(2)] == ["a", "b"]
    assert [e.id for e in cursor.next_page(2)] == ["c"] and cursor.exhausted


def test_all_youtube_requests_ask_for_spanish_titles(tmp_path):
    from feedtube.youtube import base_options, download_options

    assert base_options()["extractor_args"] == {"youtube": {"lang": ["es"]}}
    for audio_only in (False, True):
        assert download_options(tmp_path, tmp_path / "a.txt", audio_only)["extractor_args"] == {"youtube": {"lang": ["es"]}}


def test_link_from_single_video_info():
    from feedtube.youtube import link_from_info

    link = link_from_info({
        "_type": "video", "id": "v1", "title": "Hola", "webpage_url": "https://www.youtube.com/watch?v=v1",
        "url": "https://rr1.googlevideo.com/stream", "channel": "Foo", "uploader_url": "https://www.youtube.com/@Foo",
        "channel_url": "https://www.youtube.com/channel/UC1", "duration": 60,
    })
    assert (link.channel, link.playlist) == ("Foo", None)
    assert link.channel_urls == ["https://www.youtube.com/@Foo", "https://www.youtube.com/channel/UC1"]
    assert [(e.id, e.title, e.url) for e in link.entries] == [("v1", "Hola", "https://www.youtube.com/watch?v=v1")]


def test_link_from_playlist_info_skips_members_only():
    from feedtube.youtube import link_from_info

    link = link_from_info({
        "_type": "playlist", "title": "Best of", "uploader": "Foo", "uploader_url": "https://www.youtube.com/@Foo",
        "entries": [{"id": "v1", "title": "a"}, {"id": "v2", "title": "b", "availability": "subscriber_only"}, None],
    })
    assert (link.channel, link.playlist) == ("Foo", "Best of")
    assert link.channel_urls == ["https://www.youtube.com/@Foo"]
    assert [e.id for e in link.entries] == ["v1"]
