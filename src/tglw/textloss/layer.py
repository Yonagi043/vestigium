"""PDF text layer per page via pypdfium2: words with boxes, line numbers, and a junk share."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Collection

import pypdfium2 as pdfium

BBox = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class Word:
    text: str
    bbox: BBox  # (l, t, r, b), PDF points, bottom-left origin, cropbox-relative
    line: int


@dataclass(frozen=True, slots=True)
class LayerPage:
    page: int
    width: float
    height: float
    rotation: int
    words: tuple[Word, ...]
    junk: float  # share of non-space chars that are control, private-use, or U+FFFD


def _is_junk(ch: str) -> bool:
    return ch == "\ufffd" or unicodedata.category(ch) in ("Cc", "Co", "Cs")


def _page_chars(tp: pdfium.PdfTextPage) -> str:
    n = tp.count_chars()
    text = tp.get_text_range(0, n) if n else ""
    if len(text) != n:
        text = "".join((tp.get_text_range(i, 1) or " ")[:1] for i in range(n))
    return text.replace("\x02", "-").replace("\ufffe", "-")


def _words(tp: pdfium.PdfTextPage, text: str, dx: float, dy: float) -> tuple[list[Word], float]:
    words: list[Word] = []
    cur: list[int] = []
    line = junk = solid = 0

    def flush() -> None:
        if not cur:
            return
        boxes = [tp.get_charbox(i) for i in cur]
        bbox = (min(b[0] for b in boxes) - dx, max(b[3] for b in boxes) - dy,
                max(b[2] for b in boxes) - dx, min(b[1] for b in boxes) - dy)
        words.append(Word("".join(text[i] for i in cur), bbox, line))
        cur.clear()

    for i, ch in enumerate(text):
        if ch.isspace():
            flush()
            line += ch == "\n"
            continue
        solid += 1
        junk += _is_junk(ch)
        cur.append(i)
    flush()
    return words, junk / solid if solid else 0.0


def read_layer(pdf_path: Path, pages: Collection[int] | None = None) -> list[LayerPage]:
    pdf = pdfium.PdfDocument(str(pdf_path))
    out: list[LayerPage] = []
    try:
        for i in range(len(pdf)):
            if pages is not None and i + 1 not in pages:
                continue
            page = pdf[i]
            l, b, _, _ = page.get_cropbox()
            tp = page.get_textpage()
            words, junk = _words(tp, _page_chars(tp), l, b)
            w, h = page.get_size()
            out.append(LayerPage(i + 1, w, h, page.get_rotation(), tuple(words), junk))
    finally:
        pdf.close()
    return out
