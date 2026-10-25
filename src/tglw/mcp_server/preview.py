"""Previewing rules: the readings recorded on this machine (or in a given trace
directory) are replayed call by call on the current tools twice -- with no rule, and with only the rules given --
each time in a session of its own and with the events kept in memory (nothing is written, no model is called).
compare() gives, per reading and call, what each run returned (tokens, images) and the rules' decisions;
show() writes that in the reading language for the rules tool.

A call is replayed with the arguments the client sent, validated as the server validates them. A call whose query
was recorded only as its length and hash (TGLW_TRACE_QUERY=0) cannot be replayed and is marked so."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from tglw.trace import record

from . import executor

REPLAYED = ("list_documents", "outline", "read", "context", "search", "region")


def readings(directory: Path | None = None, last: int | None = None, doc: str | None = None) -> list[dict[str, Any]]:
    """Recorded readings, newest first: one per conversation of each trace file, {stem, conv, doc (the one most of
    its calls name), calls (call events in the order they started)}; `doc` keeps that document's readings."""
    from tglw.store import traces as store

    found = []
    for tf in store.list_traces(directory):
        convs: dict[str, list[dict[str, Any]]] = {}
        for e in store.read_events(tf, directory):
            if e.get("ev") == "call" and e.get("tool") in REPLAYED:
                convs.setdefault(e.get("conv") or "stdio", []).append(e)
        for conv, calls in convs.items():
            docs = Counter(c["doc"] for c in calls if isinstance(c.get("doc"), str))
            main = docs.most_common(1)[0][0] if docs else None
            if doc and main != doc:
                continue
            found.append({"stem": tf.stem, "conv": conv, "doc": main, "calls": sorted(calls, key=lambda c: c.get("seq", 0)),
                          "ended": max(c.get("t1") or 0 for c in calls)})
    found.sort(key=lambda r: -r["ended"])
    return found[:last] if last else found


def _replayable(args: dict[str, Any]) -> bool:
    return not isinstance(args.get("query"), dict)  # a query kept as length and hash


def _replay(calls: list[dict[str, Any]], rules: tuple[list[executor.Active], dict]) -> dict[int, dict[str, Any]]:
    """The calls replayed in a session of their own under `rules` (none: v1): each call's event by its index, with
    the turn the call was sent in (calls sent together share one), as `replay`."""
    from tglw.mcp_server import server
    from tglw.trace import events as trace_events

    turns = trace_events._turns(calls) if calls else []
    got: dict[int, dict[str, Any]] = {}
    session = server.Session()
    token = server.SESSION.set(session)
    try:
        with record.capture() as events, executor.override(*rules):
            for i, c in enumerate(calls):
                if not _replayable(c.get("args") or {}):
                    continue
                tool = server.server._tool_manager.get_tool(c["tool"])
                try:
                    kwargs = tool.fn_metadata.validate_arguments(dict(c.get("args") or {}))
                except Exception:  # noqa: BLE001 - arguments the server refused: the call did nothing either time
                    continue
                n = len(events)
                extra = server.TRACE_EXTRA.set({"replay": {"turn": turns[i], "i": i}})
                try:
                    getattr(server, c["tool"])(**kwargs)
                except Exception:  # noqa: BLE001 - an error is part of the replay (its event records it)
                    pass
                finally:
                    server.TRACE_EXTRA.reset(extra)
                if len(events) > n:
                    got[i] = events[-1]
    finally:
        server.SESSION.reset(token)
    return got


def _size(e: dict[str, Any] | None) -> dict[str, Any]:
    out = (e or {}).get("out") or {}
    return {"tokens": out.get("tokens"), "images": out.get("images"), "err": (e or {}).get("err")}


def _runs(texts: list[str], directory: Path | None, last: int | None, doc: str | None) -> list[tuple[dict, dict, dict]]:
    """Each recorded reading, replayed with no rule and with only `texts` (RuleError for one that does not parse)."""
    active, types = executor.parse(texts)
    return [(r, _replay(r["calls"], ([], {})), _replay(r["calls"], (active, types))) for r in readings(directory, last, doc)]


def compare(texts: list[str], directory: Path | None = None, last: int | None = None,
            doc: str | None = None) -> list[dict[str, Any]]:
    """Each recorded reading replayed with no rule (off) and with only `texts` (on: rules and type definitions;
    RuleError for one that does not parse): {stem, conv, doc, steps: [{i, tool, args, replayed, off: {tokens,
    images, err}, on: {tokens, images, err, rules: [the `rule` decisions of the trace]}}]}."""
    out = []
    for r, off, on in _runs(texts, directory, last, doc):
        steps = []
        for i, c in enumerate(r["calls"]):
            e = on.get(i)
            steps.append({"i": i + 1, "tool": c["tool"], "args": c.get("args"), "replayed": i in on,
                          "off": _size(off.get(i)),
                          "on": {**_size(e), "rules": [d for d in (e or {}).get("dec", []) if d.get("k") == "rule"]}})
        out.append({"stem": r["stem"], "conv": r["conv"], "doc": r["doc"], "steps": steps})
    return out


def show(texts: list[str], last: int = 3, doc: str | None = None, directory: Path | None = None) -> str:
    """The preview in the reading language (task LANG's render.preview): per reading, the steps the rules act on,
    what each rule adds, later steps that only re-read what a rule added; then the total."""
    from tglw.trace import events as trace_events
    from tglw.trace import render

    pairs = []
    for _, off, on in _runs(texts, directory, last, doc):
        a = trace_events.traces_of([off[i] for i in sorted(off)])
        b = trace_events.traces_of([on[i] for i in sorted(on)])
        if a and b:
            pairs.append((a[0], b[0]))
    return render.preview(pairs) if pairs else "no readings recorded yet to preview on"


def effect(texts: list[str], found: list[dict[str, Any]]) -> dict[str, int]:
    """What the rules in `texts` do on these readings (preview's replays): the steps they act on, the tokens they add,
    and the later steps that read a page a rule had already added."""
    active, types = executor.parse(texts)
    acted = tokens = reread = 0
    for r in found:
        off, on = _replay(r["calls"], ([], {})), _replay(r["calls"], (active, types))
        added: set[int] = set()
        for i in range(len(r["calls"])):
            a, b = off.get(i) or {}, on.get(i) or {}
            rules = [d for d in b.get("dec", []) if d.get("k") == "rule" and d.get("tokens")]
            if rules:
                acted += 1
                tokens += ((b.get("out") or {}).get("tokens") or 0) - ((a.get("out") or {}).get("tokens") or 0)
                added |= {p for d in rules for p in d.get("pages") or []}
            elif added & set((a.get("out") or {}).get("pages") or []):
                reread += 1
    return {"acted": acted, "tokens": tokens, "reread": reread}

