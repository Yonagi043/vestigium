"""Empty table cells written out, so a row read as text keeps its columns.

Docling's grid (`bundle.table_grid`) leaves an empty cell as an empty string, and the views drop the empty cells
that end a row: the row 'Security Strength ≤ 80, SHA-1 under the second column, nothing under the third' reads
'≤ 80 | SHA-1 74', which fits any two columns. Writing each empty cell as EMPTY keeps the row's positions
('≤ 80 | SHA-1 74 | ·'). EMPTY is a middle dot, not a dash: financial tables print '-' for nil (about 5,800 cells
in the corpus, most in exchange filings), and a dash written where nothing is printed would read as a zero. A slot
a spanning cell covers beyond its top-left one is not an empty cell and stays as it is; so does a row with nothing
in it.
"""

from __future__ import annotations

from typing import Any

EMPTY = "·"


def covered(item: dict[str, Any]) -> set[tuple[int, int]]:
    """Grid slots (row, col) a spanning cell covers besides its own top-left slot, from Docling's table data
    (`table_grid` leaves an empty string there too)."""
    data = item.get("data") or {}
    nr, nc = data.get("num_rows", 0), data.get("num_cols", 0)
    out = set()
    for c in data.get("table_cells", []):
        r0, c0 = c.get("start_row_offset_idx", 0), c.get("start_col_offset_idx", 0)
        r1 = c.get("end_row_offset_idx", r0 + 1)
        c1 = c.get("end_col_offset_idx", c0 + 1)
        out |= {(r, k) for r in range(r0, min(r1, nr)) for k in range(c0, min(c1, nc))} - {(r0, c0)}
    return out


def mark_empty(grid: list[list[str]], item: dict[str, Any], mark: str = EMPTY) -> list[list[str]]:
    """The grid with every empty cell of a row that has something in it written as `mark`; slots a spanning cell
    covers, and rows with nothing in them, unchanged. `grid` is the table's rows as the views show them (glyph
    codes stripped, reliable moves applied), `item` the table's Docling item."""
    skip = covered(item)
    return [[mark if not c.strip() and (i, j) not in skip else c for j, c in enumerate(row)]
            if any(c.strip() for c in row) else row for i, row in enumerate(grid)]
