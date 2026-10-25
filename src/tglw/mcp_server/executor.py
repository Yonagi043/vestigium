"""The rule executor: after a read, search or region has its v1 return, the rules in
effect whose signal fired, whose scope covers the document and whose conditions hold add to that return -- whole
pages, a page's first line, the rest of a cut read, the headings it did not reach, an absence report, an image, a
zoom -- each block under a line naming the rule, within the rule's budget, the call's cap and the client's
limits. Signals and conditions come from what the v1 code reported to the call's trace event (tglw.trace.record),
never from the text. Each action that added something or was held back is a `rule` decision in the trace.

With no rule in effect (store all-off or empty, or TGLW_RULES=0) none of this runs and the return is v1's. An action
that fails adds nothing: the return stays as it was and its decision records the error."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from tglw.common.text import count_tokens
from tglw.trace import build, record
from tglw.trace import rules as lang
from tglw.views import api
from tglw.views.tracing import guarded, report

RULES_ENV = "TGLW_RULES"
DELTA_ENV = "TGLW_COVERAGE_DELTA"  # on: a later coverage summary in a conversation gives only what is new (next version)
RULE_BUDGET = 2_000  # tokens a rule may add to one call when it names no budget
RULES_CALL_CAP = 4_000  # tokens all rules together may add to one call (gate 7)
HEADINGS_MAX = 12
FIRST_LINE_WORDS = 50
ABSENCE_MAX = 300
ORDER = ("read", "continues", "cut", "zero", "saturated", "view")
COVERAGE_MAX = 250
TIER = {"doc": 2, "type": 1, "all": 0}
PAGE_ACTIONS = ("add next-page", "add prev-page")  # and widen N


@dataclass(frozen=True)
class Active:
    """A rule in effect: id, version, layer (user or recommended) and the parsed rule."""
    id: str
    ver: int
    layer: str
    rule: lang.Rule


_override: ContextVar[tuple[list[Active], dict[str, tuple[str, ...]]] | None] = ContextVar("tglw_rules_override", default=None)


def _off() -> bool:
    return os.environ.get(RULES_ENV, "1").strip().lower() in ("0", "false", "off", "no")


def _delta() -> bool:
    return os.environ.get(DELTA_ENV, "").strip().lower() in ("1", "true", "on", "yes")


def in_force() -> tuple[list[Active], dict[str, tuple[str, ...]]]:
    """The rules in effect and the types they may name: rules given for this context (a preview asks for exactly
    those, whatever TGLW_RULES says), else the store's (task PKG, re-read when its files change); none with
    TGLW_RULES=0 or when the store cannot be read."""
    given = _override.get()
    if given is not None:
        return given
    if _off():
        return [], {}
    try:
        from tglw.store import rules as store
        rs = store.load()
        return [Active(e.id, e.ver, e.layer, e.parsed) for e in rs.effective if isinstance(e.parsed, lang.Rule)], rs.types
    except Exception:  # noqa: BLE001 - a store that cannot be read has no rules in effect: the tools work as v1
        record.lost("error:rules_load")
        return [], {}


def on() -> bool:
    """Whether any rule is in effect (one stat of each rules file when nothing changed)."""
    return bool(in_force()[0])


def parse(texts: list[str], prefix: str = "new") -> tuple[list[Active], dict[str, tuple[str, ...]]]:
    """Rules and type definitions given as text (a preview's), in order: rules get ids new1, new2, ... (nothing that
    reads as an address); RuleError for the first that does not parse."""
    types = {}
    for t in texts:
        if t.strip().startswith("type"):
            d = lang.parse(t)
            types[d.name] = d.patterns
    out = []
    for t in texts:
        if not t.strip().startswith("type"):
            out.append(Active(f"{prefix}{len(out) + 1}", 1, "user", lang.parse(t, types)))
    return out, types


@contextmanager
def override(active: list[Active], types: dict[str, tuple[str, ...]]) -> Iterator[None]:
    """These rules (none: an empty list) instead of the store's, in this context."""
    token = _override.set((active, types))
    try:
        yield
    finally:
        _override.reset(token)


# ---- what the session remembers for rules ------------------------------------------------------------------------

