"""Normalised cell view: row path (sections + labels), column path, unit and period, with uncertainty reasons."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .context import Context, header_unit, is_period
from .grid import Cell, Table
from .inspect import Shift
from .layout import UNITS_HEADERS, Layout, col_path, leaf_header

TOL = 2.0
SCOPE_INDENT = 3.0
MAX_SECTION_LEN = 80
MAX_SPAN_SECTION_LEN = 200
MAX_PERIOD_LABEL = 60
_TOTAL = re.compile(r"^[\s\-–—•*]*(?:sub-?\s?)?totals?\b", re.I)
_BARE_TOTAL = re.compile(r"^[\s\-–—•*]*totals?\s*:?$", re.I)
_NUMBERED = re.compile(r"^(?:[IVX]+|\d+)(?:\.\d+)+[A-Z]?\b|^[A-Z]{1,3}-\d")
_LEADERS = re.compile(r"(?:\s*\.){3,}|\s+\.$")
_AMOUNT = re.compile(r"^\(?[-–—]?\s*(?:\$|RMB|HK\$|US\$)?\s*([\d,]*\d(?:\.\d+)?)\s*\)?$")
_NIL = frozenset({"-", "–", "—", "nil", "Nil"})
_SENTENCE_BREAK = re.compile(r"[.!?]\s+[A-Z(]")
_FUNCTION_END = re.compile(r"\b(?:a|an|the|of|and|or|in|for|to|with|by|on|from|at)$", re.I)
ROW_REASONS = frozenset({"row_label_inherited", "unlabeled_total", "no_row_label", "section_inferred",
                         "section_missing_above", "label_restored_partial", "packed_cells", "table_garbled", "scanned"})
COL_REASONS = frozenset({"header_inferred", "header_from_previous_part", "no_col_header", "spans_columns",
                         "header_had_values", "header_partly_flagged", "column_shift", "header_misaligned",
                         "header_bleed", "packed_cells",
                         "table_garbled", "scanned"})
UNIT_REASONS = frozenset({"unit_unparsed", "unit_exception", "packed_cells", "table_garbled", "scanned"})


@dataclass
class ViewCell:
    row: int
    col: int
    text: str
    row_path: list[str]
    col_path: list[str]
    unit: str | None
    unit_src: str | None
    period: str | None
    period_src: str | None
    why: list[str] = field(default_factory=list)
    col_fixed: int | None = None

    @property
    def row_certain(self) -> bool:
        return not ROW_REASONS.intersection(self.why)

    @property
    def col_certain(self) -> bool:
        return not COL_REASONS.intersection(self.why)

    @property
    def unit_certain(self) -> bool:
        return not UNIT_REASONS.intersection(self.why)

    @property
    def certain(self) -> bool:
        return self.row_certain and self.col_certain and self.unit_certain


def clean_label(s: str) -> str:
    """Drop dot leaders and a trailing run of a repeated junk token (e.g. leaders decoded as 'H1100 H1100')."""
    s = _LEADERS.sub(" ", s).strip()
    toks = s.split()
    while len(toks) >= 3 and toks[-1] == toks[-2]:
        junk = toks[-1]
        while toks and toks[-1] == junk:
            toks.pop()
    return " ".join(toks)


def amount(text: str) -> float | None:
    s = text.strip()
    if s in _NIL:
        return 0.0
    m = _AMOUNT.match(s)
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    return -v if s.startswith("(") or s.lstrip("(").startswith(("-", "–", "—")) else v


@dataclass
class _Section:
    text: str
    rank: int  # 0 numbered or period heading, 1 spanning row, 2 plain label row
    indent: float | None
    marked: bool = False  # Docling row_section, trailing colon, spanning, or rank 0
    scoped: bool | None = None

    @property
    def confirmed(self) -> bool:
        return self.marked or bool(self.scoped)


class SectionStack:
    """Open section rows; a section closes on a same-or-higher-rank section, a total, or (when the table indents) a shallower row."""

    def __init__(self) -> None:
        self.items: list[_Section] = []

    def push(self, s: _Section) -> None:
        while self.items and self._closed_by(self.items[-1], s):
            self.items.pop()
        self.items.append(s)

    @staticmethod
    def _closed_by(top: _Section, new: _Section) -> bool:
        if top.rank != new.rank:
            return top.rank > new.rank
        if new.rank < 2 or top.indent is None or new.indent is None:
            return True
        return top.indent >= new.indent - TOL

    def enter_row(self, indent: float | None, closes_block: bool = False) -> None:
        """A shallower row leaves an indented section, unless it is that section's sum line (`closes_block`)."""
        for s in self.items:
            if s.scoped is None:
                s.scoped = indent is not None and s.indent is not None and indent > s.indent + SCOPE_INDENT
        if closes_block:
            return
        while (self.items and self.items[-1].rank == 2 and self.items[-1].scoped and indent is not None
               and indent <= self.items[-1].indent + TOL):
            self.items.pop()

    def close_grand_total(self, indent: float | None) -> None:
        while (self.items and self.items[-1].rank == 2
               and (indent is None or self.items[-1].indent is None or self.items[-1].indent >= indent - TOL)):
            self.items.pop()

    def close_total(self) -> None:
        if self.items and self.items[-1].rank == 2:
            self.items.pop()

    def reset(self) -> None:
        self.items.clear()

    def path(self) -> list[str]:
        return [s.text for s in self.items]

    def all_confirmed(self) -> bool:
        return all(s.confirmed for s in self.items)


