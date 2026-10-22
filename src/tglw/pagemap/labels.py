"""Parse printed page numbers ("Page 12", "- iii -", "A-3", "2010-6") into a canonical label key.

Canonical key: decorations ("Page", "- x -", "of N") removed, dashes unified to "-", no spaces
around the dash, case kept ("I" and "i" are different labels). Chinese forms: "第 3 页", "第三頁",
"第3页 共120页", "共120页 第3页", and "3 / 143" (A-share annual reports) give "3".
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from tglw.zh.numerals import cn_int

_DASHES = str.maketrans({c: "-" for c in "‐‑‒–—―−﹣－"})
_LEAD_WORD = re.compile(r"^(?:page|pg\.?|p\.)\s*(?=\S)", re.I)
_OF_N = re.compile(r"\s+of\s+\d{1,4}$", re.I)
_EDGE = " \t-|•·*~_.,:;()[]{}\"'"
_DASH_WS = re.compile(r"\s*-\s*")
_ROMAN = re.compile(r"^m{0,3}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$")
_ARABIC = re.compile(r"^\d{1,4}$")
_PREFIXED = re.compile(r"^([A-Z][A-Za-z]{0,11}|\d{1,4})-(\d{1,4})$")
_ZH_PAGE = re.compile(r"^(?:共\s*\S{1,6}?\s*[页頁]\s*[,、/|]?\s*)?第\s*(\S{1,6}?)\s*[页頁]"
                      r"(?:\s*[,、/|]?\s*共\s*\S{1,6}?\s*[页頁])?$")
_SLASH_OF = re.compile(r"^(\d{1,4})\s*/\s*(\d{1,4})$")
_OCR_DIGITS = re.compile(r"^(?=.*\d)[\dOoIl ]+$")
_OCR_ROMAN = re.compile(r"^[ivxlcdm](?: ?[ivxlcdm])+$|^[IVXLCDM](?: ?[IVXLCDM])+$")
MAX_ROMAN = 300
_ROMAN_VALUES = (
    (1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
    (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"),
)


@dataclass(frozen=True, slots=True)
class PageNum:
    scheme: str  # "arabic" | "roman" | "prefixed"
    value: int
    prefix: str = ""
    upper: bool = False

    @property
    def label(self) -> str:
        if self.scheme == "arabic":
            return str(self.value)
        if self.scheme == "roman":
            r = to_roman(self.value)
            return r.upper() if self.upper else r
        return f"{self.prefix}-{self.value}"

    @property
    def family(self) -> tuple[str, str]:
        return (self.scheme, self.prefix)

    def shifted(self, delta: int) -> PageNum | None:
        v = self.value + delta
        return PageNum(self.scheme, v, self.prefix, self.upper) if v >= 1 else None


def to_roman(n: int) -> str:
    out = []
    for v, s in _ROMAN_VALUES:
        while n >= v:
            out.append(s)
            n -= v
    return "".join(out)


def from_roman(s: str) -> int | None:
    s = s.lower()
    if not s or not _ROMAN.match(s):
        return None
    total, i = 0, 0
    for v, sym in _ROMAN_VALUES:
        while s.startswith(sym, i):
            total += v
            i += len(sym)
    return total or None


def _zh(s: str) -> str:
    """'第三页 共12页' -> '3'; '3 / 143' -> '3'; anything else unchanged."""
    if m := _ZH_PAGE.match(s):
        v = cn_int(m.group(1))
        return str(v) if v is not None else m.group(1)
    if (m := _SLASH_OF.match(s)) and 1 <= int(m.group(1)) <= int(m.group(2)):
        return m.group(1)
    return s


def _clean(text: str) -> str:
    s = _zh(unicodedata.normalize("NFKC", text).translate(_DASHES).strip())
    s = _OF_N.sub("", s)
    s = _LEAD_WORD.sub("", s)
    s = s.strip(_EDGE)
    return _DASH_WS.sub("-", s)


def _ocr_fix(s: str) -> str:
    if _OCR_DIGITS.match(s):
        return s.replace(" ", "").translate(str.maketrans("OoIl", "0011"))
    if _OCR_ROMAN.match(s):
        return s.replace(" ", "")
    return s


def parse(text: str) -> PageNum | None:
    """Parse one page-number string; None when it does not look like a page number."""
    s = _ocr_fix(_clean(text))
    if _ARABIC.match(s):
        v = int(s)
        return PageNum("arabic", v) if v >= 1 else None
    if s.isalpha() and (s.islower() or s.isupper()):
        v = from_roman(s)
        return PageNum("roman", v, upper=s.isupper()) if v and v <= MAX_ROMAN else None
    m = _PREFIXED.match(s)
    if m and int(m.group(2)) >= 1:
        return PageNum("prefixed", int(m.group(2)), m.group(1))
    return None


def normalize_label(text: str) -> str | None:
    """Canonical label key for a printed page number, e.g. "- VI - 4 -" -> "VI-4"."""
    n = parse(text)
    return n.label if n else None
