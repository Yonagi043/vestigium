"""Chinese section numbers at the start of a heading (rules D2, for task A).

    第一编 / 第一部分 / 第一篇   -> style "zh_part"      第一章 -> "zh_chapter"   第一节 -> "zh_section"
    第一条                       -> "zh_article"        附录一 / 附錄一 / 附件1 -> "zh_appendix"
    一、                         -> "zh_list"           （一） / (一) -> "zh_paren"
    1、 / 1．                    -> "zh_arabic"         （1） / (1) -> "zh_arabic_paren"
    1.1 / 3.2.1 + Chinese title  -> "dotted"            ① -> "zh_circled"

`parse` reads the number and title of one heading; it does not decide whether a text block is a heading.
Within one document the styles nest in the order of RANK (smaller is higher), which matches both laws
(编 > 章 > 节 > 条 > (一)项 > 1.目) and annual reports (第X节 > 一、 > (一) > 1、 > (1)).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .display import normalize_display
from .numerals import CHARS as _CN, cn_int

RANK = {
    "zh_part": 0, "zh_appendix": 0, "zh_chapter": 1, "zh_section": 2, "zh_article": 3, "dotted": 4,
    "zh_list": 5, "zh_paren": 6, "zh_arabic": 7, "zh_arabic_paren": 8, "zh_circled": 9,
}
UNIT_STYLE = {
    "编": "zh_part", "編": "zh_part", "部分": "zh_part", "篇": "zh_part", "章": "zh_chapter",
    "节": "zh_section", "節": "zh_section", "条": "zh_article", "條": "zh_article",
}
KIND = {
    "zh_part": "part", "zh_chapter": "chapter", "zh_section": "zh_section", "zh_article": "zh_article",
    "zh_appendix": "appendix", "dotted": "dotted", "zh_list": "zh_list", "zh_paren": "zh_paren",
    "zh_arabic": "zh_arabic", "zh_arabic_paren": "zh_arabic_paren", "zh_circled": "zh_circled",
}
_NUM = rf"[0-9]{{1,4}}|[{_CN}](?:\s?[{_CN}]){{0,5}}"
_ORDINAL = re.compile(rf"^(第\s*({_NUM})\s*(部分|编|編|章|节|節|条|條|篇))(\s*[、:.]?\s*)(.*)$", re.S)
_APPENDIX = re.compile(rf"^((?:附\s?录|附\s?錄|附\s?件)\s*([0-9]{{1,2}}|[{_CN}]{{1,4}}|[A-Z]))(?![0-9A-Za-z])\s*[-–—－:、.]?\s*(.*)$", re.S)
MAX_APPENDIX = 20
_LIST = re.compile(rf"^(([{_CN}](?:\s?[{_CN}]){{0,3}})\s*、)\s*(.*)$", re.S)
_PAREN = re.compile(rf"^(\(\s*({_NUM})\s*\))\s*[.、]?\s*(.*)$", re.S)
_ARABIC = re.compile(r"^(([0-9]{1,3})\s*([、.]))\s*(.*)$", re.S)
_DOTTED = re.compile(r"^(([0-9]{1,2})((?:\s?\.\s?[0-9]{1,3}){1,4}))\.?\s*(.*)$", re.S)
_CIRCLED = re.compile(r"^([①-⑳])\s*(.*)$", re.S)
_HAN_START = re.compile(r"^[一-鿿㐀-䶿「《“]")
_TITLE_STOP = re.compile(r"^(?:所(?:述|称|稱|列|载|載|指|示|规定|規定|说|說|提|附|涉)|规定|規定|的|之|中|至|及|和|与|與|或|项|項|款|下|第|、"
                         r"|，|,|。|；|;|等|内|內|起|后|後|前|以|规|規|载|載|列)")
_NUMERIC_TITLE = re.compile(r"^(?:[0-9%‰]|[亿億万萬千百元倍个個年月日号號点點])")


@dataclass(frozen=True, slots=True)
class ZhNum:
    style: str
    value: int | str
    raw: str
    title: str

    @property
    def rank(self) -> int:
        return RANK[self.style]

    @property
    def kind(self) -> str:
        return KIND[self.style]

    @property
    def key(self) -> str:
        return str(self.value)


def clean(text: str) -> str:
    """NFKC except circled numbers (（ -> (, ： -> :, １ -> 1); spaces are kept so that a number is
    never glued to its title. Titles are cleaned separately with `normalize_display`."""
    return "".join(c if "①" <= c <= "⑳" else unicodedata.normalize("NFKC", c) for c in text).strip()


def _num(tok: str) -> int | None:
    return cn_int(re.sub(r"\s", "", tok))


_LEADER = re.compile(r"\s*(?:\.{3,}|…{2,}|(?:\.\s){3,})[\s.…]*[\dA-Za-z\-]*\s*$")


def _title(t: str) -> str:
    """Title text: Chinese spacing removed; a table-of-contents leader and page number dropped."""
    return normalize_display(_LEADER.sub("", t.strip()))


def _title_ok(title: str, allow_empty: bool = False, arabic: bool = False) -> bool:
    """A title, not the rest of a reference ("第三条规定的…") or of a number ("1.5 亿元", "2.35%")."""
    if not title:
        return allow_empty
    return not (_TITLE_STOP.match(title) or (arabic and _NUMERIC_TITLE.match(title)))


def _ordinal(s: str) -> ZhNum | None:
    m = _ORDINAL.match(s)
    if not m:
        return None
    value, title = _num(m.group(2)), _title(m.group(5))
    separated = bool(m.group(4)) and not re.search(r"\s", m.group(1))
    if not value or not (separated or _title_ok(title, allow_empty=True)):
        return None
    return ZhNum(UNIT_STYLE[m.group(3)], value, m.group(1).replace(" ", ""), title)


def _appendix(s: str) -> ZhNum | None:
    m = _APPENDIX.match(s)
    if not m:
        return None
    tok, rest = m.group(2), m.group(3)
    if tok.isalpha() and tok.isascii():
        value: int | str | None = tok
    else:
        value = _num(tok)
        while value and value > MAX_APPENDIX and len(tok) > 1:
            tok, rest = tok[:-1], tok[-1] + rest
            value = _num(tok)
    title = _title(rest)
    if not value or not _title_ok(title, allow_empty=True):
        return None
    raw = m.group(1).replace(" ", "")
    return ZhNum("zh_appendix", value, raw[: len(raw) - len(m.group(2)) + len(tok)], title)


def _simple(s: str) -> ZhNum | None:
    for rx, style in ((_LIST, "zh_list"), (_PAREN, None), (_ARABIC, "zh_arabic")):
        m = rx.match(s)
        if not m:
            continue
        tok, title = m.group(2), _title(m.group(m.lastindex))
        value = _num(tok)
        if not value or value > 200 or not title or (tok.isdigit() and _NUMERIC_TITLE.match(title)):
            return None
        if style == "zh_arabic" and m.group(3) == "." and not _HAN_START.match(title):
            return None
        if style is None:
            style = "zh_arabic_paren" if tok.isdigit() else "zh_paren"
        if style == "zh_list" and re.match(rf"^[{_CN}]{{1,4}}\s*、", title):
            return None
        return ZhNum(style, value, m.group(1).replace(" ", ""), title)
    return None


def _dotted(s: str) -> ZhNum | None:
    m = _DOTTED.match(s)
    if not m:
        return None
    title = _title(m.group(4))
    if not title or not re.match(r"^[一-鿿㐀-䶿「《“\"A-Za-z]", title) or not _title_ok(title, arabic=True):
        return None
    key = ".".join([str(int(m.group(2)))] + [str(int(p)) for p in re.findall(r"[0-9]+", m.group(3))])
    return ZhNum("dotted", key, m.group(1).replace(" ", ""), title)


def _circled(s: str) -> ZhNum | None:
    m = _CIRCLED.match(s)
    if not m or not _title_ok(_title(m.group(2))):
        return None
    return ZhNum("zh_circled", ord(m.group(1)) - 0x245F, m.group(1), _title(m.group(2)))


def parse(text: str) -> ZhNum | None:
    """The Chinese section number at the start of a heading, or None. Rejects references that only
    look like headings ("第十四章項下之…", "第三条规定的…"), enumerations ("一、二、三级"), and
    numbers that start a wrapped line of running text ("1.57%，…", "2.35 亿元")."""
    s = clean(text)
    if not s:
        return None
    if "①" <= s[0] <= "⑳":
        return _circled(s)
    if s[0] == "第":
        return _ordinal(s)
    if s[0] == "附":
        return _appendix(s)
    return _simple(s) or _dotted(s)


def to_num(z: ZhNum):
    """The same number as task A's `tglw.structure.numbering.Num` (family = style, or "dec" for 1.1-style numbers; key split on dots)."""
    from tglw.structure.numbering import Num

    return Num("dec" if z.style == "dotted" else z.style, tuple(z.key.split(".")), z.raw, z.title, kind=z.kind)