def _values_in_row(t: Table, lay: Layout, r: int) -> bool:
    return any(t.slot_text(r, k) for k in range(t.ncols) if k not in lay.key_cols)


def section_cell(t: Table, lay: Layout, r: int) -> Cell | None:
    """The single label cell of a label-only row, else None."""
    filled = t.filled(r)
    if len(filled) != 1:
        return None
    x = filled[0]
    if x.col not in lay.key_cols and x.cspan < 2:
        return None
    if x.cspan < 2 and _values_in_row(t, lay, r):
        return None
    return x


def _rank(x: Cell, text: str) -> int:
    if _NUMBERED.match(text) or is_period(text) and not text.endswith(":"):
        return 0
    return 1 if x.cspan >= 2 else 2


def _is_wrap(x: Cell, text: str, nxt: str) -> bool:
    """A label line that continues on the next row ('Assets included in a' / 'discontinued operation' / '(note 12)')."""
    if text.endswith(":") or x.cspan >= 2:
        return False
    return text.endswith(",") or bool(_FUNCTION_END.search(text)) or nxt[:1].islower() or nxt[:1] == "("


def _too_long(x: Cell, text: str) -> bool:
    if x.cspan >= 2:
        return len(text) > MAX_SPAN_SECTION_LEN or bool(_SENTENCE_BREAK.search(text))
    return len(text) > MAX_SECTION_LEN


def _next_label(t: Table, lay: Layout, r: int, body: list[int]) -> str:
    later = [b for b in body if b > r]
    return t.slot_text(later[0], lay.key_cols[0]) if later and lay.key_cols else ""


def _indent(t: Table, lay: Layout, r: int) -> float | None:
    for k in lay.key_cols:
        x = t.slot(r, k)
        if x is not None and x.text and x.row == r and x.bbox is not None:
            return x.bbox[0]
    return None


def _sums_up(t: Table, r: int, run: list[int], cols: list[int]) -> bool:
    """True when every numeric value of row r equals the column sum over `run` (at least one non-zero match)."""
    if len(run) < 2:
        return False
    hits = 0
    for k in cols:
        v = amount(t.slot_text(r, k))
        if v is None:
            continue
        if abs(sum(amount(t.slot_text(q, k)) or 0.0 for q in run) - v) > 0.5:
            return False
        hits += v != 0
    return hits > 0


