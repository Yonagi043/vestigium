"""Work done only for reading traces (tglw.trace.record), run so that an error in it costs the trace, never the tool
call: it is dropped and counted as lost (the next call event's `lost`), and the tool returns what it would anyway."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tglw.trace import record


def lost(what: str) -> None:
    """Count a report the trace does not get (record.lost, which never raises)."""
    record.lost(what)


def guarded(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    """fn(*args, **kwargs) for the current call's trace; nothing without one; an error in it is counted as lost."""
    if record.current() is None:
        return
    try:
        fn(*args, **kwargs)
    except Exception:  # noqa: BLE001 - see the module docstring
        lost(f"error:{getattr(fn, '__name__', 'trace')}")


def report(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    """record.sig / dec / addr / out / doc (fn) called so that even a malformed report -- a field named like one of
    fn's own parameters, say -- costs the trace, not the call. Unlike guarded it runs with no call current too, so
    that record counts such a report as lost."""
    try:
        fn(*args, **kwargs)
    except Exception:  # noqa: BLE001 - see the module docstring
        lost(f"error:{getattr(fn, '__name__', 'report')}")
