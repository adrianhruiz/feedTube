# FeedTube

Follow YouTube channels and download their uploads at the best available quality with
[yt-dlp](https://github.com/yt-dlp/yt-dlp), from a terminal UI.

## Install / run

```bash
uv run feedtube          # open the TUI (requires ffmpeg for merging)
uv tool install .        # optional: install the `feedtube` command globally
```

On first start it asks for a download folder. Every channel is saved to `<folder>/<channel name>/`.

Files are named `YYYY-MM-DD - <title> [<id>].mkv` using the publish date (the air date for
streams). The file's modification time is also set to that date, and the date, channel,
description and chapters are embedded in the `.mkv`, so a video's age is visible however it is
sorted or renamed.

## How it works

Each channel has a set of **subscribed feeds** — `videos`, `streams` and/or `shorts`:

- **Subscribed** (one or more feeds checked): every time FeedTube starts it scans those feeds,
  queues anything new since the channel was added, downloads it, sends a desktop notification
  when the queue finishes (and asks Jellyfin to rescan its libraries, if configured) and records today's date as the last sync.
- **Manual** (no feed checked): the channel is just saved. Open it with `Enter`, page through
  any feed and pick what to download.

**Podcast channels**: check "Save as podcast" when adding or editing a channel and it follows the
same procedure, but each video is saved as best-quality MP3 audio (with the thumbnail as cover
art) in `<podcast folder>/<channel name>/`. The podcast folder is asked for the first time you
need it and can be changed with `p`.

When adding a channel you can also download its **existing back catalog**. Without that, only
uploads published after the channel was added are downloaded. New uploads are detected by video
ID, so nothing is downloaded twice (`~/.local/share/feedtube/archive.txt`). Streams that are
still live or scheduled are skipped and picked up on a later scan once they have finished.
Members-only videos are hidden and never downloaded. Titles are always requested in Spanish, so
YouTube's automatic translations do not leak into the list or the file names.

Downloads run one at a time, in queue order. This holds even across processes: if the TUI and
`feedtube sync` run at the same time, they take turns.

**Direct links**: paste any video or playlist URL (`l` in the TUI, or `feedtube download <url>`)
to download it once without subscribing. A video goes to `<folder>/<channel name>/` and a playlist
to `<folder>/<channel name>/<playlist title>/`. If the channel is already saved, its folder and
format (video or podcast MP3) are reused. A `watch?v=…&list=…` URL downloads just that video; use
the `playlist?list=…` URL for the whole playlist.

## Keys

Main screen: `a` add · `Enter` browse · `e` edit feeds · `l` download a link · `x` remove · `s` scan now ·
`b` download the back catalog of the subscribed feeds · `o` download folder ·
`p` podcast folder · `j` Jellyfin server (URL + API key) · `q` quit

Browser: `1`/`2`/`3` videos/streams/shorts · `Space` select · `A` select all ·
`d`/`Enter` download selected (or the current row) · `D` download the whole feed ·
`m` load more (also loads when scrolling to the bottom) · `Esc` back.
Downloaded videos are shown struck through with a ✓.

## Command line

```bash
feedtube add https://www.youtube.com/@channel/streams       # subscribe to the feed in the URL
feedtube add https://www.youtube.com/@channel --feeds videos,streams --backfill
feedtube add https://www.youtube.com/@channel --feeds ""     # manual only
feedtube add https://www.youtube.com/@channel --podcast      # save as MP3 in the podcast folder
feedtube list
feedtube download https://www.youtube.com/watch?v=<id>           # one video, no subscription
feedtube download https://www.youtube.com/playlist?list=<id>     # a whole playlist
feedtube folder ~/Videos/FeedTube
feedtube podcast-folder ~/Music/Podcasts
feedtube jellyfin http://localhost:8096 <api-key>          # refresh Jellyfin after downloads
feedtube sync        # headless scan + download, handy for cron or a systemd timer
```

Config lives in `~/.config/feedtube/config.json` (`scan_limit` = how many recent videos per feed
each scan checks, `notify` = desktop notifications).

## Development

```bash
uv run pytest
```
