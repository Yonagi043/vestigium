"""Calls of the server's raw events (see record), one trace per conversation of a server process."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from .build import build
from .model import Call, Trace
from .session import _addrs

TIE = 0.0005  # seconds: a call starting before the turn's calls all ended was sent together with them


def _turns(events: list[dict[str, Any]]) -> list[int]:
    """A turn number per event: from the replay's turn when replayed, else from the times (a call that starts
    before every call of the current turn has ended was sent together with them)."""
    if events and all(isinstance(e.get("replay"), dict) and "turn" in e["replay"] for e in events):
        return [int(e["replay"]["turn"]) for e in events]
    out, turn, end = [], 0, None
    for e in events:
        if end is None or e["t0"] >= end - TIE:
            turn += 1
            end = e["t1"]
        else:
            end = max(end, e["t1"])
        out.append(turn)
    return out


def _signals(c: Call, sigs: list[dict[str, Any]]) -> None:
    for s in sigs:
        k = s.get("k")
        if k == "hits":
            c.hits += [{"r": i.get("r", 0), "a": [i["a"]] if i.get("a") else [], "p": [i["p"]] if i.get("p") else [],
                        "sec": i.get("sec")} for i in s.get("items", [])]
            if s.get("total") is not None:
                c.count = s["total"]
            if not s.get("items") or s.get("total") == 0:
                c.signals.append({"k": "zero"})
        elif k == "exact":
            c.count = s.get("n")
        elif k == "ident":  # {"items": [{"key": "SI-4", "n": 78, "shown": ["t1262", ...], "pages": [76, ...]}]}
            for x in s.get("items", []):
                pages = list(x.get("pages") or [])
                c.hits += [{"r": 0, "a": [a], "p": [pages[i]] if i < len(pages) else [], "sec": None, "ident": x.get("key")}
                           for i, a in enumerate(x.get("shown", []))]
        elif k in ("zero", "saturated"):
            if not any(x["k"] == k for x in c.signals):
                c.signals.append({"k": k})
            if k == "zero" and s.get("mode") == "literal" and c.count is None:
                c.count = 0  # "0 matches for ..."
        elif k == "continues":
            c.signals.append({"k": "continues", "page": s.get("page"), "sec": s.get("sec")})
        elif k == "next":
            c.signals.append({"k": "cut", "rest": list(s.get("rest", [])), "why": s.get("why", "budget")})
        elif k == "cut":
            c.signals.append({"k": "cut", "rest": [], "why": "chars"})
        elif k == "too_long":
            c.signals.append({"k": "too-long", "n_addrs": s.get("n_addrs")})
        elif k in ("scan", "qc"):  # one signal per kind, each page once
            have = next((x for x in c.signals if x["k"] == k), None)
            pages = sorted(set(s.get("pages") or []) | set(have["pages"] if have else []))
            if have:
                have["pages"] = pages
            else:
                c.signals.append({"k": k, "pages": pages})
        elif k == "fig_hint":
            _merge(c, "figure", list(s.get("figs", [])))
        elif k == "table_note":  # one per kind of note; one table signal for the call
            _merge(c, "table", list(s.get("tabs", [])))
        elif k == "struct":  # outline, context's path, siblings, references ...: every part lists addresses
            c.listed = list(dict.fromkeys(c.listed + list(s.get("units", []))))


def _counted(d: dict[str, Any], sigs: list[dict[str, Any]]) -> dict[str, Any]:
    """The tool's counts with what they counted, from the signals that carry it: the exact-text count's n, and for
    identifier hits (whose dec gives only how many keys) each key with its count."""
    if d.get("k") == "exact_note" and d.get("n") is None:
        d["n"] = next((s.get("n") for s in sigs if s.get("k") == "exact"), None)
    elif d.get("k") == "ident_hits" and not isinstance(d.get("keys"), list):
        d["keys"] = [{"key": x.get("key"), "n": x.get("n")} for s in sigs if s.get("k") == "ident" for x in s.get("items", [])]
    return d


def _merge(c: Call, k: str, units: list[str]) -> None:
    have = next((x for x in c.signals if x["k"] == k), None)
    if have is None:
        c.signals.append({"k": k, "units": units})
    else:
        have["units"] = list(dict.fromkeys(list(have.get("units") or []) + units))


def _shown(c: Call, k: str, prefix: str) -> None:
    """A figure or table among the blocks shown is a signal whether or not the tool added a note to it."""
    units = [u for u in c.units if u.startswith(prefix) and u[len(prefix):].isdigit()]
    if units:
        _merge(c, k, units)


def _drop(c: Call, gone: dict[str, Any]) -> None:
    """A pointer line the tool took out of the return because what it points to follows in it (M8's adjacent page,
    a rule's next page or rest): the model never saw it. The first such signal is v1's own; later ones came with
    the added text and stay."""
    for i, s in enumerate(c.signals):
        if s["k"] == gone.get("k") and (s["k"] != "continues" or s.get("page") == gone.get("page")):
            del c.signals[i]
            return


def call_of(e: dict[str, Any], n: int, turn: int) -> Call:
    tool, args = e["tool"], dict(e.get("args") or {})
    doc = e.get("doc")
    c = Call(n=n, turn=turn, tool=tool, args=args, doc=doc[0] if isinstance(doc, list) and doc else doc,
             addrs=_addrs(tool, args), t0=e.get("t0"), t1=e.get("t1"))
    out = e.get("out") or {}
    c.pages = list(out.get("pages", []))
    if not c.pages and isinstance(out.get("img"), dict) and out["img"].get("page"):
        c.pages = [out["img"]["page"]]
    c.units, c.refs = list(out.get("units", [])), list(out.get("refs", []))
    c.tokens, c.images = int(out.get("tokens", 0)), int(out.get("images", 0))
    c.addr_errors = [a for a in e.get("addr", []) if a.get("err")]
    _signals(c, e.get("sig", []))
    _shown(c, "figure", "fig")
    _shown(c, "table", "tab")
    if c.tool == "context":  # its address's own page, as the log's header line gives it
        c.at = sorted({min(a["pages"]) for a in e.get("addr", []) if a.get("pages")})[:1]
        c.pages = sorted(set(c.pages) | set(c.at))
    c.decisions = [_counted(dict(d), e.get("sig", [])) for d in e.get("dec", [])]
    for d in c.decisions:
        gone = d.get("dropped") or ({"k": "continues", "page": d["dropped_continues"]} if d.get("dropped_continues") else None)
        if isinstance(gone, dict):
            _drop(c, gone)
    if err := e.get("err"):
        c.error = f"{err.get('type')}: {err.get('msg')}"
    return c


def traces_of(events: Iterable[dict[str, Any]]) -> list[Trace]:
    """One trace per (process, conversation), calls in the order they started."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    lost: dict[str, dict[str, int]] = defaultdict(dict)
    for e in events:
        if e.get("ev") == "call":
            groups[(e["proc"], e.get("conv") or "stdio")].append(e)
        if e.get("lost"):
            bucket = lost[e["proc"]]
            for k, v in e["lost"].items():
                bucket[k] = bucket.get(k, 0) + v
    out = []
    for (proc, conv), evs in groups.items():
        evs.sort(key=lambda e: (e["t0"], e["seq"]))
        reading = [e for e in evs if e.get("tool") != "rules"]
        calls = [call_of(e, i + 1, t) for i, (e, t) in enumerate(zip(reading, _turns(reading)))]
        trace = build(calls, "events", conv, None, lost.get(proc))
        for e in evs:  # the rule tool is no reading step: where in the reading the rules changed
            if e.get("tool") == "rules":
                before = [s.label for s, r in zip(trace.steps, reading) if (r["t0"], r["seq"]) < (e["t0"], e["seq"])]
                args = e.get("args") or {}
                trace.rules.append({"after": before[-1] if before else None, "action": args.get("action"),
                                    "text": args.get("text"), "error": bool(e.get("err"))})
        out.append(trace)
    return out
