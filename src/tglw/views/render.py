"""Compact text for single blocks: lists keep their markers, tables are rows with a column legend
from D2, D1's missing text-layer words follow the table, pictures carry their inner text."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from tglw.common import bundle
from tglw.tables.fix import apply_moves
from tglw.common.bundle import Block

from .docview import DocView, Segment, short_ref
from .switches import V1

NUMBER_HEADINGS = True  # M6: a heading shows the number Docling keeps in its marker (switchable for replays)
_PREFIX = {"footnote": "fn: ", "page_header": "[header] ", "page_footer": "[footer] "}
_LEADER = re.compile(r"(?:\s*[.·…_]){4,}\s*")
_ENUM = re.compile(r"^(?:\(?[0-9a-zA-Z]{1,3}[.)]|[•●▪◦‣-])\s")


def squeeze(text: str) -> str:
    """Collapse whitespace and dot leaders ("Purpose ........ 2" -> "Purpose … 2")."""
    return " ".join(_LEADER.sub(" … ", text).split())


def _cell(text: str) -> str:
    return squeeze(text).replace("|", "/")


def column_legend(view: DocView, ref: str, grid: list[list[str]]) -> tuple[list[str] | None, set[int]]:
    """Per-column header path from D2 cells with col_certain; other columns take their printed header
    text, or "?". The header rows are dropped only when no used column is left as "?"; while they
    stay, the legend is shown only if D2 resolves nested headers ("BASELINES > HIGH")."""
    t = view.tables.get(ref)
    if not t:
        return None, set()
    paths: dict[int, Counter[tuple[str, ...]]] = {}
    for c in t.get("cells", []):
        if c.get("col_certain") and c.get("col_path"):
            paths.setdefault(c["col"], Counter())[tuple(c["col_path"])] += 1
    if not paths:
        return None, set()
    width = max((len(r) for r in grid), default=0)
    header_rows = set(t.get("header_rows") or [])
    legend = []
    for col in range(width):
        printed = " ".join(_cell(grid[r][col]) for r in sorted(header_rows) if r < len(grid) and grid[r][col])
        legend.append(" > ".join(paths[col].most_common(1)[0][0]) if col in paths else printed or "?")
    used = {col for row in grid for col, text in enumerate(row) if text}
    if all(legend[c] != "?" for c in used):
        return legend, header_rows
    nested = any(len(p) > 1 for counts in paths.values() for p in counts)
    return (legend if nested else None), set()


def table_rows(view: DocView, b: Block) -> list[list[str]]:
    """The table's cells, glyph codes stripped, with D2's reliable moves applied (a cell back under the
    column it is printed under, e.g. 800-53B SA-22)."""
    grid = [[view.clean(b.ref, c) for c in row] for row in bundle.table_grid(view.doc.item(b.ref))]
    return apply_moves(grid, view.tables.get(b.ref) or {})


def render_table(view: DocView, b: Block) -> str:
    t = view.tables.get(b.ref) or {}
    grid = table_rows(view, b)
    head = f"[{short_ref(b.ref)} {len(grid)}×{max((len(r) for r in grid), default=0)}"
    if t.get("continues"):
        head += f" cont. of {short_ref(t['continues'])}"
    if any(f["kind"] == "scanned" for f in t.get("flags", [])):
        head += " · scanned table; check image"
    if t.get("col_note"):
        head += f" · {t['col_note']}"
    if V1["table_notes"] and t.get("row_note"):
        head += f" · {t['row_note']}"
    lines = [head + "]"]
    legend, header_rows = column_legend(view, b.ref, grid)
    if legend:
        lines.append("cols: " + " | ".join(legend))
    skip = header_rows | set(t.get("repeated_header_rows") or [])
    restored = {int(k): v for k, v in (t.get("restored_labels") or {}).items()}
    for i, row in enumerate(grid):
        if i in skip:
            continue
        cells = [_cell(c) for c in row]
        if i in restored and cells:
            cells[0] = f"{restored[i]} {cells[0]}".strip()
        while cells and not cells[-1]:
            cells.pop()
        if cells:
            lines.append(" | ".join(cells))
    for x in view.losses.get(b.ref, []):
        lines.append(render_residue(x))
    if t.get("continued_by"):
        lines.append(f"[continues in {short_ref(t['continued_by'])}]")
    return "\n".join(lines)


def render_picture(view: DocView, b: Block) -> str:
    inner = [_cell(s.text) for c in view.children.get(b.ref, [])
             for s in view.block_segments(c) if s.text and not view.hidden(s.page, c.ref)]
    return f"[{short_ref(b.ref)}: " + " · ".join(inner) + "]" if inner else f"[{short_ref(b.ref)}]"


def render_segment(view: DocView, seg: Segment, refs: bool = False, heading_as: str | None = None) -> str:
    b = seg.block
    if b.kind == "table":
        return render_table(view, b)
    if b.kind == "picture":
        return render_picture(view, b)
    tag = f"[{short_ref(b.ref)}] " if refs else ""
    text = squeeze(seg.text)
    if NUMBER_HEADINGS and (heading_as is not None or b.is_heading) and b.marker and not text.startswith(b.marker):
        text = f"{b.marker} {text}"  # the number Docling keeps apart, e.g. "(8)" of SI-4(8)
    if heading_as is not None:
        return f"## {heading_as} · {tag}{text}"
    if b.is_heading:
        return f"### {tag}{text}"
    if b.label == "list_item" and seg.page == b.page:
        marker = b.marker or ("" if _ENUM.match(text) else "-")
        return f"{marker} {tag}{text}".lstrip()
    return f"{_PREFIX.get(b.label, '')}{tag}{text}"


def render_residue(x: dict[str, Any]) -> str:
    """A D1 table residue ("after row 3 col 1") or a D1 v1 loss, as one line after the table."""
    where = ""
    if x.get("row") is not None and x.get("anchor") not in (None, "none"):
        where = f', {x["anchor"]} row {x["row"]} col {x["col"]}'
    ocr = ", OCR" if x.get("ocr") else ""
    return f'[+ text layer p{x["pdf_page"]}{where}{ocr}: "{_cell(x["text"])}"]'


def render_loss(x: dict[str, Any]) -> str:
    return f'[+ text layer p{x["pdf_page"]}, missing in parse: "{_cell(x["text"])}"]'
