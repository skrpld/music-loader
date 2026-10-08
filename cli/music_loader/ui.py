"""Live terminal dashboard built on `rich`: overall stats, a link-queue
progress bar, a track-level progress bar, one progress row per active
download (percent/speed/ETA when available), and a scrolling panel showing
what is happening right now.

Two levels of counting are tracked on purpose:
- "links" - how many URLs from the input were processed (an entire album,
  playlist, or artist discography counts as ONE link).
- "tracks" - how many individual songs inside those links were found and
  processed (downloaded / already had it / failed).

Every public method is safe to call from the worker threads. Every text that
comes from outside (track titles, tool output, paths) is escaped before it
reaches rich: a title such as "Song [prod. x]" used to vanish from the log,
and one containing "[/...]" crashed the whole display.
"""
from collections import deque
from dataclasses import dataclass
from typing import Optional
import threading

from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskID, TaskProgressColumn, TextColumn
from rich.table import Table
from rich.text import Text

from .availability import UnavailableTrack
from .runlog import RunLog

_LOG_LINES = 10


@dataclass
class Stats:
    spotify_ok: int = 0
    spotify_fail: int = 0
    soundcloud_ok: int = 0
    soundcloud_fail: int = 0
    lyrics_ok: int = 0
    lyrics_fail: int = 0
    lyrics_skipped: int = 0

    # Track-level counters. "total" accumulates as it's discovered (a link
    # can be an album/playlist/discography with many tracks inside it), so
    # it may keep growing for a while before settling once every link in the
    # queue has reported in.
    spotify_tracks_total: int = 0
    spotify_tracks_done: int = 0
    spotify_tracks_skipped: int = 0
    spotify_tracks_failed: int = 0

    soundcloud_tracks_total: int = 0
    soundcloud_tracks_done: int = 0
    soundcloud_tracks_skipped: int = 0
    soundcloud_tracks_failed: int = 0
    # Skipped because SoundCloud does not give the track out (DRM, preview,
    # blocked, removed). Not a failure, see availability.py.
    soundcloud_tracks_unavailable: int = 0


