"""Live terminal dashboard built on `rich`: overall stats, a link-queue
progress bar, a per-file progress bar (percent/speed/ETA when available),
and a scrolling panel showing what is happening right now."""
from collections import deque
from dataclasses import dataclass

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table

_LOG_LINES = 10


@dataclass
class Stats:
    spotify_ok: int = 0
    spotify_fail: int = 0
    soundcloud_ok: int = 0
    soundcloud_fail: int = 0
    lyrics_ok: int = 0
    lyrics_fail: int = 0


class Dashboard:
    def __init__(self, console: Console, source_label: str, output_dir: str):
        self.console = console
        self.source_label = source_label
        self.output_dir = output_dir
        self.stats = Stats()
        self._log: deque[str] = deque(maxlen=_LOG_LINES)

        self.queue_progress = Progress(
            TextColumn("[bold cyan]Queue[/bold cyan]"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("{task.completed}/{task.total} links"),
            console=console,
        )
        self._queue_task = self.queue_progress.add_task("queue", total=1)

        self.file_progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.fields[label]}", justify="left"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("{task.fields[speed]}"),
            TextColumn("{task.fields[eta]}"),
            console=console,
        )
        self._file_task = self.file_progress.add_task(
            "file", total=100, label="Idle", speed="", eta="", visible=False
        )

        self._live = Live(self._render(), console=console, refresh_per_second=8)

    def __enter__(self) -> "Dashboard":
        self._live.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._live.stop()

    # -- updates called from the downloader modules -------------------------
    def log(self, message: str) -> None:
        self._log.append(message)
        self._refresh()

    def set_queue(self, completed: int, total: int) -> None:
        self.queue_progress.update(self._queue_task, completed=completed, total=max(total, 1))
        self._refresh()

    def start_file(self, label: str) -> None:
        self.file_progress.reset(self._file_task)
        self.file_progress.update(
            self._file_task, total=100, completed=0, label=label, speed="", eta="", visible=True
        )
        self._refresh()

    def update_file(self, percent: float | None = None, label: str | None = None,
                     speed: str | None = None, eta: str | None = None) -> None:
        fields = {}
        if label is not None:
            fields["label"] = label
        if speed is not None:
            fields["speed"] = speed
        if eta is not None:
            fields["eta"] = eta
        if percent is not None:
            fields["completed"] = percent
        self.file_progress.update(self._file_task, **fields)
        self._refresh()

    def finish_file(self) -> None:
        self.file_progress.update(self._file_task, visible=False)
        self._refresh()

    def record(self, kind: str, ok: bool) -> None:
        attr = f"{kind}_{'ok' if ok else 'fail'}"
        setattr(self.stats, attr, getattr(self.stats, attr) + 1)
        self._refresh()

    # -- rendering ------------------------------------------------------------
    def _stats_table(self) -> Table:
        table = Table.grid(padding=(0, 2))
        table.add_column(justify="right", style="bold")
        table.add_column()
        table.add_row("Source:", self.source_label)
        table.add_row("Destination:", self.output_dir)
        table.add_row(
            "Spotify:",
            f"[green]{self.stats.spotify_ok} ok[/green] / [red]{self.stats.spotify_fail} failed[/red]",
        )
        table.add_row(
            "SoundCloud:",
            f"[green]{self.stats.soundcloud_ok} ok[/green] / [red]{self.stats.soundcloud_fail} failed[/red]",
        )
        table.add_row(
            "Lyrics:",
            f"[green]{self.stats.lyrics_ok} found[/green] / [yellow]{self.stats.lyrics_fail} missing[/yellow]",
        )
        return table

    def _render(self) -> Group:
        log_text = "\n".join(self._log) or "..."
        return Group(
            Panel(self._stats_table(), title="Music Loader", border_style="blue"),
            self.queue_progress,
            self.file_progress,
            Panel(log_text, title="Activity", border_style="grey50"),
        )

    def _refresh(self) -> None:
        self._live.update(self._render())