def _unit(cell: Cell, cpath: list[str], rpath: list[str], ctx: Context,
          row_unit: str | None) -> tuple[str | None, str | None, str | None]:
    """(unit, source, reason it is uncertain). Only numbers, marks, and percentages carry a unit."""
    if cell.text.rstrip().endswith("%"):
        return "%", "cell", None
    if cell.kind not in ("number", "mark"):
        return None, None, None
    for src, texts in (("col", reversed(cpath)), ("row", reversed(rpath))):
        for s in texts:
            if (u := header_unit(s)):
                return u, src, None
    if row_unit:
        return row_unit, "units_column", None
    if ctx.unit:
        return ctx.unit, "outside", "unit_exception" if "except" in (ctx.unit_text or "").lower() else None
    if cell.kind == "number" and any("(" in s for s in [*cpath, *rpath]):
        return None, None, "unit_unparsed"
    return None, None, None


def _period(cpath: list[str], rpath: list[str], ctx: Context) -> tuple[str | None, str | None]:
    for src, texts in (("col", cpath), ("row", [s for s in rpath if len(s) <= MAX_PERIOD_LABEL])):
        hits = [s for s in texts if is_period(s)]
        if hits:
            return " / ".join(hits), src
    if ctx.period and len(ctx.period) <= 120:
        return ctx.period, "outside"
    return None, None


@dataclass
class Inherited:
    table: Table
    layout: Layout
    ref: str


@dataclass
class Restored:
    labels: dict[int, str]
    complete: bool


@dataclass
class _Ctx:
    t: Table
    lay: Layout
    ctx: Context
    shift_of: dict[int, Shift]
    inherited: Inherited | None
    n_value_cols: int
    units_col: int | None


def _units_col(t: Table, lay: Layout) -> int | None:
    for k in range(t.ncols):
        if k not in lay.key_cols and leaf_header(t, lay.header_rows, k).lower() in UNITS_HEADERS:
            return k
    return None


def label_gap_rows(t: Table, lay: Layout) -> list[int]:
    """Body rows with content but an empty first key column."""
    hdr = set(lay.header_rows)
    return [r for r in range(t.nrows) if r not in hdr and lay.key_cols and not t.slot_text(r, lay.key_cols[0])
            and any(x.col not in lay.key_cols for x in t.filled(r))]


def build_view(t: Table, lay: Layout, ctx: Context, shifts: list[Shift],
               inherited: Inherited | None = None, restored: Restored | None = None) -> list[ViewCell]:
    c = _Ctx(t, lay, ctx, {id(s.cell): s for s in shifts}, inherited, t.ncols - len(lay.key_cols), _units_col(t, lay))
    restored = restored or Restored({}, False)
    hdr = set(lay.header_rows)
    body = lay.body_rows(t)
    value_cols = [k for k in range(t.ncols) if k not in lay.key_cols]
    stack = SectionStack()
    last: dict[int, str] = {}
    run: list[int] = []
    prefix = ""
    out: list[ViewCell] = []
    first_span_section = _first_span_section(t, lay) if not lay.header_rows else None
    for r in range(t.nrows):
        if r in hdr:
            stack.reset()
            last.clear()
            run = []
            continue
        sec = section_cell(t, lay, r)
        if sec is not None:
            text = clean_label(sec.text)
            if _too_long(sec, text):
                continue
            if _is_wrap(sec, text, _next_label(t, lay, r, body)):
                prefix = f"{prefix} {text}".strip()
                continue
            rank = _rank(sec, text)
            marked = sec.row_section or text.endswith(":") or sec.cspan >= 2 or rank == 0
            stack.push(_Section(text, rank, sec.bbox[0] if sec.bbox else None, marked))
            last.clear()
            run = []
            continue
        filled = [x for x in t.filled(r) if x.col not in lay.key_cols or not lay.key_cols]
        if not filled:
            continue
        indent = _indent(t, lay, r)
        sums = _sums_up(t, r, run, value_cols)
        stack.enter_row(indent, sums)
        own = [clean_label(t.slot_text(r, k)) for k in lay.key_cols]
        if own and not own[0] and r in restored.labels:
            own[0] = restored.labels[r]
        is_total = any(_TOTAL.match(s) for s in own if s)
        if any(_BARE_TOTAL.match(s) for s in own if s):
            stack.close_grand_total(indent)
        if restored.labels and not own[0]:
            last.pop(lay.key_cols[0], None)
        labels, why_row = _labels(t, r, lay, own, last, sums, is_total, prefix)
        if r in restored.labels and not restored.complete:
            why_row.append("label_restored_partial")
        if not stack.all_confirmed():
            why_row.append("section_inferred")
        if first_span_section is not None and r < first_span_section:
            why_row.append("section_missing_above")
        prefix = ""
        rpath = stack.path() + labels
        row_unit = clean_label(t.slot_text(r, c.units_col)) if c.units_col is not None else None
        for x in filled:
            if x.col != c.units_col:
                out.append(_view_cell(c, r, x, rpath, why_row, row_unit if x.kind == "number" else None))
        if is_total or sums:
            stack.close_total()
            run = []
        else:
            run.append(r)
    return out


