"""Tables Docling did not recognise: a block of figures left as one-line text blocks, one per cell, outside
any table (hkex 2134 p462, the 13 August 2025 maturity table; its row labels sit in their own blocks at
the left). Found and rebuilt from the blocks' own boxes: values in columns (shared right edge) and rows
(shared height), labels to the left, column headings above.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tglw.common.bundle import Block, Doc

from .grid import value_kind

ROW_TOL = 3.0  # points: blocks on one printed row
COL_TOL = 4.0  # points: right edges of one column of figures
HEAD_SPAN = 60.0  # points: how far above the first row column headings may sit
MIN_ROWS = 3
MIN_COLS = 2
LABEL_SHARE = 0.5


@dataclass
class Loose:
    page: int
    values: list[Block]
    columns: list[float] = field(default_factory=list)  # right edges
    rows: list[float] = field(default_factory=list)  # y centres, top first
    cells: dict[tuple[int, int], Block] = field(default_factory=dict)
    labels: dict[int, list[Block]] = field(default_factory=dict)
    heads: dict[int, list[Block]] = field(default_factory=dict)
    title: list[Block] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _cy(b: Block) -> float:
    return (b.bbox[1] + b.bbox[3]) / 2


def _clusters(xs: list[float], tol: float) -> list[float]:
    out: list[list[float]] = []
    for x in sorted(xs):
        if out and x - out[-1][-1] <= tol:
            out[-1].append(x)
        else:
            out.append([x])
    return [sum(c) / len(c) for c in out]


def _inside(b: Block, box: tuple[float, float, float, float]) -> bool:
    cx, cy = (b.bbox[0] + b.bbox[2]) / 2, _cy(b)
    return box[0] - 2 <= cx <= box[2] + 2 and box[3] - 2 <= cy <= box[1] + 2


def page_candidates(doc: Doc) -> dict[int, list[Block]]:
    """Per page, one-line body text blocks outside tables and pictures."""
    boxes: dict[int, list[tuple[float, float, float, float]]] = {}
    for b in doc.blocks:
        if b.kind in ("table", "picture") and b.bbox and b.page:
            boxes.setdefault(b.page, []).append(b.bbox)
    out: dict[int, list[Block]] = {}
    for b in doc.blocks:
        if b.kind == "text" and b.layer == "body" and not b.in_picture and b.bbox and len(b.pages) == 1 \
                and b.text.strip() and not any(_inside(b, x) for x in boxes.get(b.page, [])):
            out.setdefault(b.page, []).append(b)
    return out


def find(doc: Doc, skip: set[int] = frozenset()) -> list[Loose]:
    """Grids of loose figures (each column with a number in it, not only dashes), most rows with a label to
    their left: two or more columns over three or more printed rows, three or more columns over two rows,
    or one column over five rows when nearly every row has a label and the column a heading. Pages in
    `skip` (scans: their figures are OCR of a picture) are left alone."""
    out = []
    for page, blocks in sorted(page_candidates(doc).items()):
        if page in skip:
            continue
        values = [b for b in blocks if value_kind(b.text) in ("number", "mark") and len(b.text) <= 24
                  and b.text.strip() not in _LEADERS]
        cols = [c for c in _clusters([b.bbox[2] for b in values], COL_TOL)
                if sum(abs(b.bbox[2] - c) <= COL_TOL for b in values) >= 2
                and any(abs(b.bbox[2] - c) <= COL_TOL and value_kind(b.text) == "number" for b in values)]
        if not cols:
            continue
        in_cols = [b for b in values if any(abs(b.bbox[2] - c) <= COL_TOL for c in cols)]
        rows = sorted(_clusters([_cy(b) for b in in_cols], ROW_TOL), reverse=True)
        lo = Loose(page, in_cols, cols, rows)
        for b in in_cols:
            key = (min(range(len(rows)), key=lambda i: abs(rows[i] - _cy(b))),
                   min(range(len(cols)), key=lambda j: abs(cols[j] - b.bbox[2])))
            if key in lo.cells:
                lo.problems.append(f"two figures in one cell: {lo.cells[key].text!r}, {b.text!r}")
            lo.cells[key] = b
        filled = sum(sum((i, j) in lo.cells for j in range(len(cols))) >= min(2, len(cols)) for i in range(len(rows)))
        single = len(cols) == 1
        if not (len(cols) >= 2 and filled >= 3 or len(cols) >= 3 and filled >= 2 or single and filled >= 5):
            continue
        _labels(lo, blocks)
        if sum(i in lo.labels for i in range(len(rows))) < (0.8 if single else LABEL_SHARE) * len(rows):
            continue
        _heads(lo, blocks)
        if single and not lo.heads:
            continue
        _check(lo, values)
        out.append(lo)
    return out


_LEADERS = frozenset({".", "…", "·", "..", "..."})


def _check(lo: Loose, values: list[Block]) -> None:
    """Restored only when every figure between the first and last row is in a cell, every printed row of
    figures has a label and every column a heading."""
    left, right = min(b.bbox[0] for b in lo.values), max(lo.columns) + COL_TOL
    for b in values:
        if b not in lo.values and lo.rows[-1] - ROW_TOL <= _cy(b) <= lo.rows[0] + ROW_TOL and left <= b.bbox[2] <= right:
            lo.problems.append(f"figure outside the columns: {b.text!r}")
    for i in range(len(lo.rows)):
        if i not in lo.labels and any((i, j) in lo.cells for j in range(len(lo.columns))):
            lo.problems.append(f"figures without a row label at y {lo.rows[i]:.0f}")
    for j in range(len(lo.columns)):
        if j not in lo.heads:
            lo.problems.append(f"column {j + 1} without a heading")


def _left_edge(lo: Loose) -> float:
    return min(b.bbox[0] for b in lo.values)


def _labels(lo: Loose, blocks: list[Block]) -> None:
    """Text left of the figures, each line to the row at or below it (a label wraps onto the value's line)."""
    left, top, bottom = _left_edge(lo), lo.rows[0] + ROW_TOL + 14, lo.rows[-1] - ROW_TOL
    for b in blocks:
        if b in lo.values or b.bbox[2] > left - 2 or not bottom <= _cy(b) <= top:
            continue
        below = [i for i, y in enumerate(lo.rows) if y <= _cy(b) + ROW_TOL]
        if below:
            lo.labels.setdefault(below[0], []).append(b)


def _heads(lo: Loose, blocks: list[Block]) -> None:
    """Text above the first row over a column: its right edge near the column's, or its centre over the
    column's figures (headings centred over right-aligned numbers)."""
    first = lo.rows[0]
    lefts = {j: min(b.bbox[0] for (i, k), b in lo.cells.items() if k == j) for j in range(len(lo.columns))}
    used = {b.ref for bs in lo.labels.values() for b in bs}
    left_side = []
    for b in blocks:
        if b in lo.values or b.ref in used or not first + ROW_TOL < _cy(b) <= first + HEAD_SPAN:
            continue
        cx = (b.bbox[0] + b.bbox[2]) / 2
        j = next((j for j, c in enumerate(lo.columns) if abs(b.bbox[2] - c) <= COL_TOL
                  or b.bbox[0] - 2 <= c <= b.bbox[2] + 2 or lefts[j] - 20 <= cx <= c + 5), None)
        if j is not None:
            lo.heads.setdefault(j, []).append(b)
        elif b.bbox[2] < min(lefts.values()) - 2:
            left_side.append(b)
    heads = [b for bs in lo.heads.values() for b in bs]
    if heads:  # "As at 31 December 2024" over the row labels, above the column headings
        lo.title = [b for b in left_side if b.bbox[3] >= max(h.bbox[1] for h in heads) - 1]


def grid(lo: Loose) -> list[list[str]]:
    """Header row, then one row per printed line of figures: label, figures by column."""
    head = [""] + [" ".join(b.text.strip() for b in sorted(lo.heads.get(j, []), key=lambda b: -b.bbox[1]))
                   for j in range(len(lo.columns))]
    out = [head]
    for i in range(len(lo.rows)):
        label = " ".join(b.text.strip() for b in sorted(lo.labels.get(i, []), key=lambda b: -b.bbox[1]))
        out.append([label] + [lo.cells[(i, j)].text.strip() if (i, j) in lo.cells else "" for j in range(len(lo.columns))])
    return out


def entry(lo: Loose) -> dict[str, Any]:
    refs = sorted({b.ref for b in lo.values} | {b.ref for bs in lo.labels.values() for b in bs}
                  | {b.ref for bs in lo.heads.values() for b in bs}, key=lambda r: int(r.rsplit("/", 1)[1]))
    boxes = [b.bbox for b in lo.values] + [b.bbox for bs in lo.labels.values() for b in bs] \
        + [b.bbox for bs in lo.heads.values() for b in bs]
    return {"page": lo.page, "bbox": [round(min(x[0] for x in boxes), 1), round(max(x[1] for x in boxes), 1),
                                      round(max(x[2] for x in boxes), 1), round(min(x[3] for x in boxes), 1)],
            "refs": refs, "restored": not lo.problems,
            "title": " ".join(b.text.strip() for b in sorted(lo.title, key=lambda b: -b.bbox[1])),
            "grid": grid(lo), "problems": lo.problems}


def render(e: dict[str, Any]) -> str:
    """A loose-table entry for a reader: the rebuilt rows when they checked out, else only the warning."""
    title = f" {e['title']!r}" if e.get("title") else ""
    head = f"[table{title} rebuilt from loose figures on p{e['page']} (Docling did not recognise it); check the page image"
    if not e["restored"]:
        return f"[p{e['page']}: figures here form a table Docling did not recognise; read them on the page image]"
    rows = e["grid"]
    lines = [head + "]", "cols: " + " | ".join(c or "?" for c in rows[0][1:])]
    lines += [" | ".join(r).strip() for r in rows[1:]]
    return "\n".join(lines)
