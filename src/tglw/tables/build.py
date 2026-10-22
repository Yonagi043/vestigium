"""Inspect every table of a document and write `data/D2/wip/<doc>.tables.json`.

Output always goes to the work-in-progress directory; delivered data in `data/D2/` is never written here
(on delivery the files are moved up by hand and the old version is archived under `data/D2/v<N>/`).

    python -m tglw.tables.build            # all cached documents -> data/D2/wip/
    python -m tglw.tables.build DOC [DOC]  # selected documents  -> data/D2/wip/
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any

from tglw.common import bundle, registry
from tglw.common.bundle import Doc
from tglw.common.paths import DATA

from .context import Context, cells_unit, table_context
from . import loose
from .grid import Table, load_tables
from .fix import col_note
from .rows import lines as text_lines, merged_rows, row_note
from .inspect import Shift, column_shifts, continuation, garbled, misaligned_columns, packed
from .layout import Layout, col_path, layout, value_columns
from .pagecells import OCR_SHARE, ocr_share, restore_numbers, scanned_pages
from .view import Inherited, Restored, ViewCell, build_view, label_gap_rows

PRODUCER = "tglw.tables v4"
DELIVERED_DIR = DATA / "D2"
OUT_DIR = DELIVERED_DIR / "wip"
MAX_EVIDENCE_ROWS = 5


@dataclass
class TableReport:
    table: Table
    layout: Layout
    context: Context
    shifts: list[Shift]
    garbled: str | None = None
    scanned: str | None = None
    restored: Restored | None = None
    gap_rows: list[int] = field(default_factory=list)
    continues: str | None = None
    continued_by: str | None = None
    split_evidence: str | None = None
    header_from: str | None = None
    cells: list[ViewCell] = field(default_factory=list)
    flags: list[dict[str, str]] = field(default_factory=list)
    split_shifts: list[Shift] = field(default_factory=list)
    moves: list[dict[str, Any]] = field(default_factory=list)
    misaligned: dict[int, int] = field(default_factory=dict)
    merged: list[tuple[int, str]] = field(default_factory=list)
    packed: list[str] = field(default_factory=list)


def _flag(kind: str, evidence: str) -> dict[str, str]:
    return {"kind": kind, "evidence": evidence}


def _short(s: str, n: int = 60) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _shift_evidence(rep: TableReport, s: Shift) -> str:
    t, lay = rep.table, rep.layout
    c = s.cell
    to_hdr = " / ".join(col_path(t, lay, c.row, c, s.to_col)) or f"column {s.to_col}"
    own_hdr = " / ".join(col_path(t, lay, c.row, c)) or f"column {c.col}"
    label = t.slot_text(c.row, 0)
    return (f"row {c.row} ({_short(label, 30)!r}): {c.text!r} at x {c.bbox[0]:.0f}–{c.bbox[2]:.0f} lies under "
            f"{to_hdr!r} (x {s.target[0]:.0f}–{s.target[1]:.0f}) but Docling put it in column {c.col} "
            f"{own_hdr!r} (x {s.own[0]:.0f}–{s.own[1]:.0f})")


def _gap_evidence(rep: TableReport) -> str | None:
    rows = sorted({v.row for v in rep.cells if "row_label_inherited" in v.why})
    lost = rep.gap_rows
    data_rows = {v.row for v in rep.cells}
    if _lost_column(rep):
        got = len(rep.restored.labels) if rep.restored else 0
        return (f"row-label column empty in {len(lost)} of {len(data_rows)} data rows "
                f"(rows {', '.join(map(str, lost[:MAX_EVIDENCE_ROWS]))}{' …' if len(lost) > MAX_EVIDENCE_ROWS else ''}); "
                f"labels missing from the grid; {got} restored from numbering text outside the grid")
    if not rows:
        return None
    first = next(v for v in rep.cells if v.row == rows[0])
    shown = ", ".join(str(r) for r in rows[:MAX_EVIDENCE_ROWS]) + (" …" if len(rows) > MAX_EVIDENCE_ROWS else "")
    return (f"{len(rows)} data rows have an empty row-label cell (rows {shown}); "
            f"label carried down from above, e.g. row {rows[0]} ← {_short(' | '.join(first.row_path), 80)!r}")


def _moves(rep: TableReport, has_packed: bool) -> list[dict[str, Any]]:
    """Strict shifts as moves; reliable only when the row has its own label, the target column has a header,
    and nothing else in the table is broken (scan, garbled grid, packed cells)."""
    t, lay = rep.table, rep.layout
    out = []
    for s in rep.shifts:
        c = s.cell
        label = t.slot_text(c.row, 0) if c.col != 0 else ""
        to_header = " / ".join(col_path(t, lay, c.row, c, s.to_col))
        reliable = bool(label and to_header) and not (rep.scanned or rep.garbled or has_packed)
        out.append({"row": c.row, "from_col": c.col, "to_col": s.to_col, "text": c.text, "row_label": label,
                    "to_header": to_header, "reliable": reliable})
    return out


def _header_name(rep: TableReport, col: int) -> str:
    src = rep.table, rep.layout
    if not rep.layout.header_rows and rep.header_from:
        return f"column {col}"
    probe = next((c for c in rep.table.cells if c.col == col), None)
    return " / ".join(col_path(*src, rep.table.nrows, probe, col)) if probe else f"column {col}"


def _misaligned_evidence(rep: TableReport) -> str:
    parts = []
    for col, under in sorted(rep.misaligned.items()):
        sample = next((c.text for c in rep.table.cells if c.col == col and c.text and c.row not in rep.layout.header_rows), "")
        parts.append(f"values of column {col} (e.g. {_short(sample, 30)!r}) are printed under the header of column {under}")
    return "; ".join(parts) + "; header and data columns disagree, Docling's header indices or data indices are off"


def _row_names(t: Table, rows: list[int]) -> list[str]:
    return [repr(_short(t.slot_text(r, 0), 30)) if t.slot_text(r, 0) else f"grid row {r}" for r in rows]


def _lost_column(rep: TableReport) -> bool:
    data_rows = {v.row for v in rep.cells}
    return len(rep.gap_rows) >= 2 and len(rep.gap_rows) * 2 >= len(data_rows)


def _is_value_table(t: Table, lay: Layout) -> bool:
    return bool(value_columns(t, lay.body_rows(t), set(lay.key_cols)))


def flags(rep: TableReport) -> list[dict[str, str]]:
    t, lay, out = rep.table, rep.layout, []
    if t.nrows == 0 or t.ncols == 0 or t.is_blank():
        out.append(_flag("empty", f"{t.nrows}x{t.ncols} grid, {len(t.cells)} cells, no text"))
        return out
    if rep.garbled:
        out.append(_flag("garbled", rep.garbled))
    if rep.scanned:
        out.append(_flag("scanned", rep.scanned))
    if (ev := _gap_evidence(rep)):
        out.append(_flag("row_header_gap", ev))
    for s in rep.shifts:
        out.append(_flag("column_shift", _shift_evidence(rep, s)))
    for s in rep.split_shifts:
        out.append(_flag("column_shift", "split value, half left in the neighbouring column: " + _shift_evidence(rep, s)))
    if rep.misaligned:
        out.append(_flag("header_misaligned", _misaligned_evidence(rep)))
    if not lay.header_rows and _is_value_table(t, lay):
        if rep.header_from:
            out.append(_flag("header_missing", f"no header rows; column names taken from {rep.header_from}"))
        else:
            out.append(_flag("header_missing", "no header rows although the table has number/mark columns"))
    if rep.split_evidence:
        out.append(_flag("split_table", rep.split_evidence))
    if rep.merged:
        shown = "; ".join(f"row {r} ({_short(t.slot_text(r, 0), 30)!r}) {how}" for r, how in rep.merged[:MAX_EVIDENCE_ROWS])
        out.append(_flag("rows_merged", f"{len(rep.merged)} grid row(s) hold values of two printed rows (text layer): "
                                        f"{shown}{' …' if len(rep.merged) > MAX_EVIDENCE_ROWS else ''}"))
    if rep.context.unit and not cells_unit(t):
        out.append(_flag("unit_outside", f"{rep.context.unit!r} from {rep.context.unit_ref}: "
                                         f"{_short(rep.context.unit_text or '', 160)!r}"))
    if lay.valued_headers:
        ex = lay.valued_headers[0].text
        out.append(_flag("header_misflag", f"{len(lay.valued_headers)} header cells also hold data amounts, "
                                           f"e.g. {_short(ex, 60)!r}; amounts dropped from column paths"))
    if lay.misflagged_rows:
        rows = lay.misflagged_rows
        out.append(_flag("header_misflag", f"Docling marks numeric rows {rows[:MAX_EVIDENCE_ROWS]} as column headers; "
                                           f"treated as data, e.g. {_short(' | '.join(c.text for c in t.filled(rows[0])), 80)!r}"))
    return out


def _link_parts(doc: Doc, reps: list[TableReport]) -> None:
    reps = [r for r in reps if not r.table.is_index]
    for a, b in zip(reps, reps[1:]):
        ev = continuation(doc, a.table, b.table, bool(b.layout.header_rows))
        if ev:
            b.continues, b.split_evidence = a.table.ref, ev
            a.continued_by = b.table.ref
            if not a.split_evidence:
                a.split_evidence = f"continued by {b.table.ref} on p{b.table.page}"


def _header_donor(reps: list[TableReport], i: int, by_ref: dict[str, int]) -> TableReport | None:
    rep = reps[i]
    while rep.continues is not None:
        rep = reps[by_ref[rep.continues]]
        if rep.layout.header_rows:
            return rep
    return None


def _scan_evidence(t: Table, scanned: set[int], pages: list[dict[str, Any]]) -> str | None:
    hit = [p for p in t.pages if p in scanned]
    if hit:
        return f"page {hit[0]} is a full-page image with an invisible OCR text layer"
    share = ocr_share(pages, t)
    return f"{share:.0%} of Docling text lines inside the table come from OCR" if share >= OCR_SHARE else None


def analyse(doc: Doc) -> list[TableReport]:
    tables = load_tables(doc)
    pages = bundle.load_pages(doc.name) if tables else []
    scanned = scanned_pages(str(doc.info.pdf_path), {p for t in tables for p in t.pages})
    reps = []
    for t in tables:
        lay = layout(t)
        reps.append(TableReport(t, lay, table_context(doc, t), column_shifts(t, lay), garbled(t),
                                _scan_evidence(t, scanned, pages)))
    _link_parts(doc, reps)
    by_ref = {r.table.ref: i for i, r in enumerate(reps)}
    for i, rep in enumerate(reps):
        if rep.table.is_index:
            continue
        donor = _header_donor(reps, i, by_ref) if not rep.layout.header_rows else None
        inherited = Inherited(donor.table, donor.layout, donor.table.ref) if donor else None
        rep.header_from = donor.table.ref if donor else None
        rep.gap_rows = label_gap_rows(rep.table, rep.layout)
        rep.restored = None
        if len(rep.gap_rows) >= 2:
            labels, complete = restore_numbers(doc, rep.table, rep.gap_rows)
            rep.restored = Restored(labels, complete) if labels else None
        rep.cells = build_view(rep.table, rep.layout, rep.context, rep.shifts, inherited, rep.restored)
        rep.packed = [x.text for x in rep.table.cells if packed(x.text)]
        has_packed = bool(rep.packed)
        if not rep.scanned and len(rep.table.pages) == 1 and rep.table.page and rep.table.page <= len(pages):
            rep.merged = merged_rows(rep.table, rep.layout, text_lines(pages[rep.table.page - 1]))
        rep.split_shifts = column_shifts(rep.table, rep.layout, empty_target=False)
        rep.misaligned = misaligned_columns(rep.table, rep.layout, (donor.table, donor.layout) if donor else None)
        rep.moves = _moves(rep, has_packed)
        split_cells = {(s.cell.row, s.cell.col) for s in rep.split_shifts}
        for v in rep.cells:
            if has_packed:
                v.why.append("packed_cells")
            if rep.garbled:
                v.why.append("table_garbled")
            if rep.scanned:
                v.why.append("scanned")
            if (v.row, v.col) in split_cells:
                v.why.append("column_shift")
            if v.col in rep.misaligned:
                v.why.append("header_misaligned")
        rep.flags = flags(rep)
    return reps


def _cell_json(v: ViewCell) -> dict[str, Any]:
    d: dict[str, Any] = {"row": v.row, "col": v.col, "text": v.text, "row_path": v.row_path,
                         "col_path": v.col_path, "unit": v.unit, "certain": v.certain,
                         "row_certain": v.row_certain, "col_certain": v.col_certain, "unit_certain": v.unit_certain}
    if v.unit_src:
        d["unit_src"] = v.unit_src
    if v.period:
        d["period"], d["period_src"] = v.period, v.period_src
    if v.why:
        d["why"] = v.why
    if v.col_fixed is not None:
        d["col_fixed"] = v.col_fixed
    return d


def table_json(rep: TableReport) -> dict[str, Any]:
    t, lay = rep.table, rep.layout
    bbox = t.raw_bbox and [round(t.raw_bbox[k], 1) for k in "ltrb"]
    return {
        "ref": t.ref, "label": t.label, "pages": list(t.pages), "shape": [t.nrows, t.ncols],
        "bbox": bbox, "bbox_origin": t.raw_bbox and t.raw_bbox.get("coord_origin"),
        "continues": rep.continues, "continued_by": rep.continued_by,
        "header_rows": lay.header_rows, "header_source": "previous_part" if rep.header_from else lay.header_source,
        "key_cols": lay.key_cols, "repeated_header_rows": lay.repeated_rows,
        "restored_labels": {str(r): x for r, x in sorted(rep.restored.labels.items())} if rep.restored else {},
        "flags": rep.flags,
        "cell_moves": rep.moves,
        "col_note": col_note(rep.moves, _row_names(t, sorted({m["row"] for m in rep.moves if not m["reliable"]}
                                                             | {s.cell.row for s in rep.split_shifts})),
                             sorted(rep.misaligned)),
        "row_note": None if rep.scanned else row_note(t, rep.merged, rep.packed),
        "cells": [_cell_json(v) for v in rep.cells],
    }


def doc_json(name: str) -> dict[str, Any]:
    doc = bundle.load(name)
    return {"doc": name, "producer": PRODUCER, "tables": [table_json(r) for r in analyse(doc)],
            "loose_tables": loose_tables(doc)}


def loose_tables(doc: Doc) -> list[dict[str, Any]]:
    """Tables Docling did not recognise (figures left as loose text blocks), rebuilt when they check out;
    scanned pages skipped."""
    found = loose.find(doc)
    skip = scanned_pages(str(doc.info.pdf_path), {x.page for x in found}) if found else set()
    return [loose.entry(x) for x in found if x.page not in skip]


def write(name: str) -> dict[str, Any]:
    out = doc_json(name)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{name}.tables.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    tmp.replace(path)
    return out


def main(argv: list[str]) -> None:
    names = argv or [d.name for d in registry.docs()]
    for name in names:
        out = write(name)
        n_flag = sum(bool(t["flags"]) for t in out["tables"])
        print(f"{name}: {len(out['tables'])} tables, {n_flag} flagged")


if __name__ == "__main__":
    main(sys.argv[1:])
