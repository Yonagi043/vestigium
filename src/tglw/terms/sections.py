"""Definition collections: abbreviation lists, definitions chapters, and glossaries.

A collection starts at a title block ("Abbreviations", "Appendix B. Glossary", "DEFINITIONS",
"LEXICON", ...) and grows page by page while pages keep yielding term/definition pairs. Pairs come
from two-column tables, side-by-side text blocks on one row, one-line "T - D" / "T: D" entries, or a
heading-styled term followed by its definition paragraph(s).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from tglw.common.bundle import Block, Doc, table_grid
from tglw.common.text import norm

from .model import Definition
from .parse import (
    cite_only, clean, is_acronym, is_definitionlike, is_header_row, is_termlike, split_colon_entry, split_dash_entries, split_period_entry,
    split_term_cell,
)

_VOCAB = {
    "list", "of", "symbols", "abbreviations", "abbreviation", "acronyms", "acronym", "and", "glossary",
    "terms", "term", "technical", "definitions", "definition", "common", "lexicon", "nomenclature",
    "special", "the", "key", "selected",
}
_PREFIX = re.compile(r"^(?:appendix\s+[a-z0-9]{1,3}\b\s*[.:\-]*\s*|\d+(?:\.\d+)*\.?\s+)")
_STOP = re.compile(r"^(?:appendix|chapter|part|section|annex|attachment|enclosure)\b|^\d+(?:\.\d+)*\.?\s+[A-Za-z]",
                   re.I)
_CAPS_TAIL = re.compile(r"\b[A-Z]{2,}(?:\s+[A-Z]{2,})+$")
_SKIP_LABELS = {"page_header", "page_footer", "caption", "footnote", "picture"}
MAX_PAGES = 80
MIN_PAIRS_TO_SPLIT = 5
MIN_ALPHA_ORDER = 0.6
_FAMILY = {"table": "grid", "row": "grid", "line": "line", "heading": "heading"}


def title_kind(b: Block) -> str | None:
    """Collection kind named by a title block, or None if the block is not such a title."""
    if b.kind != "text" or len(b.text) > 70 or re.search(r"[()?]|:$", b.text.strip()):
        return None
    kind = _title_kind(b.text)
    if kind is None and (m := _CAPS_TAIL.search(b.text.strip())):
        kind = _title_kind(m.group(0))
    return kind


def _title_kind(text: str) -> str | None:
    t = norm(text)
    for _ in range(2):
        t = _PREFIX.sub("", t)
    toks = re.findall(r"[a-z]+", t)
    if not toks or len(toks) > 7 or not set(toks) <= _VOCAB:
        return None
    s = set(toks)
    if s & {"abbreviations", "acronyms"} and "glossary" not in s:
        return "abbrev_table"
    if s & {"glossary", "lexicon", "nomenclature"} or ("terms" in s and s & {"definition", "definitions"}):
        return "glossary"
    if s == {"definitions"}:
        return "definitions_chapter"
    return None


def _is_stop_heading(b: Block) -> bool:
    if not b.is_heading or b.text.strip() == "[REDACTED]":
        return False
    t = b.text.strip()
    letters = [c for c in t if c.isalpha()]
    all_caps = len(letters) >= 10 and all(c.isupper() for c in letters) and len(t.split()) >= 2
    return bool(_STOP.match(t)) or all_caps


@dataclass
class Section:
    source: str
    title: str
    title_ref: str
    titles: set[str]
    pages: list[int] = field(default_factory=list)
    refs: set[str] = field(default_factory=set)
    pairs: list[Definition] = field(default_factory=list)
    rows_seen: int = 0

    @property
    def page_range(self) -> tuple[int, int]:
        return (min(self.pages), max(self.pages)) if self.pages else (0, 0)


def _overlap(a: Block, b: Block) -> float:
    (_, at, _, ab), (_, bt, _, bb) = a.bbox, b.bbox
    inter = min(at, bt) - max(ab, bb)
    return inter / max(1e-6, min(at - ab, bt - bb))


def _row_pairs(blocks: list[Block]) -> list[tuple[Block, Block]]:
    """Term block on the left, definition block to its right on the same text row."""
    cands = [b for b in blocks if b.kind == "text" and b.bbox]
    used: set[str] = set()
    out = []
    for t in sorted(cands, key=lambda b: (b.bbox[0], -b.bbox[1])):
        if t.ref in used or not is_termlike(clean(t.text), max_words=8) or split_dash_entries(t.text):
            continue
        right = [d for d in cands if d.ref not in used and d.ref != t.ref and d.parent == t.parent
                 and not is_acronym(d.text.strip(" -:"))
                 and d.bbox[0] >= t.bbox[2] - 3 and d.bbox[0] - t.bbox[2] < 320 and _overlap(t, d) >= 0.5]
        if right:
            d = min(right, key=lambda d: d.bbox[0])
            used.update((t.ref, d.ref))
            out.append((t, d))
    return out


def _definition(term_text: str, definition: str, ref: str, page: int, source: str, order: int,
                **extra) -> Definition | None:
    term, variants, cite = split_term_cell(term_text)
    definition = clean(definition)
    if (not term or not is_definitionlike(definition) or is_header_row(term, definition)
            or not is_termlike(term) or _STOP.match(term)):
        return None
    return Definition(term=term, variants=variants, definition=definition, ref=ref, page=page,
                      source=source, cite=cite, order=order, extra={k: v for k, v in extra.items() if v})


def _table_pairs(doc: Doc, b: Block, source: str, prev: Definition | None) -> tuple[list[Definition], int]:
    grid = table_grid(doc.item(b.ref))
    out: list[Definition] = []
    rows = 0
    for i, row in enumerate(grid):
        cells = [c for c in row if c]
        if not cells:
            continue
        rows += 1
        if not row[0] and (out or prev):
            tgt = out[-1] if out else prev
            tgt.definition = f"{tgt.definition} {clean(' '.join(cells))}"
            continue
        if len(cells) < 2:
            continue
        last = out[-1] if out else prev
        if (cite := cite_only(cells[0])) and last:
            d = _definition(last.term, " ".join(cells[1:]), b.ref, b.page, source, b.order, row=i, via="table")
            if d:
                d.variants, d.cite = list(last.variants), cite
                out.append(d)
            continue
        d = _definition(cells[0], " ".join(cells[1:]), b.ref, b.page, source, b.order, row=i, via="table")
        if d:
            out.append(d)
    return out, rows


def _line_entries(text: str, source: str) -> list[tuple[str, str]]:
    if source == "abbrev_table":
        return split_dash_entries(text) or ([e] if (e := split_colon_entry(text)) else [])
    e = split_colon_entry(text) or (split_period_entry(text) if source == "glossary" else None)
    return [e] if e else split_dash_entries(text, max_words=8)[:1]


def _line_pairs(doc: Doc, b: Block, source: str) -> list[Definition]:
    """One-line entries; a block spanning several pages is split per page so each entry gets its page."""
    if len(b.pages) > 1 and b.spans and b.spans[0].start is not None:
        raw = doc.item(b.ref).get("text") or ""
        parts = [(s.page, raw[s.start:s.end]) for s in b.spans]
    else:
        parts = [(b.page, b.text)]
    return [d for page, text in parts for t, x in _line_entries(text, source)
            if (d := _definition(t, x, b.ref, page, source, b.order, via="line"))]


def _page_pairs(doc: Doc, blocks: list[Block], source: str, prev: Definition | None
                ) -> tuple[list[Definition], int, set[str]]:
    """Pairs on one page of a collection; also the table rows seen and refs of the blocks used."""
    pairs: list[Definition] = []
    used: set[str] = set()
    rows = 0
    for b in blocks:
        if b.kind == "table":
            got, n = _table_pairs(doc, b, source, pairs[-1] if pairs else prev)
            rows += n
            if got or n:
                used.add(b.ref)
            pairs += got
    for t, d in _row_pairs([b for b in blocks if b.ref not in used]):
        if x := _definition(t.text.rstrip(" -:"), d.text, d.ref, d.page, source, t.order, term_ref=t.ref, via="row"):
            pairs.append(x)
            used.update((t.ref, d.ref))
    for b in blocks:
        if b.ref not in used and b.kind == "text" and not b.is_heading and (got := _line_pairs(doc, b, source)):
            pairs += got
            used.add(b.ref)
    rest = [b for b in blocks if b.ref not in used and b.kind == "text"]
    for i, h in enumerate(rest):
        if not h.is_heading or not is_termlike(clean(h.text)):
            continue
        body = []
        for b in rest[i + 1:]:
            if b.is_heading:
                break
            body.append(b)
        multi = len(body) > 1
        if body and (x := _definition(h.text, " ".join(b.text for b in body), body[0].ref, body[0].page,
                                      source, h.order, term_ref=h.ref, via="heading",
                                      def_refs=[b.ref for b in body] if multi else None,
                                      def_pages=sorted({b.page for b in body}) if multi else None)):
            pairs.append(x)
            used.update([h.ref] + [b.ref for b in body])
    pairs.sort(key=lambda d: d.order)
    return pairs, rows, used


def _family(d: Definition) -> str:
    return _FAMILY[d.extra["via"]]


def _accept(pairs: list[Definition], source: str, families: set[str]) -> list[Definition]:
    """Pairs that continue the collection: same layout family as its first page (unless the page repeats
    the collection title), acronym-shaped terms in an abbreviation list."""
    kept = [d for d in pairs if not families or _family(d) in families]
    if source == "abbrev_table" and kept and sum(is_acronym(d.term) for d in kept) < 0.5 * len(kept):
        return []
    return kept


def alpha_order(pairs: list[Definition]) -> float:
    """Share of neighbouring terms in alphabetical order (glossaries and abbreviation lists are sorted)."""
    keys = [re.sub(r"[^a-z0-9]", "", d.term.lower()) for d in pairs]
    if len(keys) < 2:
        return 1.0
    return sum(a <= b for a, b in zip(keys, keys[1:])) / (len(keys) - 1)


def _grow(doc: Doc, blocks: list[Block], i: int, source: str) -> tuple[Section, int]:
    title = blocks[i]
    sec = Section(source=source, title=title.text, title_ref=title.ref, titles={norm(title.text)})
    sec.refs.add(title.ref)
    by_page: dict[int, list[Block]] = {}
    title_pages = {title.page}
    j = i + 1
    while j < len(blocks):
        b = blocks[j]
        if b.page is None or b.page - title.page > MAX_PAGES or b.order < title.order:
            break
        if (k := title_kind(b)) is not None:
            key = norm(b.text)
            if key not in sec.titles and sum(len(v) for v in by_page.values()) and _count_ready(doc, by_page, sec):
                break
            sec.titles.add(key)
            sec.source = k
            sec.refs.add(b.ref)
            title_pages.add(b.page)
            j += 1
            continue
        if _is_stop_heading(b):
            break
        if b.label not in _SKIP_LABELS and b.text.strip() != "[REDACTED]":
            by_page.setdefault(b.page, []).append(b)
        j += 1
    end = blocks[j].order if j < len(blocks) else float("inf")
    prev: Definition | None = None
    families: set[str] = set()
    for page in sorted(by_page):
        pairs, rows, used = _page_pairs(doc, by_page[page], sec.source, prev)
        pairs = _accept(pairs, sec.source, set() if page in title_pages else families)
        thin = len(pairs) < 2 and len(used) < 0.5 * len(by_page[page])
        if (not pairs or (thin and page not in title_pages)) and (page > title.page or families):
            end = min(b.order for b in by_page[page])
            break
        families = families or {_family(d) for d in pairs}
        sec.pages.append(page)
        sec.pairs += pairs
        sec.rows_seen += rows
        sec.refs |= used
        prev = pairs[-1] if pairs else prev
    for d in sec.pairs:
        d.section = sec.title_ref
    nxt = next((k for k in range(i + 1, len(blocks)) if blocks[k].order >= end), len(blocks))
    return sec, max(nxt, i + 1)


def _count_ready(doc: Doc, by_page: dict[int, list[Block]], sec: Section) -> bool:
    """True once the blocks gathered so far already hold enough pairs to stand as their own collection."""
    n = 0
    prev = None
    for page in sorted(by_page):
        pairs, _, _ = _page_pairs(doc, by_page[page], sec.source, prev)
        n += len(pairs)
        prev = pairs[-1] if pairs else prev
        if n >= MIN_PAIRS_TO_SPLIT:
            return True
    return False


def find_sections(doc: Doc) -> list[Section]:
    orphans = set(doc.orphans)
    blocks = [b for b in doc.blocks if b.layer == "body" and b.ref not in orphans]
    sections: list[Section] = []
    i = 0
    while i < len(blocks):
        kind = title_kind(blocks[i])
        if kind is None:
            i += 1
            continue
        sec, i = _grow(doc, blocks, i, kind)
        if sec.pairs and alpha_order(sec.pairs) >= MIN_ALPHA_ORDER:
            sections.append(sec)
    return sections
