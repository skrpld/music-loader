"""In-process tool runs (the Android phone mode), exercised with stand-in tools."""
import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rich.console import Console

from music_loader import cli, inprocess, process, server
from music_loader.links import parse_link


def _talker(name: str, count: int):
    def tool(args):
        def helper():
            print(f"{name} helper")

        thread = threading.Thread(target=helper)
        thread.start()
        thread.join()
        for index in range(count):
            print(f"{name} {index}")
            time.sleep(0.01)
        return 0

    return tool


def _sleeper(args):
    while True:
        time.sleep(0.05)


def test_tool_command_runs_in_process(monkeypatch):
    monkeypatch.setattr(inprocess, "ENABLED", True)
    command = process.tool_command("yt-dlp")
    assert command == [inprocess.MARK, "yt_dlp"]
    assert process.runs_in_this_python(command)


def test_yt_dlp_runs_in_process_with_its_exit_status():
    code, stdout, _stderr = process.run_captured([inprocess.MARK, "yt_dlp", "--version"], timeout=60)
    assert code == 0
    assert stdout.strip()


def test_parallel_runs_keep_their_output_apart(monkeypatch):
    monkeypatch.setitem(inprocess._TOOLS, "a", _talker("a", 20))
    monkeypatch.setitem(inprocess._TOOLS, "b", _talker("b", 20))
    lines = {"a": [], "b": []}

    def run(name):
        return process.run_streamed([inprocess.MARK, name], lines[name].append, timeout=30)

    with ThreadPoolExecutor(2) as pool:
        codes = list(pool.map(run, ["a", "b"]))
    assert codes == [0, 0]
    for name in ("a", "b"):
        assert f"{name} helper" in lines[name]  # a thread the tool started
        assert [line for line in lines[name] if line != f"{name} helper"] == [f"{name} {i}" for i in range(20)]


def test_abort_stops_the_tool(monkeypatch):
    monkeypatch.setitem(inprocess._TOOLS, "sleeper", _sleeper)
    started = time.monotonic()
    abort_at = started + 0.3
    code = process.run_streamed([inprocess.MARK, "sleeper"], lambda line: None, timeout=30,
                                should_abort=lambda: time.monotonic() > abort_at)
    assert code == -2
    assert time.monotonic() - started < 10
    assert not any(thread.name == "sleeper-run" for thread in threading.enumerate())


def test_interrupt_reaches_a_thread_waiting_for_a_future():
    result = {}

    def worker():
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(time.sleep, 2)
            try:
                process.wait_future(future)
                result["outcome"] = "finished"
            except KeyboardInterrupt:
                result["outcome"] = "interrupted"
                result["at"] = time.monotonic()

    thread = threading.Thread(target=worker)
    thread.start()
    time.sleep(0.2)
    started = time.monotonic()
    inprocess.raise_in(thread, KeyboardInterrupt)
    thread.join(10)
    assert result["outcome"] == "interrupted"
    # Within the polling interval, not when the 2-second task ends.
    assert result["at"] - started < 1.0


def test_in_process_job_is_cancelled(monkeypatch, tmp_path: Path):
    monkeypatch.setitem(inprocess._TOOLS, "sleeper", _sleeper)

    def process_links(links, config, dashboard):
        dashboard.log("started")
        process.run_streamed([inprocess.MARK, "sleeper"], lambda line: None, timeout=60)

    monkeypatch.setattr(cli, "process_links", process_links)
    manager = server.JobManager(tmp_path, {}, Console(file=io.StringIO()), in_process=True)
    manager.start()
    try:
        job = manager.submit([parse_link("https://soundcloud.com/artist/track")], {}, [])
        deadline = time.monotonic() + 10
        while manager.job(job.id)["status"] != "running" and time.monotonic() < deadline:
            time.sleep(0.05)
        manager.cancel(job.id)
        while manager.job(job.id)["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.05)
        detail = manager.job(job.id)
        assert detail["status"] == "cancelled"
        assert any(entry["text"] == "started" for entry in detail["log"])
    finally:
        manager.shutdown(timeout=10)
