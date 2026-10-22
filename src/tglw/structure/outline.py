"""Bookmark route: clean the PDF outline, anchor entries to Docling blocks, measure its quality."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from tglw.common.bundle import Block, Doc, table_grid
from tglw.common.text import norm
from tglw.zh import is_cjk_doc
from tglw.zh.chars import HAN_RE

from .numbering import parse
from .roles import static_role, text_key

MATCH = 85
_FIELD = re.compile(r"^[A-Z][A-Za-z /'-]{2,40}:\s+\S")


@dataclass
class Entry:
    i: int
    level: int
    title: str
    page: int | None
    top: float | None
    junk: str | None = None
    ref: str | None = None
    order: int | None = None
    method: str | None = None
    score: float = 0.0
    drop: str | None = None
    parent: int | None = None
    depth: int = 0
    flags: list[str] = field(default_factory=list)
    number: str | None = None  # the line to read a number from, when not the title (header chapters)


def junk_kind(title: str, page: int | None) -> str | None:
    t = norm(title, lower=False)
    if not t or page is None:
        return "no_target"
    if t.lower().startswith("quick link"):
        return "link"
    role = static_role(t)
    if role in ("table_title", "figure_title"):
        return "caption"
    words = t.split()
    if len(words) >= 20 or t.endswith(("...", "…")) or (len(words) >= 12 and re.search(r"[a-z]\.\s+[A-Z]", t)):
        return "sentence"
    if _FIELD.match(t) and len(words) >= 4 and parse(t) is None:
        return "field"
    if t.lower().endswith(".pdf"):
        return "filename"
    if " " not in t and "-" not in t and not HAN_RE.search(t) and not parse(t) and (
            len(re.findall(r"[a-z][A-Z]", t)) >= 2 or (re.search(r"\d", t) and len(t) >= 10)):
        return "identifier"  # 'OLE_LINK18'; a Chinese title has no spaces either ('26、其他非流动资产')
    return None


def is_filename(title: str) -> bool:
    """Bookmarks named after source files, e.g. 'di-nist-sp1800-11b-final'."""
    t = norm(title, lower=False)
    return " " not in t and t.count("-") >= 2 and bool(re.search(r"\d", t)) and t == t.lower()


def volume_title(title: str) -> str | None:
    m = re.search(r"\d+-\d+([a-z])-", norm(title))
    return f"Volume {m.group(1).upper()}" if m else None


def _variants(b: Block, doc: Doc) -> list[tuple[str, str]]:
    """(kind, normalised text) forms a heading can take inside a block."""
    if b.kind == "table":
        grid = table_grid(doc.item(b.ref))
        cells = [row[0] for row in grid if row and row[0]] + [c for row in grid[:1] for c in row[1:] if c]
        return [("cell", text_key(c)) for c in cells[:60]]
    if b.kind != "text":
        return []
    full = text_key(b.text)
    out = [("full", full)]
    n = parse(b.text)
    if n and n.title:
        out.append(("full", text_key(n.title)))
    nxt = doc.blocks[b.order + 1] if b.is_heading and b.order + 1 < len(doc.blocks) else None
    if nxt is not None and nxt.is_heading and nxt.page == b.page:
        out.append(("joined", text_key(b.text + " " + nxt.text)))
    return out


def _similarity(title: str, kind: str, cand: str, heading: bool) -> tuple[float, str]:
    if not title or not cand:
        return 0.0, ""
    best, how = fuzz.ratio(title, cand), "text"
    if len(title) >= 12 and len(cand) > len(title) + 3:
        pre = fuzz.ratio(title, cand[: len(title)])
        if pre > best:
            best, how = pre, "prefix"
        if heading or kind == "cell":
            part = fuzz.partial_ratio(title, cand)
            if part > best + 2:
                best, how = part - 2, "substring"
    if kind == "cell" and how == "text":
        how = "cell"
    return best, how


def _chapter_head(doc: Doc, b: Block, title: str) -> Block:
    """'INTRODUCTION' just after a bare 'CHAPTER ONE' block: anchor the chapter at the number.

    Only when neither the bookmark nor the matched block carries its own number, and no other
    heading sits in between (10-K: 'Company Background' must not jump over 'Item 1.' to 'PART I')."""
    if parse(title) is not None or parse(b.text) is not None:
        return b
    for prev in reversed(doc.blocks[max(0, b.order - 2):b.order]):
        if prev.page != b.page:
            break
        if prev.is_heading:
            n = parse(prev.text)
            return prev if n is not None and not n.title else b
    return b


def anchor(doc: Doc, e: Entry, after: int) -> None:
    key = text_key(e.title)
    pages = [p for p in (e.page, e.page + 1, e.page - 1) if p and 1 <= p <= doc.page_count]
    best: tuple[float, Block | None, str] = (0.0, None, "")
    for p in pages:
        for b in doc.page_blocks(p):
            if b.layer != "body" or b.in_picture:
                continue
            for kind, cand in _variants(b, doc):
                sim, how = _similarity(key, kind, cand, b.is_heading)
                if sim < MATCH:
                    continue
                score = sim + (3 if b.is_heading else 0) + (2 if p == e.page else -4)
                if how == "text" and sim >= 99:
                    score += 4
                if kind == "joined" or how == "substring":
                    score -= 3
                if e.top is not None and b.bbox and p == e.page:
                    d = abs(b.bbox[1] - e.top)
                    score += 6 if d < 40 else 2 if d < 120 else 0
                if b.order < after:
                    score -= 8
                if score > best[0]:
                    best = (score, b, how)
    if best[1] is not None:
        b = _chapter_head(doc, best[1], e.title)
        e.ref, e.order, e.method, e.score = b.ref, b.order, best[2], best[0]
        return
    body = [b for b in doc.page_blocks(e.page) if b.layer == "body" and not b.in_picture and b.page == e.page]
    if not body:
        return
    if e.top is not None:
        below = [b for b in body if b.bbox and b.bbox[1] <= e.top + 6]
        if below:
            b = max(below, key=lambda x: (x.bbox[1], -x.order))
            e.ref, e.order, e.method = b.ref, b.order, "position"
            return
    e.ref, e.order, e.method = body[0].ref, body[0].order, "page"


def _lis(orders: list[int]) -> set[int]:
    """Indices of a longest non-decreasing subsequence."""
    import bisect

    tails: list[int] = []
    tail_idx: list[int] = []
    prev = [-1] * len(orders)
    for i, o in enumerate(orders):
        j = bisect.bisect_right(tails, o)
        if j == len(tails):
            tails.append(o)
            tail_idx.append(i)
        else:
            tails[j] = o
            tail_idx[j] = i
        prev[i] = tail_idx[j - 1] if j > 0 else -1
    out, k = set(), tail_idx[-1] if tail_idx else -1
    while k >= 0:
        out.add(k)
        k = prev[k]
    return out


def clean(doc: Doc, raw: list[dict]) -> list[Entry]:
    entries = [Entry(i, r["level"], r["title"], r["page"], r.get("top")) for i, r in enumerate(raw)]
    repeats: dict[str, int] = {}
    for e in entries:
        repeats[text_key(e.title)] = repeats.get(text_key(e.title), 0) + 1
    stack: list[Entry] = []
    for e in entries:
        while stack and stack[-1].level >= e.level:
            stack.pop()
        e.junk = junk_kind(e.title, e.page) or ("repeated" if repeats[text_key(e.title)] >= 3 else None)
        if not e.junk and stack and stack[-1].junk in ("no_target", "identifier"):
            e.junk = "under_junk"
        if not e.junk and stack and is_filename(e.title) and text_key(e.title) == text_key(stack[-1].title):
            e.junk = "duplicate_wrapper"
        if not e.junk and is_filename(e.title):
            if volume_title(e.title):
                e.flags.append("filename")
            else:
                e.junk = "filename"
        stack.append(e)
    after = 0
    seen: dict[str, Entry] = {}
    early = is_cjk_doc(doc)
    for e in entries:
        if e.junk:
            continue
        anchor(doc, e, after)
        if e.ref is None:
            e.drop = "unanchored"
            continue
        if early:
            _read_late(doc, e)
        if e.method not in ("position", "page"):
            after = max(after, e.order or 0)
        first = seen.get(e.ref)
        if first is not None and _continues(doc, first, e):
            first.title = f"{first.title.strip()} {e.title.strip()}"
            e.drop = "continuation"
            continue
        if first is not None and _same_section(first, e):
            e.drop = "duplicate"
            continue
        if first is not None:
            e.flags.append("shared_block")
        seen.setdefault(e.ref, e)
    live = [e for e in entries if not e.junk and not e.drop]
    keep = _lis([e.order for e in live])
    top = min((e.level for e in live), default=1)
    for k, e in enumerate(live):
        if k not in keep:
            e.drop = "out_of_order"
            if e.level == top and _leaf(entries, e):
                e.drop, e.flags = None, e.flags + ["reordered"]
    _levels(entries)
    return entries


def _read_late(doc: Doc, e: Entry) -> None:
    """A title Docling read after blocks printed below it on its page (Maotai's "第五节 重要事项" at the
    top of p37 comes after its own "一、承诺事项履行情况"; "第八节 财务报告" in mid-page after its "一、审计
    报告"): the entry starts at the first of the blocks just before it in reading order that are all
    printed below it."""
    b = doc.by_ref[e.ref]
    if not b.bbox or e.method in ("position", "page"):
        return
    before = sorted((x for x in doc.blocks if x.page == b.page and x.layer == "body" and not x.in_picture
                     and x.bbox and x.order < b.order), key=lambda x: x.order)
    k = len(before)
    while k and before[k - 1].bbox[1] <= b.bbox[3] + 1:
        k -= 1
    if k < len(before):
        e.order = before[k].order


def _continues(doc: Doc, first: Entry, e: Entry) -> bool:
    """A bookmark split over two entries ('3.15 ... PROCESSING AND' + 'TRANSPARENCY FAMILY')."""
    if e.level != first.level or e.method in ("position", "page"):
        return False
    block = text_key(doc.by_ref[e.ref].text)
    joined = text_key(first.title + " " + e.title)
    return fuzz.ratio(joined, block) >= 90 and fuzz.ratio(joined, block) > fuzz.ratio(text_key(first.title), block) + 5


def _same_section(first: Entry, e: Entry) -> bool:
    if e.method in ("position", "page"):
        return True
    a, b = text_key(first.title), text_key(e.title)
    return a in b or b in a


def _leaf(entries: list[Entry], e: Entry) -> bool:
    nxt = next((x for x in entries[e.i + 1:] if not x.junk), None)
    return nxt is None or nxt.level <= e.level


def _levels(entries: list[Entry]) -> None:
    stack: list[Entry] = []
    live = sorted((e for e in entries if not e.junk and not e.drop), key=lambda x: (x.order, x.i)) \
        if any("reordered" in e.flags for e in entries) else entries
    for e in live:
        if e.junk or e.drop:
            continue
        while stack and stack[-1].level >= e.level:
            stack.pop()
        e.parent = stack[-1].i if stack else None
        e.depth = stack[-1].depth + 1 if stack else 1
        stack.append(e)


def quality(entries: list[Entry]) -> dict:
    kept = [e for e in entries if not e.junk]
    live = [e for e in kept if not e.drop]
    text = [e for e in live if e.method not in ("position", "page")]
    junk: dict[str, int] = {}
    for e in entries:
        if e.junk:
            junk[e.junk] = junk.get(e.junk, 0) + 1
    drops: dict[str, int] = {}
    for e in kept:
        if e.drop:
            drops[e.drop] = drops.get(e.drop, 0) + 1
    return {
        "entries": len(entries),
        "junk": junk,
        "kept": len(kept),
        "dropped": drops,
        "used": len(live),
        "text_matched": len(text),
        "position_matched": sum(1 for e in live if e.method == "position"),
        "page_only": sum(1 for e in live if e.method == "page"),
        "match_rate": round(len(text) / len(kept), 3) if kept else 0.0,
        "max_depth": max((e.depth for e in live), default=0),
    }