class Dashboard:
    def __init__(
        self,
        console: Console,
        source_label: str,
        output_dir: str,
        runlog: Optional[RunLog] = None,
    ):
        self.console = console
        self.source_label = source_label
        self.output_dir = output_dir
        self.stats = Stats()
        self.runlog = runlog
        self._log: deque[Text] = deque(maxlen=_LOG_LINES)
        self._lock = threading.RLock()
        self._started = False

        self.queue_progress = Progress(
            TextColumn("[bold cyan]Queue [/bold cyan]"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("{task.completed}/{task.total} links"),
            console=console,
        )
        self._queue_task = self.queue_progress.add_task("queue", total=1)

        self.tracks_progress = Progress(
            TextColumn("[bold cyan]Tracks[/bold cyan]"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("{task.completed}/{task.total} tracks"),
            console=console,
        )
        self._tracks_task = self.tracks_progress.add_task("tracks", total=1)

        # One row per slot: the Spotify run uses slot 0, parallel SoundCloud
        # downloads use one slot per download worker.
        self.file_progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.fields[label]}", justify="left", markup=False),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("{task.fields[speed]}", markup=False),
            TextColumn("{task.fields[eta]}", markup=False),
            console=console,
        )
        self._file_tasks: dict[int, TaskID] = {}

        self._live = Live(self._render(), console=console, refresh_per_second=8)

    def __enter__(self) -> "Dashboard":
        self._live.start()
        self._started = True
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._started = False
        self._live.stop()

    # -- updates called from the downloader modules -------------------------
    def log(self, message: str) -> None:
        """Adds a line to the live 'Activity' panel only. Use this for
        routine/informational events. For failures, prefer `log_error` so
        the failure also survives in the run's log file."""
        with self._lock:
            self._log.append(Text(str(message)))
        self._refresh()

    def log_error(self, source: str, message: str) -> None:
        """Records a failed operation: shows it in the Activity panel AND
        appends it to the persistent run log file (if one is configured),
        so it isn't lost once it scrolls off screen or the run ends."""
        with self._lock:
            self._log.append(Text(f"[{source}][!] {message}", style="red"))
        self._refresh()
        if self.runlog is not None:
            self.runlog.record(source, message)

    def set_queue(self, completed: int, total: int) -> None:
        with self._lock:
            self.queue_progress.update(
                self._queue_task, completed=completed, total=max(total, 1)
            )
        self._refresh()

    def add_tracks_total(self, kind: str, count: int) -> None:
        """Adds newly discovered tracks to the running total."""
        if count <= 0:
            return
        self._bump(f"{kind}_tracks_total", count)

    def record_track(self, kind: str, status: str, amount: int = 1) -> None:
        """Records the outcome of tracks; a negative amount corrects an
        earlier live estimate once the exact result is known."""
        if amount:
            self._bump(f"{kind}_tracks_{status}", amount)

    def record_unavailable(self, kind: str, track: UnavailableTrack, note: str = "") -> None:
        """A track that cannot be downloaded and is skipped: counted apart from
        failures, explained in the Activity panel and listed in the run's
        unavailable-tracks file (not in the failure log)."""
        self.record_track(kind, "unavailable")
        self.log(f"[{track.source}] {track.message(note)}")
        if self.runlog is not None:
            self.runlog.record_unavailable(track)

    def record_lyrics(self, found: bool) -> None:
        """Records one lyrics lookup result (found / not found)."""
        self._bump("lyrics_ok" if found else "lyrics_fail", 1)

    def record_lyrics_skipped(self) -> None:
        """A track that is deliberately left without lyrics (instrumental,
        a variant in strict mode, a recent failed search)."""
        self._bump("lyrics_skipped", 1)

    def record(self, kind: str, ok: bool) -> None:
        """Records the outcome of a whole link."""
        self._bump(f"{kind}_{'ok' if ok else 'fail'}", 1)

    def _bump(self, attr: str, amount: int) -> None:
        with self._lock:
            if not hasattr(self.stats, attr):
                # An unknown counter name must never crash a worker thread.
                self._log.append(Text(f"[UI][!] Unknown counter '{attr}'"))
            else:
                setattr(self.stats, attr, max(0, getattr(self.stats, attr) + amount))
        self._refresh()

    def _file_task(self, slot: int) -> TaskID:
        task = self._file_tasks.get(slot)
        if task is None:
            task = self.file_progress.add_task(
                f"file-{slot}", total=100, label="Idle", speed="", eta="", visible=False
            )
            self._file_tasks[slot] = task
        return task

    def start_file(self, label: str, slot: int = 0) -> None:
        with self._lock:
            task = self._file_task(slot)
            self.file_progress.reset(task)
            self.file_progress.update(
                task, total=100, completed=0, label=label,
                speed="", eta="", visible=True,
            )
        self._refresh()

    def update_file(self, percent: float | None = None, label: str | None = None,
                    speed: str | None = None, eta: str | None = None, slot: int = 0) -> None:
        fields = {}
        if label is not None:
            fields["label"] = label
        if speed is not None:
            fields["speed"] = speed
        if eta is not None:
            fields["eta"] = eta
        if percent is not None:
            fields["completed"] = percent
        with self._lock:
            task = self._file_task(slot)
            self.file_progress.update(task, visible=True, **fields)
        self._refresh()

    def finish_file(self, slot: int | None = 0) -> None:
        """Hides one progress row, or all of them when `slot` is None."""
        with self._lock:
            slots = list(self._file_tasks) if slot is None else [slot]
            for item in slots:
                task = self._file_tasks.get(item)
                if task is not None:
                    self.file_progress.update(task, visible=False)
        self._refresh()

    # -- rendering ------------------------------------------------------------
    @staticmethod
    def _track_line(done: int, skipped: int, failed: int, total: int, unavailable: int = 0) -> str:
        total = max(total, done + skipped + failed + unavailable)
        extra = f" / [yellow]{unavailable} unavailable[/yellow]" if unavailable else ""
        return (
            f"[green]{done} downloaded[/green] / [cyan]{skipped} already had[/cyan] / "
            f"[red]{failed} failed[/red]{extra} [dim](of {total} found so far)[/dim]"
        )

    def _stats_table(self) -> Table:
        s = self.stats
        table = Table.grid(padding=(0, 2))
        table.add_column(justify="right", style="bold")
        table.add_column()
        table.add_row("Source:", escape(self.source_label))
        table.add_row("Destination:", escape(self.output_dir))
        if self.runlog is not None:
            table.add_row("Failure log:", f"[dim]{escape(str(self.runlog.path))}[/dim]")
            if self.runlog.unavailable_count:
                table.add_row("Unavailable:", f"[dim]{escape(str(self.runlog.unavailable_path))}[/dim]")
        table.add_row(
            "Spotify links:",
            f"[green]{s.spotify_ok} ok[/green] / [red]{s.spotify_fail} failed[/red]",
        )
        table.add_row(
            "Spotify tracks:",
            self._track_line(s.spotify_tracks_done, s.spotify_tracks_skipped,
                             s.spotify_tracks_failed, s.spotify_tracks_total),
        )
        table.add_row(
            "SoundCloud links:",
            f"[green]{s.soundcloud_ok} ok[/green] / [red]{s.soundcloud_fail} failed[/red]",
        )
        table.add_row(
            "SoundCloud tracks:",
            self._track_line(s.soundcloud_tracks_done, s.soundcloud_tracks_skipped,
                             s.soundcloud_tracks_failed, s.soundcloud_tracks_total,
                             s.soundcloud_tracks_unavailable),
        )
        table.add_row(
            "Lyrics:",
            f"[green]{s.lyrics_ok} found[/green] / [yellow]{s.lyrics_fail} not found[/yellow]"
            f" / [dim]{s.lyrics_skipped} skipped[/dim]",
        )
        return table

    def _sync_tracks_progress(self) -> None:
        s = self.stats
        total = s.spotify_tracks_total + s.soundcloud_tracks_total
        done = (
            s.spotify_tracks_done + s.spotify_tracks_skipped + s.spotify_tracks_failed
            + s.soundcloud_tracks_done + s.soundcloud_tracks_skipped
            + s.soundcloud_tracks_failed + s.soundcloud_tracks_unavailable
        )
        total = max(total, done)
        self.tracks_progress.update(self._tracks_task, completed=done, total=max(total, 1))

    def _render(self) -> Group:
        self._sync_tracks_progress()
        log_text = Text("\n").join(self._log) if self._log else Text("...")
        return Group(
            Panel(self._stats_table(), title="Music Loader", border_style="blue"),
            self.queue_progress,
            self.tracks_progress,
            self.file_progress,
            Panel(
                log_text,
                title="Activity",
                subtitle="[dim]recent events, newest at bottom - failures are also saved "
                         "to the log file above[/dim]",
                border_style="grey50",
            ),
        )

    def _refresh(self) -> None:
        if not self._started:
            return
        with self._lock:
            self._live.update(self._render())
