"""Page-level evidence for one table: OCR share of its text lines, and row labels Docling left outside the grid."""

from __future__ import annotations

import re
from typing import Any

from tglw.common.bundle import Doc

from .grid import Table

OCR_SHARE = 0.5
ROW_TOP_TOL = 6.0
MIN_MATCHES = 2
MATCH_SHARE = 0.6
_NUMBER_LABEL = re.compile(r"^\(?(\d{1,3})[.)]$")


def ocr_share(pages: list[dict[str, Any]], t: Table) -> float:
    """Share of Docling text lines inside the table box that came from OCR."""
    if t.bbox is None or t.page is None or t.page > len(pages):
        return 0.0
    l, top, r, bot = t.bbox
    n = ocr = 0
    for c in pages[t.page - 1].get("textline_cells", []):
        if not c.get("text", "").strip():
            continue
        xs = [p[0] for p in c["bbox"]]
        ys = [p[1] for p in c["bbox"]]
        cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
        if l <= cx <= r and top <= cy <= bot:
            n += 1
            ocr += bool(c.get("from_ocr"))
    return ocr / n if n else 0.0


def _row_tops(t: Table, rows: list[int]) -> dict[int, float]:
    out = {}
    for r in rows:
        tops = [x.bbox[1] for x in t.row(r) if x.text and x.bbox is not None]
        if tops:
            out[r] = min(tops)
    return out


def _stray_numbers(doc: Doc, t: Table) -> list[tuple[int, float]]:
    """(number, top y in top-left page coordinates) of numbering text blocks on the table's page."""
    if t.page_height is None:
        return []
    out = []
    for b in doc.blocks:
        if b.page != t.page or b.kind != "text" or b.bbox is None or b.layer != "body":
            continue
        m = _NUMBER_LABEL.match(b.text.strip())
        if m:
            out.append((int(m.group(1)), t.page_height - b.bbox[1]))
    return out


def restore_numbers(doc: Doc, t: Table, rows: list[int]) -> tuple[dict[int, str], bool]:
    """Numbering labels ('36)') for `rows`, registered by one vertical shift onto row tops.

    Returns ({row: label}, complete) where complete means every row got a label and the numbers run consecutively.
    """
    nums = _stray_numbers(doc, t)
    tops = _row_tops(t, rows)
    if len(nums) < MIN_MATCHES or len(tops) < MIN_MATCHES:
        return {}, False
    best: dict[int, str] = {}
    for shift in {top - y for _, y in nums for top in tops.values()}:
        hit: dict[int, str] = {}
        for n, y in nums:
            r = next((r for r, top in tops.items() if abs(top - (y + shift)) <= ROW_TOP_TOL and r not in hit), None)
            if r is not None:
                hit[r] = f"{n})"
        if len(hit) > len(best):
            best = hit
    if len(best) < MIN_MATCHES or len(best) < MATCH_SHARE * len(nums):
        return {}, False
    ordered = [int(best[r][:-1]) for r in sorted(best)]
    if ordered != sorted(ordered):
        return {}, False
    complete = len(best) == len(tops) and ordered == list(range(ordered[0], ordered[0] + len(ordered)))
    return best, complete


SCAN_IMAGE_SHARE = 0.8
SCAN_INVISIBLE_SHARE = 0.5


def scanned_pages(pdf_path: str, pages: set[int]) -> set[int]:
    """Pages that are a full-page image under an invisible OCR text layer."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    pdf = pdfium.PdfDocument(pdf_path)
    out = set()
    for p in sorted(pages):
        if not 1 <= p <= len(pdf):
            continue
        page = pdf[p - 1]
        w, h = page.get_size()
        image = 0.0
        text = invisible = 0
        for obj in page.get_objects(max_depth=3):
            if obj.type == raw.FPDF_PAGEOBJ_IMAGE:
                l, b, r, t = obj.get_bounds()
                image = max(image, (r - l) * (t - b) / (w * h))
            elif obj.type == raw.FPDF_PAGEOBJ_TEXT:
                text += 1
                invisible += raw.FPDFTextObj_GetTextRenderMode(obj.raw) == raw.FPDF_TEXTRENDERMODE_INVISIBLE
        if image >= SCAN_IMAGE_SHARE and (not text or invisible >= SCAN_INVISIBLE_SHARE * text):
            out.add(p)
    return out
