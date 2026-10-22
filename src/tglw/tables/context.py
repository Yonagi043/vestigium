"""Units and periods written in table headers, labels, captions, or the text just before a table."""

from __future__ import annotations

import re
from dataclasses import dataclass

from tglw.common.bundle import Doc

from .grid import Table

_CUR = r"(?:RMB|HK\$|US\$|S\$|A\$|NT\$|USD|HKD|CNY|EUR|GBP|JPY|\$|€|£|¥)"
_UNIT = re.compile(
    rf"\b(?:dollars\s+)?in\s+(?:(?:U\.S\.|US|HK|Hong\s+Kong)\s+)?(?:dollars\s+in\s+)?(?:thousands|millions|billions)\b"
    rf"|\b(?:thousands|millions|billions)\s+of\s+(?:U\.S\.\s+)?(?:dollars|{_CUR}|yuan|renminbi)"
    rf"|{_CUR}\s*['’‘]\s*0{{3}}(?:,000)?"
    rf"|\b{_CUR}\s*(?:million|billion|thousand|mn|bn)s?\b"
    rf"|(?<![\w$])['’]000\b"
    rf"|\bin\s+(?:percent(?:age)?s?|%)"
    rf"|\(\s*%\s*\)"
    rf"|^\s*%\s*$",
    re.I,
)
_PAREN_UNIT = re.compile(
    r"\((?:in\s+)?(%|percent|[KMB]|mn|bn|millions?|billions?|thousands?|flops|PF-days|days|hours|years|picas|points"
    r"|[kMG]b/s|[KMGT]B|kg|t|tons|metric tons(?:\s+CO\s?2\s?e)?|MT|[kMG]Wh|kWe|[kM]W|ms|sec|mm|cm|km|USD|\$)\)"
)
_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
_PERIOD = re.compile(
    rf"\b(?:19|20)\d\d\b|\bFY\s?\d{{2,4}}\b|\b{_MONTH}\s+\d{{1,2}}\b|\b\d{{1,2}}\s+{_MONTH}"
    rf"|\b(?:years?|months|quarters?|weeks|period)\s+ended\b|\bas\s+(?:at|of)\b|\bQ[1-4]\b",
    re.I,
)
_STRONG_PERIOD = re.compile(
    rf"\bFY\s?\d{{2,4}}\b|\b{_MONTH}\s+\d{{1,2}}\b|\b\d{{1,2}}\s+{_MONTH}"
    rf"|\b(?:years?|months|quarters?|weeks|period)\s+ended\b|\bas\s+(?:at|of)\b",
    re.I,
)
_SKIP_LABELS = frozenset({"page_header", "page_footer"})


def find_unit(text: str) -> str | None:
    m = _UNIT.search(text)
    return " ".join(m.group(0).split()) if m else None


def header_unit(text: str) -> str | None:
    """Unit named in a header or label: scale words, currency '000, or a short unit in parentheses."""
    if (u := find_unit(text)):
        return u
    m = _PAREN_UNIT.search(text)
    return m.group(1) if m else None


def is_period(text: str) -> bool:
    return bool(_PERIOD.search(text))


@dataclass(frozen=True, slots=True)
class Context:
    unit: str | None
    unit_ref: str | None
    unit_text: str | None
    period: str | None
    period_ref: str | None


def _nearby(doc: Doc, table: Table, before: int = 3) -> list[tuple[str, str]]:
    """(ref, text) of captions, footnotes, then up to `before` body texts just above the table."""
    out = [("caption", t) for t in table.captions] + [("footnote", t) for t in table.footnotes]
    i, taken = table.order - 1, 0
    while i >= 0 and taken < before:
        b = doc.blocks[i]
        i -= 1
        if b.kind in ("table", "picture"):
            break
        if b.layer != "body" or b.label in _SKIP_LABELS or not b.text:
            continue
        out.append((b.ref, b.text))
        taken += 1
    return out


def table_context(doc: Doc, table: Table) -> Context:
    unit = unit_ref = unit_text = period = period_ref = None
    for ref, text in _nearby(doc, table):
        if unit is None and (u := find_unit(text)):
            unit, unit_ref, unit_text = u, ref, text[:200]
        if period is None and _STRONG_PERIOD.search(text) and len(text) <= 200:
            period, period_ref = text, ref
    return Context(unit, unit_ref, unit_text, period, period_ref)


def cells_unit(table: Table) -> str | None:
    for c in table.cells:
        if (u := find_unit(c.text)):
            return u
    return None
