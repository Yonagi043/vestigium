"""Column fixes for renderers: move cells Docling put in the wrong column, or say where columns are doubtful.

A renderer that prints Docling's own grid (bundle.table_grid) applies the sidecar's reliable `cell_moves`
with `apply_moves` and prints `col_note` next to the table.
"""

from __future__ import annotations

from typing import Any


def apply_moves(grid: list[list[str]], table: dict[str, Any]) -> list[list[str]]:
    """A copy of `grid` with each reliable move applied (the cell's text goes to the empty slot it is printed under)."""
    out = [list(row) for row in grid]
    for m in table.get("cell_moves") or []:
        r, a, b = m["row"], m["from_col"], m["to_col"]
        if not m.get("reliable") or r >= len(out) or max(a, b) >= len(out[r]) or out[r][b]:
            continue
        out[r][b], out[r][a] = out[r][a], ""
    return out


def col_note(moves: list[dict[str, Any]], doubtful_rows: list[str], misaligned: list[int] | None = None) -> str | None:
    """One line for the table head: what was moved, which rows still have doubtful columns, and whether the
    header and data columns disagree as a whole."""
    parts = []
    done = [m for m in moves if m["reliable"]]
    if done:
        shown = "; ".join(f"{m['row_label'] or 'row ' + str(m['row'])}: {m['text']!r} -> {m['to_header']}" for m in done[:3])
        parts.append(f"{len(done)} cell(s) moved to the column they are printed under ({shown})")
    if doubtful_rows:
        rows = ", ".join(map(str, doubtful_rows[:5])) + (" …" if len(doubtful_rows) > 5 else "")
        parts.append(f"columns may be misplaced in row(s) {rows}; check the page image")
    if misaligned:
        parts.append("column headers and data columns do not line up; match values to headers on the page image")
    return "; ".join(parts) or None
