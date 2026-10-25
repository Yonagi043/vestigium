"""Rule suggestions from the readings recorded on this machine (stage 4; DELIVERY_LANG §13).

A suggestion needs evidence that a signal's target was wanted: it was left unfollowed at the time, but read later in
the same reading, or (for `continues`) the page it pointed to was read in another reading of the same document. Only
when that repeats -- at least MIN_EVENTS times in at least MIN_READINGS readings of one type of document -- is a rule
suggested, never from one reading. Nothing takes effect by itself: the user decides (through the agent), and a
suggestion the user turned down, or one the rules in effect already cover, is not made again.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from . import rules as R
from .build import base
from .model import Trace

MIN_EVENTS, MIN_READINGS = 3, 2
ACTION = {"continues": "add next-page max 1", "cut": "add unread-headings"}
WHAT = {"continues": "the page after 'continues' was read later or in another reading",
        "cut": "the rest of a cut read was read later"}


@dataclass(frozen=True)
class Suggestion:
    id: str                    # "s-" + 4 hex of the canonical rule text: the same for the same rule, every time
    rule: str                  # a rule the user could add as it is
    typedef: str | None        # the type it needs ("type hkex = hkex__*"), None when it names one document
    why: str                   # the evidence, one line
    readings: tuple[int, ...]  # indexes of the traces the evidence came from (for a preview on them)


def canonical(text: str) -> str:
    """A rule's text with its why dropped and its spelling normalized (how the store recognizes a suggestion)."""
    r = R.parse(text)
    return R.Rule(r.signal, r.conds, r.scope, r.actions, r.max, r.budget, None).text() if isinstance(r, R.Rule) else r.text()


def _scope(doc: str) -> tuple[str, str | None, str]:
    """(scope, type definition or None, the documents it means): a type by the registered name's prefix."""
    if "__" in doc:
        prefix = doc.split("__")[0]
        name = prefix.replace("_", "-").lower()
        return f"type {name}", f"type {name} = {prefix}__*", f"{prefix} documents"
    return f"doc {doc}", None, doc


def _needed(traces: Sequence[Trace]) -> tuple[dict[tuple[str, str], list[int]], dict[tuple[str, str], set[int]]]:
    """(scope, kind) -> traces of each event worth a rule, and -> traces where the signal showed at all."""
    read: dict[str, dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))  # doc -> trace -> pages read
    for i, t in enumerate(traces):
        for s in t.steps:
            if s.call.block in ("read", "map") and t.doc:
                read[t.doc][i] |= set(s.call.pages)
    events: dict[tuple[str, str], list[int]] = defaultdict(list)
    seen: dict[tuple[str, str], set[int]] = defaultdict(set)
    for i, t in enumerate(traces):
        if not t.doc:
            continue
        scope = _scope(t.doc)[0]
        for j, s in enumerate(t.steps):
            later = t.steps[j + 1:]
            for sig in s.call.signals:
                if sig["k"] == "continues" and sig.get("page") and sig["page"] not in s.call.pages:
                    seen[(scope, "continues")].add(i)
                    nxt = next((x.call.turn for x in later if x.call.turn != s.call.turn), None)
                    hits = [x for x in later if sig["page"] in x.call.pages and x.call.block in ("read", "map")]
                    late = bool(hits) and hits[0].call.turn != nxt
                    elsewhere = not hits and any(sig["page"] in pages for k, pages in read[t.doc].items() if k != i)
                    if late or elsewhere:
                        events[(scope, "continues")].append(i)
                elif sig["k"] == "cut" and sig.get("rest"):
                    seen[(scope, "cut")].add(i)
                    want = {sig["rest"][0], base(sig["rest"][0])}
                    nxt = next((x.call.turn for x in later if x.call.turn != s.call.turn), None)
                    hits = [x for x in later if want & ({a for a in x.call.addrs} | {base(a) for a in x.call.addrs})]
                    if hits and hits[0].call.turn != nxt:
                        events[(scope, "cut")].append(i)
    return events, seen


def _covered(rule: R.Rule, effective: Sequence[R.Rule]) -> bool:
    """A rule in effect already does it: same signal and action, no condition, for all documents or this scope."""
    return any(r.signal == rule.signal and not r.conds and {a.text() for a in r.actions} >= {a.text() for a in rule.actions}
               and (r.scope == ("all", None) or r.scope == rule.scope) for r in effective)


def suggest(traces: Sequence[Trace], rules: Sequence[str] = (), suppressed: Iterable[str] = ()) -> list[Suggestion]:
    """Suggestions from these readings; `rules`: the rule texts in effect (what they cover is not suggested);
    `suppressed`: canonical texts not to suggest again (turned down, or already kept: the store's suppressed())."""
    effective = [r for r in (R.parse(t) for t in rules if R.check(t) is None) if isinstance(r, R.Rule)]
    skip = set(suppressed)
    events, seen = _needed(traces)
    out = []
    for (scope, kind), where in sorted(events.items()):
        rs = sorted(set(where))
        if len(where) < MIN_EVENTS or len(rs) < MIN_READINGS:
            continue
        doc = next(t.doc for i, t in enumerate(traces) if i in rs and t.doc)
        _, typedef, label = _scope(doc)
        text = f"on {kind} in {scope} do {ACTION[kind]}"
        rule = R.parse(text)
        key = canonical(text)
        if key in skip or _covered(rule, effective):
            continue
        out.append(Suggestion(id="s-" + hashlib.sha1(key.encode()).hexdigest()[:4], rule=text, typedef=typedef,
                              why=f"in {len(rs)} of {len(seen[(scope, kind)])} readings of {label}, {WHAT[kind]} "
                                  f"({len(where)} times)", readings=tuple(rs)))
    return out
