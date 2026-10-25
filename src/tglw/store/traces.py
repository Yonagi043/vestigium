"""Reading traces on disk: one JSONL file per server process, written by append(), read by list_traces()
and read_events(). The events themselves are built by tglw.trace.record, which also defines their fields.

Files: <trace_dir()>/<UTC YYYYmmddTHHMMSSZ>-<proc id, 12 chars>.jsonl, mode 0600. The directory is made 0700
when this module creates it; an existing one keeps its permissions. A file past MAX_FILE_BYTES continues in
<stem>.1.jsonl, <stem>.2.jsonl, ...; each part starts with the process's "proc" event again, so every file
reads on its own. When all trace files together pass the total limit
(TGLW_TRACE_MAX_MB, default 100 MB), the oldest are deleted first; files written in the last
ACTIVE_SECONDS (another server may be writing them) and this process's own file are kept.

append() never raises: a failed write is dropped, reported once on stderr, and the tool call goes on.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tglw.common.paths import trace_dir

MAX_FILE_BYTES = 8 << 20
MAX_TOTAL_ENV = "TGLW_TRACE_MAX_MB"
MAX_TOTAL_MB = 100
ACTIVE_SECONDS = 60.0
_NAME = re.compile(r"^(?P<stem>\d{8}T\d{6}Z-[0-9A-Za-z]+)(?:\.(?P<part>\d+))?\.jsonl$")


def max_total_bytes() -> int:
    try:
        return int(float(os.environ.get(MAX_TOTAL_ENV, MAX_TOTAL_MB)) * (1 << 20))
    except ValueError:
        return MAX_TOTAL_MB << 20


@dataclass(frozen=True)
class TraceFile:
    """One server process's trace: its parts in order, total size, and what its "proc" event says."""

    stem: str
    files: tuple[Path, ...]
    bytes: int
    modified: float
    proc: dict[str, Any] | None

    @property
    def run(self) -> str | None:
        return (self.proc or {}).get("run")


