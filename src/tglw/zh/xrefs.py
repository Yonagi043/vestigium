"""Chinese cross-reference mentions (rules D5, for task C2).

Returns `tglw.xrefs.detect.Mention` objects with offsets in the ORIGINAL text, so they can be merged
with the English detector's output. Forms covered (simplified and traditional):

    见第三章 / 如第三章所述 / 本办法第二十条第一款 / 第十条第（三）项   -> section (qual zh_unit 章/条/…)
    详见本节十七、 / 第八节…七、                                          -> section, qual zh_unit "、"
    详见附注 40 / 附注五、12 / 財務資料附註2 / （附註9）                   -> note
    載於本通函附錄二 / 见附件1                                             -> appendix
    见表 3 / 如图 2-1 所示 / 附表1                                         -> table / figure
    第 7 页 / 第SGM-1至SGM-4頁                                             -> page
    详见“第三节 管理层讨论与分析” / 「代價及付款條款」一節                 -> section by quoted title
    《证券法》第一百九十一条 / 上市規則第14.07條                           -> qual "external"

A number at the very start of the text followed by a title is a heading or caption, not a reference.
"""

from __future__ import annotations

import re

from tglw.xrefs.detect import Mention

from .display import normalize_map
from .headings import MAX_APPENDIX
from .headings import parse as parse_heading
from .numerals import CHARS as _CN, cn_int

_NUM = rf"[0-9]{{1,4}}(?:\.[0-9]{{1,3}})*(?:\([0-9a-z]{{1,3}}\))*|[{_CN}]{{1,6}}"
_UNITS = {"编": "编", "編": "编", "部分": "部分", "篇": "篇", "章": "章", "节": "节", "節": "节",
          "条": "条", "條": "条", "款": "款", "项": "项", "項": "项"}
_ORD = rf"第\s*(?:\(\s*)?({_NUM})(?:\s*\))?\s*(部分|编|編|篇|章|节|節|条|條|款|项|項)"
_CHAIN = re.compile(rf"{_ORD}((?:\s*{_ORD})*)")
_ORD_ONE = re.compile(_ORD)
_PAGE_KEY = r"[A-Z]{1,4}-?[0-9]{1,4}|[IVX]{1,6}-[0-9]{1,4}|[0-9]{1,4}|[ivx]{1,6}"
_PAGE = re.compile(rf"第\s*({_PAGE_KEY})(?:\s*(?:至|-|~|～|—)\s*(?:第\s*)?({_PAGE_KEY}))?\s*[页頁]")
_NOTE = re.compile(
    rf"(附\s*注|附\s*註)\s*(?P<op>\(\s*)?([{_CN}]{{1,4}}\s*、\s*[0-9]{{1,3}}(?![0-9])|[{_CN}]{{1,4}}(?=\s*[(、])|[0-9]{{1,3}}(?:\.[0-9]{{1,3}})*(?![0-9]))"
    rf"(?(op)\s*\))(?:\s*\(\s*([0-9]{{1,3}}|[{_CN}]{{1,3}})\s*\))?")
_APPENDIX = re.compile(rf"(附\s*录|附\s*錄|附\s*件)\s*([{_CN}]{{1,4}}|[0-9]{{1,2}}|[A-Z](?![A-Za-z]))")
_TABFIG = re.compile(r"(附表|表|图|圖)\s*([0-9]{1,3}(?:\s*[-.]\s*[0-9]{1,3})*)(?![0-9])")
_TABFIG_BEFORE = set("见見如附下上据據按照参參和与與及、在由从從于於(,:;") | {" "}
_TRIGGER = r"(?:参见|參見|详见|詳見|请参阅|請參閱|参阅|參閱|参照|參照|载于|載於|列于|列於|載列於|见|見)"
_DOC_SELF = r"(?:本报告|本報告|本通函|本文件|本招股章程|本招股书|本招股書|本公告|本年报|本年報|下文|上文|本节|本節)"
_QUOTED = re.compile(
    rf"(?:({_TRIGGER})\s*(?:{_DOC_SELF})?\s*)?[“「『\"]([^“”「」『』\"]{{2,60}})[”」』\"]\s*(一节|一節|一章|章节|章節|部分|节|節|章)?")