def remember_hits(s: Any, name: str) -> None:
    """The hits the current search showed (its trace event), for later reads' from-hit."""
    ev = record.current()
    if ev is None:
        return
    seen = s.hits.setdefault(name, {"a": set(), "sec": set(), "p": set()})
    for x in ev.data["sig"]:
        if "via" in x:
            continue
        if x["k"] == "hits":
            for i in x.get("items", []):
                seen["a"].update([i["a"]] if i.get("a") else [])
                seen["p"].update([i["p"]] if i.get("p") else [])
                seen["sec"].update([i["sec"]] if i.get("sec") else [])
        elif x["k"] == "ident":
            for i in x.get("items", []):
                seen["a"].update(i.get("shown", []))
                seen["p"].update(i.get("pages", []))


def remember_image(s: Any, name: str, target: str) -> None:
    """An image the conversation has seen (the model's region calls and add image), keyed as task VIS keys them."""
    from tglw.figures.actions import target_key
    s.images.setdefault(name, set()).add(target_key(target))


# ---- one call ----------------------------------------------------------------------------------------------------

@dataclass
class Call:
    """A return being added to: its v1 lines (head), its final next: line (tail), the rules' blocks, images, the
    pages it holds, and what the rules spent."""
    tool: str
    name: str
    s: Any
    lim: Any
    head: list[str]
    tail: str | None = None
    v1: str = ""
    blocks: list[str] = field(default_factory=list)
    images: list[Any] = field(default_factory=list)
    pages: set[int] = field(default_factory=set)
    added: int = 0
    spent: dict[str, int] = field(default_factory=dict)
    used: set[str] = field(default_factory=set)

    def text(self) -> str:
        return "\n".join(self.head + self.blocks + ([self.tail] if self.tail else []))

    def save(self) -> tuple:
        return (list(self.head), self.tail, list(self.blocks), list(self.images), set(self.pages), self.added,
                dict(self.spent), set(self.used))

    def restore(self, saved: tuple) -> None:
        (self.head, self.tail, self.blocks, self.images, self.pages, self.added, self.spent, self.used) = saved

    def room(self, rule: Active) -> tuple[int, str]:
        """Tokens the rule may still add in this call, and which limit that is (budget or call)."""
        own = (rule.rule.budget or RULE_BUDGET) - self.spent.get(rule.id, 0)
        cap = RULES_CALL_CAP - self.added
        return (own, "budget") if own <= cap else (cap, "call")

    def why_not(self, rule: Active, text: str, tokens: int) -> str | None:
        """None when `text` (tokens, images included) can be added; else the limit it would pass."""
        left, limit = self.room(rule)
        if tokens > left:
            return limit
        from tglw.mcp_server.server import CHAR_CAP
        total = self.text()
        if len(total) + 1 + len(text) >= CHAR_CAP:
            return "chars"
        if self.lim.total is not None and len(total.encode()) + 1 + len(text.encode()) > self.lim.total - 200:
            return "client"
        return None

    def spend(self, rule: Active, tokens: int) -> None:
        self.added += tokens
        self.spent[rule.id] = self.spent.get(rule.id, 0) + tokens
        self.used.add(rule.id)


def _decision(rule: Active, sig: str, act: str, tokens: int, **fields: Any) -> None:
    report(record.dec, "rule", by="rule", src=rule.id, ver=rule.ver, layer=rule.layer, sig=sig, act=act, tokens=tokens,
           **{k: v for k, v in fields.items() if v not in (None, [], {})})


def _head(rule: Active, act: str) -> str:
    return f"[rule {rule.id}: {act}]"


@dataclass
class Fired:
    """A signal of the v1 return: its kind and what the trace says about it (continues: page, sec; cut: rest...)."""
    kind: str
    info: dict[str, Any] = field(default_factory=dict)


def _v1(ev: Any) -> tuple[list[dict], list[dict], dict, list[dict]]:
    """The v1 part of the call's event: signals and decisions not made by a rule, what it delivered, its addresses."""
    d = ev.data
    return ([x for x in d["sig"] if "via" not in x], [x for x in d["dec"] if "via" not in x and x.get("k") != "rule"],
            dict(d["out"]), [a for a in d["addr"] if "via" not in a])


