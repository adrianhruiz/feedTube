import shutil
import subprocess
import urllib.error
import urllib.request


def desktop_notify(title: str, body: str) -> None:
    """Best-effort desktop notification; silently does nothing without notify-send."""
    if not shutil.which("notify-send"):
        return
    try:
        subprocess.run(["notify-send", "-a", "FeedTube", title, body], check=False, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


def refresh_jellyfin(url: str, api_key: str) -> str | None:
    """Ask Jellyfin to rescan its libraries. Returns an error message, or None on success."""
    request = urllib.request.Request(
        url.rstrip("/") + "/Library/Refresh",
        method="POST",
        headers={"Authorization": f'MediaBrowser Token="{api_key}"'},
    )
    try:
        with urllib.request.urlopen(request, timeout=10):
            return None
    except urllib.error.HTTPError as exc:
        return f"Jellyfin refresh failed: HTTP {exc.code}"
    except (urllib.error.URLError, OSError) as exc:
        return f"Jellyfin refresh failed: {getattr(exc, 'reason', exc)}"
