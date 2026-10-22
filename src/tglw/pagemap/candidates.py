"""Collect page-number candidates per PDF page from Docling furniture and the PDF text layer."""

from __future__ import annotations

import re
from dataclasses import dataclass

import pypdfium2 as pdfium

from tglw.common.bundle import Doc

from .labels import PageNum, parse

EDGE_BAND = 0.08
BODY_MAX_CHARS = 20
_SEGMENTS = re.compile(r"\s*\|\s*|\s{2,}|\t")

# (channel, how the number was found) -> weight
WEIGHTS = {
    ("docling", "whole"): 1.0,
    ("docling", "segment"): 0.8,
    ("docling", "token"): 0.5,
    ("docling_body", "whole"): 0.6,
    ("textlayer", "whole"): 0.7,
    ("textlayer", "segment"): 0.5,
    ("textlayer", "token"): 0.3,
}


@dataclass(frozen=True, slots=True)
class Cand:
    page: int
    num: PageNum
    weight: float
    channel: str  # "docling" | "docling_body" | "textlayer"
    position: str  # "header" | "footer"
    raw: str
    ref: str | None = None

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.num.scheme, self.num.prefix, self.num.value - self.page)

    @property
    def source(self) -> str:
        return "textlayer" if self.channel == "textlayer" else self.position


def find_numbers(text: str) -> list[tuple[PageNum, str]]:
    """Page-number readings of one header/footer string, most specific first."""
    text = text.strip()
    if not text:
        return []
    whole = parse(text)
    if whole:
        return [(whole, "whole")]
    out: list[tuple[PageNum, str]] = []
    segs = [s for s in _SEGMENTS.split(text) if s]
    if len(segs) > 1:
        out += [(n, "segment") for n in map(parse, dict.fromkeys((segs[0], segs[-1]))) if n]
    toks = text.split()
    if not out and len(toks) > 1:
        out += [(n, "token") for n in map(parse, dict.fromkeys((toks[0], toks[-1]))) if n]
    return out


def _cands(page: int, text: str, channel: str, position: str, ref: str | None,
           kinds: tuple[str, ...] = ("whole", "segment", "token")) -> list[Cand]:
    return [Cand(page, n, WEIGHTS[(channel, how)], channel, position, text, ref)
            for n, how in find_numbers(text) if how in kinds and (channel, how) in WEIGHTS]


def docling_candidates(doc: Doc) -> list[Cand]:
    out: list[Cand] = []
    heights = {int(k): v["size"]["height"] for k, v in doc.raw["pages"].items()}
    for b in doc.blocks:
        if b.kind != "text" or b.page is None or b.bbox is None:
            continue
        h = heights[b.page]
        top, bottom = b.bbox[1], b.bbox[3]
        position = "header" if top > h * (1 - EDGE_BAND) or b.label == "page_header" else "footer"
        if b.layer == "furniture":
            out += _cands(b.page, b.text, "docling", position, b.ref)
        elif len(b.text) <= BODY_MAX_CHARS and (top > h * (1 - EDGE_BAND) or bottom < h * EDGE_BAND):
            out += _cands(b.page, b.text, "docling_body", position, b.ref, ("whole",))
    return out


def textlayer_candidates(pdf_path: str) -> list[Cand]:
    out: list[Cand] = []
    pdf = pdfium.PdfDocument(pdf_path)
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            w, h = page.get_size()
            tp = page.get_textpage()
            bands = (("footer", tp.get_text_bounded(0, 0, w, h * EDGE_BAND)),
                     ("header", tp.get_text_bounded(0, h * (1 - EDGE_BAND), w, h)))
            for position, text in bands:
                for line in text.splitlines():
                    out += _cands(i + 1, line, "textlayer", position, None)
    finally:
        pdf.close()
    return out