def _select(sig: str, active: list[Active], types: dict, name: str, holds: Any) -> list[tuple[Active, list[str]]]:
    """The rules that act on one signal: scope covers the document and every condition holds; only the most specific
    scope (doc, type, all); within it user rules, not recommended ones. Each with the ids of those that gave way."""
    cands = [a for a in active if a.rule.signal == sig and a.rule.applies_to(name, types) and all(holds(c) for c in a.rule.conds)]
    if not cands:
        return []
    tier = max(TIER[a.rule.scope[0]] for a in cands)
    top = [a for a in cands if TIER[a.rule.scope[0]] == tier]
    if any(a.layer == "user" for a in top):
        top = [a for a in top if a.layer == "user"]
    over = [a.id for a in cands if a not in top]
    return [(a, over) for a in top]


def _compare(value: int, op: str, n: int) -> bool:
    return {"<=": value <= n, ">=": value >= n, "<": value < n, ">": value > n}[op]


def _run(call: Call, sig: str, rule: Active, over: list[str], act: lang.Action, fn: Any, *args: Any) -> None:
    """One action of one rule, all or nothing: an error restores the return and is recorded."""
    saved, m = call.save(), record.mark()
    try:
        with record.via(rule.id):
            fn(call, sig, rule, over, act, *args)
    except Exception as err:  # noqa: BLE001 - an action that fails adds nothing
        call.restore(saved)
        record.rewind(m)
        _decision(rule, sig, act.text(), 0, over=over, err={"type": type(err).__name__, "msg": str(err)[:200]})


def _uses_left(call: Call, rule: Active) -> bool:
    return not rule.rule.max or call.s.uses.get(rule.id, 0) < rule.rule.max or rule.id in call.used


def _finish(call: Call) -> None:
    for rid in call.used:
        call.s.uses[rid] = call.s.uses.get(rid, 0) + 1


# ---- read --------------------------------------------------------------------------------------------------------

def after_read(name: str, text: str, s: Any, lim: Any) -> tuple[str, list[Any]]:
    """A one-document read's v1 text with what the rules in effect add, and the images they add."""
    active, types = in_force()
    ev = record.current()
    if not active or ev is None:
        return text, []
    sigs, decs, out, addrs = _v1(ev)
    lines = text.split("\n")
    call = Call("read", name, s, lim, lines[:-1] if lines[-1].startswith("next: ") else lines,
                lines[-1] if lines[-1].startswith("next: ") else None, text, pages=set(out.get("pages", [])))
    v = api.view(name)
    dropped = {d.get("dropped_continues") for d in decs if d.get("k") == "adjacent"} - {None}
    fired: dict[str, list[Fired]] = {"read": [Fired("read")] if out.get("pages") else []}
    fired["continues"] = [Fired("continues", x) for x in sigs if x["k"] == "continues" and x.get("page") not in dropped]
    fired["cut"] = [Fired("cut", x) for x in sigs if x["k"] == "next"][-1:]
    tokens = None

    def holds(c: lang.Cond, sig: str, f: Fired) -> bool:
        nonlocal tokens
        if c.name == "same-section":
            return sig == "continues" or (f.info.get("sec") is not None and f.info.get("sec") == f.info.get("last_sec"))
        if c.name == "from-hit":
            seen = s.hits.get(name) or {}
            return any(build.base(a["in"]) in seen.get("a", ()) or build.base(a["in"]) in seen.get("sec", ())
                       or build.page_set(a["in"]) & seen.get("p", set()) for a in addrs)
        if c.name == "scan":
            return any(x["k"] == "scan" for x in sigs)
        if c.name == "figure":
            return bool(_figures(v, out))
        if c.name == "table":
            return any(u.startswith("tab") for u in out.get("units", []))
        if c.name == "searches":
            return _compare(s.searches, c.op, c.value)
        if c.name == "pages":
            return _compare(len(out.get("pages", [])), c.op, c.value)
        if c.name == "tokens":
            tokens = count_tokens(text) if tokens is None else tokens
            return _compare(tokens, c.op, c.value)
        return False

    for sig in ("read", "continues", "cut"):
        for f in fired[sig]:
            for rule, over in _select(sig, active, types, name, lambda c, sig=sig, f=f: holds(c, sig, f)):
                if not _uses_left(call, rule):
                    continue
                for act in rule.rule.actions:
                    _read_action(call, sig, rule, over, act, f, fired, v, sigs, out)
    _finish(call)
    return call.text(), call.images


