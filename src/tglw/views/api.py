"""outline / read / search: compact text views of one document for a reading model."""

from __future__ import annotations

import os
import re
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from rank_bm25 import BM25Okapi

from tglw.common import bundle
from tglw.common.text import count_tokens, norm
from tglw.figures import rank as figure_rank
from tglw.pagemap.build import pdf_pages
from tglw.structure.numbering import number_key

from . import sidecars
from .docview import Block, DocView, Segment, long_ref, short_ref
from .render import render_loss, render_segment, squeeze, table_rows
from .switches import V1

READ_MAX_TOKENS = 8000
OUTLINE_MAX_LINES = 60
XREFS_PER_BLOCK = 6
TERMS_MAX = 12
EXCERPT_MAX_WORDS = 50  # the hit sentence is shown whole up to this length
EXCERPT_MIN_WORDS = 12  # a shorter hit sentence takes its neighbours
EXCERPT_TARGET_WORDS = 40  # neighbouring sentences are added while the excerpt stays this short
EXACT_CHECK_WORDS = 3   # semantic queries this short also get their exact-text count
CJK_HIT_CHARS = 8  # in Chinese text a hit is looked for in this many characters from each one
SEARCH_PATH_DEPTH = 2
PATH_PART_CHARS = 32
PATH_LEAF_CHARS = 60
BLOCK_MIN_K = 3
# M6 changes (R3's recommendations), each switchable so a replay can compare with the M5 behaviour.
M6 = {"page_context": True, "inline_pages": True, "continues": True, "contents": True, "ident_hits": True,
      "next_page_small_reads": "blocks"}  # replay: same evidence as after every small read, at a third of the cost
# M8 (AGENT P1): addresses read by intent -- lenient forms, and a section named with a page inside it read from that page
M8 = {"intent_addresses": True}
_FOOTNOTE = re.compile(r"^(?:fn|foot\s*notes?)\s*[:.#-]?\s*(\d{1,4})(?!\d)", re.I)  # "fn: 69", "fn69", "footnote 69"
_FOOTNOTE_NUMBER = re.compile(r"^\s*(\d{1,4})(?![\d,.]\d)")  # a footnote's text starts with its number
_ID = r"(?:(?:t|tab|fig|s)\d+|p\d+(?:-p?\d+)?)"
_ID_LIST = re.compile(rf"^{_ID}(?:[\s,;]+{_ID})+$")  # "t516 t517 t518" written as one address
_LOOSE_SECTION = re.compile(r"^(s\d+)\s+(?!from\s)\S.*$")  # "s34 2.2.4", "s1 Checklist Purpose …"
_BOX_ADDR = re.compile(r"^(p\d+):[-\d.,\s]+$")  # a region box "p11:0,300,800,600" given to read
READ_TOC_ADDRS = 8  # a read naming this many sections or blocks that does not fit returns their contents first (5 caught ordinary reads)
_PAGES_ADDR = re.compile(r"^p\d+(?:-p?\d+)?(?:\s+from\s+\S+)?$")  # page lists read on with next: instead
IDENT_KEYS, IDENT_HITS, IDENT_CHARS = 2, 3, 160  # numbers / quoted phrases per query, their literal hits shown, excerpt length

_PRINTED = re.compile(r"^(?:P(?=[0-9ivxlcdmIVXLCDM]|[A-Z]{1,4}-\d)|page:\s*)(?P<label>[^@]+?)\s*(?:@\s*(?P<ctx>.+))?$")
_ALIAS_WORDS = {"note": "note", "notes": "note", "item": "item", "part": "part", "chapter": "chapter",
                "appendix": "appendix", "annex": "appendix", "section": None, "sec": None, "§": None}
_WORD = re.compile(r"\w+(?:[-.]\w+)*")
# Chinese (with task G, off by default): TGLW_CJK=1 makes each Han character a word, matches Han text
# literally whatever the spaces between its characters ("中 醫 藥" in 74% of the HKEX Chinese), and says
# "no searchable words" instead of "0 matches" when the query has none
CJK_ENV = "TGLW_CJK"
_HAN = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_HAN_CHAR = re.compile(f"[{_HAN}]")
_NON_HAN = rf"(?:(?![{_HAN}])\w)+"
_WORD_CJK = re.compile(rf"[{_HAN}]|{_NON_HAN}(?:[-.]{_NON_HAN})*")
NO_WORDS = "query has no searchable words in this index; try other wording, or mode='literal'"
_SENT_END = re.compile(r"(?<=[.!?])\s+(?=[\"“'(\[]?[A-Z0-9])")
_ABBREV = re.compile(r"(?:\b[A-Z]|\b(?:[A-Za-z]\.)+[A-Za-z]|\bet al|\b(?:Figs?|Nos?|Inc|Co|Corp|Ltd|Mr|Mrs|Ms|Dr|St|vs|Vol|pp|Sec|Art|Ch|Eq|Ref|Rev|"
                     r"Dept|approx|cf|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept?|Oct|Nov|Dec))\.$")
_DIGIT = re.compile(r"\d")


@bundle.doc_cache
def view(name: str) -> DocView:
    return DocView(name)


def _view(doc: str | DocView) -> DocView:
    return doc if isinstance(doc, DocView) else view(doc)


# ---- addresses ------------------------------------------------------------------------------

@dataclass
class Resolved:
    addr: str
    segments: list[Segment] = field(default_factory=list)
    note: str | None = None


def _alias_nodes(v: DocView, text: str) -> list[str]:
    """Sections whose number matches "Note 7", "Item 7A", "§2.2", "AC-2(3)", "Appendix B", "Part II"."""
    s = text.strip()
    m = re.match(r"^(§+|[A-Za-z]+)\.?\s*(\S.*)$", s)
    word = m.group(1).lower() if m else ""
    if word.startswith("§"):
        word = "§"
    if m and word in _ALIAS_WORDS:
        rest, kinds = m.group(2), [_ALIAS_WORDS[word]] if _ALIAS_WORDS[word] else ["dotted", "roman", "letter"]
    else:
        rest, kinds = s, ["control", "dotted", "letter", "roman"]
    keys = {(k, key) for k in kinds if (key := number_key(k, rest))}
    return [sid for sid, n in v.sections.items() if (n.get("number_kind"), n.get("number_key")) in keys]


def resolve(v: DocView, addr: str, page_context: bool = False) -> Resolved:
    """Segments of an address ("… from t123" continues inside it). With page_context, a block
    address also brings the rest of its page(s) in its section (what read() shows)."""
    base, _, cursor = addr.strip().partition(" from ")
    r = _resolve_base(v, base.strip(), addr)
    if page_context and M6["page_context"] and r.segments and long_ref(base.strip()) is not None:
        r.segments = _page_context(v, r.segments)
    if cursor and r.segments:
        keys = [v.place(s) for s in r.segments if short_ref(s.block.ref) == cursor.strip()]
        if keys:
            start = min(keys)
            r.segments = [s for s in r.segments if v.place(s) >= start]
    return r


