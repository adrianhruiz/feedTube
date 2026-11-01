"""Textual TUI."""

import threading
from collections import deque
from dataclasses import replace

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Checkbox, DataTable, Footer, Header, Input, Label, RichLog

from .core import TabUnavailable, parse_channel_url
from .models import TABS, Channel, Entry
from .notify import desktop_notify, refresh_jellyfin
from .service import FeedTube
from .youtube import FeedCursor, Progress

LIVE_LABELS = {"was_live": "stream", "is_live": "LIVE", "is_upcoming": "upcoming", "post_live": "processing"}
NOT_DOWNLOADABLE = {"is_live", "is_upcoming", "post_live"}


def fmt_duration(seconds: float | None) -> str:
    if not seconds:
        return ""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def fmt_progress(p: Progress) -> str:
    if p.phase in ("merging", "converting"):
        return f"{p.phase}…"
    parts = [p.phase]
    if p.percent is not None:
        parts.append(f"{p.percent:5.1f}%")
    if p.speed:
        parts.append(f"{p.speed / 1_048_576:.1f} MiB/s")
    if p.eta:
        parts.append(f"ETA {fmt_duration(p.eta)}")
    return " ".join(parts)


def feeds_label(channel: Channel) -> str:
    return "auto: " + ", ".join(channel.tabs) if channel.subscribed else "manual"


# --------------------------------------------------------------------------- dialogs


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "dismiss(False)", "Cancel")]

    def __init__(self, message: str):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.message)
            with Horizontal(classes="buttons"):
                yield Button("Yes", variant="error", id="yes")
                yield Button("No", id="no")

    @on(Button.Pressed)
    def answer(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class DirScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def __init__(self, current: str | None, title: str = "Download folder", default: str = "~/Videos/FeedTube"):
        super().__init__()
        self.current = current
        self.title_text = title
        self.default = default

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text, classes="title")
            yield Label("Each channel gets its own subfolder: <folder>/<channel name>", classes="hint")
            yield Input(value=self.current or self.default, id="dir")
            with Horizontal(classes="buttons"):
                yield Button("Save", variant="primary", id="save")
                yield Button("Cancel", id="cancel")

    @on(Input.Submitted)
    @on(Button.Pressed, "#save")
    def save(self) -> None:
        value = self.query_one("#dir", Input).value.strip()
        if value:
            self.dismiss(value)

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.dismiss(None)