def _read_action(call: Call, sig: str, rule: Active, over: list[str], act: lang.Action, f: Fired,
                 fired: dict[str, list[Fired]], v: Any, sigs: list[dict], out: dict) -> None:
    a = act.text()
    if act.verb == "widen" or a in PAGE_ACTIONS:
        if call.tail is not None:  # a cut read: its rest comes first (add rest)
            return
        if sig == "continues":
            want = [f.info["page"]]
        elif a == "add next-page":
            want = [max(out["pages"]) + 1]
        elif a == "add prev-page":
            want = [min(out["pages"]) - 1]
        else:
            want = _widen(sorted(out["pages"]), int(act.arg))
        last = v.doc.page_count
        _run(call, sig, rule, over, act, _add_pages, [p for p in want if 1 <= p <= last],
             {x.info["page"]: x.info.get("sec") for x in fired["continues"]})
    elif a == "add first-line":
        _run(call, sig, rule, over, act, _add_first_line, v, f.info["page"])
    elif a == "add rest":
        _run(call, sig, rule, over, act, _add_rest, f.info.get("rest", []))
    elif a == "add unread-headings":
        _run(call, sig, rule, over, act, _add_headings, v, f.info.get("rest", []))
    elif a == "add image":
        kind = next((c.name for c in rule.rule.conds if c.name in lang.IMAGE_CONDS), None)
        _run(call, sig, rule, over, act, _add_image, v, kind, sigs, out)


def _widen(pages: list[int], n: int) -> list[int]:
    """Each run of pages, n pages either side: nearest first; at one distance the page after before the page
    before; runs in page order."""
    runs: list[list[int]] = []
    for p in pages:
        if runs and p == runs[-1][1] + 1:
            runs[-1][1] = p
        else:
            runs.append([p, p])
    return list(dict.fromkeys(q for d in range(1, n + 1) for a, b in runs for q in (b + d, a - d)))


def _page(name: str, p: int, s: Any) -> str | None:
    """read(doc, ["pK"]) as the model would get it, without M8's adjacent pages; None when it is not whole."""
    from tglw.mcp_server import server
    text = server._read_one(name, [f"p{p}"], server.READ_CAP, server.READ_CHARS, s, adjacent=False, grow=False)
    if not text.strip() or text.split("\n")[-1].startswith("next: ") or text.startswith("[note]"):
        return None
    return text


def _add_pages(call: Call, sig: str, rule: Active, over: list[str], act: lang.Action, want: list[int],
               pointers: dict[int, str | None]) -> None:
    head = _head(rule, act.text())
    got, skipped, dropped, tokens = [], [], [], 0
    shown = call.s.pages.get(call.name, set())
    for p in want:
        if p in call.pages or p in shown:
            continue
        m = record.mark()
        page = _page(call.name, p, call.s)
        piece = page if got else f"{head}\n{page}" if page is not None else None
        n = count_tokens(piece) if piece is not None else 0
        why = "budget" if piece is None else call.why_not(rule, piece, n)
        if why:
            record.rewind(m)
            skipped.append({"what": f"p{p}", "why": why})
            continue
        record.rewind(m, kinds=("addr",))  # the page is the rule's, not an address asked for
        if got:
            call.blocks[-1] += "\n" + piece
        else:
            call.blocks.append(piece)
        call.spend(rule, n)
        tokens += n
        got.append(p)
        call.pages.add(p)
        if p in pointers:  # the page a continues: line points to follows: the line goes (as M8 does)
            line = f"continues: p{p} ({pointers[p]})"
            if line in call.head:
                del call.head[len(call.head) - 1 - call.head[::-1].index(line)]
                dropped.append({"k": "continues", "page": p})
    if got or skipped:
        _decision(rule, sig, act.text(), tokens, pages=got, dropped=dropped[0] if len(dropped) == 1 else dropped,
                  over=over, skipped=skipped)


