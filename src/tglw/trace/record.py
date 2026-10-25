"""Collects one raw event per tool call (fields: `Event` and `validate` below); it does not write files.

The server wraps each tool call in `call(...)`; code deeper down reports what it shows or decides with `sig`,
`dec`, `addr` and `out`, which find the current call through a context variable. When the call ends the
event goes to the sink the server set (`set_sink`, the store's append); without a sink it is dropped.
With TGLW_TRACE=0 nothing is collected. A thread started inside a call does not see the call unless
started through `contextvars.copy_context().run`; what it reports is then dropped, never misfiled, and
counted: the next call event carries the counts in `lost` (and the end event the rest), so a trace that
misses something says so. Recording never fails a tool call: every public function here swallows its own
errors and counts them in `lost` (as "error:<what>"), and the tool's own result and errors pass unchanged.
"""

from __future__ import annotations

import contextvars
import functools
import hashlib
import itertools
import os
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

FORMAT = 0
TRACE_ENV = "TGLW_TRACE"
QUERY_ENV = "TGLW_TRACE_QUERY"
RUN_ENV = "VESTIGIUM_TRACE_RUN"
REFS_MAX = 200

SIGNALS = {"hits", "exact", "ident", "zero", "saturated", "continues", "next", "cut", "too_long", "scan", "qc",
           "fig_hint", "table_note", "struct"}
DECISIONS = {"grow", "adjacent", "exact_note", "ident_hits", "saturation_note", "seen_marks", "auto_zoom", "shrink", "scan_notes",
             "split", "qc_notes", "fig_hints", "table_notes", "rule"}
TOOLS = {"list_documents", "outline", "search", "read", "context", "region", "rules"}  # rules: the rule tool (stage 2)

PROC = uuid.uuid4().hex[:12]
_seq = itertools.count(1)
_seq_lock = threading.Lock()
_current: contextvars.ContextVar[Event | None] = contextvars.ContextVar("tglw_trace_call", default=None)
_quiet: contextvars.ContextVar[bool] = contextvars.ContextVar("tglw_trace_quiet", default=False)
_via: contextvars.ContextVar[str | None] = contextvars.ContextVar("tglw_trace_via", default=None)
_capture: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar("tglw_trace_capture", default=None)
_sink: Callable[[dict[str, Any]], None] | None = None
_started = False
_start_lock = threading.Lock()
_lost: Counter[str] = Counter()  # reports made with no call current, by kind, since the last call event
_lost_lock = threading.Lock()


def lost(what: str) -> None:
    """Count a report that could not be made (no call current, or the report raised); the next call event, or
    the end event, carries the counts. Never raises."""
    try:
        if enabled():
            with _lost_lock:
                _lost[what] += 1
    except Exception:  # noqa: BLE001 - recording must never fail a tool call
        pass


def _drop(what: str) -> None:
    if _quiet.get():
        return  # work outside any call on purpose (e.g. warming caches): not a loss
    lost(what)