def _page_context(v: DocView, segs: list[Segment]) -> list[Segment]:
    """The addressed blocks with the other blocks of their pages in the same section: a block alone
    can leave out the condition or sentence next to it (a read of three table rows missed the condition
    stated in a paragraph on the same page)."""
    have = {(s.page, s.block.order) for s in segs}
    sections = {v.section_of(s.block) for s in segs}
    pages = sorted({s.page for s in segs if v.visible(s.block, s.page) or s.block.in_picture})
    extra = [x for p in pages for x in v.page_segments(p)
             if (x.page, x.block.order) not in have and v.section_of(x.block) in sections]
    return sorted(segs + extra, key=v.place)


def _resolve_base(v: DocView, base: str, addr: str) -> Resolved:
    try:
        return Resolved(addr, v.resolve(base))
    except ValueError as err:
        error = str(err)
    m = _PRINTED.match(base)
    if m and v.side.pagemap:
        label, ctx = m.group("label").strip(), m.group("ctx")
        pages = pdf_pages(v.side.pagemap, label, context=ctx)
        if len(pages) == 1:
            return Resolved(addr, v.page_segments(pages[0]))
        if not pages:
            return Resolved(addr, note=f"{addr}: no page printed {label!r}" + (f" in {ctx!r}" if ctx else ""))
        runs = v.side.pagemap["runs"]
        run_of = {p["pdf_page"]: p.get("run") for p in v.side.pagemap["pages"]}
        opts = [f"p{p}" + (f" ({runs[run_of[p]]['run_context']['text']})"
                           if run_of[p] is not None and runs[run_of[p]].get("run_context", {}).get("text") else "")
                for p in pages]
        return Resolved(addr, note=f"{addr}: printed {label!r} is on several pages: " + "; ".join(opts)
                        + " — read one p<N>, or add @context")
    sids = _alias_nodes(v, base)
    if len(sids) == 1:
        return Resolved(addr, v.resolve(sids[0]))
    if sids:
        return Resolved(addr, note=f"{addr}: several sections match: "
                        + "; ".join(f"{sid} {v.section_path(sid)}" for sid in sids[:8]))
    if V1["zh_addresses"] and _HAN_CHAR.search(base) and (sids := _zh_nodes(v, base)):
        if len(sids) == 1:
            return Resolved(addr, v.resolve(sids[0]))
        return Resolved(addr, note=f"{addr}: several sections match: "
                        + "; ".join(f"{sid} {v.section_path(sid)}" for sid in sids[:8]))
    if V1["footnote_addresses"] and (m := _FOOTNOTE.match(base)):
        return _footnote(v, int(m.group(1)), addr)
    return Resolved(addr, note=f"{addr}: {error}; also not a printed page (P36, page:iii@context) or a section number")


def _zh_nodes(v: DocView, text: str) -> list[str]:
    """Sections a Chinese number names -- "第二十七条", "第三章", "附錄二" -- by task ZH's rules, whose kind and
    key task A keeps for the sections it numbers so."""
    from tglw.zh import headings
    z = headings.parse(text)
    return [sid for sid, n in v.sections.items() if z and (n.get("number_kind"), n.get("number_key")) == (z.kind, z.key)]


def _footnote(v: DocView, number: int, addr: str) -> Resolved:
    """The footnote whose text starts with the number, for "fn: 69", "fn69", "footnote 69" (reads show
    footnotes as "fn: 69 …", and a model asked for six footnotes so and got six errors). Several footnotes
    with that number (numbering restarts in appendices) are listed."""
    found = [b for b in v.doc.blocks if b.label == "footnote" and not b.in_picture and v.visible(b)
             and (m := _FOOTNOTE_NUMBER.match(b.text)) and int(m.group(1)) == number]
    if len(found) == 1:
        return Resolved(addr, v.block_segments(found[0]))
    if found:
        return Resolved(addr, note=f"{addr}: several footnotes numbered {number}: "
                        + ", ".join(f"{short_ref(b.ref)} {v.page_tag(b.page)}" for b in found[:8]) + " — read one by id")
    return Resolved(addr, note=f"{addr}: no footnote numbered {number}; search mode='literal' finds its text")


# ---- read -----------------------------------------------------------------------------------

def _next(resolved: list[Resolved], left: list[tuple[int, tuple[int, int], Segment]]) -> str:
    """Addresses that cover what was not delivered; a cut address resumes " from <block>"."""
    rest = []
    for i, r in enumerate(resolved):
        mine = [s for j, _, s in left if j == i]
        if not mine:
            continue
        if len(mine) == len(r.segments):
            rest.append(r.addr)
        else:
            rest.append(f"{r.addr.split(' from ')[0]} from {short_ref(mine[0].block.ref)}")
    return "next: " + ", ".join(rest)


def _xref_note(v: DocView, ref: str) -> str | None:
    seen, out = set(), []
    for r in v.xrefs.get(ref, []):
        if r["key"] in seen:
            continue
        seen.add(r["key"])
        t = r["targets"][0]
        where = f"p{t['page']}" if t.get("doc") in (None, v.name) else f"{t['doc']} p{t['page']}"
        out.append(f"{r['span']} = {short_ref(t['ref'])} {where}" if t.get("ref") and t.get("doc") in (None, v.name)
                   else f"{r['span']} = {where}")
        if len(out) >= XREFS_PER_BLOCK:
            break
    return "  → " + "; ".join(out) if out else None


def _term_notes(v: DocView, text: str, pages: set[int]) -> list[str]:
    out = []
    for t in (v.side.terms or {}).get("terms", []):
        term = t["term"]
        if len(term) < 2 or t.get("def_page") in pages or (t.get("definition") or "\0") in text:
            continue
        flags = 0 if t["kind"] == "abbreviation" or term.isupper() else re.I
        if not re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, flags):
            continue
        where = f"p{t['def_page']}"
        if t.get("uncertain"):
            src = v.doc.by_ref.get(t.get("def_ref"))
            sent = next((s for s in re.split(r"(?<=[.;])\s+", src.text) if term.lower() in s.lower()), "") if src else ""
            out.append(f'{term} (uncertain, source only) {where}: "{sent[:160]}"')
        else:
            out.append(f"{term} = {t['definition'][:100]} ({where})")
        if len(out) >= TERMS_MAX:
            break
    return out


