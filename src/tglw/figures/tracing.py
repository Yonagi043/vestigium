"""What figures reports to the reading trace (tglw.trace.record). Work done only for the trace never costs the
tool call: it runs only while a call is being recorded, and an error in it is dropped and counted as lost
(tglw.views.tracing), so the tool returns what it would anyway."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from tglw.trace import record


def lost(what: str) -> None:
    """Count a report the trace does not get (views.tracing.lost; imported late, as views imports figures)."""
    try:
        from tglw.views.tracing import lost as count

        count(what)
    except Exception:  # noqa: BLE001 - counting is itself only for the trace
        pass


def guarded(fn: Callable[..., Any], *args: Any) -> None:
    """fn(*args) for the current call's trace; nothing without one; an error in it is counted as lost."""
    if record.current() is None:
        return
    try:
        fn(*args)
    except Exception:  # noqa: BLE001 - see the module docstring
        lost(f"error:{getattr(fn, '__name__', 'fig_trace')}")


def added(before: str, after: str) -> int | None:
    """o200k tokens `after` has over `before`; None when no call is being recorded or counting fails."""
    if record.current() is None:
        return None
    try:
        from tglw.common.text import count_tokens

        return count_tokens(after) - count_tokens(before)
    except Exception:  # noqa: BLE001 - see the module docstring
        lost("error:fig_tokens")
        return None


@contextmanager
def discarded() -> Iterator[None]:
    """Reports made inside are dropped from the current call: work (a rule's extra render) whose result the
    caller reports itself."""
    try:
        m = record.mark()
    except Exception:  # noqa: BLE001 - see the module docstring
        m = None
        lost("error:fig_mark")
    try:
        yield
    finally:
        try:
            record.rewind(m)
        except Exception:  # noqa: BLE001 - see the module docstring
            lost("error:fig_rewind")
