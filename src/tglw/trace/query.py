"""What the trace tool shows: the last readings recorded on this machine, in the reading language."""

from __future__ import annotations

from pathlib import Path

from . import events, render
from .model import Trace

NONE = "no readings recorded yet"


def readings(last: int = 1, doc: str | None = None, directory: Path | None = None) -> list[Trace]:
    """The `last` most recent readings (one per conversation of a server process), newest first; `doc` keeps
    readings of that document only."""
    from tglw.store import traces as store

    out: list[Trace] = []
    for tf in store.list_traces(directory):
        found = events.traces_of(store.read_events(tf, directory))
        for t in sorted(found, key=_ended, reverse=True):
            if doc and t.doc != doc:
                continue
            out.append(t)
            if len(out) >= last:
                return out
    return out


def _ended(t: Trace) -> float:
    return max((s.call.t1 or 0.0 for s in t.steps), default=0.0)


def show(last: int = 1, ignored: bool = False, doc: str | None = None, directory: Path | None = None) -> str:
    """The text the trace tool returns."""
    found = readings(max(1, last), doc, directory)
    if not found:
        return NONE
    parts = []
    for i, t in enumerate(found, 1):
        head = f"reading {i} · doc {t.doc or '-'} · {len(t.steps)} steps"
        parts.append(head + "\n" + render.render(t, ignored_only=ignored))
    return "\n\n".join(parts)