def _add_first_line(call: Call, sig: str, rule: Active, over: list[str], act: lang.Action, v: Any, p: int) -> None:
    if p in call.pages or p in call.s.pages.get(call.name, set()):
        return
    first = api.first_line(v, p, FIRST_LINE_WORDS)
    if first is None:
        return
    line = f'{_head(rule, act.text())} p{p} starts: "{first}"'
    n = count_tokens(line)
    why = call.why_not(rule, line, n)
    if why:
        _decision(rule, sig, act.text(), 0, first=p, over=over, skipped=[{"what": f"p{p}", "why": why}])
        return
    call.blocks.append(line)
    call.spend(rule, n)
    _decision(rule, sig, act.text(), n, first=p, over=over)


def _add_rest(call: Call, sig: str, rule: Active, over: list[str], act: lang.Action, rest: list[str]) -> None:
    from tglw.mcp_server import server
    if call.tail is None or not rest:
        return
    head = _head(rule, act.text())
    left, limit = call.room(rule)
    base = call.text()
    chars = server.CHAR_CAP - 2 - len(base) - len(head)
    if call.lim.total is not None:
        chars = min(chars, call.lim.total - 202 - len(base.encode()) - len(head.encode()))
    tokens = left - count_tokens(head)
    m = record.mark()
    held = None
    if tokens < 1 or chars < 1:
        held = limit if tokens < 1 else "chars" if call.lim.total is None else "client"
    else:
        before = set((record.current().data["out"] if record.current() else {}).get("pages", []))
        more = server._read_one(call.name, rest, tokens, chars, call.s, adjacent=False, grow=False, page_context=False)
        lines = more.split("\n")
        tail = lines[-1] if lines[-1].startswith("next: ") else None
        body = lines[:-1] if tail else lines
        piece = head + "\n" + "\n".join(body)
        n = count_tokens(piece)
        if not "".join(body).strip():
            held = limit
        else:
            held = call.why_not(rule, piece, n) if n > left or len(piece) > chars else None
        if held is None:
            record.rewind(m, kinds=("addr",))
            after = set((record.current().data["out"] if record.current() else {}).get("pages", []))
            call.blocks.append(piece)
            call.tail = tail
            call.spend(rule, n)
            call.pages |= after
            _decision(rule, sig, act.text(), n, pages=sorted(after - before), dropped={"k": "cut"}, over=over)
            return
    record.rewind(m)
    _decision(rule, sig, act.text(), 0, over=over, skipped=[{"what": rest[0], "why": held}])


def _add_headings(call: Call, sig: str, rule: Active, over: list[str], act: lang.Action, v: Any, rest: list[str]) -> None:
    lines, listed = api.unread_headings(v, rest, call.pages | call.s.pages.get(call.name, set()), HEADINGS_MAX)
    if not lines:
        return
    piece = _head(rule, act.text()) + "\n" + "\n".join(lines)
    n = count_tokens(piece)
    why = call.why_not(rule, piece, n)
    if why:
        _decision(rule, sig, act.text(), 0, over=over, skipped=[{"what": "headings", "why": why}])
        return
    call.blocks.append(piece)
    call.spend(rule, n)
    _decision(rule, sig, act.text(), n, listed=listed, over=over)


def _figures(v: Any, out: dict) -> list[str]:
    """The pictures the return shows that are worth an image (task VIS's is_figure; logos are not), in order."""
    from tglw.figures.inline import is_figure
    from tglw.views.docview import long_ref
    return [u for u in out.get("units", []) if u.startswith("fig") and (b := v.doc.by_ref.get(long_ref(u))) is not None
            and is_figure(v, b)]


