"""Docling tables as cell grids with top-left page coordinates and simple value typing."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from tglw.common.bundle import Doc

Box = tuple[float, float, float, float]  # l, t, r, b with top-left origin

_NUM = re.compile(
    r"^[\s(（\[]*[-+−–—]?\s*(?:RMB|HK\$|US\$|S\$|A\$|\$|€|£|¥)?\s*[-+−–—]?\s*"
    r"(?:\d[\d,]*(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?\s*(?:%|x|bps|pp)?[\s)）\]*]*$",
    re.I,
)
_MARKS = frozenset(
    "x X ✓ ✔ √ ● ○ ■ □ ◆ ◇ • * - – — −  n/a N/A NA n.a. nil Nil Yes No Y N yes no ✗ ✘ † ‡".split()
) | {"", "–", "—"}
_UNIT_TOKENS = frozenset({"%", "$", "€", "£", "¥", "RMB", "HK$", "US$"})
_YEAR = re.compile(r"^\s*(?:FY\s?)?(?:19|20)\d\d[A-Z]?\s*$", re.I)


def value_kind(text: str) -> str:
    """'empty', 'year', 'unit', 'number', 'mark', or 'text'."""
    s = text.strip()
    if not s:
        return "empty"
    if s in _UNIT_TOKENS:
        return "unit"
    if _YEAR.match(s):
        return "year"
    if _NUM.match(s):
        return "number"
    if s in _MARKS or len(s) == 1 and not s.isalnum():
        return "mark"
    return "text"


def is_value(text: str) -> bool:
    return value_kind(text) in ("number", "mark")


@dataclass(frozen=True, slots=True)
class Cell:
    row: int
    col: int
    rspan: int
    cspan: int
    text: str
    bbox: Box | None
    column_header: bool
    row_header: bool
    row_section: bool

    @property
    def cx(self) -> float | None:
        return None if self.bbox is None else (self.bbox[0] + self.bbox[2]) / 2

    @property
    def kind(self) -> str:
        return value_kind(self.text)


@dataclass
class Table:
    ref: str
    label: str
    pages: tuple[int, ...]
    nrows: int
    ncols: int
    cells: list[Cell]
    bbox: Box | None
    page_height: float | None
    order: int
    raw_bbox: dict[str, Any] | None = None
    captions: list[str] = field(default_factory=list)
    footnotes: list[str] = field(default_factory=list)
    _slots: list[list[int | None]] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self._slots = [[None] * self.ncols for _ in range(self.nrows)]
        for i, c in enumerate(self.cells):
            for r in range(c.row, min(c.row + c.rspan, self.nrows)):
                for k in range(c.col, min(c.col + c.cspan, self.ncols)):
                    if self._slots[r][k] is None:
                        self._slots[r][k] = i

    @property
    def is_index(self) -> bool:
        return self.label == "document_index"

    @property
    def page(self) -> int | None:
        return self.pages[0] if self.pages else None

    def slot(self, r: int, c: int) -> Cell | None:
        i = self._slots[r][c]
        return None if i is None else self.cells[i]

    def slot_text(self, r: int, c: int) -> str:
        cell = self.slot(r, c)
        return cell.text if cell else ""

    def row(self, r: int) -> list[Cell]:
        return sorted((c for c in self.cells if c.row == r), key=lambda c: c.col)

    def filled(self, r: int) -> list[Cell]:
        return [c for c in self.row(r) if c.text]

    def is_blank(self) -> bool:
        return not any(c.text for c in self.cells)


def _cell(raw: dict[str, Any]) -> Cell:
    b = raw.get("bbox")
    bbox = (b["l"], b["t"], b["r"], b["b"]) if b and b.get("coord_origin", "TOPLEFT") == "TOPLEFT" else None
    return Cell(
        row=raw.get("start_row_offset_idx", 0),
        col=raw.get("start_col_offset_idx", 0),
        rspan=max(1, raw.get("row_span", 1)),
        cspan=max(1, raw.get("col_span", 1)),
        text=" ".join((raw.get("text") or "").split()),
        bbox=bbox,
        column_header=bool(raw.get("column_header")),
        row_header=bool(raw.get("row_header")),
        row_section=bool(raw.get("row_section")),
    )


def _page_box(doc: Doc, prov: dict[str, Any]) -> tuple[Box | None, float | None]:
    size = (doc.raw.get("pages") or {}).get(str(prov.get("page_no")), {}).get("size") or {}
    h = size.get("height")
    b = prov.get("bbox")
    if not b:
        return None, h
    if b.get("coord_origin") == "BOTTOMLEFT":
        if h is None:
            return None, h
        return (b["l"], h - b["t"], b["r"], h - b["b"]), h
    return (b["l"], b["t"], b["r"], b["b"]), h


def load_tables(doc: Doc) -> list[Table]:
    """All tables of a Doc in reading order."""
    out = []
    for blk in doc.blocks:
        if blk.kind != "table":
            continue
        item = doc.item(blk.ref)
        data = item.get("data") or {}
        prov = item.get("prov") or []
        bbox, height = _page_box(doc, prov[0]) if prov else (None, None)
        out.append(Table(
            ref=blk.ref,
            label=item.get("label", "table"),
            pages=blk.pages,
            nrows=data.get("num_rows", 0),
            ncols=data.get("num_cols", 0),
            cells=[_cell(c) for c in data.get("table_cells", [])],
            bbox=bbox,
            page_height=height,
            order=blk.order,
            raw_bbox=prov[0].get("bbox") if prov else None,
            captions=[doc.item(c["$ref"]).get("text", "") for c in item.get("captions") or []],
            footnotes=[doc.item(c["$ref"]).get("text", "") for c in item.get("footnotes") or []],
        ))
    return out