def read(doc: str | DocView, addrs: list[str] | str, max_tokens: int = READ_MAX_TOKENS, refs: bool = False,
         xrefs: bool = False, terms: bool = False, max_chars: int | None = None, grow: bool = True) -> str:
    """Text of the addressed sections/pages/blocks in page order, with page and section marks.

    Stops at a block boundary once max_tokens (or max_chars) is passed and ends with a "next:" line
    to continue."""
    v = _view(doc)
    addrs = [addrs] if isinstance(addrs, str) else list(addrs)
    intent: list[str] = []
    if M8["intent_addresses"]:
        addrs, intent = _intent(v, addrs, max_tokens)
    resolved = [resolve(v, a, page_context=True) for a in addrs]
    queue: list[tuple[int, tuple[int, int], Segment]] = []
    seen: set[tuple[int, int]] = set()
    for i, r in enumerate(resolved):
        for s in sorted(r.segments, key=v.place):
            key = (s.page, s.block.order)
            if key not in seen:
                seen.add(key)
                queue.append((i, key, s))
    lines = [f"[note] {r.note}" for r in resolved if r.note] + intent
    if M6["contents"] and sum(not _PAGES_ADDR.match(a.strip()) for a in addrs) >= READ_TOC_ADDRS and \
            (toc := _contents(v, resolved, queue, max_tokens, max_chars)):
        lines += toc  # the map first, then as much as fits (replay: haiku used the rest of such a read)
    used = count_tokens("\n".join(lines)) if lines else 0
    chars = len("\n".join(lines))
    page = section = None
    last: Segment | None = None
    shown_pages: set[int] = set()
    body: list[str] = []
    loose, rebuilt = v.loose_tables if V1["loose_tables"] else ({}, set())
    for n, (i, key, seg) in enumerate(queue):
        b = seg.block
        table = loose.get((seg.page, b.ref))  # task D2 v4: a table Docling did not recognise starts here
        if table is None and ((seg.page, b.ref) in rebuilt or not v.visible(b, seg.page)):
            continue
        if M6["inline_pages"] and last is not None and b.ref == last.block.ref and seg.page != last.page \
                and b.kind == "text" and body:
            # the same paragraph going on to the next page: kept whole, the page marked inside it
            text = f" [p{seg.page}] {squeeze(seg.text)}"
            cost = count_tokens(text)
            if used + cost > max_tokens or (max_chars is not None and chars + len(text) > max_chars):
                lines += body
                lines.append(_next(resolved, queue[n:]))
                return "\n".join(lines)
            body[-1] += text
            used += cost
            chars += len(text)
            page, last = None, seg  # the next block on this page gets its page mark
            shown_pages.add(seg.page)
            continue
        chunk = []
        if seg.page != page:
            chunk.append(f"== {v.page_tag(seg.page)} ==")
        opens = v.section_heading.get(b.ref)
        sid = opens or v.section_of(b)
        if sid and v.sections[sid].get("kind") == "root":
            sid = None
        heading_as = None
        if sid and sid != section:
            if opens:
                heading_as = sid
            else:
                chunk.append(f"## {sid} · {v.section_path(sid)} (cont.)")
        elif opens:
            heading_as = sid
        rendered = render_segment(v, seg, refs=refs, heading_as=heading_as)
        if table is not None:
            from tglw.tables.loose import render as render_loose
            shown = not table["restored"] and v.visible(b, seg.page)
            rendered = render_loose(table) + ("\n" + rendered if shown else "")
        if rendered.strip(" …") == "":
            continue
        chunk.append(rendered)
        if b.kind != "table":
            chunk += [render_loss(x) for x in v.losses.get(b.ref, []) if seg.page == b.page]
        if xrefs and (note := _xref_note(v, b.ref)):
            chunk.append(note)
        text = "\n".join(chunk)
        cost = count_tokens(text)
        if body and (used + cost > max_tokens or (max_chars is not None and chars + len(text) + 1 > max_chars)):
            lines += body
            lines.append(_next(resolved, queue[n:]))
            return "\n".join(lines)
        body.append(text)
        used += cost
        chars += len(text) + 1
        page, section, last = seg.page, sid or section, seg
        shown_pages.add(seg.page)
    lines += body
    if M6["continues"] and last is not None and (more := _continues(v, last, shown_pages)):
        if M6["next_page_small_reads"] and grow and len(shown_pages) <= 2 and \
                (M6["next_page_small_reads"] != "blocks" or any(long_ref(a.split(" from ")[0].strip()) for a in addrs)):
            return read(v, addrs + [more.split()[1]], max_tokens, refs, xrefs, terms, max_chars, grow=False)
        lines.append(more)
    if terms:
        notes = _term_notes(v, "\n".join(body), shown_pages)
        if notes:
            lines.append("terms: " + " · ".join(notes))
    return "\n".join(lines)


def _intent(v: DocView, addrs: list[str], budget: int = READ_MAX_TOKENS) -> tuple[list[str], list[str]]:
    """Addresses as meant (AGENT P1). An address that does not resolve as written is read leniently:
    several ids in one string are split, a section id followed by its number or title is the id, a
    region box is its page. A section named together with a page or block inside it (after its first
    page) is read from that page on, with a note, when its text before that page alone fills the
    budget -- a read of a page with its 56-page chapter re-read the chapter from its first page and was
    cut before the page after the one asked for; a short section is read whole (a three-page section named
    with a figure on its last page)."""
    out: list[str] = []
    for a in addrs:
        s = str(a).strip()
        if _resolve_base(v, s.partition(" from ")[0].strip(), s).segments:
            out.append(s)
        elif _ID_LIST.match(s):
            out += [x for x in re.split(r"[\s,;]+", s) if x]
        elif m := _LOOSE_SECTION.match(s) or _BOX_ADDR.match(s):
            out.append(m.group(1))
        else:
            out.append(s)
    pages = sorted({seg.page for a in out if re.fullmatch(r"p\d+(?:-p?\d+)?|(?:t|tab|fig)\d+", a)
                    for seg in _resolve_base(v, a, a).segments})
    notes = []
    for i, a in enumerate(out):
        span = v.section_pages(a) if re.fullmatch(r"s\d+", a) and a in v.sections else None
        inside = [p for p in pages if span and span[0] < p <= span[1]]
        if inside and _fills(v, a, inside[0], budget):
            first = next((seg for seg in sorted(v.resolve(a), key=v.place) if seg.page >= inside[0]), None)
            if first is not None:
                out[i] = f"{a} from {short_ref(first.block.ref)}"
                notes.append(f"[note] {a}: from p{inside[0]}, the page named with it ({a} starts p{span[0]})")
    return out, notes


def _fills(v: DocView, sid: str, page: int, budget: int) -> bool:
    """Whether the section's text before the page reaches the token budget (counted until it does)."""
    used = 0
    for seg in sorted(v.resolve(sid), key=v.place):
        if seg.page >= page:
            return False
        if v.visible(seg.block, seg.page):
            used += count_tokens(render_segment(v, seg))
            if used >= budget:
                return True
    return False


