"""Per-table defect checks: column shifts (geometry), split tables (neighbours), garbled grids (content)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from tglw.common.bundle import Doc
from tglw.common.text import norm

from .grid import Cell, Table
from .layout import Layout

TOL = 2.0
EDGE_TOL = 40.0
SAME_PAGE_GAP = 30.0
LOOKBACK = 30
_CONTINUED = ("continued", "(cont'd)", "(cont.)", "續")
_PASS_LABELS = frozenset({"page_header", "page_footer", "footnote", "caption"})


@dataclass(frozen=True, slots=True)
class Shift:
    cell: Cell
    to_col: int
    own: tuple[float, float]
    target: tuple[float, float]


def _band(cells: list[Cell]) -> tuple[float, float] | None:
    boxes = [c.bbox for c in cells if c.bbox is not None]
    if not boxes:
        return None
    return min(b[0] for b in boxes), max(b[2] for b in boxes)


def _column_cells(t: Table) -> dict[int, list[Cell]]:
    cols: dict[int, list[Cell]] = {}
    for c in t.cells:
        if c.text and c.cspan == 1 and c.bbox is not None:
            cols.setdefault(c.col, []).append(c)
    return cols


def column_shifts(t: Table, lay: Layout, empty_target: bool = True) -> list[Shift]:
    """Cells lying wholly outside their column's x-band and inside another column's band. With `empty_target`
    that column's slot in the row must be empty (a value in the wrong column); without it, the slot may hold text
    (typically a value Docling split in two, one half left in the neighbouring column)."""
    cols = _column_cells(t)
    bands = {k: _band(v) for k, v in cols.items()}
    hdr = set(lay.header_rows)
    out = []
    for c in t.cells:
        if c.row in hdr or not c.text or c.cspan != 1 or c.bbox is None:
            continue
        own = _band([x for x in cols.get(c.col, []) if x is not c])
        if own is None or c.bbox[2] >= own[0] - TOL and c.bbox[0] <= own[1] + TOL:
            continue
        for k, band in bands.items():
            if k == c.col or band is None or bool(t.slot_text(c.row, k)) == empty_target:
                continue
            if band[0] - TOL <= c.cx <= band[1] + TOL:
                out.append(Shift(c, k, own, band))
                break
    return out


def _inside(block, t: Table) -> bool:
    """Block lies within the table's own area (text Docling leaked out of the grid)."""
    b, tb = block.bbox, t.raw_bbox
    if b is None or tb is None or block.page not in t.pages:
        return False
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    lo, hi = sorted((tb["t"], tb["b"]))
    return tb["l"] <= cx <= tb["r"] and lo <= cy <= hi


def _between(doc: Doc, a: Table, b: Table) -> list:
    return [x for x in doc.blocks[a.order + 1:b.order]
            if x.layer == "body" and x.label not in _PASS_LABELS and x.kind != "table"
            and (x.text or x.kind == "picture") and not _inside(x, a) and not _inside(x, b)]


def _is_repeat(doc: Doc, a: Table, text: str) -> bool:
    n = norm(text)
    if any(w in n for w in _CONTINUED):
        return True
    recent = doc.blocks[max(0, a.order - LOOKBACK):a.order]
    return any(norm(x.text) == n for x in recent if x.text)


def continuation(doc: Doc, a: Table, b: Table, b_has_header: bool) -> str | None:
    """Evidence that b continues a (next page or directly below on the same page), else None."""
    if not a.pages or not b.pages or b.order <= a.order:
        return None
    others = [x for x in _between(doc, a, b) if not _is_repeat(doc, a, x.text)]
    if others:
        return None
    if any(x.kind == "table" for x in doc.blocks[a.order + 1:b.order]):
        return None
    if a.ncols != b.ncols and (b_has_header or abs(a.ncols - b.ncols) > 1):
        return None
    if a.bbox and b.bbox and (abs(a.bbox[0] - b.bbox[0]) > EDGE_TOL or abs(a.bbox[2] - b.bbox[2]) > EDGE_TOL):
        return None
    cols = f"{a.ncols} columns" if a.ncols == b.ncols else f"{a.ncols} vs {b.ncols} columns"
    if b.page == a.pages[-1] + 1:
        return f"continues {a.ref} from p{a.pages[-1]} to p{b.page}; {cols}; only page furniture or repeated titles between"
    if b.page == a.pages[-1] and not b_has_header and a.bbox and b.bbox and 0 <= b.bbox[1] - a.bbox[3] <= SAME_PAGE_GAP:
        return f"continues {a.ref} directly below on p{b.page}; {cols}; no header of its own"
    return None


