"""Header rows, key (row-label) columns, and column paths of one table."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .grid import Cell, Table

MAX_INFERRED_HEADER_ROWS = 3
MIN_REPEAT_CELLS = 2
BLEED_SHARE = 0.5
UNITS_HEADERS = frozenset({"unit", "units", "unit of measure"})
_TRAILING_AMOUNT = re.compile(r"\s+(?:\$|RMB|HK\$|US\$)?\s*\(?\d{1,3}(?:,\d{3})+(?:\.\d+)?\)?\s*$")


def header_text(text: str) -> str:
    """Header text without a data amount Docling glued onto it ('September 28, 2024 $ 30,737')."""
    return _TRAILING_AMOUNT.sub("", text)


@dataclass
class Layout:
    header_rows: list[int]
    header_source: str  # docling | inferred | none
    misflagged_rows: list[int] = field(default_factory=list)
    key_cols: list[int] = field(default_factory=list)
    valued_headers: list[Cell] = field(default_factory=list)
    repeated_rows: list[int] = field(default_factory=list)
    bleeding_cols: set[int] = field(default_factory=set)
    partly_flagged: list[int] = field(default_factory=list)

    @property
    def blocks(self) -> list[list[int]]:
        """Header blocks that name columns; a repeated mid-table header only separates, it does not rename."""
        out: list[list[int]] = []
        for r in (r for r in self.header_rows if r not in self.repeated_rows):
            if out and out[-1][-1] == r - 1:
                out[-1].append(r)
            else:
                out.append([r])
        return out

    def body_rows(self, table: Table) -> list[int]:
        hdr = set(self.header_rows)
        return [r for r in range(table.nrows) if r not in hdr]


def _docling_header_rows(t: Table) -> tuple[list[int], list[int]]:
    rows, misflagged = [], []
    for r in range(t.nrows):
        filled = t.filled(r)
        flagged = [c for c in filled if c.column_header]
        if not flagged or len(flagged) * 2 < len(filled):
            continue
        if sum(c.kind == "number" for c in flagged) * 2 > len(flagged):
            misflagged.append(r)
        else:
            rows.append(r)
    return rows, misflagged


def value_columns(t: Table, rows: list[int], skip: set[int]) -> list[int]:
    """Columns (outside `skip`) where at least half of the non-empty cells are numbers or marks."""
    out = []
    for c in range(t.ncols):
        if c in skip:
            continue
        kinds = [x.kind for r in rows for x in t.row(r) if x.col == c and x.cspan == 1 and x.text]
        if kinds and sum(k in ("number", "mark") for k in kinds) * 2 >= len(kinds):
            out.append(c)
    return out


def _inferred_header_rows(t: Table) -> list[int]:
    rows = list(range(t.nrows))
    vcols = set(value_columns(t, rows, {0}))
    if not vcols:
        return []
    out = []
    for r in range(min(MAX_INFERRED_HEADER_ROWS + 1, t.nrows)):
        cells = [c for c in t.filled(r) if c.col > 0]
        if not cells:
            if out:
                break
            continue
        if any(c.kind in ("number", "mark") for c in cells if c.col in vcols):
            return out if len(out) <= MAX_INFERRED_HEADER_ROWS else []
        out.append(r)
    return []


def leaf_header(t: Table, header_rows: list[int], c: int) -> str:
    x = t.slot(header_rows[-1], c) if header_rows else None
    return x.text.strip() if x is not None and x.cspan == 1 else ""


def key_cols(t: Table, body: list[int], header_rows: list[int]) -> list[int]:
    """Column 0; column 1 too when it is text (or Docling row headers) followed by value columns, unless it is a Units column."""
    if t.ncols < 2:
        return []
    keys = [0]
    if t.ncols < 3 or leaf_header(t, header_rows, 1).lower() in UNITS_HEADERS:
        return keys
    col1 = [x for r in body for x in t.row(r) if x.col == 1 and x.cspan == 1 and x.text]
    if not col1:
        return keys
    by_flag = sum(x.row_header for x in col1) * 2 > len(col1)
    texty = sum(x.kind == "text" for x in col1) >= 0.8 * len(col1)
    if by_flag or (texty and value_columns(t, body, {0, 1})):
        keys.append(1)
    return keys


def _norm(s: str) -> str:
    return " ".join(s.lower().replace("’", "'").split())


def repeated_header_rows(t: Table, header_rows: list[int]) -> list[int]:
    """Body rows whose text cells all re-state pieces of the top header block (a header repeated mid-table)."""
    if not header_rows:
        return []
    top = [header_rows[0]]
    while top[-1] + 1 in header_rows:
        top.append(top[-1] + 1)
    names = {c: _norm(" ".join(block_col_path(t, top, c, c))) for c in range(t.ncols)}
    def restates(r: int, min_cells: int) -> bool:
        cells = [x for x in t.filled(r) if x.col > 0]
        if len(cells) < min_cells or any(x.kind in ("number", "mark") for x in cells):
            return False
        return all(len(_norm(x.text)) >= 3 and _norm(x.text) in names.get(x.col, "") for x in cells)

    out = [r for r in range(top[-1] + 1, t.nrows) if r not in header_rows and restates(r, MIN_REPEAT_CELLS)]
    for r in list(out):
        for q in (r - 1, r + 1):
            if q > top[-1] and q not in out and q not in header_rows and q < t.nrows and restates(q, 1):
                out.append(q)
    return sorted(out)


def _bands(t: Table, rows: set[int]) -> dict[int, tuple[float, float]]:
    out: dict[int, tuple[float, float]] = {}
    for x in t.cells:
        if x.row in rows and x.cspan == 1 and x.text and x.bbox is not None:
            l, r = out.get(x.col, (x.bbox[0], x.bbox[2]))
            out[x.col] = (min(l, x.bbox[0]), max(r, x.bbox[2]))
    return out


def bleeding_cols(t: Table, lay: Layout) -> set[int]:
    """Columns whose single-column header cell reaches over most of another column's body (scrambled headers)."""
    bands = _bands(t, set(lay.body_rows(t)))
    out: set[int] = set()
    for x in t.cells:
        if x.row not in lay.header_rows or x.cspan != 1 or not x.text or x.bbox is None:
            continue
        for k, (l, r) in bands.items():
            overlap = min(x.bbox[2], r) - max(x.bbox[0], l)
            if k != x.col and overlap > 0 and overlap > BLEED_SHARE * max(r - l, 1.0):
                out |= {x.col, k}
    return out


