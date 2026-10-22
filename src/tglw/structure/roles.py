"""Roles of heading blocks that are not section headings: running headers, captions, labels."""

from __future__ import annotations

import re
import statistics
from collections import defaultdict

from rapidfuzz import fuzz, process

from tglw.common import bundle
from tglw.common.bundle import Block, Doc
from tglw.common.text import norm
from tglw.zh.chars import HAN, HAN_RE

ROLES = ("section", "running_header", "table_title", "figure_title", "figure_text", "redacted", "label",
         "front_matter", "unknown")

_TABLE = re.compile(r"^table\s*[A-Z]?[\d.\-–]*\d[\w.\-–]*\s*([:.\-–—]|$)", re.I)
ATTACH = re.compile(r"^(exhibit\s+\d+(\.\d+)*[a-z]?\b|certifications?\s*$|certification (of|pursuant)\b)", re.I)
_FIGURE = re.compile(r"^(figure|fig\.|chart|graph|illustration)\s*[A-Z]?[\d.\-–]*\d[\w.\-–]*\s*([:.\-–—]|$)", re.I)
_REDACTED = re.compile(r"^(\[redacted\]\s*)+$", re.I)


_NOT_KEY = re.compile(f"[^a-z0-9{HAN}]+")
_HAN_GAP = re.compile(f"(?<=[{HAN}]) (?=[{HAN}])")


def text_key(s: str) -> str:
    """Lower-case letters and digits; Chinese characters too, without the spaces Docling leaves between
    them ("釋 義" -> "釋義"). Text without Chinese characters keys as before."""
    t = norm(s)
    if not HAN_RE.search(t):
        return re.sub(r"[^a-z0-9]+", " ", t).strip()
    return _HAN_GAP.sub("", _NOT_KEY.sub(" ", t).strip())


def page_height(doc: Doc, page: int) -> float:
    size = (doc.raw.get("pages") or {}).get(str(page), {}).get("size") or {}
    return float(size.get("height") or 792.0)


def static_role(text: str) -> str | None:
    s = norm(text, lower=False)
    if _REDACTED.match(s):
        return "redacted"
    if _TABLE.match(s):
        return "table_title"
    if _FIGURE.match(s):
        return "figure_title"
    if s.endswith(":") and len(s.split()) <= 6:
        return "label"
    return None


MARGIN_TOL = 10.0  # points: a running header keeps its height from page to page
MARGIN_GAP = 4.0  # points: and clears the first (last) body line of pages without it
MARGIN_PAGES = 3
MARGIN_WORDS = 40
MARGIN_BAND = 0.12  # share of the page height: where a heading that repeats a page header/footer may sit
MARGIN_BACK = 3  # pages: a running header repeats one printed on one of the pages just before
MARGIN_RUN = 10  # consecutive pages


def margin_key(text: str) -> str:
    return re.sub(r"\d+", "#", text_key(text))


def _span_boxes(doc: Doc) -> dict[int, list[tuple[Block, tuple[float, float, float, float]]]]:
    """Body content per page (pictures' inner text left out), one box per page a block touches."""
    out: dict[int, list[tuple[Block, tuple[float, float, float, float]]]] = defaultdict(list)
    for b in doc.blocks:
        if b.layer == "body" and not b.in_picture:
            for s in b.spans:
                if s.bbox is not None:
                    out[s.page].append((b, s.bbox))
    return out


def _repeats(lines: list[Block]) -> list[list[Block]]:
    """Short lines with the same text (digits aside) at one height (within MARGIN_TOL) on >= MARGIN_PAGES pages."""
    by_key: dict[str, list[Block]] = defaultdict(list)
    for b in lines:
        if key := margin_key(b.text):
            by_key[key].append(b)
    out = []
    for blocks in by_key.values():
        blocks.sort(key=lambda b: b.bbox[1])
        run = [blocks[0]]
        for b in blocks[1:] + [None]:
            if b is not None and b.bbox[1] - run[-1].bbox[1] <= MARGIN_TOL:
                run.append(b)
                continue
            if len({x.page for x in run}) >= MARGIN_PAGES:
                out.append(run)
            run = [b] if b is not None else []
    return out


def _outer(doc: Doc, b: Block) -> bool:
    """In the top or bottom quarter of its page."""
    h = page_height(doc, b.page)
    return b.bbox[1] >= 0.75 * h or b.bbox[3] <= 0.25 * h