def _add_image(call: Call, sig: str, rule: Active, over: list[str], act: lang.Action, v: Any, kind: str | None,
               sigs: list[dict], out: dict) -> None:
    """Task VIS's add_image: the first image of the kind the rule's condition names that the read shows and the
    conversation has not seen, as region(doc, target) would return it."""
    from mcp.server.mcpserver import Image

    from tglw.figures import actions
    left, limit = call.room(rule)
    got = actions.add_image(call.name, kind or "figure", call.v1, call.s.images.get(call.name, set()), left,
                            prefix=_head(rule, act.text()) + " ", why=limit)
    if isinstance(got, list):
        if got:
            _decision(rule, sig, act.text(), 0, over=over, skipped=got)
        return
    why = call.why_not(rule, got.line, got.tokens)
    if why:
        _decision(rule, sig, act.text(), 0, target=got.target, over=over, skipped=got.skipped + [{"what": got.target, "why": why}])
        return
    call.blocks.append(got.line)
    call.images.append(Image(data=got.png, format="png"))
    call.spend(rule, got.tokens)
    remember_image(call.s, call.name, got.target)
    _decision(rule, sig, act.text(), got.tokens, target=got.target, img_tokens=got.img_tokens, px=list(got.px), over=over,
              skipped=got.skipped)


# ---- search ------------------------------------------------------------------------------------------------------

def after_search(name: str, query: str, mode: str, text: str, s: Any, lim: Any) -> str:
    """A one-document search's v1 text with what the rules in effect add when it found nothing (zero) or found mostly
    hits listed before (saturated)."""
    active, types = in_force()
    ev = record.current()
    if not active or ev is None or mode not in ("bm25", "literal"):
        return text
    sigs, _, _, _ = _v1(ev)
    call = Call("search", name, s, lim, text.split("\n"))
    v = api.view(name)
    fired = {"zero": any(x["k"] == "zero" for x in sigs), "saturated": any(x["k"] == "saturated" for x in sigs)}
    keys = None
    tokens = None

    def holds(c: lang.Cond) -> bool:
        nonlocal keys, tokens
        if c.name == "has-id":
            keys = api.ident_keys(v, query, limit=100) if keys is None else keys
            return bool(keys)
        if c.name == "searches":
            return _compare(s.searches, c.op, c.value)
        if c.name == "pages":
            return _compare(len({i["p"] for x in sigs if x["k"] == "hits" for i in x.get("items", []) if i.get("p")}), c.op, c.value)
        if c.name == "tokens":
            tokens = count_tokens(text) if tokens is None else tokens
            return _compare(tokens, c.op, c.value)
        return False

    for sig in ("zero", "saturated"):
        if not fired[sig]:
            continue
        for rule, over in _select(sig, active, types, name, holds):
            if not _uses_left(call, rule):
                continue
            for act in rule.rule.actions:
                if act.text() == "add absence":
                    _run(call, sig, rule, over, act, _add_absence, v, query, mode)
                elif act.text() == "add coverage":
                    _run(call, sig, rule, over, act, _add_coverage, v)
    _finish(call)
    return call.text()


def _add_absence(call: Call, sig: str, rule: Active, over: list[str], act: lang.Action, v: Any, query: str, mode: str) -> None:
    try:
        from tglw.search.absence import absence
    except ImportError:  # task SRCH's content not delivered yet
        return
    head = _head(rule, act.text())
    left, limit = call.room(rule)
    listed = call.s.hits.get(call.name, {}).get("a", set()) | call.s.listed.get(call.name, set())
    got = absence(v, query, mode, sorted(listed), min(ABSENCE_MAX, left - count_tokens(head)))
    if got is None:
        _decision(rule, sig, act.text(), 0, over=over, skipped=[{"what": "absence", "why": limit}])
        return
    piece = head + "\n" + "\n".join(got.lines)
    n = count_tokens(piece)
    why = call.why_not(rule, piece, n)
    if why:
        _decision(rule, sig, act.text(), 0, over=over, skipped=[{"what": "absence", "why": why}])
        return
    call.blocks.append(piece)
    call.spend(rule, n)
    near = [dict(x) for x in got.near]
    report(record.sig, "hits", mode="absence", n=len(near),
           items=[{"r": i, "a": x.get("addr"), "p": x.get("page"), "sec": x.get("sec")} for i, x in enumerate(near, 1)])
    _decision(rule, sig, act.text(), n, near=near, variants=[dict(x) for x in got.variants],
              parts=[dict(x) for x in getattr(got, "parts", None) or []], trimmed=got.trimmed, over=over)