class JellyfinScreen(ModalScreen[tuple[str, str] | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def __init__(self, url: str | None, api_key: str | None):
        super().__init__()
        self.url = url
        self.api_key = api_key

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label("Jellyfin server", classes="title")
            yield Label("Its libraries are rescanned when a queue finishes downloading", classes="hint")
            yield Input(value=self.url or "http://localhost:8096", placeholder="Server URL", id="jf_url")
            yield Label("API key (Dashboard > API Keys)", classes="hint")
            yield Input(value=self.api_key or "", placeholder="API key", password=True, id="jf_key")
            with Horizontal(classes="buttons"):
                yield Button("Save", variant="primary", id="save")
                yield Button("Cancel", id="cancel")

    @on(Input.Submitted)
    @on(Button.Pressed, "#save")
    def save(self) -> None:
        url = self.query_one("#jf_url", Input).value.strip()
        api_key = self.query_one("#jf_key", Input).value.strip()
        if url and api_key:
            self.dismiss((url, api_key))
        else:
            self.notify("Both the URL and the API key are required.", severity="warning")

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.dismiss(None)


class LinkScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label("Download a link", classes="title")
            yield Label("Video → <folder>/<channel>   ·   Playlist → <folder>/<channel>/<playlist>", classes="hint")
            yield Input(placeholder="https://www.youtube.com/watch?v=… or …/playlist?list=…", id="link")
            with Horizontal(classes="buttons"):
                yield Button("Download", variant="primary", id="save")
                yield Button("Cancel", id="cancel")

    @on(Input.Submitted)
    @on(Button.Pressed, "#save")
    def save(self) -> None:
        value = self.query_one("#link", Input).value.strip()
        if value:
            self.dismiss(value)

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.dismiss(None)


class ChannelScreen(ModalScreen[dict | None]):
    """Add or edit a channel and choose which feeds it is subscribed to."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def __init__(self, channel: Channel | None = None):
        super().__init__()
        self.channel = channel
        self._feeds_touched = channel is not None

    def compose(self) -> ComposeResult:
        ch = self.channel
        with Vertical(classes="dialog"):
            yield Label("Edit channel" if ch else "Add channel", classes="title")
            yield Input(
                value=ch.url if ch else "",
                placeholder="https://www.youtube.com/@channel/streams",
                id="url",
                disabled=ch is not None,
            )
            yield Input(value=ch.name if ch else "", placeholder="Folder name (empty = channel name)", id="name")
            yield Label("Subscribe: auto-download new uploads from")
            with Horizontal(classes="feeds"):
                for tab in TABS:
                    yield Checkbox(tab.capitalize(), value=bool(ch and tab in ch.tabs) or (not ch and tab == "videos"), id=f"tab-{tab}")
            yield Label("No feed checked = saved only; pick videos by hand with Enter.", classes="hint")
            yield Checkbox("Also download the existing back catalog of the checked feeds", id="backfill")
            yield Checkbox("Save as podcast (MP3 audio, in the podcast folder)", value=bool(ch and ch.podcast), id="podcast")
            yield Label("", id="error", classes="error")
            with Horizontal(classes="buttons"):
                yield Button("Save", variant="primary", id="save")
                yield Button("Cancel", id="cancel")

    @on(Input.Changed, "#url")
    def preselect_feed(self, event: Input.Changed) -> None:
        if self._feeds_touched:
            return
        try:
            _, tab = parse_channel_url(event.value)
        except ValueError:
            return
        wanted = tab or "videos"
        for t in TABS:
            self.query_one(f"#tab-{t}", Checkbox).value = t == wanted
        self._feeds_touched = False  # programmatic change, keep following the URL

    @on(Checkbox.Changed)
    def feed_touched(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id not in ("backfill", "podcast") and event.checkbox.has_focus:
            self._feeds_touched = True

    @on(Input.Submitted)
    @on(Button.Pressed, "#save")
    def save(self) -> None:
        error = self.query_one("#error", Label)
        url = self.query_one("#url", Input).value.strip()
        try:
            parse_channel_url(url)
        except ValueError as exc:
            error.update(str(exc))
            return
        tabs = [t for t in TABS if self.query_one(f"#tab-{t}", Checkbox).value]
        backfill = self.query_one("#backfill", Checkbox).value
        if backfill and not tabs:
            error.update("Check at least one feed to download its back catalog.")
            return
        self.dismiss(
            {
                "url": url,
                "name": self.query_one("#name", Input).value,
                "tabs": tabs,
                "backfill": backfill,
                "podcast": self.query_one("#podcast", Checkbox).value,
            }
        )

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.dismiss(None)


# --------------------------------------------------------------------------- browser


class BrowseScreen(Screen):
    """Pages through one feed of a channel so videos can be picked by hand."""

    PAGE_SIZE = 30
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("1", "feed('videos')", "Videos"),
        Binding("2", "feed('streams')", "Streams"),
        Binding("3", "feed('shorts')", "Shorts"),
        Binding("space", "toggle", "Select"),
        Binding("A", "select_all", "Select all"),
        Binding("d", "download", "Download"),
        Binding("m", "more", "Load more"),
        Binding("D", "download_feed", "Download whole feed"),
    ]

    def __init__(self, channel: Channel):
        super().__init__()
        self.channel = channel
        self.tab = channel.tabs[0] if channel.tabs else "videos"
        self.entries: list[Entry] = []
        self.selected: set[str] = set()
        self.cursor: FeedCursor | None = None
        self.loading = False

    @property
    def ft(self) -> "FeedTubeApp":
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label("", id="feedbar", classes="section")
        yield DataTable(id="feed", cursor_type="row", zebra_stripes=True)
        yield Label("", id="status", classes="hint")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#feed", DataTable)
        table.add_column(" ", key="mark", width=2)
        table.add_column("Title", key="title")
        table.add_column("Type", key="type", width=10)
        table.add_column("Length", key="length", width=8)
        self.set_interval(1.5, self.refresh_marks)
        self.action_feed(self.tab)

    def _render_feedbar(self) -> None:
        tabs = "  ".join(
            f"[reverse] {i}:{t} [/]" if t == self.tab else f" {i}:{t} " for i, t in enumerate(TABS, 1)
        )
        self.query_one("#feedbar", Label).update(f"{self.channel.name}   {tabs}   ({feeds_label(self.channel)})")

    def action_feed(self, tab: str) -> None:
        self.tab = tab
        self.entries, self.selected, self.cursor, self.loading = [], set(), None, False
        self.query_one("#feed", DataTable).clear()
        self._render_feedbar()
        self.action_more()

    def action_more(self) -> None:
        if self.loading or (self.cursor and self.cursor.exhausted):
            return
        self.loading = True
        self.query_one("#status", Label).update("Loading…")
        self._fetch_page(self.tab, self.cursor)

    @work(thread=True, group="page")
    def _fetch_page(self, tab: str, cursor: FeedCursor | None) -> None:
        error = None
        page: list[Entry] = []
        try:
            cursor = cursor or self.ft.service.open_feed(self.channel, tab)
            page = cursor.next_page(self.PAGE_SIZE)
        except TabUnavailable:
            error = f"This channel has no {tab} feed."
        except Exception as exc:
            error = f"Could not load feed: {exc}"
        self.app.call_from_thread(self._add_page, tab, cursor, page, error)

    def _add_page(self, tab: str, cursor: FeedCursor | None, page: list[Entry], error: str | None) -> None:
        if tab != self.tab:
            return  # user switched feed meanwhile
        self.loading = False
        self.cursor = cursor
        table = self.query_one("#feed", DataTable)
        for e in page:
            e = replace(e, channel=self.channel.url)
            self.entries.append(e)
            table.add_row(*self._cells(e), key=e.id)
        status = self.query_one("#status", Label)
        if error:
            status.update(error)
            return
        end = "end of feed" if cursor and cursor.exhausted else "scroll down or press m for more"
        status.update(f"{len(self.entries)} loaded — {end}.  ✓ downloaded  ⏳ queued  ● selected")

    def _cells(self, e: Entry) -> tuple:
        return (self._mark(e), self._title(e), LIVE_LABELS.get(e.live_status or "", self.tab.rstrip("s")), fmt_duration(e.duration))

    def _mark(self, e: Entry) -> Text:
        if e.id in self.ft.downloaded_ids:
            return Text("✓", style="green")
        if e.id in self.ft.queued_ids:
            return Text("⏳")
        return Text("●", style="bold yellow") if e.id in self.selected else Text("")

    def _title(self, e: Entry) -> Text:
        return Text(e.title, style="strike dim") if e.id in self.ft.downloaded_ids else Text(e.title)

    def refresh_marks(self) -> None:
        table = self.query_one("#feed", DataTable)
        for e in self.entries:
            table.update_cell(e.id, "mark", self._mark(e))
            table.update_cell(e.id, "title", self._title(e))

    def _current(self) -> Entry | None:
        table = self.query_one("#feed", DataTable)
        if not self.entries or table.cursor_row >= len(self.entries):
            return None
        return self.entries[table.cursor_row]

    @on(DataTable.RowHighlighted)
    def maybe_load_more(self, event: DataTable.RowHighlighted) -> None:
        if event.cursor_row >= len(self.entries) - 3:
            self.action_more()

    def _pickable(self, e: Entry) -> bool:
        return e.id not in self.ft.downloaded_ids and e.id not in self.ft.queued_ids and e.live_status not in NOT_DOWNLOADABLE

    def action_toggle(self) -> None:
        e = self._current()
        if not e:
            return
        if not self._pickable(e):
            self.notify("Already downloaded, queued, or not finished streaming.", severity="warning")
        else:
            self.selected ^= {e.id}
            self.query_one("#feed", DataTable).update_cell(e.id, "mark", self._mark(e))
        self.query_one("#feed", DataTable).action_cursor_down()

    def action_select_all(self) -> None:
        self.selected = {e.id for e in self.entries if self._pickable(e)}
        self.refresh_marks()

    @on(DataTable.RowSelected)
    def action_download(self) -> None:
        targets = [e for e in self.entries if e.id in self.selected]
        if not targets and (current := self._current()):
            targets = [current]
        targets = [e for e in targets if self._pickable(e)]
        if not targets:
            self.notify("Nothing to download.", severity="warning")
            return
        self.ft.enqueue(targets)
        self.selected.clear()
        self.refresh_marks()
        self.notify(f"{len(targets)} queued.")


    def action_download_feed(self) -> None:
        tab = self.tab

        def done(confirmed: bool) -> None:
            if confirmed:
                self.ft.backfill(self.channel, [tab])

        self.app.push_screen(ConfirmScreen(f"Download every video in the {tab} feed of {self.channel.name}?"), done)


# --------------------------------------------------------------------------- main app


class FeedTubeApp(App):
    TITLE = "FeedTube"
    CSS = """
    .section { padding: 0 1; color: $accent; text-style: bold; }
    .hint { color: $text-muted; padding: 0 1; }
    .title { text-style: bold; margin-bottom: 1; }
    .error { color: $error; }
    #channels { height: 1fr; }
    #queue { height: 1fr; }
    #log { height: 8; border: round $panel; }
    ConfirmScreen, DirScreen, ChannelScreen, LinkScreen { align: center middle; }
    .dialog { width: 80; height: auto; border: thick $accent; background: $surface; padding: 1 2; }
    .feeds { height: auto; }
    .buttons { height: auto; align-horizontal: right; margin-top: 1; }
    .buttons Button { margin-left: 1; }
    """
    BINDINGS = [
        Binding("a", "add_channel", "Add"),
        Binding("enter", "browse", "Browse", show=True),
        Binding("e", "edit_channel", "Edit"),
        Binding("l", "download_link", "Link"),
        Binding("x", "remove_channel", "Remove"),
        Binding("s", "scan", "Scan"),
        Binding("b", "backfill", "Backfill"),
        Binding("o", "set_dir", "Folder"),
        Binding("p", "set_podcast_dir", "Podcast folder"),
        Binding("j", "set_jellyfin", "Jellyfin"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, service: FeedTube | None = None):
        super().__init__()
        self.service = service or FeedTube()
        self.downloaded_ids: set[str] = self.service.downloaded()
        self.queued_ids: set[str] = set()
        self._pending: deque[Entry] = deque()
        self._qlock = threading.Lock()
        self._downloading = False
        self._run_ok = 0
        self._run_failed = 0
        self._scanning = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label("Channels", classes="section")
        yield DataTable(id="channels", cursor_type="row", zebra_stripes=True)
        yield Label("Download queue", classes="section")
        yield DataTable(id="queue", cursor_type="row")
        yield RichLog(id="log", markup=True, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        channels = self.query_one("#channels", DataTable)
        channels.add_column("Channel", key="name")
        channels.add_column("Feeds", key="feeds")
        channels.add_column("Format", key="format")
        channels.add_column("Last scan", key="scan")
        queue = self.query_one("#queue", DataTable)
        queue.add_column(" ", key="mark", width=2)
        queue.add_column("Channel", key="channel", width=24)
        queue.add_column("Title", key="title")
        queue.add_column("Progress", key="progress", width=34)
        self.refresh_channels()
        self.refresh_subtitle()
        if not self.service.config.download_dir:
            self.push_screen(DirScreen(None), self._first_dir_chosen)
        else:
            self.action_scan()

    # helpers ---------------------------------------------------------------

    def log_line(self, message: str) -> None:
        self.query_one("#log", RichLog).write(message)

    def refresh_subtitle(self) -> None:
        last = self.service.state.last_run or "never"
        self.sub_title = f"{self.service.config.download_dir or 'no folder set'}  ·  last sync: {last}"

    def refresh_channels(self) -> None:
        table = self.query_one("#channels", DataTable)
        row = table.cursor_row
        table.clear()
        for c in self.service.config.channels:
            table.add_row(
                c.name,
                feeds_label(c),
                "podcast (mp3)" if c.podcast else "video",
                self.service.state.last_scan.get(c.url, "—"),
                key=c.url,
            )
        if table.row_count:
            table.move_cursor(row=min(row, table.row_count - 1))

    def selected_channel(self) -> Channel | None:
        table = self.query_one("#channels", DataTable)
        if not table.row_count:
            self.notify("Add a channel first (a).", severity="warning")
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        return self.service.config.find(key)

    def _safe_call(self, fn, *args) -> None:
        """call_from_thread that tolerates the app shutting down."""
        try:
            self.call_from_thread(fn, *args)
        except Exception:
            pass

    # actions ---------------------------------------------------------------

    def _first_dir_chosen(self, directory: str | None) -> None:
        if directory:
            self.service.set_download_dir(directory)
            self.refresh_subtitle()
            self.log_line(f"Download folder: {self.service.config.download_dir}. Press [b]a[/b] to add a channel.")
        else:
            self.log_line("[yellow]No download folder set. Press o to choose one.[/]")

    def action_set_dir(self) -> None:
        def done(directory: str | None) -> None:
            if directory:
                self.service.set_download_dir(directory)
                self.refresh_subtitle()

        self.push_screen(DirScreen(self.service.config.download_dir), done)

    def action_set_podcast_dir(self, then=None) -> None:
        def done(directory: str | None) -> None:
            if directory:
                self.service.set_podcast_dir(directory)
                self.log_line(f"Podcast folder: {self.service.config.podcast_dir}")
                if then:
                    then()

        self.push_screen(
            DirScreen(self.service.config.podcast_dir, title="Podcast folder (MP3)", default="~/Music/Podcasts"), done
        )

    def action_set_jellyfin(self) -> None:
        def done(result: tuple[str, str] | None) -> None:
            if result:
                self.service.set_jellyfin(*result)
                self.log_line(f"Jellyfin: {result[0]} will be refreshed after downloads.")

        config = self.service.config
        self.push_screen(JellyfinScreen(config.jellyfin_url, config.jellyfin_api_key), done)

    def action_add_channel(self) -> None:
        self.push_screen(ChannelScreen(), lambda data: data and self._submit_channel(None, data))

    def action_edit_channel(self) -> None:
        if channel := self.selected_channel():
            self.push_screen(ChannelScreen(channel), lambda data: data and self._submit_channel(channel.url, data))

    def _submit_channel(self, existing_url: str | None, data: dict) -> None:
        """Ask for the podcast folder first when a podcast channel needs one."""
        if data["podcast"] and not self.service.config.podcast_dir:
            self.action_set_podcast_dir(then=lambda: self._save_channel(existing_url, data))
        else:
            self._save_channel(existing_url, data)

    @work(thread=True, group="channels")
    def _save_channel(self, existing_url: str | None, data: dict) -> None:
        self._safe_call(self.log_line, f"Saving channel {data['url']}…")
        try:
            if existing_url:
                channel, pending = self.service.update_channel(
                    existing_url, data["name"], data["tabs"], data["backfill"], data["podcast"]
                )
            else:
                channel, pending = self.service.add_channel(
                    data["url"], data["name"], data["tabs"], data["backfill"], data["podcast"]
                )
        except Exception as exc:
            self._safe_call(self.log_line, f"[red]Could not save channel: {exc}[/]")
            return
        self._safe_call(self.refresh_channels)
        kind = ", podcast" if channel.podcast else ""
        self._safe_call(self.log_line, f"[green]{channel.name}[/] saved ({feeds_label(channel)}{kind}).")
        if pending:
            self._safe_call(self.enqueue, pending)

    def action_download_link(self) -> None:
        if not self.service.config.download_dir:
            self.notify("Choose a download folder first (o).", severity="warning")
            return
        self.push_screen(LinkScreen(), lambda url: url and self._resolve_link(url))

    @work(thread=True, group="channels")
    def _resolve_link(self, url: str) -> None:
        self._safe_call(self.log_line, f"Reading {url}…")
        try:
            entries = self.service.resolve_link(url)
        except Exception as exc:
            self._safe_call(self.log_line, f"[red]Could not read link: {exc}[/]")
            return
        if not entries:
            self._safe_call(self.log_line, "[yellow]Nothing to download in that link.[/]")
            return
        self._safe_call(self.log_line, f"{self.service.source_label(entries[0])}: {len(entries)} video(s) queued")
        self._safe_call(self.enqueue, entries)

    def action_remove_channel(self) -> None:
        channel = self.selected_channel()
        if not channel:
            return

        def done(confirmed: bool) -> None:
            if confirmed:
                self.service.remove_channel(channel.url)
                self.refresh_channels()
                self.log_line(f"Removed {channel.name} (downloaded files are kept).")

        self.push_screen(ConfirmScreen(f"Remove {channel.name}? Downloaded files are kept."), done)

    def action_browse(self) -> None:
        if channel := self.selected_channel():
            self.push_screen(BrowseScreen(channel))

    @on(DataTable.RowSelected, "#channels")
    def _channel_selected(self) -> None:
        self.action_browse()

    def action_scan(self) -> None:
        if self._scanning:
            return
        if not self.service.config.download_dir:
            self.notify("Choose a download folder first (o).", severity="warning")
            return
        self._scanning = True
        self._scan_worker()

    @work(thread=True, group="scan")
    def _scan_worker(self) -> None:
        channels = [c for c in self.service.config.channels if c.subscribed]
        self._safe_call(self.log_line, f"Scanning {len(channels)} subscribed channel(s)…")
        found = 0
        for channel in channels:
            new, errors = self.service.scan(channel)
            for err in errors:
                self._safe_call(self.log_line, f"[yellow]{err}[/]")
            if new:
                found += len(new)
                self._safe_call(self.log_line, f"[green]{channel.name}[/]: {len(new)} new")
                self._safe_call(self.enqueue, new)
        self._safe_call(self._scan_done, found)

    def _scan_done(self, found: int) -> None:
        self._scanning = False
        self.refresh_channels()
        if found == 0 and not self._downloading and not self._pending:
            self.service.finish_run()
            self.refresh_subtitle()
            self.log_line("Nothing new. All caught up — see you another day.")

    def action_backfill(self) -> None:
        channel = self.selected_channel()
        if not channel:
            return
        if not channel.subscribed:
            self.notify("Manual channel: press Enter to pick videos, or edit it to subscribe to feeds.", severity="warning")
            return

        def done(confirmed: bool) -> None:
            if confirmed:
                self.backfill(channel)

        self.push_screen(ConfirmScreen(f"Download the whole back catalog of {channel.name} ({', '.join(channel.tabs)})?"), done)

    @work(thread=True, group="channels")
    def backfill(self, channel: Channel, tabs: list[str] | None = None) -> None:
        feeds = ", ".join(tabs if tabs is not None else channel.tabs)
        self._safe_call(self.log_line, f"Listing the full {feeds} catalog of {channel.name}…")
        entries = self.service.backfill(channel, tabs)
        self._safe_call(self.log_line, f"{channel.name}: {len(entries)} video(s) pending")
        if entries:
            self._safe_call(self.enqueue, entries)

    def action_quit(self) -> None:
        if not self._downloading:
            self.exit()
            return
        self.push_screen(
            ConfirmScreen("Downloads in progress. Quit anyway? Unfinished videos resume next time."),
            lambda yes: yes and self.exit(),
        )

    # download queue --------------------------------------------------------

    def enqueue(self, entries: list[Entry]) -> None:
        queue = self.query_one("#queue", DataTable)
        added = 0
        for e in entries:
            if e.id in self.queued_ids or e.id in self.downloaded_ids:
                continue
            if e.id in queue.rows:
                queue.remove_row(e.id)  # retry of a failed download
            self.queued_ids.add(e.id)
            queue.add_row(Text("⏳"), self.service.source_label(e), Text(e.title), "queued", key=e.id)
            with self._qlock:
                self._pending.append(e)
            added += 1
        if added:
            self._ensure_downloader()

    def _ensure_downloader(self) -> None:
        with self._qlock:
            if self._downloading:
                return
            self._downloading = True
        threading.Thread(target=self._download_loop, daemon=True).start()

    def _download_loop(self) -> None:
        while True:
            with self._qlock:
                if not self._pending:
                    self._downloading = False
                    break
                entry = self._pending.popleft()
            self._safe_call(self._download_started, entry)
            ok = self.service.download(
                entry,
                on_progress=lambda p, e=entry: self._safe_call(self._on_progress, e, p),
                on_log=lambda msg: self._safe_call(self.log_line, f"[red]{msg}[/]"),
            )
            self._safe_call(self._download_finished, entry, ok)
        self._safe_call(self._queue_drained)

    def _download_started(self, entry: Entry) -> None:
        self._set_progress(entry.id, "starting…")

    def _on_progress(self, entry: Entry, p: Progress) -> None:
        self._set_progress(entry.id, fmt_progress(p))

    def _set_progress(self, entry_id: str, text: str) -> None:
        queue = self.query_one("#queue", DataTable)
        if entry_id in queue.rows:
            queue.update_cell(entry_id, "mark", Text("⬇", style="cyan"))
            queue.update_cell(entry_id, "progress", text)

    def _download_finished(self, entry: Entry, ok: bool) -> None:
        self.queued_ids.discard(entry.id)
        queue = self.query_one("#queue", DataTable)
        if ok:
            self._run_ok += 1
            self.downloaded_ids.add(entry.id)
            queue.update_cell(entry.id, "mark", Text("✓", style="green"))
            queue.update_cell(entry.id, "title", Text(entry.title, style="strike dim"))
            queue.update_cell(entry.id, "progress", "done")
            self.log_line(f"[green]✓[/] {entry.title}")
        else:
            self._run_failed += 1
            queue.update_cell(entry.id, "mark", Text("✗", style="red"))
            queue.update_cell(entry.id, "progress", "failed / skipped")

    def _queue_drained(self) -> None:
        if self._downloading or self._pending or self._scanning:
            return
        ok, failed = self._run_ok, self._run_failed
        self._run_ok = self._run_failed = 0
        summary = f"{ok} video(s) downloaded" + (f", {failed} failed" if failed else "")
        self.service.finish_run()
        self.refresh_subtitle()
        self.log_line(f"[b]Queue finished:[/] {summary}. See you another day.")
        self.notify(summary, title="FeedTube")
        if self.service.config.notify:
            desktop_notify("FeedTube: downloads finished", summary)
        if ok:
            self._refresh_jellyfin()

    @work(thread=True)
    def _refresh_jellyfin(self) -> None:
        config = self.service.config
        if not (config.jellyfin_url and config.jellyfin_api_key):
            return
        error = refresh_jellyfin(config.jellyfin_url, config.jellyfin_api_key)
        message = f"[red]{error}[/]" if error else "Jellyfin library refresh requested."
        self._safe_call(self.log_line, message)