_LIST_REF = re.compile(rf"({_TRIGGER}\s*(?:本节|本節|本章)\s*)([{_CN}]{{1,4}})\s*、")
_LAW_SUFFIX = r"(?:法|條例|条例|規則|规则|守則|守则|準則|准则|辦法|办法|細則|细则|指引)"
_EXT_NAME = re.compile(rf"(?:《[^《》]{{2,40}}》|[一-鿿]{{0,12}}{_LAW_SUFFIX})(?:\s*\([^()]{{1,12}}\))?\s*$")
_SELF_LAW = re.compile(rf"本{_LAW_SUFFIX}(?:\s*\([^()]{{1,12}}\))?\s*$")
_ABOVE = re.compile(r"(?:上文|上述|前述|前文)\s*$")
_BELOW = re.compile(r"(?:下文|下述|后文|後文)\s*$")
_LEAD_VERB = re.compile(r"^.*?(?:根据|根據|依照|依据|依據|按照|遵照|遵守|违反|違反|参照|參照|适用|適用|发生|發生|按|依|据|據|照)")
_ACCOUNTANTS = re.compile(r"(?:會計師報告|会计师报告)")
_FS = re.compile(r"(?:財務報表|财务报表|財務資料|财务资料)")


def _key(tok: str) -> str:
    tok = re.sub(r"\s", "", tok)
    v = cn_int(tok)
    return str(v) if v is not None else tok


def _note_key(main: str, sub: str | None) -> str:
    parts = [p for p in re.split(r"[、.]", re.sub(r"\s", "", main)) if p]
    if sub:
        parts.append(sub)
    return ".".join(_key(p) for p in parts)


class _Text:
    def __init__(self, text: str) -> None:
        self.raw = text
        self.s, self.idx = normalize_map(text, nfkc=True)

    def mention(self, kind: str, key: str, a: int, b: int, qual: dict[str, str]) -> Mention:
        start, end = self.idx[a], self.idx[b - 1] + 1
        return Mention(kind, key, self.raw[start:end], start, end, qual)


_HAN1 = re.compile(r"[一-鿿]")


def _is_label(t: _Text, a: int, b: int | None = None) -> bool:
    """A heading or caption number, not a reference: it starts the text and the text parses as a
    heading/caption; or (when `b` is given) it is followed by a space and a title and preceded by the
    start, a sentence end, a digit or a leader dot (table of contents, run-in article headings)."""
    if not t.s[:a].strip():
        return parse_heading(t.raw) is not None or bool(re.match(r"\s*\S+\s+\S", t.s[a:]))
    if b is None:
        return False
    end = t.idx[b - 1] + 1
    if not re.match(r"[ \t　]+[一-鿿]", t.raw[end:end + 3]):
        return False
    prev = t.s[:a].rstrip()[-1:]
    return not _HAN1.match(prev) and prev not in "《》「」“”()（）、，,"


def _external(t: _Text, a: int) -> str | None:
    """The other document named right before an article/chapter number: 《证券法》, 上市規則 (not 本办法)."""
    before = t.s[max(0, a - 40):a]
    m = _EXT_NAME.search(before)
    if not m or _SELF_LAW.search(before):
        return None
    name = m.group(0).strip()
    return name if name.startswith("《") else _LEAD_VERB.sub("", name) or name


def _where(t: _Text, a: int) -> dict[str, str]:
    """"above"/"below" when the mention follows 上文/上述 or 下文/下述 ("見上文附註(11)")."""
    before = t.s[max(0, a - 8):a]
    if _ABOVE.search(before):
        return {"where": "above"}
    return {"where": "below"} if _BELOW.search(before) else {}


def _ordinals(t: _Text) -> list[Mention]:
    out = []
    for m in _CHAIN.finditer(t.s):
        if _is_label(t, m.start(), m.end()):
            continue
        unit = _UNITS[m.group(2)]
        qual = {"label": f"第{unit}", "zh_unit": unit}
        for sub in _ORD_ONE.finditer(m.group(3) or ""):
            qual[{"条": "article", "款": "clause", "项": "item", "节": "zh_section"}.get(_UNITS[sub.group(2)], "sub")] = \
                _key(sub.group(1))
        if unit == "章":
            qual["chapter"] = "1"
        if ext := _external(t, m.start()):
            qual["external"] = ext
        out.append(t.mention("section", _key(m.group(1)), m.start(), m.end(), qual))
    return out


