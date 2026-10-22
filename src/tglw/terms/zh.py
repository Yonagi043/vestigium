"""Chinese definitions (rules D4): 释义 lists and in-text definitions.

Collections ("definitions_chapter"):
    「本公司」 / 指 萬達酒店發展有限公司…        two text blocks (HK circulars)
    本公司、公司 | 指 | 贵州茅台酒股份有限公司     a table whose middle column is 指 (A-share 释义)
    「EV」 指 企業價值                              one block
In text ("inline"):
    中国证券监督管理委员会（以下简称中国证监会）  / （下称“X”） / （简称“X”）     via zh_abbrev
    萬達酒店發展有限公司（「本公司」）                                              via zh_quoted
    交易日，是指… / 子公司是指… / 本办法所称X，是指… / （二）X，是指… / X系指…        via zh_means

Only documents that `tglw.zh.is_cjk_doc` accepts are scanned, so English documents are unaffected.
"""

from __future__ import annotations

import re

from tglw.common.bundle import Block, Doc, table_grid
from tglw.common.text import norm
from tglw.zh.display import normalize_display

from .inline import _page_map
from .model import Definition
from .parse import clean
from .sections import Section

_HAN = "一-鿿㐀-䶿"
_Q_OPEN, _Q_CLOSE = "「『\"《", "」』\"》"
_QTERM = re.compile(rf"^[{_Q_OPEN}]([^{_Q_CLOSE}]{{1,40}})[{_Q_CLOSE}]$")
_PAIR_LINE = re.compile(rf"^[{_Q_OPEN}]([^{_Q_CLOSE}]{{1,40}})[{_Q_CLOSE}]\s*指\s+(\S.*)$", re.S)
_PLAIN_LINE = re.compile(r"^((?:[^\s,。;:]\s?){1,30}?(?:、\s?(?:[^\s,。;:]\s?){1,20}?)*)\s{2,}指\s{2,}(\S.*)$|"
                         r"^([^\s,。;:]{1,30}(?:、[^\s,。;:]{1,20})*)\s+指\s+(\S.*)$", re.S)
_DEF_START = re.compile(r"^指\s+(\S.*)$", re.S)
_TITLE = re.compile(r"^(?:第[一二三四五六七八九十]+[节節章]\s*|[一二三四五六七八九十]+、\s*)?"
                    r"(?:常用词语|常用詞語|技术|技術|专业|專業)?(?:释义|釋義|词汇|詞彙|词汇表|詞彙表|术语|術語|定义|定義)(?:表)?$")
_SPLIT_TERMS = re.compile(r"\s*(?:、|或)\s*")
MAX_GAP = 6

_ABBREV = re.compile(
    r"\((?:以下|下文)?(?:简称|簡稱|统称|統稱|合称|合稱|称|稱)(?:为|為)?\s*:?\s*"
    rf"((?:[{_Q_OPEN}][^{_Q_CLOSE}()]{{1,30}}[{_Q_CLOSE}]|[^\s()\"「」,。;]{{1,20}})"
    rf"(?:\s*(?:或|及|、)\s*(?:[{_Q_OPEN}][^{_Q_CLOSE}()]{{1,30}}[{_Q_CLOSE}]|[^\s()\"「」,。;]{{1,20}}))*)\s*\)")
_HKQUOTE = re.compile(
    r"\(((?:\s*(?:或|及|和|、|,|連同|连同|統稱|统称|合稱|合称|各稱|各称|各自稱為|各自称为|各為|各为)?\s*"
    r"[「\"][^」\"()]{1,30}[」\"])+)\s*\)")
_QUOTED_IN = re.compile(rf"[{_Q_OPEN}]([^{_Q_CLOSE}]{{1,30}})[{_Q_CLOSE}]")
_MEANS = re.compile(r"(?:是指|系指|係指|乃指)")
_SO_CALLED = re.compile(r"^(?:本(?:办法|辦法|法|规定|規定|规则|規則|条例|條例|指引|细则|細則|章程|准则|準則|报告|報告|通函|公告)(?:中)?)?所[称稱]")
_ADVERB = re.compile(r"(?:通常|一般|主要|均|亦|也|则|則|仅|僅|在本报告中|在本通函中)$")
_WITH_PREV = re.compile(r"^(?:連同|连同|及其|和其|与其|與其|以及其)")
_BAD_TERM = re.compile(r"[,。;:!?]|^\d+$")
MAX_TERM = 20
MAX_MEANS_TERM = 40
MAX_NP = 40


def _strip_q(s: str) -> str:
    return s.strip().strip("「」『』\"《》“”").strip()


def _terms(cell: str) -> list[str]:
    out = []
    for t in _SPLIT_TERMS.split(normalize_display(clean(cell))):
        t = _strip_q(t)
        if t and t not in out and len(t) <= MAX_TERM:
            out.append(t)
    return out


def _pair(term_cell: str, definition: str, ref: str, page: int, order: int, via: str,
          **extra) -> Definition | None:
    terms = _terms(term_cell)
    d = normalize_display(clean(definition))
    if not terms or not d or len(d) < 1:
        return None
    return Definition(term=terms[0], variants=terms[1:], definition=d, ref=ref, page=page,
                      source="definitions_chapter", order=order, extra={"via": via, **extra})


