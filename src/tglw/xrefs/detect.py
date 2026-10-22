"""Find explicit cross-reference mentions in a piece of text (no document context)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

KINDS = ("section", "note", "item", "appendix", "table", "figure", "page", "control", "footnote", "external")
FAMILIES = "AC|AT|AU|CA|CM|CP|IA|IR|MA|MP|PE|PL|PM|PS|PT|RA|SA|SC|SI|SR"
NUMWORDS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve".split())}


@dataclass(frozen=True, slots=True)
class Mention:
    kind: str
    key: str
    span: str
    start: int
    end: int
    qual: dict[str, str] = field(default_factory=dict)


_SEP = r"(?:\s*,\s*(?:and\s+|or\s+)?|\s+and\s+|\s+or\s+|\s*&\s*|\s+through\s+|\s*–\s*)"
_FIG_KEY = r"(?:[A-Z]{1,2}[-.]?)?\d+(?:(?:\s\.\s|[.\-])\d+)*(?:-[A-Za-z]{1,4})?[a-z]?(?![\w-]|\.\d)"
_SEC_KEY = r"(?:[A-Z]\.)?\d+(?:\.\d+)*[a-z]?(?![\w-]|\.\d)"
_KEY = {
    "table": _FIG_KEY,
    "figure": _FIG_KEY,
    "section": _SEC_KEY,
    "chapter": _SEC_KEY,
    "appendix": r"(?:[IVX]+[AB]?|[A-Z](?:\.\d+)*|\d+)(?!\w|-\d)",
    "note": r"\d{1,2}(?:\.\d+)?[A-Z]?(?:\([A-Za-z0-9]{1,4}\))*(?![\w-])",
    "item": r"\d{1,2}[A-C]?(?![\w-])",
}
_LABELS = {
    "table": r"Tables?",
    "figure": r"Figures?|Figs?\.",
    "section": r"Sections?|Secs?\.|§§?",
    "chapter": r"Chapters?",
    "appendix": r"Appendix|Appendices|Appendixes|Annex(?:es)?",
    "note": r"Notes?",
}
_LABELLED = {
    kind: re.compile(rf"(?<![\w-])(?P<label>{lab})\s*(?P<keys>{_KEY[kind]}(?:{_SEP}{_KEY[kind]})*)", re.I)
    for kind, lab in _LABELS.items()
}
_CHAPTER_WORD = re.compile(r"(?<![\w-])Chapter\s+(?P<word>" + "|".join(NUMWORDS) + r")\b", re.I)
_ITEM = re.compile(
    rf"(?<![\w-])(?:Part\s+(?P<part>[IVX]+)\s*,?\s*)?Items?\s+(?P<keys>{_KEY['item']}(?:{_SEP}{_KEY['item']})*)")
_PAGE = re.compile(
    r"\b(?:see|on|at|in|to|refer to)\s+(?:the\s+)?(?:pages?|pp?\.)\s*(?P<keys>(?:[IVX]+[AB]?-)?\d{1,4})\b",
    re.I)
_CONTROL = re.compile(
    rf"(?<![\w.\-/])(?P<fam>{FAMILIES})-(?P<num>\d{{1,2}})(?:\s?\((?P<enh>\d{{1,2}})\))?(?P<part>[a-z](?:\.\d+)?)?(?![\w-])")
_REQUIREMENT = re.compile(r"(?<![\w.])(?P<key>0[1-9]\.\d{2}\.\d{2})(?![\w.]*\d)")
_QUOTE_OPEN = "'‘’\"“”"
_QUOTED = re.compile(
    r"\b(?:see|refer to|set out in|set forth in|described in|disclosed in|discussed in|under\s+the\s+(?:heading|caption)s?)\s+(?:also\s+)?"
    r"(?:the\s+)?(?:(?:sections?|paragraphs?|sub-sections?|subsections?|headings?)\s+(?:headed\s+|entitled\s+|titled\s+)?)?"
    rf"(?:[{_QUOTE_OPEN}]\s*){{1,2}}(?P<title>[^{_QUOTE_OPEN}]{{3,160}}?)\s*[.,]?\s*(?:[{_QUOTE_OPEN}]){{1,2}}",
    re.I)
_DOCREF = re.compile(
    r"\b(?:see|refer to|described in|defined in|specified in|in accordance with|according to)\s+(?:also\s+)?(?:the\s+)?"
    r"(?:NIST\s+)?\[?(?P<doc>(?:SP|Special Publication|FIPS|NISTIR|CNSSI|OMB\s+Circular|OMB\s+Memorandum)"
    r"\s*[A-Z]?-?\d[\w.\-]*)", re.I)

_LAW = ("Act Acts Code Rule Rules Regulation Regulations Ordinance Notice Law Laws Plan Circular Bulletin Guide Guides "
        "Guideline Guidelines Directive Convention Treaty Constitution Statute Statutes Measures Provisions Order Orders").split()
LAW_WORDS = "|".join(sorted({w for x in _LAW for w in (x, x.upper())}, key=len, reverse=True))
_EXT_TAIL = re.compile(
    r"""^(?:\([\w.]{1,4}\))*(?:\s+(?:or|and)\s+(?:Sections?\s+)?\d+\w*(?:\([\w.]{1,4}\))*)?
      (?:\s*,?\s*['‘’"“]{1,2}[^'‘’"“”]{3,100}['’"”]{1,2}\s*,?)?
      [\s,]*\[?(?:of|in|under|to|from)\s+(?!this\b|these\b|our\b)(?:the\s+)?(?:
        (?:\d{4}\s+|prior\s+|previous\s+)?Form\s+\d
      | [\w.'’()/\- ]{0,100}?\b(?-i:{LAW_WORDS})\b
      | [\w.'’/\- ]{0,60}?\b(?:guide|manual|documentation|handbook)\b
      | [A-Z][\w\-]+\s+(?:et\s+al|and\s+[A-Z][\w\-]+\s*\(\d{4})
      | \[\s*[\w+]
      | title\s+\d+
      | (?:SP|NIST|FIPS|OMB|ISO|IEC|IEEE|RFC|SFFAS|SFFAC|TB|TR|A-136|WSJ|CFR|U\.S\.C|IFRS|IAS|HKFRS|HKAS|GAAP|ASC)\b
      | (?-i:[A-Z]{2,6})\b(?!\s*\d)
    )""".replace("{LAW_WORDS}", LAW_WORDS), re.X | re.I)
_EXT_HEAD = re.compile(
    r"(?:U\.\s?S\.\s?C\.?,?|C\.\s?F\.\s?R\.?,?|\bCFR|\b(?-i:[A-Z]{1,6})-\d{1,4}[A-Z]?,|\bHandbook,|\bold|Circular\s+[\w\-]+,?|Regulation\s+S-[KX],?|Volume\s+[IVX\d]+,\s+Part\s+\w+,"
    r"|SFFAS\s+\d+,?|\b(?:SP|FIPS|NISTIR|IR|ISO|RFC)\s*\d[\w.\-]*,?|\btit\.\s*[IVX]+,|\bTitle\s+\d+\b.{0,40},|Fed\.\s?Reg\.)\s*$")
_ACRONYM_HEAD = re.compile(
    r"\b(?!(?:SEE|IN|AND|OR|OF|THE|TO|ON|AT|BY|FOR|FROM|UNDER|PER|WITH|AS|ALSO|THIS|ITS|OUR)\s)[A-Z]{2,6}(?:-[A-Z]{1,2})?"
    r"(?:\s?[A-Z]?-?\d[\d\-]+)?,?\s+$")
_LEGAL_NEAR = re.compile(r"Pub\.\s?L\.|U\.\s?S\.\s?C\.|C\.\s?F\.\s?R\.|\bStat\.|\bCFR\b|Fed\.\s?Reg\.|Exec\.\s?Order|of this chapter")
_STANDARD_NEAR = re.compile(
    r"\b(?:SAS|SSAE|SSARS|SFFAS|SFFAC|ASU|ASC|IFRS|IAS|HKFRS|HKAS|AU-C|AT-C|AR-C)\s+(?:No\.\s*)?\d+"
    r"(?:[^.;)(]|\b(?:paras?|No|app|pp?|ch)\.){0,130}$")
_SCOPE_TAIL = re.compile(
    r"^[\s,]*(?:to|of|in)\s+the\s+(?P<scope>Accountants\s*['’]?\s*Reports?|financial statements|consolidated financial statements)"
    r"(?:[^.;]{0,40}?\bAppendix\s+(?P<app>[IVX]+[AB]?))?", re.I)
_APPENDIX_TAIL = re.compile(r"^[^.;]{0,60}?\b(?:in|to)\s+Appendix\s+(?P<app>[IVX]+[AB]?)\b(?!\s*-)", re.I)


def _split_keys(keys: str, kind: str) -> list[str]:
    parts = [m.group(0) for m in re.finditer(_KEY[kind], keys)]
    if kind in ("table", "figure") and parts and "." not in parts[0]:
        parts = [p for p in parts if "." not in p]
    if re.search(r"\s+through\s+|–", keys) and len(parts) == 2 and all(p.isdigit() for p in parts):
        a, b = int(parts[0]), int(parts[1])
        if 0 < b - a <= 10:
            return [str(i) for i in range(a, b + 1)]
    return parts


def norm_key(kind: str, key: str) -> str:
    key = key.strip().rstrip(".")
    return key.upper() if kind in ("table", "figure", "appendix", "item") else key


def external_tail(text: str, end: int) -> str | None:
    m = _EXT_TAIL.match(text[end:end + 160])
    return m.group(0).strip(" ,") if m else None


def external_head(text: str, start: int, label: str = "") -> bool:
    """Another document named just before the mention: '18 U.S.C. Section 1350', 'AFR section 1',
    'AU-C Appendix B', 'SFFAS 3, paras. 72-75 and table 1', 'old Appendix J'."""
    before = text[max(0, start - 80):start]
    if label.startswith("§") and _LEGAL_NEAR.search(text[max(0, start - 80):start + 80]):
        return True
    if label.lower().startswith(("sec", "chap", "app")) and _ACRONYM_HEAD.search(before):
        return True
    if _STANDARD_NEAR.search(text[max(0, start - 160):start]):
        return True
    return bool(_EXT_HEAD.search(before))


def _labelled(text: str) -> list[Mention]:
    out: list[Mention] = []
    for kind, rx in _LABELLED.items():
        for m in rx.finditer(text):
            if kind == "note" and re.match(r"\s*[:–]", text[m.end():]):
                continue
            if kind in ("table", "figure") and re.search(r"\b(?:a|an)\s+$", text[max(0, m.start() - 4):m.start()]):
                continue
            real = "section" if kind == "chapter" else kind
            qual = {"label": m.group("label")}
            if kind == "chapter":
                qual["chapter"] = "1"
            for k in _split_keys(m.group("keys"), kind):
                out.append(Mention(real, norm_key(real, k), m.group(0), m.start(), m.end(), dict(qual)))
    for m in _CHAPTER_WORD.finditer(text):
        out.append(Mention("section", m.group("word"), m.group(0), m.start(), m.end(),
                           {"label": "Chapter", "chapter": "1"}))
    return out


def _items(text: str) -> list[Mention]:
    out = []
    for m in _ITEM.finditer(text):
        qual = {"part": m.group("part").upper()} if m.group("part") else {}
        for k in _split_keys(m.group("keys"), "item"):
            out.append(Mention("item", k.upper(), m.group(0), m.start(), m.end(), dict(qual)))
    return out


def _pages(text: str) -> list[Mention]:
    return [Mention("page", m.group("keys").upper(), m.group(0), m.start(), m.end()) for m in _PAGE.finditer(text)]


_FRAMEWORK = re.compile(r"(?<![\w])(?P<fw>[A-Z][A-Z0-9]{3,})\s:\s")


def _controls(text: str) -> list[Mention]:
    out = []
    labels = [(f.start(), f.group("fw")) for f in _FRAMEWORK.finditer(text)]
    for m in _CONTROL.finditer(text):
        qual = {"part": m.group("part")} if m.group("part") else {}
        if fw := next((name for pos, name in reversed(labels) if pos < m.start()), None):
            qual["framework"] = fw
        raw = f"{m.group('fam')}-{m.group('num')}" + (f"({m.group('enh')})" if m.group("enh") else "")
        out.append(Mention("control", raw, m.group(0), m.start(), m.end(), qual))
    return out


def _requirements(text: str) -> list[Mention]:
    return [Mention("section", m.group("key"), m.group(0), m.start(), m.end(), {"label": "requirement"})
            for m in _REQUIREMENT.finditer(text)]


def _quoted(text: str) -> list[Mention]:
    out = []
    for m in _QUOTED.finditer(text):
        title = re.sub(r"\s+", " ", m.group("title")).strip(" .,")
        if not title or not (title[0].isupper() or title[0] in "-–—"):
            continue
        out.append(Mention("section", title, m.group(0), m.start(), m.end(), {"label": "quoted"}))
    return out


def _docrefs(text: str) -> list[Mention]:
    return [Mention("external", re.sub(r"\s+", " ", m.group("doc")).rstrip(".,"), m.group(0), m.start(), m.end())
            for m in _DOCREF.finditer(text)]


def find_mentions(text: str, controls: bool = False, requirements: bool = False) -> list[Mention]:
    """All mentions in `text`, sorted by position, with external/scope qualifiers attached."""
    found = _labelled(text) + _items(text) + _pages(text) + _quoted(text) + _docrefs(text)
    if controls:
        found += _controls(text)
    if requirements:
        found += _requirements(text)
    found = _drop_nested(found)
    out = []
    for m in sorted(found, key=lambda x: (x.start, x.end)):
        qual = dict(m.qual)
        if m.kind not in ("external", "control"):
            tail = external_tail(text, m.end)
            if tail or external_head(text, m.start, m.qual.get("label", "")):
                qual["external"] = tail or text[max(0, m.start - 40):m.start].strip()
        if m.kind == "note":
            if text[m.start - 1:m.start] == "(" and text[m.end:m.end + 1] == ")":
                qual["paren"] = "1"
            sm = _SCOPE_TAIL.match(text[m.end:m.end + 120])
            if sm:
                qual["scope"] = "accountants" if "ccountant" in sm.group("scope") else "fs"
                if sm.group("app"):
                    qual["appendix"] = sm.group("app").upper()
        if m.kind in ("note", "section"):
            am = _APPENDIX_TAIL.match(text[m.end:m.end + 120])
            if am:
                qual.setdefault("appendix", am.group("app").upper())
        out.append(Mention(m.kind, m.key, m.span, m.start, m.end, qual))
    return _attach_titles(out)


def _drop_nested(ms: list[Mention]) -> list[Mention]:
    """Drop mentions whose span lies inside a longer mention of another kind (e.g. 'Note 7' inside a quote)."""
    keep = []
    for m in ms:
        inside = any(o is not m and o.kind != m.kind and o.start <= m.start and m.end <= o.end
                     and (o.end - o.start) > (m.end - m.start) and o.qual.get("label") == "quoted" for o in ms)
        if not inside:
            keep.append(m)
    return keep


def _attach_titles(ms: list[Mention]) -> list[Mention]:
    """A quoted title right after an item/note mention names that target; fold it in as a qualifier."""
    out: list[Mention] = []
    for m in ms:
        if m.qual.get("label") == "quoted" and out:
            prev = out[-1]
            if prev.kind in ("item", "note") and 0 <= m.start - prev.end <= 60:
                out[-1] = Mention(prev.kind, prev.key, prev.span, prev.start, prev.end, {**prev.qual, "title": m.key})
                continue
        out.append(m)
    return out