def _pages(t: _Text) -> list[Mention]:
    out = []
    for m in _PAGE.finditer(t.s):
        around = t.s[max(0, m.start() - 2):m.end() + 2]
        if "共" in around:
            continue
        qual = {"label": "第页"}
        if m.group(2):
            qual["to"] = m.group(2)
        out.append(t.mention("page", m.group(1), m.start(), m.end(), qual))
    return out


def _notes(t: _Text) -> list[Mention]:
    out = []
    for m in _NOTE.finditer(t.s):
        if re.match(r"\s*(?::|[.、]\s*[一-鿿])", t.s[m.end():]):
            continue
        qual = {"label": re.sub(r"\s", "", m.group(1))}
        if t.s[m.start() - 1:m.start()] == "(" and t.s[m.end():m.end() + 1] == ")":
            qual["paren"] = "1"
        before = t.s[max(0, m.start() - 16):m.start()]
        if _ACCOUNTANTS.search(before):
            qual["scope"] = "accountants"
        elif _FS.search(before):
            qual["scope"] = "fs"
        if am := _APPENDIX.search(before):
            qual["appendix"] = _key(am.group(2))
        qual |= _where(t, m.start())
        out.append(t.mention("note", _note_key(m.group(3), m.group(4)), m.start(), m.end(), qual))
    return out


def _appendices(t: _Text) -> list[Mention]:
    out = []
    for m in _APPENDIX.finditer(t.s):
        if _is_label(t, m.start(), m.end()) or t.s[m.end():m.end() + 1] in ("所", "的"):
            continue
        tok, end = m.group(2), m.end()
        while (v := cn_int(tok)) and v > MAX_APPENDIX and len(tok) > 1:
            tok, end = tok[:-1], end - 1
        out.append(t.mention("appendix", _key(tok), m.start(), end,
                             {"label": re.sub(r"\s", "", m.group(1))} | _where(t, m.start())))
    return out


def _tables_figures(t: _Text) -> list[Mention]:
    out = []
    for m in _TABFIG.finditer(t.s):
        prev = t.s[m.start() - 1:m.start()]
        if (m.start() > 0 and prev not in _TABFIG_BEFORE) or _is_label(t, m.start()):
            continue
        kind = "figure" if m.group(1) in ("图", "圖") else "table"
        key = re.sub(r"\s", "", m.group(2))
        out.append(t.mention(kind, key, m.start(), m.end(), {"label": m.group(1)}))
    return out


def _quoted(t: _Text) -> list[Mention]:
    out = []
    for m in _QUOTED.finditer(t.s):
        if not (m.group(1) or m.group(3)):
            continue
        title = re.sub(r"\s+", " ", m.group(2)).strip()
        qual = {"label": "quoted"}
        if m.group(3):
            qual["zh_unit"] = m.group(3)
        if (h := parse_heading(title)) is not None:
            qual["zh_number"] = f"{h.style}:{h.key}"
        out.append(t.mention("section", title, m.start(), m.end(), qual))
    return out


def _list_refs(t: _Text) -> list[Mention]:
    out = []
    for m in _LIST_REF.finditer(t.s):
        v = cn_int(m.group(2))
        if v:
            out.append(t.mention("section", str(v), m.start(2), m.end(), {"label": "、", "zh_unit": "、", "scope": "本节"}))
    return out


def find_mentions(text: str) -> list[Mention]:
    """All Chinese cross-reference mentions in `text`, sorted by position; empty for text without
    Chinese characters."""
    if not re.search(r"[一-鿿]", text):
        return []
    t = _Text(text)
    found = _ordinals(t) + _pages(t) + _notes(t) + _appendices(t) + _tables_figures(t) + _quoted(t) + _list_refs(t)
    return sorted(found, key=lambda x: (x.start, x.end))