def _table_pairs(doc: Doc, b: Block) -> list[Definition]:
    out: list[Definition] = []
    for i, row in enumerate(table_grid(doc.item(b.ref))):
        cells = [clean(c) for c in row]
        if len(cells) >= 3 and cells[1] in ("指", "指:", "係指", "系指", "是指"):
            term, definition = cells[0], " ".join(c for c in cells[2:] if c)
        elif len(cells) == 2 and _QTERM.match(cells[0]) and (m := re.match(r"^指\s*(\S.*)$", cells[1], re.S)):
            term, definition = cells[0], m.group(1)
        else:
            if out and not cells[0] and any(cells[1:]):
                out[-1].definition += normalize_display(" ".join(c for c in cells[1:] if c and c != "指"))
            continue
        if term and (d := _pair(term, definition, b.ref, b.page, b.order, "zh_table", row=i)):
            out.append(d)
    return out


def _spaced(s: str) -> str:
    """NFKC and quote normalisation, keeping the spaces around 指 that `clean` would drop."""
    return norm(s, lower=False)


def _block_pairs(blocks: list[Block], i: int, in_list: bool) -> tuple[Definition | None, int]:
    """A pair starting at blocks[i] and the number of blocks it uses (0 if none)."""
    b = blocks[i]
    text = _spaced(b.text)
    if m := _PAIR_LINE.match(text):
        return _pair(m.group(1), m.group(2), b.ref, b.page, b.order, "zh_line"), 1
    if in_list and (m := _PLAIN_LINE.match(text)):
        return _pair(m.group(1) or m.group(3), m.group(2) or m.group(4), b.ref, b.page, b.order, "zh_line"), 1
    if (m := _QTERM.match(clean(b.text))) and i + 1 < len(blocks):
        nxt = blocks[i + 1]
        if nxt.kind == "text" and (dm := _DEF_START.match(_spaced(nxt.text))):
            return _pair(m.group(1), dm.group(1), nxt.ref, nxt.page, b.order, "zh_pair", term_ref=b.ref), 2
    return None, 0


def _is_title(b: Block) -> bool:
    return b.kind == "text" and bool(_TITLE.match(normalize_display(clean(b.text)).replace(" ", "")))


def find_zh_sections(doc: Doc) -> list[Section]:
    """Runs of 指-pairs; a run is one collection, titled by the 释义 heading right before it (if any)."""
    blocks = [b for b in doc.blocks if b.layer == "body" and not b.in_picture and b.kind in ("text", "table")]
    sections: list[Section] = []
    sec: Section | None = None
    title: Block | None = None
    gap = 0
    i = 0
    while i < len(blocks):
        b = blocks[i]
        if _is_title(b):
            title = b
            if sec:
                sec.refs.add(b.ref)
            i += 1
            continue
        pairs, used = (_table_pairs(doc, b), 1) if b.kind == "table" else (None, 0)
        if b.kind != "table":
            d, used = _block_pairs(blocks, i, sec is not None or title is not None)
            pairs = [d] if d else []
            used = used or 1
        if pairs:
            if sec is None:
                head = title or b
                sec = Section(source="definitions_chapter", title=normalize_display(head.text) if title else "",
                              title_ref=head.ref, titles=set())
                if title:
                    sec.refs.add(title.ref)
                sections.append(sec)
            for d in pairs:
                d.section = sec.title_ref
            sec.pairs += pairs
            sec.refs.update(x.ref for x in blocks[i:i + used])
            sec.pages = sorted(set(sec.pages) | {x.page for x in blocks[i:i + used] if x.page})
            sec.rows_seen += len(pairs) if b.kind == "table" else 0
            gap = 0
        elif sec is not None:
            gap += 1
            if gap > MAX_GAP:
                sec, title, gap = None, None, 0
        elif title is not None and b.page and title.page and b.page - title.page > 1:
            title = None
        i += used
    return [s for s in sections if len(s.pairs) >= 2]


_STOPS = frozenset("。;:!?「」\"《》")
_TERM_STOPS = frozenset("。;:!?")
_LIST_MARK = re.compile(r"^(?:\([一二三四五六七八九十0-9]{1,3}\)\.?|[0-9]{1,2}[.、](?![0-9])|[一二三四五六七八九十]{1,3}、)\s*")
_NAME_END = re.compile(r"(?:公司|集團|集团|委員會|委员会|銀行|银行|規則|规则|協議|协议|法|條例|条例|局|部|署|院|所|會|会|辦法|办法|基金|中心|項目|项目)$")
_CUT = re.compile(r"根据|根據|依照|按照|依据|依據|通过|通過|透過|关于|關於|对于|對於|擔任|担任|包括|授權|授权|通告|訂立|订立|委任|"
                  r"載列|载列|有關|有关|參考|参考|舉行|举行|的|之|於|于|為|为|是|即|以|就|在|由|向|與|与|對|对|將|将|經|经|"
                  r"從|从|把|被|按|乃|了")
