"""E3: region's default scale from the smallest text in the crop, a wider pixel cap for wide crops,
and the crop's page box in the caption so the reader can zoom into part of it without guessing.

VIS E0 (vision pilot logs): at the fixed 1.5x, three vision-pilot figures were too small to read
(fine curves, thin lines, a sideways scanned plan); haiku then guessed page coordinates 20 times.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as raw

from tglw.common import registry

from . import switches
from .ocr import scanned

Box = tuple[float, float, float, float]  # l, t, r, b in PDF points, origin bottom-left (Docling's frame)

BASE_SCALE = 1.5
MAX_SCALE = 3.0
TARGET_EM_PX = 16.0  # the smallest text (10th percentile em height) is drawn at least this tall
MIN_LONG_PX = 800  # small figures are enlarged until their long side has this many pixels
OCR_EM = 0.75  # an OCR word box is about this share of the em height (tight box, caps and digits)
WIDE_MAX_SIDE = 1568  # the Claude API keeps up to 1568 px on the long side within about 1.15 MP
NARROW_MAX_SIDE = 1200  # tools.MAX_SIDE


def _quantile(xs: list[float], q: float) -> float | None:
    xs = sorted(x for x in xs if x > 0.5)
    return xs[min(len(xs) - 1, int(len(xs) * q))] if xs else None


def layer_em_heights(pg: Any, page_box: tuple[float, float, float, float]) -> list[float]:
    """Em heights (pt) of the text-layer letters and digits inside a box given in PDF page space
    (left, bottom, right, top): the loose glyph box measured across the glyph's own baseline, so
    sideways labels count by their height, not their advance."""
    left, bottom, right, top = page_box
    tp = pg.get_textpage()
    out = []
    try:
        for i in range(tp.count_chars()):
            u = raw.FPDFText_GetUnicode(tp.raw, i)
            if u in (0, 32, 10, 13, 0xFFFE) or not chr(u).isalnum():
                continue
            l, b, r, t = tp.get_charbox(i)
            if not (left <= (l + r) / 2 <= right and bottom <= (b + t) / 2 <= top):
                continue
            ll, lb, lr, lt = tp.get_charbox(i, loose=True)
            a = math.degrees(raw.FPDFText_GetCharAngle(tp.raw, i)) % 180
            out.append(lt - lb if a < 45 or a > 135 else lr - ll)
    finally:
        tp.close()
    return out


def ocr_em_heights(blocks: list[Any], box: Box) -> list[float]:
    """Em heights (pt) estimated from Docling's text blocks inside the box: the short side of each
    word-sized block (3-24 characters, one line), divided by OCR_EM."""
    l0, t0, r0, b0 = box
    out = []
    for b in blocks:
        if b.bbox is None or not 3 <= len(b.text.strip()) <= 24:
            continue
        l, t, r, bot = b.bbox
        if l0 <= (l + r) / 2 <= r0 and b0 <= (t + bot) / 2 <= t0:
            out.append(min(r - l, t - bot) / OCR_EM)
    return out


def auto_scale(em_pt: float | None, box: Box) -> float:
    """The scale at which the 10th-percentile text reaches TARGET_EM_PX and the crop's long side
    MIN_LONG_PX, within BASE_SCALE..MAX_SCALE."""
    l, t, r, b = box
    want = MIN_LONG_PX / max(1.0, r - l, t - b)
    if em_pt:
        want = max(want, TARGET_EM_PX / em_pt)
    return round(min(MAX_SCALE, max(BASE_SCALE, want)), 2)


@lru_cache(maxsize=4096)
def em_of(name: str, page: int, box: Box) -> float | None:
    """10th-percentile em height (pt) of the text in a crop: the text layer, or on scanned pages
    (whose layer is invisible OCR) and boxes without a layer, Docling's OCR words."""
    from tglw.mcp_server.tools import page_space  # the crop box in the text layer's frame
    from tglw.views import view

    if not scanned(name, page):
        pdf = pdfium.PdfDocument(str(registry.get(name).pdf_path))
        try:
            pg = pdf[page - 1]
            em = _quantile(layer_em_heights(pg, page_space(pg, box)), 0.1)
        finally:
            pdf.close()
        if em:
            return em
    v = view(name)
    return _quantile(ocr_em_heights([b for b in v.by_page.get(page, []) if b.page == page and b.kind == "text"], box), 0.1)


def default_scale(name: str, page: int, box: Box | None, base: float = BASE_SCALE) -> float:
    """region's scale when the caller gives none: auto_scale under ADAPTIVE_ZOOM for block and box
    crops, else base (whole pages always base)."""
    if not switches.on("ADAPTIVE_ZOOM") or box is None:
        return base
    return auto_scale(em_of(name, page, box), box)


def max_side(base: int = NARROW_MAX_SIDE) -> int:
    """Pixel cap on the long side: WIDE_MAX_SIDE under ADAPTIVE_ZOOM (the area cap still holds), else base."""
    return WIDE_MAX_SIDE if switches.on("ADAPTIVE_ZOOM") else base


def crop_note(page: int, box: Box | None) -> str:
    """Caption addition under CROP_BOX: the crop's page box, and how to zoom into part of it."""
    if not switches.on("CROP_BOX") or box is None:
        return ""
    l, t, r, b = box
    return f" · box p{page}:{l:.0f},{t:.0f},{r:.0f},{b:.0f} (pt, origin bottom-left; a part of it: region p{page}:l,t,r,b, scale up to 3)"
