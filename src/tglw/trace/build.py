"""Steps of a trace from normalized calls: labels, where each address came from, and the signals no later step
followed. Only what an earlier turn returned counts: calls sent together cannot see each other's returns."""

from __future__ import annotations

import re
import string
from typing import Any

from .model import Call, Step, Trace

AGAIN_NEW_TEXT = 0.2  # a read with no new page and under this share of new lines brings nothing new
_PAGES = re.compile(r"p(\d+)(?:\s*-\s*p?(\d+))?$")


def base(addr: str) -> str:
    """'s29 from t979' -> 's29'; 'doc:p12' -> 'p12'; 'p15:10,20,30,40' -> 'p15'."""
    a = addr.strip().split(" from ")[0].strip()
    if ":" in a and not a.startswith(("p", "t", "s", "fig", "tab")):
        a = a.split(":", 1)[1]
    return a.split(":")[0].strip()


def page_set(addr: str) -> set[int]:
    m = _PAGES.fullmatch(base(addr))
    if not m:
        return set()
    lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
    return set(range(lo, hi + 1)) if 0 <= hi - lo < 60 else {lo}


def labels(calls: list[Call]) -> list[str]:
    """'3' for a call alone in its turn; '2a', '2b' for calls sent together; numbered by turn."""
    order = list(dict.fromkeys(c.turn for c in calls))
    out = []
    for c in calls:
        same = [x for x in calls if x.turn == c.turn]
        k = order.index(c.turn) + 1
        out.append(str(k) if len(same) == 1 else f"{k}{string.ascii_lowercase[same.index(c) % 26]}")
    return out


class _Seen:
    """What earlier turns returned."""

    def __init__(self) -> None:
        self.hit_addr: dict[str, tuple[str, int]] = {}
        self.hit_page: dict[int, tuple[str, int]] = {}
        self.hit_sec: dict[str, tuple[str, int]] = {}
        self.ptr_addr: dict[str, tuple[str, str]] = {}
        self.ptr_page: dict[int, tuple[str, str]] = {}
        self.listed: dict[str, str] = {}
        self.pages: set[int] = set()
        self.viewed: set[int] = set()
        self.refs: set[str] = set()

    @staticmethod
    def _better(old: tuple[str, int] | None, label: str, rank: int) -> bool:
        """Keep the strongest scent: the best (lowest) rank; on a tie, the later search. Identifier hits
        (rank 0, added by the tool) count after ranked hits."""
        r = rank or 99
        return old is None or r <= (old[1] or 99)

    def add(self, c: Call, label: str) -> None:
        for h in c.hits:
            for a in h.get("a", []):
                if self._better(self.hit_addr.get(a), label, h["r"]):
                    self.hit_addr[a] = (label, h["r"])
            for p in h.get("p", []):
                if self._better(self.hit_page.get(p), label, h["r"]):
                    self.hit_page[p] = (label, h["r"])
            if h.get("sec") and self._better(self.hit_sec.get(h["sec"]), label, h["r"]):
                self.hit_sec[h["sec"]] = (label, h["r"])
        for s in c.signals:
            if s["k"] == "continues" and s.get("page"):
                self.ptr_page[s["page"]] = ("continues", label)
            elif s["k"] == "cut":
                for a in s.get("rest", []):
                    self.ptr_addr[a] = ("cut", label)
                    self.ptr_addr.setdefault(base(a), ("cut", label))
                    for p in page_set(a):
                        self.ptr_page.setdefault(p, ("cut", label))
        for a in c.listed + [a for d in c.decisions for a in d.get("listed") or []]:  # outline lines, a rule's headings
            self.listed[a] = label
        if c.block == "view":
            self.viewed |= set(c.pages)
        elif c.block in ("read", "map"):
            self.pages |= set(c.pages)
        self.refs |= set(c.refs)


def _origin(c: Call, v: _Seen) -> tuple[str | None, str | None, int | None]:
    addressed = c.addrs and (c.block in ("read", "view") or c.tool in ("context", "region")
                             or (c.tool == "outline" and c.args.get("section")))
    if not addressed:
        return None, None, None
    got = set(c.at or c.pages)  # a context is judged by its address's page, not by the pages it quotes
    if c.block == "view" and got and got <= v.viewed:
        return "again", None, None
    if c.block == "read" and got and got <= v.pages:  # a context shows structure: never `again`
        # nothing new: its blocks (raw events) or its text lines (logs) were all shown before
        nothing_new = (set(c.refs) <= v.refs) if c.refs else (c.novelty is None or c.novelty < AGAIN_NEW_TEXT)
        if nothing_new:
            return "again", None, None
    for a in c.addrs:
        hit = v.ptr_addr.get(a) or v.ptr_addr.get(base(a)) or next((v.ptr_page[p] for p in sorted(page_set(a))
                                                                     if p in v.ptr_page), None)
        if hit:
            return hit[0], hit[1], None
    for a in c.addrs:
        b = base(a)
        hit = v.hit_addr.get(b) or v.hit_sec.get(b) or next((v.hit_page[p] for p in sorted(page_set(a))
                                                             if p in v.hit_page), None)
        if hit:
            return "hit", hit[0], hit[1]
    for a in c.addrs:
        if base(a) in v.listed:
            return "map", v.listed[base(a)], None
    new = got - (v.viewed if c.block == "view" else v.pages)
    if c.block != "view" and new and any(abs(p - q) <= 1 for p in new for q in v.pages):
        return "near", None, None
    return "new", None, None


def _ignored(steps: list[Step]) -> list[dict[str, Any]]:
    """Boundary pointers no later step read, and zero or saturated searches followed only by more ranked searches."""
    out = []
    shown: set[int] = set()
    for i, s in enumerate(steps):
        shown |= set(s.call.pages)
        later = [t for t in steps[i + 1:] if t.call.turn != s.call.turn]
        for sig in s.call.signals:
            if sig["k"] == "continues" and sig.get("page"):
                if sig["page"] not in shown and not any(sig["page"] in t.call.pages for t in later):
                    out.append({"k": "continues", "step": s.label, "target": f"p{sig['page']}"})
            elif sig["k"] == "cut" and sig.get("rest"):
                want = {sig["rest"][0], base(sig["rest"][0])}
                if not any(want & ({a for a in t.call.addrs} | {base(a) for a in t.call.addrs}) for t in later):
                    out.append({"k": "cut", "step": s.label, "target": base(sig["rest"][0])})
            elif sig["k"] in ("zero", "saturated") and later:
                nxt = [t for t in later if t.call.turn == later[0].call.turn]
                if all(t.call.block == "find" for t in nxt):
                    out.append({"k": sig["k"], "step": s.label, "target": None})
    return out


def build(calls: list[Call], source: str, conv: str = "stdio", answer: dict[str, Any] | None = None,
          lost: dict[str, int] | None = None) -> Trace:
    calls = sorted(calls, key=lambda c: c.n)
    names = labels(calls)
    seen = _Seen()
    steps: list[Step] = []
    turn = None
    pending: list[tuple[Call, str]] = []
    for c, label in zip(calls, names):
        if c.turn != turn:
            for pc, pl in pending:
                seen.add(pc, pl)
            pending, turn = [], c.turn
        origin, ostep, rank = _origin(c, seen)
        new = sorted(set(c.pages) - (seen.viewed if c.block == "view" else seen.pages))
        steps.append(Step(c, label, origin, ostep, rank, new))
        pending.append((c, label))
    doc = next((c.doc for c in calls if c.doc), None)
    return Trace(source, conv, doc, steps, _ignored(steps), answer, lost or {})