_DEF_GROUP = re.compile(r"\([「\"][^」\"]{1,30}[」\"]\)|\((?:以下|下文)?(?:简称|簡稱|统称|統稱|合称|合稱|称|稱)[^()]{1,32}\)")
_PREV_GROUP = re.compile(r"\([「\"]([^」\"]{1,30})[」\"]\)\s*((?:及|和|與|与|連同|连同)其\S*)$")


def _phrase_start(text: str, end: int, stops: frozenset[str] = _STOPS) -> int:
    """Start of the phrase ending at `end`: back to a clause stop, an unmatched "(", or a comma that is
    not a thousands separator; balanced (…) groups such as 萬達酒店管理(香港)有限公司 are kept whole."""
    depth, i = 0, end - 1
    while i >= 0:
        c = text[i]
        if c == ")":
            depth += 1
        elif c == "(":
            if depth == 0:
                return i + 1
            depth -= 1
        elif depth == 0 and (c in stops or (c == "," and not (text[i - 1:i].isdigit() and text[i + 1:i + 2].isdigit()))):
            return i + 1
        i -= 1
    return 0


def _definiens(text: str, end: int, prev_term: str | None) -> tuple[str, bool]:
    """Noun phrase right before an abbreviation parenthesis, and whether the cut is trustworthy."""
    before = text[:end].rstrip()
    if before.endswith("》") and (k := before.rfind("《")) >= 0:
        return before[k + 1:-1], True
    np = _LIST_MARK.sub("", before[_phrase_start(before, len(before)):].strip())
    if prev_term and _WITH_PREV.match(np):
        return prev_term + np, True
    if m := _PREV_GROUP.search(np):
        return m.group(1) + m.group(2), True
    if groups := list(_DEF_GROUP.finditer(np)):
        np = re.sub(r"^(?:和|及|与|與|、|以及)\s*", "", np[groups[-1].end():].strip())
    sure = True
    cuts = [m.end() for m in _CUT.finditer(np) if len(np) - m.end() >= 2]
    if cuts:
        np, sure = np[cuts[-1]:], bool(_NAME_END.search(np[cuts[-1]:]))
    if not np or len(np) > MAX_NP:
        return np[-MAX_NP:], False
    return np, sure


def _inline_abbrev(b: Block, text: str, page_at) -> list[Definition]:
    out: list[Definition] = []
    prev: str | None = None
    for rx, via in ((_ABBREV, "zh_abbrev"), (_HKQUOTE, "zh_quoted")):
        for m in rx.finditer(text):
            inner = m.group(1)
            names = [_strip_q(q) for q in _QUOTED_IN.findall(inner)] if via == "zh_quoted" else \
                [_strip_q(x) for x in re.split(r"\s*(?:或|及|、)\s*", inner) if _strip_q(x)]
            names = [n for n in names if n and len(n) <= MAX_TERM]
            if not names:
                continue
            np, sure = _definiens(text, m.start(), prev)
            if not np or not re.search(f"[{_HAN}A-Za-z]", np):
                continue
            extra = {"via": via} if sure else {"via": via, "uncertain": True, "context": text[max(0, m.start() - 60):m.end()]}
            out.append(Definition(term=names[0], variants=names[1:], definition=np, ref=b.ref, page=page_at(m.start()),
                                  source="inline", order=b.order, extra=extra))
            prev = names[0]
    return out


def _inline_means(b: Block, text: str, page_at) -> list[Definition]:
    out: list[Definition] = []
    for m in _MEANS.finditer(text):
        before = text[:m.start()].rstrip(", ")
        term = _LIST_MARK.sub("", before[_phrase_start(before, len(before), _TERM_STOPS):].strip())
        term = _SO_CALLED.sub("", _ADVERB.sub("", term).rstrip(", ")).strip()
        term = _strip_q(term)
        if not term or len(term) > MAX_MEANS_TERM or _BAD_TERM.search(term) or not re.search(f"[{_HAN}A-Za-z]", term):
            continue
        rest = text[m.end():]
        stop = re.search(r"[。;]", rest)
        definition = rest[:stop.start() if stop else len(rest)].strip()
        if len(definition) < 2:
            continue
        out.append(Definition(term=term, variants=[], definition=definition, ref=b.ref, page=page_at(m.start()),
                              source="inline", order=b.order, extra={"via": "zh_means"}))
    return out


_LABELS = {"text", "list_item", "footnote", "caption", "paragraph"}


def find_zh_inline(doc: Doc, exclude: set[str]) -> list[Definition]:
    out: list[Definition] = []
    for b in doc.blocks:
        if (b.kind != "text" or b.layer != "body" or b.in_picture or b.ref in exclude or b.label not in _LABELS
                or b.page is None or not re.search(f"[{_HAN}]", b.text)):
            continue
        raw = re.sub("[①-⑳]", "。", b.text)
        text = normalize_display(clean(raw))
        page_at = _page_map(doc, b) if text == clean(b.text) else (lambda _, p=b.page: p)
        out += _inline_abbrev(b, text, page_at) + _inline_means(b, text, page_at)
    return out