def layout(t: Table) -> Layout:
    rows, misflagged = _docling_header_rows(t)
    source = "docling"
    if not rows:
        rows = _inferred_header_rows(t)
        source = "inferred" if rows else "none"
    repeated = repeated_header_rows(t, rows)
    rows = sorted(set(rows) | set(repeated))
    lay = Layout(rows, source, misflagged, repeated_rows=repeated)
    lay.key_cols = key_cols(t, lay.body_rows(t), rows)
    hdr = set(rows)
    lay.valued_headers = [c for c in t.cells if c.row in hdr and c.text and header_text(c.text) != c.text]
    lay.bleeding_cols = bleeding_cols(t, lay)
    if source == "docling":
        lay.partly_flagged = [r for r in rows if r not in repeated
                              and any(not x.column_header for x in t.filled(r) if x.col > 0)]
    return lay


def block_col_path(t: Table, block: list[int], c0: int, c1: int) -> list[str]:
    """Header texts above columns c0..c1 within one header block; wrapped single-column lines are joined."""
    parts: list[str] = []
    run: list[str] = []
    seen: set[int] = set()
    for h in block:
        cells = {id(x): x for k in range(c0, c1 + 1) if (x := t.slot(h, k)) is not None}
        for x in sorted(cells.values(), key=lambda x: x.col):
            if id(x) in seen or not x.text or x.col > c0 or x.col + x.cspan - 1 < c1:
                continue
            seen.add(id(x))
            if x.cspan > c1 - c0 + 1:
                if run:
                    parts.append(" ".join(run))
                    run = []
                parts.append(header_text(x.text))
            else:
                run.append(header_text(x.text))
    if run:
        parts.append(" ".join(run))
    return parts


def col_path(t: Table, lay: Layout, r: int, cell: Cell, col: int | None = None) -> list[str]:
    """Nearest header block above row r that names the cell's columns; earlier blocks are fallbacks."""
    c0 = cell.col if col is None else col
    c1 = c0 + cell.cspan - 1 if col is None else c0
    for block in reversed([b for b in lay.blocks if b[0] < r]):
        path = block_col_path(t, block, c0, min(c1, t.ncols - 1))
        if path:
            return path
    return []
