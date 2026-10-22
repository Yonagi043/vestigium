"""E5: figures Docling did not cut out. A figure caption ("Figure N: ...") with no picture next to it,
next to a cluster of vector paths or images that no picture or table covers, gets the box of that
drawing, so read can point to region pN:l,t,r,b.

Path clusters outside pictures and tables are mostly table rules, text frames and invisible paths, not
lost figures. Starting from the caption keeps the lost figures and drops those.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as raw

from tglw.common import registry

from . import switches

CAPTION = re.compile(r"^\s*(?:Figure|FIGURE|Fig\.?|FIG\.?|Chart|CHART|Exhibit|EXHIBIT|Graph)\s+"
                     r"(?:[A-Z][.\-]?)?\d+(?:[.\-]\d+)*[a-z]?(?:\s*[:.|]|\s*$)")
NEAR_PICTURE = 90.0  # pt: a caption this close to a picture (overlapping it horizontally) is that picture's
GAP = 48.0  # pt between a caption and its drawing
JOIN = 12.0  # pt between paths of one drawing
MIN_PATHS = 8
MIN_SHARE = 0.01  # of the page area
TEXT_SHARE = 0.4  # a cluster this much covered by text blocks is a text frame (boxed example, form), not a drawing
LABEL_CHARS = 40  # short text blocks inside or at the edge of a drawing are its labels


@dataclass(frozen=True)
class Drawing:
    page: int
    box: tuple[float, float, float, float]  # l, t, r, b; Docling's frame (bottom-left, crop box origin)
    caption: str  # ref of the caption block
    paths: int

    @property
    def target(self) -> str:
        l, t, r, b = self.box
        return f"p{self.page}:{l:.0f},{t:.0f},{r:.0f},{b:.0f}"


def _overlap_x(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    return a[0] < b[2] and b[0] < a[2]


def _clusters(boxes: list[tuple[float, float, float, float]]) -> list[tuple[list[float], int]]:
    """Boxes (l, t, r, b) merged while within JOIN of each other: [union box, count]."""
    groups: list[tuple[list[float], int]] = []
    for bx in sorted(boxes):
        hit = [g for g in groups if bx[0] <= g[0][2] + JOIN and bx[2] >= g[0][0] - JOIN
               and bx[3] <= g[0][1] + JOIN and bx[1] >= g[0][3] - JOIN]
        u, n = list(bx), 1
        for g in hit:
            groups.remove(g)
            u = [min(u[0], g[0][0]), max(u[1], g[0][1]), max(u[2], g[0][2]), min(u[3], g[0][3])]
            n += g[1]
        groups.append((u, n))
    return groups


def _page_objects(pg: Any, covered: list[tuple[float, ...]]) -> list[tuple[float, float, float, float]]:
    """Path and image boxes (l, t, r, b, Docling's frame) on a page, outside pictures and tables, clipped to the page."""
    w, h = pg.get_size()
    x0, y0 = pg.get_cropbox()[:2]
    out = []
    for obj in pg.get_objects(max_depth=3):
        if obj.type not in (raw.FPDF_PAGEOBJ_PATH, raw.FPDF_PAGEOBJ_IMAGE):
            continue
        l, b, r, t = obj.get_bounds()
        l, b, r, t = max(0.0, l - x0), max(0.0, b - y0), min(w, r - x0), min(h, t - y0)
        if r < l or t < b or (r - l <= 0 and t - b <= 0):
            continue  # off the page, or a point
        if (r - l) > 0.9 * w and (t - b) > 0.9 * h:
            continue  # page background or frame
        cx, cy = (l + r) / 2, (b + t) / 2
        if any(c[0] - 6 <= cx <= c[2] + 6 and c[3] - 6 <= cy <= c[1] + 6 for c in covered):
            continue
        out.append((l, t, r, b))
    return out


def _text_share(box: list[float], blocks: list[Any]) -> float:
    l0, t0, r0, b0 = box
    area = max(1.0, (r0 - l0) * (t0 - b0))
    got = 0.0
    for b in blocks:
        l, t, r, bot = b.bbox
        got += max(0.0, min(r, r0) - max(l, l0)) * max(0.0, min(t, t0) - max(bot, b0))
    return got / area


def _find(v: Any, pg: Any, page: int, cap: Any) -> Drawing | None:
    blocks = [b for b in v.by_page.get(page, []) if b.bbox is not None and b.page == page]
    pics = [b.bbox for b in blocks if b.kind in ("picture", "table")]
    c = cap.bbox
    for p in pics:
        if _overlap_x(p, c) and max(p[3] - c[1], c[3] - p[1], 0.0) <= NEAR_PICTURE:
            return None  # an unlinked caption of a picture Docling has
    w, h = pg.get_size()
    best = None
    for box, n in _clusters(_page_objects(pg, pics)):
        if not _overlap_x(box, c):
            continue
        above = box[3] >= c[1] - JOIN and box[3] - c[1] <= GAP
        below = box[1] <= c[3] + JOIN and c[3] - box[1] <= GAP
        if not (above or below) or (box[0] <= c[0] and box[2] >= c[2] and box[1] >= c[1] and box[3] <= c[3]):
            continue
        if best is None or n > best[1]:
            best = (box, n)
    if best is None:
        return None
    box, n = best
    text = [b for b in blocks if b.kind == "text" and b.ref != cap.ref]
    if n < MIN_PATHS or (box[2] - box[0]) * (box[1] - box[3]) < MIN_SHARE * w * h or _text_share(box, text) > TEXT_SHARE:
        return None
    l, t, r, bot = box
    for b in text:  # labels inside or at the edge of the drawing
        bl, bt, br, bb = b.bbox
        if len(b.text.strip()) <= LABEL_CHARS and bl >= l - JOIN and br <= r + JOIN and bb >= bot - JOIN and bt <= t + JOIN:
            l, t, r, bot = min(l, bl), max(t, bt), max(r, br), min(bot, bb)
    return Drawing(page, (round(l, 1), round(t, 1), round(r, 1), round(bot, 1)), cap.ref, n)


def caption_blocks(v: Any) -> list[Any]:
    """Visible figure captions that no picture links to."""
    linked = {x["$ref"] for b in v.doc.blocks if b.kind == "picture"
              for k in ("captions", "footnotes") for x in v.doc.item(b.ref).get(k, [])}
    return [b for b in v.doc.blocks if b.kind == "text" and not b.in_picture and b.ref not in linked
            and b.bbox is not None and b.page is not None and CAPTION.match(b.text) and v.visible(b)
            and (b.label == "caption" or re.match(r"^\s*\S+\s+\S+?\s*[:.|]", b.text))]


@lru_cache(maxsize=64)
def drawings(name: str) -> tuple[Drawing, ...]:
    from tglw.views import view

    v = view(name)
    caps = caption_blocks(v)
    if not caps:
        return ()
    pdf = pdfium.PdfDocument(str(registry.get(name).pdf_path))
    out = []
    try:
        for cap in caps:
            if (d := _find(v, pdf[cap.page - 1], cap.page, cap)) is not None:
                out.append(d)
    finally:
        pdf.close()
    return tuple(out)


def by_caption(name: str) -> dict[str, Drawing]:
    return {d.caption: d for d in drawings(name)}


def hint(name: str, caption_ref: str) -> str | None:
    """The read line after a caption whose figure has no fig address, under DRAWINGS."""
    if not switches.on("DRAWINGS"):
        return None
    d = by_caption(name).get(caption_ref)
    return f"[figure without a fig address; image: region {d.target}]" if d else None