def _first_span_section(t: Table, lay: Layout) -> int | None:
    """First full-width section row of a header-less table; rows above it may belong to a section on the previous page."""
    for r in range(t.nrows):
        x = section_cell(t, lay, r)
        if x is not None and x.cspan >= 2:
            return r
    return None


def _labels(t: Table, r: int, lay: Layout, own: list[str], last: dict[int, str], sums: bool,
            is_total: bool, prefix: str) -> tuple[list[str], list[str]]:
    labels: list[str] = []
    why: list[str] = []
    if lay.key_cols and not any(own) and sums:
        return ["(unlabeled total)"], ["unlabeled_total"]
    for k, text in zip(lay.key_cols, own):
        if not text and k in last and not is_total:
            text = last[k]
            why.append("row_label_inherited")
        elif text:
            last[k] = text
        if text:
            labels.append(text)
    if prefix and labels:
        labels[0] = f"{prefix} {labels[0]}"
    if lay.key_cols and not labels:
        why.append("no_row_label")
    return labels, why


def _span_path(c: _Ctx, r: int, x: Cell) -> list[str]:
    leaves = [p[-1] for k in range(x.col, min(x.col + x.cspan, c.t.ncols)) if (p := col_path(c.t, c.lay, r, x, k))]
    leaves = list(dict.fromkeys(leaves))
    return [" + ".join(leaves)] if leaves else []


def _view_cell(c: _Ctx, r: int, x: Cell, rpath: list[str], why_row: list[str], row_unit: str | None) -> ViewCell:
    t, lay = c.t, c.lay
    why = list(dict.fromkeys(why_row))
    shift = c.shift_of.get(id(x))
    col = shift.to_col if shift else None
    if lay.header_rows:
        cpath = col_path(t, lay, r, x, col)
        if not cpath and x.cspan > 1:
            cpath = _span_path(c, r, x)
            why.append("spans_columns")
        if lay.header_source == "inferred":
            why.append("header_inferred")
        if lay.valued_headers:
            why.append("header_had_values")
        if (x.col if col is None else col) in lay.bleeding_cols:
            why.append("header_bleed")
        if lay.partly_flagged:
            why.append("header_partly_flagged")
    elif c.inherited is not None:
        it = c.inherited.table
        cpath = col_path(it, c.inherited.layout, it.nrows, x, col) if x.col < it.ncols else []
        why.append("header_from_previous_part")
    else:
        cpath = []
    if shift:
        why.append("column_shift")
    if not cpath and c.n_value_cols > 1:
        why.append("no_col_header")
    unit, usrc, unit_why = _unit(x, cpath, rpath, c.ctx, row_unit)
    if unit_why:
        why.append(unit_why)
    period, psrc = _period(cpath, rpath, c.ctx)
    return ViewCell(r, x.col, x.text, rpath, cpath, unit, usrc, period, psrc, why, col)
