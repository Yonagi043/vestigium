"""E4: say where text is OCR. Scanned pages (a page-size image under an invisible text layer, or no
layer at all) get a mark on their read page header, with one note per read on what that means; a
region crop's text is labelled OCR on scanned pages and when it falls back to Docling's text.

E0: a reader answered from read's OCR of a scanned page, where handwritten symbols had come out
garbled, without looking at the image; nothing said the text was OCR.
"""

from __future__ import annotations

import re
from functools import lru_cache

from tglw.common import registry
from tglw.trace import record

from . import switches, tracing

PAGE_MARK = " · scanned, OCR text"
_HEADER = re.compile(r"^== p(\d+)\b(.*?) ==$", re.M)
LAYER = "text layer in the box:"
PARSED = "no text layer here; parsed text in the box:"
LAYER_OCR = "OCR text in the box (scanned page; may contain errors):"
PARSED_OCR = "no text layer here; OCR text in the box (may contain errors):"


@lru_cache(maxsize=65536)
def scanned(name: str, page: int) -> bool:
    """Page by page (about 20 ms each, cached): the whole of a 500-page document takes about 2 s."""
    from tglw.tables.pagecells import scanned_pages

    return page in scanned_pages(str(registry.get(name).pdf_path), {page})


def note_line(page: int) -> str:
    return (f"[scanned page: its text is OCR and may contain errors (letters and digits confused, symbols and "
            f"handwriting garbled); region p{page} shows the image]")


def mark_read(name: str, text: str) -> str:
    """Under OCR_NOTE: every scanned page's header in a read gets PAGE_MARK, and the first one is followed
    by note_line. Unchanged otherwise."""
    if not switches.on("OCR_NOTE"):
        return text
    marked: list[int] = []

    def mark(m: re.Match[str]) -> str:
        page = int(m.group(1))
        if not scanned(name, page):
            return m.group(0)
        line = f"== p{page}{m.group(2)}{PAGE_MARK} =="
        if not marked:
            line += "\n" + note_line(page)
        marked.append(page)
        return line

    out = _HEADER.sub(mark, text)
    if marked:
        tracing.guarded(_report_scan, text, out, marked)
    return out


def _report_scan(text: str, out: str, marked: list[int]) -> None:
    record.sig("scan", pages=marked)
    record.dec("scan_notes", src=switches.env_name("OCR_NOTE"), pages=marked, tokens=tracing.added(text, out))


def region_text_header(name: str, page: int, layer: bool) -> str:
    """The line before the text region() returns: the text layer's, or Docling's when the box has no layer.
    Under OCR_NOTE a scanned page's layer is called OCR, and Docling's fallback text always is (with no
    layer in the box, Docling could only read it from the image)."""
    if not switches.on("OCR_NOTE"):
        return LAYER if layer else PARSED
    if layer:
        return LAYER_OCR if scanned(name, page) else LAYER
    return PARSED_OCR