def _continues(v: DocView, last: Segment, shown: set[int]) -> str | None:
    """"continues: p151 (s17)" when the section of the last block shown goes on past the last page
    shown (a read of three blocks stopped at a page end; what it needed was on the next page)."""
    sid = v.section_of(last.block)
    if not sid or v.sections.get(sid, {}).get("kind") == "root":
        return None
    span = v.section_pages(sid)
    nxt = max(shown) + 1
    if not span or span[1] < nxt:
        return None
    return f"continues: p{nxt} ({sid})"


def _contents(v: DocView, resolved: list[Resolved], queue: list[tuple[int, tuple[int, int], Segment]],
              max_tokens: int, max_chars: int | None) -> list[str] | None:
    """For a read of many addresses that would not fit (tokens or characters): their sections, pages
    and sizes, shown before the text so the rest can be chosen (a read of 15 sections at once, 40k
    characters, was cut)."""
    tokens = _block_tokens(v.name)
    total = sum(tokens[b] for b in {s.block.order for _, _, s in queue})
    chars = sum(len(s.text) for _, _, s in queue)
    if total <= max_tokens and (max_chars is None or chars <= max_chars):
        return None
    out = [f"[note] {len(resolved)} addresses, about {_tok(total)} tokens: too long for one read. "
           "Contents first, then as much as fits; read the others by address."]
    for r in resolved:
        if not r.segments:
            continue
        pages = [s.page for s in r.segments]
        size = sum(tokens[b] for b in {s.block.order for s in r.segments})
        sid = r.addr.split(" from ")[0].strip()
        if sid in v.sections:
            label = v.section_label(sid)
        else:
            first = v.section_of(r.segments[0].block)
            label = v.section_label(first) if first and v.sections.get(first, {}).get("kind") != "root" else ""
        out.append(f"{r.addr} · {v.page_range_tag(min(pages), max(pages))}" + (f" · {_cut(label, 70)}" if label else "") + f" · {_tok(size)}")
    return out


# ---- outline --------------------------------------------------------------------------------

@bundle.doc_cache
def _block_tokens(name: str) -> tuple[int, ...]:
    v = view(name)
    out = []
    for b in v.doc.blocks:
        if not v.visible(b):
            out.append(0)
        else:
            out.append(sum(count_tokens(render_segment(v, s)) for s in v.block_segments(b) if v.visible(b, s.page)))
    return tuple(out)


