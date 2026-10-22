"""Docling side of the comparison, per PDF page: prov-split segments, first-page view, boxes,
and Docling's own parse cells (pages.json)."""

from __future__ import annotations

import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import regex

from tglw.common.bundle import Doc

from .layer import BBox
from .tokens import tokens


@dataclass(frozen=True, slots=True)
class Seg:
    ref: str
    label: str
    layer: str
    in_picture: bool
    first_page: int | None
    bbox: BBox | None
    tokens: tuple[str, ...]
    ocr: bool = False
    garbled: bool = False
    marker_tokens: int = 0


@dataclass
class DocPage:
    page: int
    segs: list[Seg] = field(default_factory=list)
    first: Counter[str] = field(default_factory=Counter)
    pictures: list[tuple[str, BBox]] = field(default_factory=list)
    tables: list[tuple[str, BBox]] = field(default_factory=list)
    formulas: list[tuple[str, BBox]] = field(default_factory=list)
    cells: Counter[str] = field(default_factory=Counter)
    ocr_boxes: list[BBox] = field(default_factory=list)


_GLYPH_CODE = regex.compile(r"(?<!\w)[A-Za-z]\d{2,5}(?!\w)")


def garbled(text: str) -> bool:
    """Undecoded font output: symbol/private-use characters or runs of glyph codes like "H1100"."""
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return False
    odd = sum(unicodedata.category(c) in ("So", "Co") for c in chars)
    codes = len(_GLYPH_CODE.findall(text))
    return odd >= 0.2 * len(chars) or (codes >= 3 and codes >= 0.3 * len(text.split()))


def _cell_bbox(cell: dict[str, Any], height: float) -> BBox:
    xs = [p[0] for p in cell["bbox"]]
    ys = [p[1] for p in cell["bbox"]]
    if cell.get("coordinate_origin", "TOPLEFT") == "TOPLEFT":
        return (min(xs), height - min(ys), max(xs), height - max(ys))
    return (min(xs), max(ys), max(xs), min(ys))


def overlaps(a: BBox, b: BBox) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[3] < b[1] and b[3] < a[1]


def contains(box: BBox, x: float, y: float, pad: float = 2.0) -> bool:
    return box[0] - pad <= x <= box[2] + pad and box[3] - pad <= y <= box[1] + pad


def _span_bbox(doc: Doc, ref: str, page: int) -> BBox | None:
    boxes = [s.bbox for s in doc.by_ref[ref].spans if s.page == page and s.bbox]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), max(b[1] for b in boxes),
            max(b[2] for b in boxes), min(b[3] for b in boxes))


def _page_cells(page: DocPage, cells: list[dict[str, Any]], height: float) -> None:
    for c in cells:
        page.cells.update(tokens(c.get("text") or ""))
        if c.get("from_ocr"):
            page.ocr_boxes.append(_cell_bbox(c, height))


def doc_pages(doc: Doc, parse_pages: list[dict[str, Any]]) -> dict[int, DocPage]:
    """List markers live in `marker`, outside the item text; they are added back to the tokens.
    Formulas without enrichment have empty text (the source sits in `orig`); their boxes are kept."""
    out = {p: DocPage(p) for p in range(1, doc.page_count + 1)}
    for b in doc.blocks:
        if b.page in out:
            out[b.page].first.update(tokens(b.text))
            if b.kind == "picture" and b.bbox:
                out[b.page].pictures.append((b.ref, b.bbox))
            if b.kind == "table" and b.bbox:
                out[b.page].tables.append((b.ref, b.bbox))
            if b.label == "formula" and not b.text and b.bbox:
                out[b.page].formulas.append((b.ref, b.bbox))
    for pp in parse_pages:
        page = out.get(pp["page_no"])
        if page is not None:
            _page_cells(page, pp.get("textline_cells") or [], pp["height"])
    for p, page in out.items():
        for b, text in doc.page_segments(p):
            box = _span_bbox(doc, b.ref, p)
            ocr = box is not None and any(overlaps(box, o) for o in page.ocr_boxes)
            marker = tokens(b.marker or "") if b.page == p else []
            page.segs.append(Seg(b.ref, b.label, b.layer, b.in_picture, b.page, box,
                                 tuple(marker + tokens(text)), ocr, garbled(text), len(marker)))
    return out
