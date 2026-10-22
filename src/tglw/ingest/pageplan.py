"""Classify every PDF page before conversion and pick its OCR setting (A5).

Runs on pypdfium2 alone, so it works both in the reader environment and in the Docling one.

Page kinds:
- text: a usable text layer; Docling reads it and only OCRs regions without text (picture text).
- scan_text: a full-page image carrying its own text layer (usually an invisible OCR layer, as in
  the NASA scans). The text layer is kept; Docling's OCR only fills gaps it does not cover, or is
  skipped (with_scan_text_ocr "off").
- scan: a full-page image without a text layer; the whole page is OCR'd.
- blank: a scan with (almost) no ink; still OCR'd (cheap; may hold a page number or a title), but
  an empty result is expected, not a defect.
- garbled: a born-digital text layer that does not decode: control or replacement characters, or
  Latin text with almost no vowels (fonts whose codes are shifted, e.g. "$FW" for "Act"). The whole
  page is OCR'd and the layer dropped. Private-use characters (Symbol/Wingdings bullets) and
  pdfium's Unicode-map errors are not signals: both are common on clean pages.
- sparse: little or no text and no full-page image (blank pages, vector drawings); left to Docling.

Script: "cjk" when CJK characters make up at least CJK_SHARE of the text layer's letters.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

SCAN_COVER = 0.6  # share of the page covered by image objects
MIN_CHARS = 20  # fewer non-space characters than this is "no text layer"
BAD_SHARE = 0.02  # control or replacement characters among non-space characters
VOWEL_SHARE = 0.27  # clean English pages: median 0.38, 1st percentile 0.33 (59 research PDFs)
MIN_LETTERS = 200
CJK_SHARE = 0.3
BLANK_INK = 0.0003  # dark-pixel share at half scale; NASA blank scans measure 0.0, a lone section title 0.0012
FULL_PAGE = "full_page"
DEFAULT = "default"
OFF = "off"
OCR_MODE = {"text": DEFAULT, "scan_text": DEFAULT, "scan": FULL_PAGE, "blank": FULL_PAGE, "garbled": FULL_PAGE,
            "sparse": DEFAULT}


@dataclass(frozen=True, slots=True)
class PagePlan:
    page: int
    kind: str
    script: str
    ocr: str
    chars: int
    bad_chars: int
    vowel_share: float | None
    cjk_chars: int
    image_cover: float
    invisible_text: bool
    ink: float | None = None


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or 0x20000 <= o <= 0x2FA1F
            or 0xF900 <= o <= 0xFAFF or 0x3040 <= o <= 0x30FF or 0xAC00 <= o <= 0xD7AF)


def _is_bad(ch: str) -> bool:
    return ch == "\ufffd" or (ord(ch) < 0x20 and ch not in "\t\n\r")


def _image_cover(page: pdfium.PdfPage, width: float, height: float) -> float:
    """Share of the page under image objects (bounding boxes, clipped, overlaps not merged)."""
    area = 0.0
    for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE], max_depth=4):
        left, bottom, right, top = obj.get_bounds()
        w = max(0.0, min(right, width) - max(left, 0.0))
        h = max(0.0, min(top, height) - max(bottom, 0.0))
        area += w * h
    return min(1.0, area / (width * height)) if width and height else 0.0


def _invisible_text(page: pdfium.PdfPage) -> bool:
    """True when most text objects use render mode 3 (invisible), the usual OCR-layer marker."""
    modes = [pdfium_c.FPDFTextObj_GetTextRenderMode(obj.raw)
             for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_TEXT], max_depth=4)]
    return bool(modes) and sum(m == pdfium_c.FPDF_TEXTRENDERMODE_INVISIBLE for m in modes) * 2 > len(modes)


def _vowel_share(letters: list[str]) -> float | None:
    latin = [c for c in letters if c.isascii() and c.isalpha()]
    return round(sum(c in "aeiouAEIOU" for c in latin) / len(latin), 3) if len(latin) >= MIN_LETTERS else None


def classify(page_no: int, page: pdfium.PdfPage) -> PagePlan:
    width, height = page.get_size()
    textpage = page.get_textpage()
    n = textpage.count_chars()
    text = textpage.get_text_range(0, n) if n else ""
    letters = [c for c in text if not c.isspace()]
    bad = sum(_is_bad(c) for c in letters)
    vowels = _vowel_share(letters)
    cjk = sum(_is_cjk(c) for c in letters)
    cover = _image_cover(page, width, height)
    invisible = _invisible_text(page)
    chars = len(letters)
    kind = kind_of(chars, bad, vowels, cover)
    ink = _ink(page) if kind == "scan" else None
    if ink is not None and ink < BLANK_INK:
        kind = "blank"
    script = "cjk" if letters and cjk >= CJK_SHARE * chars else "latin"
    return PagePlan(page_no, kind, script, OCR_MODE[kind], chars, bad, vowels, cjk, round(cover, 3), invisible, ink)


def _ink(page: pdfium.PdfPage) -> float:
    image = page.render(scale=0.5, grayscale=True).to_pil().convert("L")
    dark = sum(image.histogram()[:128])
    return round(dark / (image.width * image.height), 5)


def kind_of(chars: int, bad: int, vowels: float | None, cover: float) -> str:
    if cover >= SCAN_COVER:
        return "scan_text" if chars >= MIN_CHARS else "scan"
    if chars < MIN_CHARS:
        return "sparse"
    undecoded = bad >= BAD_SHARE * chars or (vowels is not None and vowels < VOWEL_SHARE)
    return "garbled" if undecoded else "text"


def plan(pdf_path: str) -> list[PagePlan]:
    pdf = pdfium.PdfDocument(pdf_path)
    try:
        return [classify(i + 1, pdf[i]) for i in range(len(pdf))]
    finally:
        pdf.close()


def with_scan_text_ocr(plans: list[PagePlan], mode: str) -> list[PagePlan]:
    """mode "fill" (Docling's default: OCR fills regions the page's own OCR layer misses) or "off"
    (keep only the page's own layer: no OCR cost, none of the misplaced duplicate OCR boxes)."""
    if mode not in ("fill", "off"):
        raise ValueError(f"scan_text OCR mode {mode!r}: use fill or off")
    return [replace(p, ocr=OFF) if mode == "off" and p.kind == "scan_text" else p for p in plans]


def as_json(plans: list[PagePlan]) -> list[dict]:
    return [asdict(p) for p in plans]