def _tok(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def _header(v: DocView) -> str:
    total = sum(_block_tokens(v.name))
    pm = v.side.pagemap
    runs = ", ".join(f"{r['label_first']}–{r['label_last']}" for r in (pm or {}).get("runs", [])[:4])
    status = (v.side.structure or {}).get("verdict", {}).get("status", "none")
    vers = ", ".join(f"{k} {val}" for k, val in sidecars.versions().items())
    return (f"{v.name} · {v.doc.page_count} pages" + (f" (printed {runs})" if runs else "")
            + f" · {_tok(total)} tokens · structure {status} · sidecars: {vers}")


def _section_lines(v: DocView, root: str, depth: int) -> list[tuple[int, str]]:
    tokens = _block_tokens(v.name)
    base = v.sections[root]["depth"]
    kids: dict[str, list[str]] = {}
    for sid, n in v.sections.items():
        kids.setdefault(n.get("parent"), []).append(sid)
    out: list[tuple[int, str]] = []
    unsure = (v.side.structure or {}).get("verdict", {}).get("status") != "reliable"

    def walk(sid: str) -> None:
        for c in kids.get(sid, []):
            n = v.sections[c]
            rel = n["depth"] - base
            hidden = _count(kids, c)
            flag = " [level?]" if unsure and n.get("confidence") != "high" else ""
            more = f" · +{hidden} sub" if rel == depth and hidden else ""
            tok = sum(tokens[n["start_order"]:n["end_order"]])
            pages = v.section_pages(c)
            where = v.page_range_tag(*pages) if pages else "no pages"
            out.append((rel, f"{'  ' * (rel - 1)}{c} {v.section_label(c)}{flag} · {where} · {_tok(tok)}{more}"))
            if rel < depth:
                walk(c)

    walk(root)
    return out


def _count(kids: dict[str, list[str]], sid: str) -> int:
    return sum(1 + _count(kids, c) for c in kids.get(sid, []))


def _toc_lines(v: DocView) -> list[str]:
    toc = v.side.toc or {}
    out = []
    for e in toc.get("entries", []):
        if e["kind"] != "section" or e["pdf_page"] is None:
            continue
        indent = "  " * (min(e.get("level_hint") or 1, 4) - 1)
        title = f"{e['number']} {e['title']}" if e.get("number") else e["title"]
        out.append(f"{indent}{v.display(title)} · {v.page_tag(e['pdf_page'])}")
    return out


def outline(doc: str | DocView, depth: int = 2, section: str | None = None, max_lines: int = OUTLINE_MAX_LINES) -> str:
    """One line per section: id, number and title, PDF pages (printed pages), tokens; deeper levels
    are counted (+N sub). Falls back to the printed TOC, then to bare headings, when A is unreliable."""
    v = _view(doc)
    lines = [_header(v)]
    status = (v.side.structure or {}).get("verdict", {}).get("status")
    if v.sections and (status in ("reliable", "partial") or section):
        root = section or next(sid for sid, n in v.sections.items() if n["kind"] == "root")
        if root not in v.sections:
            return lines[0] + f"\n[note] no section {root!r}"
        rows = _section_lines(v, root, depth)
        while len(rows) > max_lines and depth > 1:
            depth -= 1
            rows = _section_lines(v, root, depth)
        lines += [r for _, r in rows[:max_lines]]
        if len(rows) > max_lines:
            lines.append(f"… +{len(rows) - max_lines} more; use outline(section=…) or read by page")
        if status == "partial":
            lines.append("[note] structure partial: [level?] marks sections whose level is not certain")
        return "\n".join(lines)
    toc = _toc_lines(v)
    if toc:
        lines.append("[note] section levels unknown; entries below are the printed table of contents (read by page)")
        lines += toc[:max_lines] + ([f"… +{len(toc) - max_lines} more"] if len(toc) > max_lines else [])
        return "\n".join(lines)
    heads = [f"{v.display(b.text)[:80]} · {v.page_tag(b.page)}" for b in v.doc.headings() if v.visible(b)]
    lines.append("[note] section levels unknown; headings in reading order (read by page)")
    lines += heads[:max_lines] + ([f"… +{len(heads) - max_lines} more"] if len(heads) > max_lines else [])
    return "\n".join(lines)


# ---- search ---------------------------------------------------------------------------------

def cjk() -> bool:
    return os.environ.get(CJK_ENV) == "1" or _DOC_CJK.get()[0]


def _fold() -> bool:
    return _DOC_CJK.get()[1]


# For the document being searched, from task SRCH's doc_cjk_mode: (CJK on by TGLW_SEARCH_CJK_AUTO for a Chinese
# document while TGLW_CJK is unset, traditional and simplified folded together, a Chinese document searched in
# CJK mode: its excerpts are sized in characters)
_DOC_CJK: ContextVar[tuple[bool, bool, bool]] = ContextVar("tglw_doc_cjk", default=(False, False, False))


def _chinese(v: DocView) -> bool:
    from tglw.zh import is_cjk_doc
    return is_cjk_doc(v.doc)


@contextmanager
def _cjk_of(v: DocView) -> Iterator[None]:
    from tglw.search.tokens import doc_cjk_mode
    mode = doc_cjk_mode(v.doc)
    on = mode != "off" and not os.environ.get(CJK_ENV)
    chinese = (on or os.environ.get(CJK_ENV) == "1") and _chinese(v)
    token = _DOC_CJK.set((on, mode.endswith("+fold"), chinese))
    try:
        yield
    finally:
        _DOC_CJK.reset(token)


def _words(text: str) -> list[str]:
    return (_WORD_CJK if cjk() else _WORD).findall(text.lower())


def _term(w: str) -> bool:
    """A query word worth matching: 3+ characters, or (CJK on) a Han character."""
    return len(w) > 2 or (cjk() and bool(_HAN_CHAR.match(w)))


@bundle.doc_cache
def _index(name: str, unit: str, han: bool = False) -> tuple[list[tuple[int, Segment | None]], BM25Okapi]:
    v = view(name)
    units: list[tuple[int, Segment | None]] = []
    texts: list[list[str]] = []
    if unit == "page":
        for p in range(1, v.doc.page_count + 1):
            words = [w for s in v.page_segments(p) if v.visible(s.block, p) for w in _words(s.text)]
            units.append((p, None))
            texts.append(words)
    else:
        for b in v.doc.blocks:
            if v.visible(b) and b.kind in ("text", "table"):
                for s in v.block_segments(b):
                    if not v.visible(b, s.page):
                        continue
                    units.append((s.page, s))
                    texts.append(_words(s.text))
    return units, BM25Okapi([t or ["∅"] for t in texts])


def _sentences(text: str) -> list[str]:
    """Sentences of a flat text: split after . ! ? before a capital, digit or bracket, but not after a
    short abbreviation (Fig. No. Inc. U.S. et al.)."""
    out: list[str] = []
    for part in _SENT_END.split(text):
        if out and _ABBREV.search(out[-1]):
            out[-1] += " " + part
        else:
            out.append(part)
    return [x for x in out if x.strip()]


def _window(words: list[str], hit: list[bool]) -> tuple[int, int]:
    """EXCERPT_MAX_WORDS words with the most hits; of equal windows the middle one."""
    n = EXCERPT_MAX_WORDS
    counts = [sum(hit[i:i + n]) for i in range(len(words) - n + 1)]
    best = max(counts)
    ties = [j for j, c in enumerate(counts) if c == best]
    i = ties[len(ties) // 2]
    return i, i + n


def _pieces(v: DocView, seg: Segment) -> list[str]:
    """What an excerpt is cut from: a table's rows (cells joined by |), else the sentences."""
    if seg.block.kind == "table":
        rows = []
        for row in table_rows(v, seg.block):
            cells = [" ".join(x.split()) for x in row]
            while cells and not cells[-1]:
                cells.pop()
            if any(cells):  # empty cells kept: which column an "x" is under is the content (800-53B SA-22)
                rows.append(" | ".join(cells))
        return rows
    text = " ".join(seg.text.split())
    if v.chinese:  # task ZH: Chinese sentences end at 。！？ as well
        from tglw.zh import sentences
        return [x for part in sentences(text) for x in _sentences(part)]
    return _sentences(text)


def _excerpt_from(pieces: list[str], pick: Callable[[str], tuple], i: int | None = None) -> str:
    """The best piece (by `pick`, or piece i) whole, then the following (else preceding) pieces while
    the excerpt stays within EXCERPT_TARGET_WORDS (always up to EXCERPT_MIN_WORDS); beyond
    EXCERPT_MAX_WORDS it is cut around the hits."""
    if not pieces:
        return ""
    if i is None:
        i = max(range(len(pieces)), key=lambda j: (pick(pieces[j]), -j))
    lo, hi = i, i + 1

    def size(a: int, b: int) -> int:
        return len(_spans(" ".join(pieces[a:b])))

    while True:
        short = size(lo, hi) < EXCERPT_MIN_WORDS
        if hi < len(pieces) and (short or size(lo, hi + 1) <= EXCERPT_TARGET_WORDS):
            hi += 1
        elif lo > 0 and (short or size(lo - 1, hi) <= EXCERPT_TARGET_WORDS):
            lo -= 1
        else:
            break
    text = " ".join(pieces[lo:hi])
    spans = _spans(text)
    words = [text[x:y] for x, y in spans]
    head, tail = lo > 0, hi < len(pieces)
    a, b = 0, len(words)
    if len(words) > EXCERPT_MAX_WORDS:
        if _DOC_CJK.get()[2]:  # a phrase of several characters (literal search) is in no single one
            hits = [bool(pick("".join(words[j:j + CJK_HIT_CHARS]))[0]) for j in range(len(words))]
        else:
            hits = [bool(pick(w)[0]) for w in words]
        a, b = _window(words, hits)
        head, tail = head or a > 0, tail or b < len(words)
    return ("…" if head else "") + _between(text, spans, words, a, b) + ("…" if tail else "")


_NONSPACE = re.compile(r"\S+")
_UNIT_CJK = re.compile(f"[{_HAN}]|[^\\s{_HAN}]+")


def _spans(text: str) -> list[tuple[int, int]]:
    """Where the words are, for sizing excerpts: whitespace-separated, or in a Chinese document searched in
    CJK mode each Chinese character on its own (a run of Chinese text has no spaces to count)."""
    return [m.span() for m in (_UNIT_CJK if _DOC_CJK.get()[2] else _NONSPACE).finditer(text)]


def _between(text: str, spans: list[tuple[int, int]], words: list[str], a: int, b: int) -> str:
    """Words a..b-1: joined by single spaces, or in a Chinese document cut from the text as it is."""
    if not _DOC_CJK.get()[2]:
        return " ".join(words[a:b])
    return text[spans[a][0]:spans[b - 1][1]] if b > a else ""


def _term_pick(terms: set[str], also: set[str] = frozenset()) -> Callable[[str], tuple]:
    """Piece rank: distinct query words it contains, all of them, words of `also` (G's excerpt), then
    whether it has a number."""
    def pick(piece: str) -> tuple[int, int, int, bool]:
        words = _words(piece)
        return (len(terms.intersection(words)), sum(w in terms for w in words), sum(w in also for w in words),
                bool(_DIGIT.search(piece)))
    return pick


def _excerpt(text: str, terms: set[str]) -> str:
    return _excerpt_from(_sentences(" ".join(text.split())), _term_pick(terms))


def _best_segment(v: DocView, refs: list[str], terms: set[str], also: set[str] = frozenset()) -> Segment | None:
    """The shown block part sharing the most words with `also` (G's excerpt, when given), then
    with the query (distinct, then all); earlier refs first."""
    best, key = None, None
    for i, ref in enumerate(refs):
        b = v.doc.by_ref.get(ref)
        if b is None or b.kind not in ("text", "table"):
            continue
        for seg in v.block_segments(b):
            if not v.visible(b, seg.page):
                continue
            words = _words(seg.text)
            k = (len(also & set(words)), len(terms & set(words)), sum(w in terms for w in words), -i)
            if key is None or k > key:
                best, key = seg, k
    return best


def _passage(v: DocView, refs: list[str], pick: Callable[[str], tuple]) -> tuple[Segment, str] | None:
    """A G hit's passage (its blocks' shown parts in reading order, cut in pieces): the block part
    holding the best piece names the hit; the excerpt grows over neighbouring pieces on its page."""
    segs = sorted((s for r in dict.fromkeys(refs) if (b := v.doc.by_ref.get(r)) is not None and b.kind in ("text", "table")
                   for s in v.block_segments(b) if v.visible(b, s.page)), key=v.place)
    items = [(s, piece) for s in segs for piece in _pieces(v, s)]
    if not items:
        return None
    i = max(range(len(items)), key=lambda j: (pick(items[j][1]), -j))
    seg = items[i][0]
    same = [k for k, (s, _) in enumerate(items) if s.page == seg.page]
    lo, hi = min(same), max(same) + 1
    return seg, _excerpt_from([p for _, p in items[lo:hi]], pick, i - lo)


def _locate(tokens: list[str], window: list[str]) -> int | None:
    """Where G's excerpt window starts in the passage tokens (its edge words may be cut)."""
    for w in (window, window[1:-1]):
        n = len(w)
        if n:
            for i in range(len(tokens) - n + 1):
                if tokens[i] == w[0] and tokens[i:i + n] == w:
                    return i - (w is not window)
    return None


def _g_excerpt(v: DocView, refs: list[str], g_excerpt: str, pick: Callable[[str], tuple]) -> tuple[Segment, str] | None:
    """G's excerpt window (its weighted choice of the densest words) widened to whole sentences or
    table rows, trimmed from the edge with fewer query words to EXCERPT_MAX_WORDS; named by the block
    part holding most of it. None when the window is not found in the shown text."""
    segs = [s for r in dict.fromkeys(refs) if (b := v.doc.by_ref.get(r)) is not None and b.kind in ("text", "table")
            for s in v.block_segments(b) if v.visible(b, s.page)]
    pieces = [(k, piece) for k, s in enumerate(segs) for piece in _pieces(v, s)]
    flat: list[str] = []
    where: list[int] = []
    for j, (_, piece) in enumerate(pieces):
        toks = _words(piece)
        flat += toks
        where += [j] * len(toks)
    window = _words(g_excerpt)
    at = _locate(flat, window)
    if at is None:
        return None
    covered = where[max(0, at):at + len(window)]
    lo, hi = covered[0], covered[-1] + 1

    def size(a: int, b: int) -> int:
        return sum(len(_spans(pieces[j][1])) for j in range(a, b))

    while size(lo, hi) > EXCERPT_MAX_WORDS and hi - lo > 1:
        if pick(pieces[lo][1]) < pick(pieces[hi - 1][1]):
            lo += 1
        else:
            hi -= 1
    while size(lo, hi) < EXCERPT_MIN_WORDS and (lo > 0 or hi < len(pieces)):
        hi, lo = (hi + 1, lo) if hi < len(pieces) else (hi, lo - 1)
    owner = Counter(pieces[j][0] for j in range(lo, hi) for _ in _spans(pieces[j][1])).most_common(1)[0][0]
    text = " ".join(pieces[j][1] for j in range(lo, hi))
    spans = _spans(text)
    words = [text[x:y] for x, y in spans]
    head, tail = lo > 0, hi < len(pieces)
    a, b = 0, len(words)
    if len(words) > EXCERPT_MAX_WORDS:
        a, b = _window(words, [bool(pick(w)[0]) for w in words])
        head, tail = head or a > 0, tail or b < len(words)
    return segs[owner], ("…" if head else "") + _between(text, spans, words, a, b) + ("…" if tail else "")


def _hit_line(v: DocView, rank: int, page: int, seg: Segment | None, terms: set[str], addr: bool, note: str = "",
              also: set[str] = frozenset(), excerpt: str | None = None) -> str:
    """"rank. t123 · p45 · s12 path | sentence" for the block part (or page) a hit narrows to."""
    sid = v.section_of(seg.block) if seg else None
    if sid and seg and v.section_heading.get(seg.block.ref) == sid:
        sid = v.sections[sid].get("parent")
    path = v.section_path(sid) if sid and v.sections.get(sid, {}).get("kind") != "root" else ""
    path = _short_path(path.split(" > ")) if path else ""
    head = (f"{short_ref(seg.block.ref)} · " if addr and seg else "") + v.page_tag(page)
    text = excerpt if excerpt is not None else _excerpt_from(_pieces(v, seg), _term_pick(terms, also)) if seg else ""
    return f"{rank}. {head}" + (f" · {sid} {path}" if path else "") + note + f" | {text}"


def _exact_note(v: DocView, query: str) -> str:
    """For a short query: whether its exact text occurs at all (a semantic search alone cannot say)."""
    if len(query.split()) > EXACT_CHECK_WORDS or not literal_key(query):
        return ""
    hits = _literal_hits(v, query)
    if not hits:
        return " · exact text: 0 matches in this document"
    pages = sorted({h[2].page for h in hits})
    shown = ", ".join(f"p{p}" for p in pages[:3]) + (f" +{len(pages) - 3} pages" if len(pages) > 3 else "")
    return f" · exact text: {sum(h[1] for h in hits)} matches ({shown})"


_IDENT = re.compile(r"[\"“”]([^\"“”]{3,80})[\"“”]"  # a quoted phrase
                    r"|(?<![\w-])([A-Z]{2,4}-\d+(?:\(\d+\))*)(?![\w-])"  # a control number: SI-4, AC-2(3)
                    r"|(?<![\w.])((?:[IVX]+|\d+)(?:\.\d+){2,})(?![\w.])"  # a clause number: II.3.8.36, 2.4.1
                    r"|(§\s?\d+(?:\.\d+)*)")  # a section sign: §2.2


def _ident_hits(v: DocView, query: str) -> list[str]:
    """For each number or quoted phrase in a semantic query: where its exact text occurs, best first
    by the query's other words (two semantic searches for "withdrawn controls incorporated SI-4" missed
    what one literal search found)."""
    controls = any(n.get("number_kind") == "control" for n in v.sections.values())  # "GPT-3" is no control number
    keys = list(dict.fromkeys(next(g for g in m.groups() if g) for m in _IDENT.finditer(query)
                              if controls or not m.group(2)))[:IDENT_KEYS]
    out = []
    for key in keys:
        hits = _literal_hits(v, key)
        if not hits:
            out.append(f'literal "{key}": 0 matches')
            continue
        others = {w for w in _words(query) if _term(w)} - set(_words(key))
        hits.sort(key=lambda h: (-len(others & set(_words(h[2].text))), -h[0], h[2].page, h[2].block.order))
        out.append(f'literal "{key}": {sum(h[1] for h in hits)} matches, those with most query words:')
        out += [f"  {short_ref(o.ref)} · p{seg.page}{_hit_section(v, o)} | {_cut(_literal_excerpt(v, seg, key), IDENT_CHARS)}"
                for _, _, seg, o in hits[:IDENT_HITS]]
    return out


def _hit_section(v: DocView, b: Block) -> str:
    """" · s123 AU-6(2) AUTOMATED SECURITY ALERTS": which section (and control) a short hit is in."""
    sid = v.section_of(b)
    n = v.sections.get(sid or "", {})
    if not sid or n.get("kind") == "root":
        return ""
    label = v.section_label(sid)
    key = n.get("number_key") if n.get("number_kind") == "control" else None
    return f" · {sid} " + (f"{key} " if key and key not in label else "") + _cut(label, 48)


def _g_search(v: DocView, query: str, k: int, unit: str) -> str:
    """Hits from task G's search (tglw.search), each narrowed to the block part (or, for pages, the
    part of the page) with the most query words: its address, page, section and hit sentence."""
    from tglw.search import search as g_search

    hits = g_search(v.name, query, k=k, unit=unit)
    note = _exact_note(v, query)
    if not hits and cjk():  # G (CJK on) finds nothing only when no query word is in its index
        lines = [f'search "{query}" · {unit}s · top {k} · engine G' + ("" if " 0 matches" in note else note), NO_WORDS]
        return "\n".join(lines + (_ident_hits(v, query) if M6["ident_hits"] else []))
    lines = [f'search "{query}" · {unit}s · top {k} · engine G' + note]
    terms = {w for w in _words(query) if _term(w)}
    for rank, h in enumerate(hits, 1):
        fuzzy = " · fuzzy" if getattr(h, "fuzzy", False) else ""
        fig = getattr(h, "figure", None)  # G v4: a hit in a picture's caption or labels, read as the picture
        if unit == "block" and fig and fig in v.doc.by_ref:
            pic = v.doc.by_ref[fig]
            lines.append(_hit_line(v, rank, pic.page, Segment(pic, pic.page, ""), terms, addr=True, note=" · figure" + fuzzy,
                                   excerpt=_excerpt(v.display(squeeze(h.excerpt)), terms)))
            continue
        pick = _term_pick(terms, set(_words(h.excerpt)))
        refs = list(h.refs) if unit == "block" and getattr(h, "refs", None) else [b.ref for b in v.by_page.get(h.page, [])]
        head = _short_heading(v, refs) if unit == "block" and V1["heading_next"] else None
        after = _block_after(v, head) if head is not None else None
        refs = refs + [after.ref] if after is not None else refs
        found = _g_excerpt(v, refs, h.excerpt, pick) or _passage(v, refs, pick)
        if found and unit == "page" and found[0].page != h.page:
            found = None
        if found is None:
            lines.append(f"{rank}. {v.page_tag(h.page)}{fuzzy} | {_excerpt(v.display(squeeze(h.excerpt)), terms)}")
            continue
        seg, text = found
        if after is not None and head is not None:
            seg = next((s for s in v.block_segments(head) if v.visible(head, s.page)), seg)  # the hit stays the heading
        lines.append(_hit_line(v, rank, seg.page, seg, terms, addr=unit == "block", note=fuzzy, excerpt=text))
    return "\n".join(lines + (_ident_hits(v, query) if M6["ident_hits"] else []))


def _short_heading(v: DocView, refs: list[str]) -> Block | None:
    """The hit's only block when it is a heading shorter than EXCERPT_MIN_WORDS (M6.2: a hit on 800-53r5's
    IA-5(4) title showed the title alone; its withdrawal note is the next block)."""
    b = v.doc.by_ref.get(refs[0]) if len(refs) == 1 else None
    if b is None or b.kind != "text" or not (b.is_heading or b.ref in v.section_heading):
        return None
    return b if len(squeeze(b.text).split()) < EXCERPT_MIN_WORDS else None


def _block_after(v: DocView, head: Block) -> Block | None:
    """The text or table block shown right after a heading, on its page or the next (moved blocks where
    they are printed); for a section heading, only a block of that section."""
    if head.page is None:
        return None
    sid = v.section_heading.get(head.ref)
    segs = sorted((s for p in (head.page, head.page + 1) for s in v.page_segments(p)), key=v.place)
    at = next((i for i, s in enumerate(segs) if s.block.ref == head.ref), len(segs))
    for s in segs[at + 1:]:
        b = s.block
        if b.ref == head.ref or b.kind not in ("text", "table") or not v.visible(b, s.page):
            continue
        return b if not sid or v._within(v.section_of(b) or "", sid) else None
    return None


def _cut(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n - 1] + "…"


def _short_path(titles: list[str]) -> str:
    """Last SEARCH_PATH_DEPTH section titles: parents cut at PATH_PART_CHARS, the hit's own section
    (where the distinguishing words often come last, as in NIST enhancements) at PATH_LEAF_CHARS."""
    parts = [" ".join(t.split()) for t in titles[-SEARCH_PATH_DEPTH:]]
    return " > ".join(_cut(p, PATH_LEAF_CHARS if i == len(parts) - 1 else PATH_PART_CHARS) for i, p in enumerate(parts))


_NUM_SEP = re.compile(r"(?<=\d)[,\s\u00a0\u202f](?=\d{3}(?!\d))")


def literal_key(text: str) -> str:
    """Normalised text for literal matching: NFKC, unified dashes and quotes, lower case, single
    spaces, and no thousands separators inside numbers ("20,497" and "20 497" -> "20497"); with CJK
    on, no spaces between Han characters."""
    key = _NUM_SEP.sub("", norm(text))
    if cjk():
        from tglw.search.tokens import squeeze_han
        key = squeeze_han(key)
    if _fold():  # task ZH: traditional and simplified characters match each other (fold keeps the length)
        from tglw.zh import fold
        key = fold(key)
    return key


def _literal_hits(v: DocView, query: str) -> list[tuple[int, int, Segment, Block]]:
    """(whole-word hits, all hits, segment, block that reads it) for every shown text or table
    segment containing query -- text inside a picture (captions, labels) included, read through
    its picture."""
    with _cjk_of(v):
        return _literal_hits_in(v, query)


def _literal_hits_in(v: DocView, query: str) -> list[tuple[int, int, Segment, Block]]:
    q = literal_key(query)
    if not q:
        return []
    han = cjk() and bool(_HAN_CHAR.search(q))  # Han text has no word boundaries: an end in Han needs none
    word = re.compile(("" if han and _HAN_CHAR.match(q[0]) else r"(?<!\w)") + re.escape(q)
                      + ("" if han and _HAN_CHAR.match(q[-1]) else r"(?!\w)"))
    out = []
    for b in v.doc.blocks:
        if b.kind not in ("text", "table"):
            continue
        owner = b
        if b.in_picture and b.layer == "body":
            pic = v._picture_of(b)
            owner = v.doc.by_ref.get(pic) if pic else None
            if owner is None or not v.visible(owner):
                continue
        elif not v.visible(b):
            continue
        for s in v.block_segments(b):
            if owner is b and not v.visible(b, s.page):
                continue
            if owner is not b and v.hidden(s.page, owner.ref):
                continue
            t = literal_key(s.text)
            n = t.count(q)
            if n:
                out.append((len(word.findall(t)), n, s, owner))
    return _one_per_picture(out)


def _one_per_picture(hits: list[tuple[int, int, Segment, Block]]) -> list[tuple[int, int, Segment, Block]]:
    """Hits inside one picture on one page count as one block (the picture is read as one), with at
    most one whole-word hit, so a chart repeating a tick label does not outrank prose."""
    out: list[tuple[int, int, Segment, Block]] = []
    merged: dict[tuple[str, int], int] = {}
    for whole, n, seg, owner in hits:
        if owner.kind != "picture":
            out.append((whole, n, seg, owner))
            continue
        key = (owner.ref, seg.page)
        if key in merged:
            w, m, first, _ = out[merged[key]]
            out[merged[key]] = (min(1, w + whole), m + n, first if w >= whole else seg, owner)
        else:
            merged[key] = len(out)
            out.append((min(1, whole), n, seg, owner))
    return out


def _literal_excerpt(v: DocView, seg: Segment, query: str) -> str:
    q = literal_key(query)
    return _excerpt_from(_pieces(v, seg), lambda part: (q in literal_key(part),))


def literal_search(v: DocView, query: str, k: int, unit: str) -> str:
    """Whether the exact text occurs comes first ("not in this document" is an answer too), then
    the hits, whole-word matches first."""
    hits = sorted(_literal_hits(v, query), key=figure_rank.block_key)  # task VIS (E7): hits in figures after text hits
    if not hits:
        return (f'0 matches for "{query}" in this document (exact text, any case, tables included)\n'
                "try other wording, or mode='bm25' (it retries with near spellings when nothing matches)")
    total = sum(h[1] for h in hits)
    lines = [f'{total} matches for "{query}" in {len(hits)} blocks · literal · {unit}s']
    sections = _alias_nodes(v, query)
    if sections:
        lines.append("section number: " + "; ".join(f"{sid} {v.section_label(sid)}" for sid in sections[:3])
                     + f" (read('{query}') reads it)")
    if unit == "page":
        pages: dict[int, list[tuple[int, int, Segment]]] = {}
        for h in hits:
            pages.setdefault(h[2].page, []).append(h)
        ranked = sorted(pages.items(), key=figure_rank.page_key)
        for rank, (page, hs) in enumerate(ranked[:k], 1):
            seg = hs[0][2]
            sid = v.section_of(hs[0][3])
            path = _short_path(v.section_path(sid).split(" > ")) if sid and v.sections.get(sid, {}).get("kind") != "root" else ""
            lines.append(f"{rank}. {v.page_tag(page)} · {sum(h[1] for h in hs)}×" + (f" · {sid} {path}" if path else "")
                         + f" | {_literal_excerpt(v, seg, query)}")
        return "\n".join(lines)
    for rank, (_, n, seg, owner) in enumerate(hits[:k], 1):
        sid = v.section_of(owner)
        path = _short_path(v.section_path(sid).split(" > ")) if sid and v.sections.get(sid, {}).get("kind") != "root" else ""
        lines.append(f"{rank}. {short_ref(owner.ref)} · {v.page_tag(seg.page)} · {n}×" + (f" · {sid} {path}" if path else "")
                     + f" | {_literal_excerpt(v, seg, query)}")
    return "\n".join(lines)


def search(doc: str | DocView, query: str, k: int = 5, unit: str = "block", engine: str = "auto",
           mode: str = "bm25") -> str:
    """Address, page, section path and the hit sentence per hit. mode "literal" finds an exact
    string (numbers match across thousands separators) and says first whether it occurs at all.
    engine "G" and "auto" use task G's search (it needs no data of its own: a user's library has no data/G),
    "bm25" the local BM25. Block results return at least BLOCK_MIN_K hits; a short query also gets its
    exact-text count."""
    v = _view(doc)
    with _cjk_of(v):
        return _search(v, query, k, unit, engine, mode)


def _search(v: DocView, query: str, k: int, unit: str, engine: str, mode: str) -> str:
    if unit == "block":
        k = max(k, BLOCK_MIN_K)
    if mode == "literal":
        return literal_search(v, query, k, unit)
    if engine in ("G", "auto"):
        return _g_search(v, query, k, unit)
    units, bm = _index(v.name, unit, cjk())
    q = _words(query)
    scores = bm.get_scores(q)
    order = sorted(range(len(units)), key=lambda i: -scores[i])[:k]
    terms = set(q)
    lines = [f'search "{query}" · {unit}s · top {len(order)}' + _exact_note(v, query)]
    for rank, i in enumerate(order, 1):
        if scores[i] <= 0:
            break
        page, seg = units[i]
        if seg is None:
            seg = _best_segment(v, list(dict.fromkeys(b.ref for b in v.by_page.get(page, []))), terms)
            seg = seg if seg and seg.page == page else None
            lines.append(_hit_line(v, rank, page, seg, terms, addr=False))
        else:
            lines.append(_hit_line(v, rank, page, seg, terms, addr=True))
    return "\n".join(lines + (_ident_hits(v, query) if M6["ident_hits"] else []))
