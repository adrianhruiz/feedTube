import argparse
import sys

from .models import TABS


def _parse_feeds(value: str) -> list[str]:
    feeds = [f.strip() for f in value.split(",") if f.strip()]
    unknown = [f for f in feeds if f not in TABS]
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown feed(s): {', '.join(unknown)} (choose from {', '.join(TABS)})")
    return feeds


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="feedtube", description="Follow YouTube channels and download new uploads with yt-dlp.")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("tui", help="open the interactive TUI (default)")
    sub.add_parser("sync", help="scan subscribed channels and download new uploads without the TUI")
    sub.add_parser("list", help="list saved channels")
    add = sub.add_parser("add", help="save a channel")
    add.add_argument("url", help="channel URL, e.g. https://www.youtube.com/@name/streams")
    add.add_argument("--name", help="folder name (default: channel name)")
    add.add_argument(
        "--feeds",
        type=_parse_feeds,
        help=f"comma-separated feeds to auto-download ({', '.join(TABS)}); empty string = manual only. "
        "Default: the feed in the URL, or videos",
    )
    add.add_argument("--backfill", action="store_true", help="also download the existing back catalog")
    add.add_argument("--podcast", action="store_true", help="save as MP3 audio in the podcast folder")
    download = sub.add_parser(
        "download", help="download a video or playlist URL into <folder>/<channel>[/<playlist>]"
    )
    download.add_argument("url", help="video or playlist URL")
    folder = sub.add_parser("folder", help="show or set the download folder")
    folder.add_argument("path", nargs="?")
    podcast_folder = sub.add_parser("podcast-folder", help="show or set the folder for podcast (MP3) channels")
    podcast_folder.add_argument("path", nargs="?")
    jellyfin = sub.add_parser("jellyfin", help="show or set the Jellyfin server to refresh after downloads")
    jellyfin.add_argument("url", nargs="?", help="server URL, e.g. http://localhost:8096")
    jellyfin.add_argument("api_key", nargs="?", help="API key from Jellyfin's Dashboard > API Keys")
    return parser


def _sync(service) -> int:
    from .notify import desktop_notify, refresh_jellyfin

    if not service.config.download_dir:
        print("No download folder set. Run: feedtube folder <path>", file=sys.stderr)
        return 1
    pending = []
    for channel in (c for c in service.config.channels if c.subscribed):
        new, errors = service.scan(channel)
        for err in errors:
            print(f"warning: {err}", file=sys.stderr)
        print(f"{channel.name}: {len(new)} new")
        pending.extend(new)
    ok = 0
    for i, entry in enumerate(pending, 1):
        print(f"[{i}/{len(pending)}] {entry.title}")
        if service.download(entry, on_log=lambda m: print(f"  error: {m}", file=sys.stderr)):
            ok += 1
    service.finish_run()
    summary = f"{ok} video(s) downloaded" + (f", {len(pending) - ok} failed" if ok < len(pending) else "")
    print(summary)
    if pending and service.config.notify:
        desktop_notify("FeedTube: downloads finished", summary)
    config = service.config
    if ok and config.jellyfin_url and config.jellyfin_api_key:
        error = refresh_jellyfin(config.jellyfin_url, config.jellyfin_api_key)
        print(error or "Jellyfin library refresh requested.", file=sys.stderr if error else sys.stdout)
    return 0 if ok == len(pending) else 1


def _download_link(service, url: str) -> int:
    try:
        pending = service.resolve_link(url)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    ok = 0
    for i, entry in enumerate(pending, 1):
        print(f"[{i}/{len(pending)}] {entry.title}")
        if service.download(entry, on_log=lambda m: print(f"  error: {m}", file=sys.stderr)):
            ok += 1
    print(f"{ok} video(s) downloaded" + (f", {len(pending) - ok} failed" if ok < len(pending) else ""))
    return 0 if ok == len(pending) else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from .service import FeedTube

    service = FeedTube()
    if args.command in (None, "tui"):
        from .app import FeedTubeApp

        FeedTubeApp(service).run()
        return 0
    if args.command == "sync":
        return _sync(service)
    if args.command == "list":
        for c in service.config.channels:
            mode = "auto: " + ", ".join(c.tabs) if c.subscribed else "manual"
            print(f"{c.name}\t{mode}\t{'mp3' if c.podcast else 'video'}\t{c.url}")
        return 0
    if args.command == "folder":
        if args.path:
            service.set_download_dir(args.path)
        print(service.config.download_dir or "(not set)")
        return 0
    if args.command == "podcast-folder":
        if args.path:
            service.set_podcast_dir(args.path)
        print(service.config.podcast_dir or "(not set)")
        return 0
    if args.command == "jellyfin":
        if args.url and not args.api_key:
            build_parser().error("jellyfin: an API key is required together with the URL")
        if args.url:
            service.set_jellyfin(args.url, args.api_key)
        url = service.config.jellyfin_url
        print(f"{url} (api key set)" if url and service.config.jellyfin_api_key else "(not set)")
        return 0
    if args.command == "download":
        return _download_link(service, args.url)
    if args.command == "add":
        try:
            channel, pending = service.add_channel(args.url, args.name, args.feeds, args.backfill, args.podcast)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"Saved {channel.name} ({'auto: ' + ', '.join(channel.tabs) if channel.subscribed else 'manual'}"
              f"{', podcast' if channel.podcast else ''})")
        for entry in pending:
            service.download(entry, on_log=lambda m: print(f"  error: {m}", file=sys.stderr))
        return 0
    return 1