class Writer:
    """Appends events for one process. Thread-safe; follows trace_dir() if it changes between calls."""

    def __init__(self, directory: Path | None = None, max_file_bytes: int = MAX_FILE_BYTES,
                 max_bytes: int | None = None) -> None:
        self._fixed_dir = directory
        self._max_file = max_file_bytes
        self._max_total = max_bytes
        self._lock = threading.Lock()
        self._dir: Path | None = None
        self._fd: int | None = None
        self._path: Path | None = None
        self._size = 0
        self._part = 0
        self._stem: str | None = None
        self._proc_line: bytes | None = None
        self._warned = False

    @property
    def path(self) -> Path | None:
        return self._path

    def append(self, event: dict[str, Any]) -> None:
        try:
            line = (json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=str) + "\n").encode()
            with self._lock:
                if event.get("ev") == "proc":
                    self._proc_line = line
                self._write(line, is_proc=event.get("ev") == "proc")
        except Exception as err:  # noqa: BLE001 - a trace must never break a tool call
            self._warn(err)

    def close(self) -> None:
        with self._lock:
            self._close()

    def _write(self, line: bytes, is_proc: bool) -> None:
        directory = self._fixed_dir or trace_dir()
        if self._fd is None or directory != self._dir:
            self._open(directory, part=0)
        elif self._size + len(line) > self._max_file and self._size:
            self._open(directory, part=self._part + 1)
        if self._size == 0 and not is_proc and self._proc_line:  # every part starts with the proc event
            self._raw(self._proc_line)
        self._raw(line)

    def _open(self, directory: Path, part: int) -> None:
        self._close()
        try:
            directory.mkdir(parents=True, mode=0o700)
            os.chmod(directory, 0o700)  # mkdir's mode is narrowed by the umask; a directory of our own is 0700
        except FileExistsError:
            pass  # someone else's directory (an evaluation run's, /tmp, ...) keeps its permissions
        if part == 0 or self._stem is None or directory != self._dir:
            proc = re.sub(r"[^0-9A-Za-z]", "", str(json.loads(self._proc_line or b"{}").get("proc") or "")) \
                or f"{os.getpid()}"
            self._stem = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{proc[:12]}"
            part = 0
        self._dir, self._part = directory, part
        self._path = directory / (f"{self._stem}.jsonl" if part == 0 else f"{self._stem}.{part}.jsonl")
        self._fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC, 0o600)
        os.fchmod(self._fd, 0o600)
        self._size = os.fstat(self._fd).st_size
        prune(self._max_total, directory, keep=(self._path,))

    def _raw(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            view = view[os.write(self._fd, view):]
        self._size += len(data)

    def _close(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            finally:
                self._fd = None

    def _warn(self, err: Exception) -> None:
        if not self._warned:
            self._warned = True
            print(f"vestigium: a reading trace was not written ({type(err).__name__}: {err}); "
                  "tool results are not affected", file=sys.stderr, flush=True)


_writer = Writer()


def append(event: dict[str, Any]) -> None:
    """Write one event to this process's trace file. Never raises."""
    _writer.append(event)


def current_file() -> Path | None:
    return _writer.path


def _files(directory: Path) -> list[tuple[str, int, Path]]:
    out = []
    for p in directory.iterdir() if directory.is_dir() else []:
        m = _NAME.match(p.name)
        if m and p.is_file():
            out.append((m["stem"], int(m["part"] or 0), p))
    return sorted(out)


def _first_event(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("rb") as f:
            event = json.loads(f.readline())
        return event if isinstance(event, dict) and event.get("ev") == "proc" else None
    except (OSError, ValueError):
        return None


def list_traces(directory: Path | None = None) -> list[TraceFile]:
    """Trace files of every process under directory (default trace_dir()), newest first."""
    groups: dict[str, list[Path]] = {}
    for stem, _, path in _files(directory or trace_dir()):
        groups.setdefault(stem, []).append(path)
    out = []
    for stem, parts in groups.items():
        stats = [p.stat() for p in parts if p.exists()]
        out.append(TraceFile(stem, tuple(parts), sum(s.st_size for s in stats), max((s.st_mtime for s in stats), default=0.0),
                             _first_event(parts[0])))
    return sorted(out, key=lambda t: t.stem, reverse=True)


def read_events(trace: TraceFile | str, directory: Path | None = None) -> Iterator[dict[str, Any]]:
    """The events of one process in written order: its proc event once, then the rest. A line that does not
    parse (a write cut short) is skipped."""
    if isinstance(trace, str):
        trace = next((t for t in list_traces(directory) if t.stem == trace), None)
        if trace is None:
            return
    seen_proc = False
    for path in trace.files:
        try:
            lines = path.read_bytes().splitlines()
        except OSError:
            continue
        for raw in lines:
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("ev") == "proc":
                if seen_proc:
                    continue
                seen_proc = True
            yield event


def prune(max_bytes: int | None = None, directory: Path | None = None, keep: tuple[Path, ...] = ()) -> list[Path]:
    """Delete the oldest trace files until all of them together fit in max_bytes (default: the
    TGLW_TRACE_MAX_MB limit). Files in keep, and files written in the last ACTIVE_SECONDS, stay."""
    limit = max_total_bytes() if max_bytes is None else max_bytes
    files = [p for _, _, p in _files(directory or trace_dir())]
    sizes = {p: p.stat().st_size for p in files if p.exists()}
    total, removed, now = sum(sizes.values()), [], time.time()
    for p in files:
        if total <= limit:
            break
        if p in keep or p not in sizes or now - p.stat().st_mtime < ACTIVE_SECONDS:
            continue
        try:
            p.unlink()
        except FileNotFoundError:
            continue
        total -= sizes[p]
        removed.append(p)
    return removed


def clear(directory: Path | None = None) -> int:
    """Delete every trace file under directory (default trace_dir()); this process starts a new file on its
    next event. Returns how many files were deleted."""
    with _writer._lock:
        _writer._close()
        _writer._dir = None
    n = 0
    for _, _, p in _files(directory or trace_dir()):
        try:
            p.unlink()
            n += 1
        except FileNotFoundError:
            pass
    return n