def _clears(group: list[Block], first: dict[int, float]) -> bool:
    """Whether the group sits above the first body line of the pages it is not on (of its own pages when it
    is on nearly all), or runs over MARGIN_RUN consecutive pages (a checklist's running header, at the height
    where the manual's other pages start their body; Docling put table header cells above it on its own)."""
    pages = {b.page for b in group}
    bottom = statistics.median(b.bbox[3] for b in group)
    other = [y for p, y in first.items() if p not in pages]
    ref = other if len(other) >= MARGIN_PAGES else [first[p] for p in pages if p in first]
    return bool(ref) and bottom >= statistics.median(ref) + MARGIN_GAP or _longest_run(pages) >= MARGIN_RUN


def _longest_run(pages: set[int]) -> int:
    best = run = 0
    for p in sorted(pages):
        run = run + 1 if p - 1 in pages else 1
        best = max(best, run)
    return best


def running_headers(doc: Doc) -> set[str]:
    """Running headers Docling filed as body headings or one-cell tables. A line is one when the same short
    text sits at one height on >= MARGIN_PAGES pages, above where the body starts on the pages without it
    (or on a run of MARGIN_RUN consecutive pages); or when a heading near the top or bottom edge repeats, at
    the same height, a page header/footer that Docling put in furniture elsewhere, OCR variants included (a
    navigation bar names the section whose big title sits below it: that title is no twin). Single boxes
    are not trusted (OCR and merged blocks misplace some to the page top), the run of pages is. The first
    page of a run (a chapter's own title page) stays a heading: a running header repeats a line printed
    near the edge of one of the MARGIN_BACK pages before it. Repeated headings at the bottom are not taken
    by height alone: in the 59 documents the one such group was a step title ("12. Click Next.")."""
    lines = [b for b in doc.blocks if b.layer == "body" and not b.in_picture and b.kind in ("text", "table")
             and b.bbox and len(b.pages) == 1 and b.text.strip() and len(b.text.split()) <= MARGIN_WORDS
             and _outer(doc, b)]
    groups = _repeats(lines)
    grouped = {b.ref for g in groups for b in g}
    first = {p: max(box[1] for b, box in boxes if b.ref not in grouped)
             for p, boxes in _span_boxes(doc).items() if any(b.ref not in grouped for b, _ in boxes)}
    found = {b.ref: b for g in groups if _clears(g, first) for b in g if b.is_heading or _one_cell(doc, b)}
    twins: dict[str, list[Block]] = defaultdict(list)
    for b in doc.blocks:
        if b.layer != "body" and b.kind == "text" and b.page and b.bbox and margin_key(b.text):
            twins[margin_key(b.text)].append(b)
    twin_of = {b.ref: _twins(margin_key(b.text), twins) for b in lines if b.is_heading and _edge(doc, b)}
    for b in lines:
        same = [x for k in twin_of.get(b.ref, []) for x in twins[k]]
        if len({x.page for x in same}) >= MARGIN_PAGES \
                and abs(b.bbox[1] - statistics.median(x.bbox[1] for x in same)) <= MARGIN_TOL:
            found[b.ref] = b
    on_page: dict[int, set[str]] = defaultdict(set)
    for b in lines + [x for xs in twins.values() for x in xs]:
        on_page[b.page].add(margin_key(b.text))
    return {ref for ref, b in found.items() if any(
        _continues(key, k) for key in {margin_key(b.text), *twin_of.get(ref, [])}
        for p in range(b.page - MARGIN_BACK, b.page) for k in on_page[p])}


def _twins(key: str, twins: dict[str, list[Block]]) -> list[str]:
    """The page header/footer texts a heading repeats: the same text, or OCR variants of a longer one."""
    if len(key) < 15:
        return [key] if key in twins else []
    return [k for k, _, _ in process.extract(key, list(twins), scorer=fuzz.ratio, score_cutoff=80, limit=None)]


def _one_cell(doc: Doc, b: Block) -> bool:
    """A table that is one line of text (hkex 0088 sets some running headers, rule lines and all, as tables)."""
    return b.kind == "table" and len(bundle.table_grid(doc.item(b.ref))) == 1


def _edge(doc: Doc, b: Block) -> bool:
    h = page_height(doc, b.page)
    return b.bbox[1] >= (1 - MARGIN_BAND) * h or b.bbox[3] <= MARGIN_BAND * h


def _continues(key: str, earlier: str) -> bool:
    """The same running header, or one line of it ('APPENDIX IV' under 'APPENDIX IV STATUTORY ...')."""
    short, long_ = sorted((key, earlier), key=len)
    return key == earlier or (len(short) >= 6 and f" {short} " in f" {long_} ")


def classify(doc: Doc, margin: set[str] | None = None) -> dict[str, str]:
    """Initial role per heading block; the tree builder later marks real section headings."""
    roles = {b.ref: static_role(b.text) or "unknown" for b in doc.headings()}
    for ref in running_headers(doc) if margin is None else margin:
        if roles.get(ref) == "unknown":
            roles[ref] = "running_header"
    return roles