def _guarded(what: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """The function, with its errors swallowed and counted as lost "error:<what>" (it then returns None)."""
    def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def safe(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except Exception:  # noqa: BLE001 - recording must never fail a tool call
                lost(f"error:{what}")
                return None
        return safe
    return wrap


def _take_lost() -> dict[str, int]:
    with _lost_lock:
        got = dict(_lost)
        _lost.clear()
    return got


def enabled() -> bool:
    return os.environ.get(TRACE_ENV, "1").strip().lower() not in ("0", "false", "off", "no")


def set_sink(fn: Callable[[dict[str, Any]], None] | None) -> None:
    """Where finished events go (the store's append). None: events are dropped."""
    global _sink
    _sink = fn


def _emit(event: dict[str, Any]) -> None:
    if _sink is None:
        return
    try:
        _sink(event)
    except Exception:  # noqa: BLE001 - recording must never change a tool's result
        pass


@_guarded("start")
def start(**info: Any) -> None:
    """The process event, once per process before its first call: package and data versions, switches, rules."""
    global _started
    if not enabled():
        return
    with _start_lock:
        if _started:
            return
        _started = True
    _emit({"v": FORMAT, "ev": "proc", "proc": PROC, "t": time.time(), "run": os.environ.get(RUN_ENV) or None, **info})


@_guarded("end")
def end() -> None:
    if enabled() and _started:
        lost = _take_lost()
        _emit({"v": FORMAT, "ev": "end", "proc": PROC, "t": time.time(), **({"lost": lost} if lost else {})})


def said(text: str) -> object:
    """Text the caller wrote (a query, or a key taken from one) as a trace keeps it: as it is, or with
    TGLW_TRACE_QUERY=0 its length and a hash."""
    try:
        if os.environ.get(QUERY_ENV, "1").strip().lower() not in ("0", "false", "off", "no"):
            return text
        text = str(text)
        return {"len": len(text), "sha1": hashlib.sha1(text.encode()).hexdigest()[:12]}
    except Exception:  # noqa: BLE001 - recording must never fail a tool call
        lost("error:said")
        return {"len": None, "sha1": None}


def _query(args: dict[str, Any]) -> dict[str, Any]:
    if "query" not in args:
        return args
    q = said(str(args["query"]))
    return args if isinstance(q, str) else {**args, "query": q}


def _via_field(fields: dict[str, Any]) -> dict[str, Any]:
    v = _via.get()
    return {"via": v} if v and "via" not in fields else {}


class Event:
    """One tool call being recorded."""

    def __init__(self, tool: str, args: dict[str, Any], conv: str | None, client: dict[str, Any] | None,
                 **extra: Any) -> None:
        with _seq_lock:
            seq = next(_seq)
        self.data: dict[str, Any] = {
            "v": FORMAT, "ev": "call", "proc": PROC, "seq": seq, "conv": conv or "stdio", "client": client,
            "t0": time.time(), "t1": None, "tool": tool,
            "thread": f"{threading.current_thread().name}:{threading.get_native_id()}",  # worker threads share a name
            "args": _query(dict(args)), "doc": None, "addr": [], "out": {}, "sig": [], "dec": [], "err": None, **extra}
        self._refs: list[str] = []  # every block reported, before the REFS_MAX cap
        if lost := _take_lost():
            self.data["lost"] = lost  # reports since the previous call that found no call to go with

    @_guarded("sig")
    def sig(self, k: str, **fields: Any) -> None:
        self.data["sig"].append({"k": k, **fields, **_via_field(fields)})

    @_guarded("dec")
    def dec(self, k: str, by: str = "tool", src: str = "", **fields: Any) -> None:
        self.data["dec"].append({"k": k, "by": by, "src": src, **fields, **_via_field(fields)})

    @_guarded("addr")
    def addr(self, inp: str, **fields: Any) -> None:
        self.data["addr"].append({"in": inp, **fields})

    @_guarded("out")
    def out(self, **fields: Any) -> None:
        """Merged over the call: pages as a sorted union, units and refs in order without repeats (refs capped
        at REFS_MAX after merging, with refs_n the full count when capped), other fields replaced."""
        o = self.data["out"]
        if "pages" in fields:
            o["pages"] = sorted(set(o.get("pages", [])) | set(fields.pop("pages")))
        if "units" in fields:
            o["units"] = list(dict.fromkeys([*o.get("units", []), *fields.pop("units")]))
        if "refs" in fields:
            self._refs = list(dict.fromkeys([*self._refs, *fields.pop("refs")]))
            o["refs"] = self._refs[:REFS_MAX]
            if len(self._refs) > REFS_MAX:
                o["refs_n"] = len(self._refs)
            else:
                o.pop("refs_n", None)
        o.update(fields)

    @_guarded("doc")
    def doc(self, name: str | list[str]) -> None:
        self.data["doc"] = name

    @_guarded("result")
    def result(self, value: Any) -> None:
        """Size of what the tool returned: o200k tokens and characters of its text parts, parts, images."""
        from tglw.common.text import count_tokens

        items = value if isinstance(value, list) else [value]
        texts = [x for x in items if isinstance(x, str)]
        images = sum(1 for x in items if not isinstance(x, str) and x is not None)
        self.data["out"].update(tokens=sum(count_tokens(t) for t in texts), chars=sum(len(t) for t in texts),
                                parts=len(texts), images=images)

    @_guarded("error")
    def error(self, err: BaseException) -> None:
        self.data["err"] = {"type": type(err).__name__, "msg": str(err)[:200]}


class _Null:
    """Stands in for an event when recording is off: every method does nothing."""

    def __getattr__(self, name: str) -> Callable[..., None]:
        return lambda *a, **k: None


NULL = _Null()


@contextmanager
def call(tool: str, args: dict[str, Any], conv: str | None = None, client: dict[str, Any] | None = None,
         collect: bool = False, **extra: Any) -> Iterator[Event | _Null]:
    """Record one tool call: the event is current inside the block and goes to the sink when it ends (also on
    an error, which is recorded and raised again). An event that cannot be made is counted as lost and the
    call runs unrecorded. With TGLW_TRACE=0 nothing is collected unless `collect` (rules are in effect: the
    executor needs the call's signals) or a `capture` is open; such an event goes to no sink."""
    cap = _capture.get()
    if not (enabled() or collect or cap is not None):
        yield NULL
        return
    try:
        ev = Event(tool, args, conv, client, **extra)
        token = _current.set(ev)
    except Exception:  # noqa: BLE001 - recording must never fail a tool call
        lost("error:call")
        yield NULL
        return
    try:
        yield ev
    except BaseException as err:
        ev.error(err)
        raise
    finally:
        try:
            ev.data["t1"] = time.time()
            _current.reset(token)
        except Exception:  # noqa: BLE001
            lost("error:call")
        if cap is not None:
            cap.append(ev.data)
        elif enabled():
            _emit(ev.data)


def current() -> Event | None:
    return _current.get()


@contextmanager
def via(src: str) -> Iterator[None]:
    """What is reported inside carries "via": src -- what a rule added, and what that brought (LANG_SPEC 11.8)."""
    token = _via.set(src)
    try:
        yield
    finally:
        _via.reset(token)


@contextmanager
def capture() -> Iterator[list[dict[str, Any]]]:
    """Calls that end inside, in this context only, go to the yielded list instead of the sink, recording on or
    off (a preview replays past calls and must not write them into the real traces)."""
    got: list[dict[str, Any]] = []
    token = _capture.set(got)
    try:
        yield got
    finally:
        _capture.reset(token)


@contextmanager
def quiet() -> Iterator[None]:
    """Reports made inside are dropped without counting them as lost (work outside any call on purpose)."""
    token = _quiet.set(True)
    try:
        yield
    finally:
        _quiet.reset(token)


@_guarded("mark")
def mark() -> tuple[int, int, int, dict[str, Any], list[str]] | None:
    """A point to rewind the current call's reports to, for work that is computed and then thrown away."""
    ev = _current.get()
    if ev is None:
        return None
    d = ev.data
    return len(d["sig"]), len(d["dec"]), len(d["addr"]), dict(d["out"]), list(ev._refs)


@_guarded("rewind")
def rewind(m: tuple[int, int, int, dict[str, Any], list[str]] | None,
           kinds: tuple[str, ...] = ("sig", "dec", "addr", "out")) -> None:
    """Drop what the current call reported after `mark()`, for the kinds named."""
    ev = _current.get()
    if ev is None or m is None:
        return
    n_sig, n_dec, n_addr, out_, refs = m
    d = ev.data
    if "sig" in kinds:
        del d["sig"][n_sig:]
    if "dec" in kinds:
        del d["dec"][n_dec:]
    if "addr" in kinds:
        del d["addr"][n_addr:]
    if "out" in kinds:
        d["out"] = dict(out_)
        ev._refs = list(refs)


@_guarded("doc")
def doc(name: str | list[str]) -> None:
    if (ev := _current.get()) is not None:
        ev.doc(name)
    elif enabled():
        _drop("doc")


@_guarded("sig")
def sig(k: str, **fields: Any) -> None:
    if (ev := _current.get()) is not None:
        ev.sig(k, **fields)
    elif enabled():
        _drop(f"sig:{k}")


@_guarded("dec")
def dec(k: str, by: str = "tool", src: str = "", **fields: Any) -> None:
    if (ev := _current.get()) is not None:
        ev.dec(k, by, src, **fields)
    elif enabled():
        _drop(f"dec:{k}")


@_guarded("addr")
def addr(inp: str, **fields: Any) -> None:
    if (ev := _current.get()) is not None:
        ev.addr(inp, **fields)
    elif enabled():
        _drop("addr")


@_guarded("out")
def out(**fields: Any) -> None:
    if (ev := _current.get()) is not None:
        ev.out(**fields)
    elif enabled():
        _drop("out")


def validate(event: dict[str, Any]) -> list[str]:
    """What is wrong with an event, as short messages; empty when it follows the format."""
    problems: list[str] = []
    if event.get("v") != FORMAT:
        problems.append(f"v: expected {FORMAT}")
    kind = event.get("ev")
    if kind == "proc":
        for f in ("proc", "t"):
            if f not in event:
                problems.append(f"proc event: missing {f}")
        return problems
    if kind == "end":
        return [] if "proc" in event and "t" in event else problems + ["end event: missing proc or t"]
    if kind != "call":
        return problems + [f"ev: unknown event type {kind!r}"]
    for f, typ in (("proc", str), ("seq", int), ("conv", str), ("t0", (int, float)), ("t1", (int, float)),
                   ("tool", str), ("args", dict), ("addr", list), ("out", dict), ("sig", list), ("dec", list)):
        if not isinstance(event.get(f), typ):
            problems.append(f"{f}: missing or not {typ.__name__ if isinstance(typ, type) else 'a number'}")
    if isinstance(event.get("t0"), (int, float)) and isinstance(event.get("t1"), (int, float)) and event["t1"] < event["t0"]:
        problems.append("t1 before t0")
    if event.get("tool") not in TOOLS:
        problems.append(f"tool: unknown {event.get('tool')!r}")
    for s in event.get("sig") or []:
        if s.get("k") not in SIGNALS:
            problems.append(f"sig: unknown kind {s.get('k')!r}")
    for d in event.get("dec") or []:
        if d.get("k") not in DECISIONS:
            problems.append(f"dec: unknown kind {d.get('k')!r}")
        if d.get("by") not in ("tool", "rule"):
            problems.append(f"dec {d.get('k')}: by must be 'tool' or 'rule'")
        if "tokens" not in d and d.get("k") not in ("auto_zoom", "split", "seen_marks", "shrink"):
            problems.append(f"dec {d.get('k')}: missing tokens")
    for a in event.get("addr") or []:
        if "in" not in a:
            problems.append("addr: missing in")
    err = event.get("err")
    if err is not None and not (isinstance(err, dict) and "type" in err and "msg" in err):
        problems.append("err: expected null or {type, msg}")
    return problems