_WORD = re.compile(r"^[a-z][a-z'’\-]+[,.;:]?$")
_STOP = frozenset("a an and as at by for from in into of on or the to with".split())
_NUMTOK = re.compile(r"^[(\[]?[-–—]?(?:RMB|HK\$|US\$|\$)?[\d,]*\d(?:\.\d+)?%?[)\]]?$|^[-–—]$")
MIN_GARBLED_CELLS = 5
FRAG_SHARE = 0.25
PACKED_SHARE = 0.10


def packed(text: str) -> bool:
    toks = text.split()
    if len(toks) < 2 or not all(_NUMTOK.match(x) for x in toks):
        return False
    return not (len(toks) == 3 and toks[1] in "-–—")


def garbled(t: Table) -> str | None:
    """Evidence that Docling broke the grid: words scattered one per cell, or several numbers packed per cell."""
    cells = [c for c in t.cells if c.text]
    if len(cells) < 2 * MIN_GARBLED_CELLS:
        return None
    words = [c.text for c in cells if _WORD.match(c.text)]
    stops = [w for w in words if w.strip(",.;:") in _STOP]
    if len(words) >= FRAG_SHARE * len(cells) and len(stops) >= MIN_GARBLED_CELLS:
        return (f"text split into single words: {len(words)} of {len(cells)} cells are one lowercase word, "
                f"{len(stops)} are stopwords, e.g. {words[:6]}")
    many = [c.text for c in cells if packed(c.text)]
    if len(many) >= MIN_GARBLED_CELLS and len(many) >= PACKED_SHARE * len(cells):
        return f"{len(many)} of {len(cells)} cells hold several numbers, e.g. {many[:3]}"
    return None


MISALIGN_SHARE = 0.5
MIN_MISALIGNED = 2


def _header_bands(t: Table, lay: Layout) -> dict[int, tuple[float, float]]:
    """x-extent of the single-column header text above each column, from the first header block."""
    rows = set(lay.blocks[0]) if lay.blocks else set()
    out: dict[int, tuple[float, float]] = {}
    for x in t.cells:
        if x.row in rows and x.cspan == 1 and x.text and x.bbox is not None:
            l, r = out.get(x.col, (x.bbox[0], x.bbox[2]))
            out[x.col] = (min(l, x.bbox[0]), max(r, x.bbox[2]))
    return out


def misaligned_columns(t: Table, lay: Layout, donor: tuple[Table, Layout] | None = None) -> dict[int, int]:
    """{column: header column its values are printed under} for columns whose data, as a whole, sits under another
    column's header text (a header offset, or a column Docling dropped so the data slid over). A continuation part
    without its own header is checked against its first part's header (`donor`)."""
    bands = _header_bands(t, lay) if lay.header_rows else _header_bands(*donor) if donor else {}
    hdr = set(lay.header_rows)
    under: dict[int, list[int | None]] = {}
    for c in t.cells:
        if c.row in hdr or not c.text or c.cspan != 1 or c.bbox is None or c.col not in bands:
            continue
        own = bands[c.col]
        if c.bbox[2] >= own[0] - TOL and c.bbox[0] <= own[1] + TOL:
            under.setdefault(c.col, []).append(None)
            continue
        hit = [k for k, b in bands.items() if k != c.col and b[0] - TOL <= c.cx <= b[1] + TOL]
        under.setdefault(c.col, []).append(hit[0] if len(hit) == 1 else None)
    out = {}
    for col, targets in under.items():
        moved = [k for k in targets if k is not None]
        if len(moved) >= MIN_MISALIGNED and len(moved) >= MISALIGN_SHARE * len(targets):
            out[col] = max(set(moved), key=moved.count)
    return out