def _add_coverage(call: Call, sig: str, rule: Active, over: list[str], act: lang.Action, v: Any) -> None:
    """Task SRCH's coverage: what this conversation has done in the document -- queries and their matches, pages and
    sections read, literal searches that found nothing -- facts only. Under TGLW_COVERAGE_DELTA a later summary is
    given the one the conversation last got for the document, and says only what is new since."""
    try:
        from tglw.search.coverage import coverage
    except ImportError as err:  # task SRCH's content not delivered yet: the rule adds nothing, the trace says why
        raise RuntimeError("add coverage: task SRCH's content is not in this build") from err
    head = _head(rule, act.text())
    left, limit = call.room(rule)
    s = call.s
    searches = [(q, "bm25") for q in s.queries.get(call.name, [])] + [(q, "literal") for q in s.literal.get(call.name, [])]
    previous = s.coverage.get(call.name) if _delta() else None
    got = coverage(v, searches, pages=s.pages.get(call.name, ()), listed=s.listed.get(call.name, ()),
                   budget=min(COVERAGE_MAX, left - count_tokens(head)), **({"previous": previous} if previous is not None else {}))
    if got is None:
        _decision(rule, sig, act.text(), 0, over=over, skipped=[{"what": "coverage", "why": limit}])
        return
    piece = head + "\n" + "\n".join(got.lines)
    n = count_tokens(piece)
    why = call.why_not(rule, piece, n)
    if why:
        _decision(rule, sig, act.text(), 0, over=over, skipped=[{"what": "coverage", "why": why}])
        return
    call.blocks.append(piece)
    call.spend(rule, n)
    s.coverage[call.name] = got
    _decision(rule, sig, act.text(), n, over=over, terms=got.terms, zeros=got.zeros, pages=got.pages, sections=got.sections,
              unread=got.unread, trimmed=got.trimmed, since=previous.searches if previous is not None else None,
              new_pages=getattr(got, "new_pages", None) if previous is not None else None)


# ---- region ------------------------------------------------------------------------------------------------------

def after_region(name: str, target: str, default_scale: bool, region: Any, s: Any, lim: Any) -> tuple[Any, str]:
    """An image region's v1 result, zoomed by a view rule when the call gave no scale, and the line naming the rule
    (empty when none acted)."""
    active, types = in_force()
    ev = record.current()
    if not active or ev is None:
        return region, ""
    _, _, out, addrs = _v1(ev)
    img = out.get("img") or {}
    kind = addrs[0].get("kind") if addrs else None
    call = Call("region", name, s, lim, [])

    def holds(c: lang.Cond) -> bool:
        if c.name == "scan":
            from tglw.figures import ocr
            return bool(img.get("page")) and ocr.scanned(name, img["page"])
        if c.name == "figure":
            return kind == "figure"
        if c.name == "table":
            return kind == "table"
        if c.name == "searches":
            return _compare(s.searches, c.op, c.value)
        if c.name == "pages":
            return _compare(1, c.op, c.value)
        if c.name == "tokens":
            return _compare(int(img.get("img_tokens") or 0), c.op, c.value)
        return False

    lines: list[str] = []
    for rule, over in _select("view", active, types, name, holds):
        if not _uses_left(call, rule):
            continue
        for act in rule.rule.actions:
            if act.verb != "zoom" or not default_scale or kind == "page" or not img:
                continue
            box = [region]

            def zoom(call: Call, sig: str, rule: Active, over: list[str], act: lang.Action) -> None:
                from tglw.figures import actions
                line = _head(rule, act.text())
                left, limit = call.room(rule)
                got = actions.zoom_view(name, target, float(act.arg), box[0], left - count_tokens(line))
                if isinstance(got, list):
                    if got:
                        _decision(rule, "view", act.text(), 0, over=over, skipped=[{**x, "why": limit} if x.get("why") == "budget" else x for x in got])
                    return
                n = got.tokens + count_tokens(line)
                box[0] = got.region
                call.spend(rule, n)
                lines.append(line)
                _decision(rule, "view", act.text(), n, over=over, **{"from": got.scale_from, "to": got.scale_to})

            _run(call, "view", rule, over, act, zoom)
            region = box[0]
    _finish(call)
    return region, "\n".join(lines)
