"""E2: the figure line in read. Under FIG_HINT a figure ends with where its image is ("(image: region
fig3)"). Under FIG_LABELS (off by default) "[fig3: caption · label …]" keeps its caption and first labels
and counts the rest ("+40 labels; read fig3 for all"); read of the figure itself shows every label. It is
off because chart labels can be the evidence: a chart's labels can hold the very values a question
asks for, and cutting them takes those values out of read. Under E4's OCR_NOTE, labels Docling read
from a raster image are marked as OCR.

Inner labels can make a figure line over a hundred tokens (research papers) and up to about two
thousand, with no pointer to the image. E0: the reader found the figure every time, so the gain
expected here is fewer read tokens.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import pypdfium2 as pdfium

from tglw.common import registry
from tglw.common.text import count_tokens

from . import switches
from .ocr import scanned

LABELS_KEEP = 8  # labels shown after the caption (those with a digit or three letters)
LABELS_TOKENS = 40  # and at most this many tokens of them
FIGURE_SHARE = 0.02  # a picture without caption or labels below this share of the page is an icon or logo
OCR_LAYER_SHARE = 1 / 3  # labels count as OCR when the text layer in the picture has fewer characters than this share of them


def _telling(label: str) -> bool:
    """A label worth showing: it has a digit or three letters (OCR debris like "e::" or "~" has neither)."""
    return any(ch.isdigit() for ch in label) or sum(ch.isalpha() for ch in label) >= 3


def short(ref: str) -> str:
    from tglw.views.docview import short_ref

    return short_ref(ref)


def _inner(v: Any, b: Any) -> list[tuple[Any, str]]:
    """(block, text) of the picture's inner text, as views.render.render_picture shows it."""
    from tglw.views.render import _cell

    return [(c, _cell(s.text)) for c in v.children.get(b.ref, [])
            for s in v.block_segments(c) if s.text and not v.hidden(s.page, c.ref)]


def _captions(v: Any, b: Any) -> set[str]:
    return {x["$ref"] for x in v.doc.item(b.ref).get("captions", [])}


def is_figure(v: Any, b: Any) -> bool:
    """A picture worth an image: it has inner text or a caption, or covers FIGURE_SHARE of its page."""
    if v.children.get(b.ref) or _captions(v, b):
        return True
    if b.bbox is None or b.page is None:
        return False
    size = v.doc.raw["pages"][str(b.page)]["size"]
    l, t, r, bot = b.bbox
    return (r - l) * (t - bot) >= FIGURE_SHARE * size["width"] * size["height"]


@lru_cache(maxsize=4096)
def labels_ocr(name: str, ref: str) -> bool:
    """Whether Docling read a picture's inner text from the image: the picture is on a scanned page, or
    the text layer inside it has too few characters for its labels."""
    from tglw.mcp_server.tools import page_space
    from tglw.views import view

    v = view(name)
    b = v.doc.by_ref[ref]
    kids = v.children.get(ref)
    if not kids or b.bbox is None or b.page is None:
        return False
    if scanned(name, b.page):
        return True
    want = sum(ch.isalnum() for c in kids for ch in c.text)
    if not want:
        return False
    pdf = pdfium.PdfDocument(str(registry.get(name).pdf_path))
    try:
        pg = pdf[b.page - 1]
        tp = pg.get_textpage()
        try:
            left, bottom, right, top = page_space(pg, b.bbox)
            got = sum(ch.isalnum() for ch in tp.get_text_bounded(left=left, bottom=bottom, right=right, top=top))
        finally:
            tp.close()
    finally:
        pdf.close()
    return got < OCR_LAYER_SHARE * want


def applied(line: str, fig: str) -> set[str]:
    """The switches that changed a figure line picture_line returned (for the trace)."""
    return ({"FIG_HINT"} if line.endswith(f" (image: region {fig})") else set()) \
        | ({"FIG_LABELS"} if f" labels; read {fig} for all]" in line else set()) \
        | ({"OCR_NOTE"} if line.startswith(f"[{fig}, labels by OCR") else set())


def picture_line(v: Any, b: Any, full: bool = False) -> str:
    """The read line for a picture. With FIG_HINT, FIG_LABELS and OCR_NOTE all off it is exactly
    views.render.render_picture's line; full keeps every label (the figure itself was asked for)."""
    from tglw.views.render import render_picture

    hint, cut, ocr = switches.on("FIG_HINT"), switches.on("FIG_LABELS") and not full, switches.on("OCR_NOTE")
    ocr = ocr and labels_ocr(v.name, b.ref)
    if not (hint or cut or ocr):
        return render_picture(v, b)
    fig = short(b.ref)
    inner = _inner(v, b)
    parts = [t for _, t in inner]
    if cut:
        caps = _captions(v, b)
        head = [t for c, t in inner if c.ref in caps or c.label == "caption"]
        labels = [t for c, t in inner if not (c.ref in caps or c.label == "caption")]
        kept, used = [], 0
        for t in (t for t in labels if _telling(t)):
            used += count_tokens(t)
            if len(kept) >= LABELS_KEEP or (kept and used > LABELS_TOKENS):
                break
            kept.append(t)
        parts = head + kept
        if len(labels) > len(kept):
            parts.append(f"+{len(labels) - len(kept)} labels; read {fig} for all")
    tag = f"{fig}, labels by OCR" if ocr and inner else fig
    line = f"[{tag}: " + " · ".join(parts) + "]" if parts else f"[{tag}]"
    if hint and is_figure(v, b):
        line += f" (image: region {fig})"
    return line
