"""Rows Docling merged: one grid row holding the values of two printed rows (checked against the PDF text
layer as Docling read it, pages.json textline cells). GPT-4's Table 1 puts "Uniform Bar Exam" and "LSAT"
with both rows' scores in one row, and moves SAT Math's GPT-4 score down into the GRE row.
"""

from __future__ import annotations

import re
from typing import Any

from .grid import Table
from .layout import Layout

BASE_TOL = 2.5  # points: text lines closer than this share a printed line
MIN_CELLS = 2
_DIGIT = re.compile(r"\d")
_MARKER = re.compile(r"[(（]?\s*\d{1,2}\s*[)）]?")
_MONTH = re.compile(r"^(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*$", re.I)

Line = tuple[float, float, str]  # x centre, y centre (top-left origin), text


def lines(page: dict[str, Any]) -> list[Line]:
    """Text lines of one page (not OCR), centres in Docling's top-left page coordinates."""
    out = []
    for c in page.get("textline_cells", []):
        if c.get("from_ocr") or not c.get("text", "").strip():
            continue
        xs, ys = [p[0] for p in c["bbox"]], [p[1] for p in c["bbox"]]
        out.append(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, c["text"]))
    return out


def value_line(text: str) -> bool:
    """A printed value, not only a footnote marker such as "(3)"."""
    return bool(_DIGIT.search(text)) and not _MARKER.fullmatch(text.strip())


def value_cell(text: str) -> bool:
    """A short cell of numbers: digits, at most one plain word, no month name (dates wrap legitimately)."""
    toks = [t.strip("()[],.;:~*") for t in text.split()]
    words = [t for t in toks if t.isalpha() and len(t) >= 3]
    return len(text) <= 80 and any(_DIGIT.search(t) for t in toks) and len(words) <= 1 \
        and not any(_MONTH.match(w) for w in words)


def _levels(ys: list[float]) -> list[float]:
    out: list[float] = []
    for y in sorted(ys):
        if not out or y - out[-1] > BASE_TOL:
            out.append(y)
    return out


def _near(y: float, ys: list[float]) -> bool:
    return any(abs(y - v) <= BASE_TOL for v in ys)


def merged_rows(t: Table, lay: Layout, page_lines: list[Line]) -> list[tuple[int, str]]:
    """(row, "merged" | "displaced") for data rows holding values of two printed rows. Merged: two or more
    value cells hold values on two or more printed lines and the row label holds two of those lines, the
    later starting a new label (capital or digit; a lower-case start is the label wrapping). Displaced: a
    value printed on the line of another row's label. One-page tables only."""
    hdr, keys = set(lay.header_rows), set(lay.key_cols) or {0}
    rows = [r for r in range(t.nrows) if r not in hdr]
    labels: dict[int, list[tuple[float, str]]] = {}
    values: dict[int, list[list[float]]] = {}
    for r in rows:
        for c in t.row(r):
            if not c.text or c.bbox is None:
                continue
            l, top, rt, bot = c.bbox
            inside = [ln for ln in page_lines if l - 1 <= ln[0] <= rt + 1 and top - 1 <= ln[1] <= bot + 1]
            if c.col in keys:
                labels.setdefault(r, []).extend((y, txt) for _, y, txt in inside)
            elif value_cell(c.text):
                values.setdefault(r, []).append(_levels([y for _, y, txt in inside if value_line(txt)]))
    out = []
    for r in rows:
        own = _levels([y for y, _ in labels.get(r, [])])
        multi = [v for v in values.get(r, []) if len(v) >= 2]
        shared = [y for y in own if _near(y, [v for vs in multi for v in vs])]
        later = [txt for y, txt in labels.get(r, []) if len(shared) >= 2 and y > shared[0] + BASE_TOL and _near(y, shared)]
        if len(multi) >= MIN_CELLS and any(x[:1].isupper() or x[:1].isdigit() for x in later):
            out.append((r, "merged"))
            continue
        others = [y for r2, ls in labels.items() if r2 != r for y, _ in ls]
        if any(_near(v, others) and not _near(v, own) for vs in multi for v in vs):
            out.append((r, "displaced"))
    return out


def row_note(t: Table, merged: list[tuple[int, str]], packed_cells: list[str]) -> str | None:
    """One line for the table head when rows cannot be trusted: merged or displaced rows, numbers packed
    into one cell."""
    parts = []
    if merged:
        names = [repr(_short(t.slot_text(r, 0))) if t.slot_text(r, 0) else f"grid row {r}" for r, _ in merged]
        shown = ", ".join(names[:4]) + (" …" if len(names) > 4 else "")
        parts.append(f"rows may be merged or values moved to another row ({shown})")
    if packed_cells:
        parts.append(f"{len(packed_cells)} cell(s) hold several numbers, e.g. {_short(packed_cells[0])!r}")
    return "; ".join(parts) + "; check the page image" if parts else None


def _short(s: str, n: int = 40) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"
