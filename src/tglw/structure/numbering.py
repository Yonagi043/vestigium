"""Section numbers in heading text, and the shared number_key normaliser (also used by task C2).

Families (tree-building): dec (1, 2.2.4, 03.01.01, 1.0, A.1, II.3.8.14A, 2-1-2, Chapter 1,
CHAPTER ONE, SECTION II), app (Appendix A, Annex I), part (PART II), item (Item 7A), note (Note 7),
ctl (NIST AC-2, AC-2(3)), enh (NIST enhancement "(3) ACCOUNT MANAGEMENT | DISABLE ACCOUNTS").
Kinds (sidecar `number_kind`): dotted, item, part, note, appendix, chapter, roman, letter, control.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from tglw.common.text import norm

ZH_KINDS = ("zh_section", "zh_article", "zh_list", "zh_paren", "zh_arabic", "zh_arabic_paren", "zh_circled")
KINDS = ("dotted", "item", "part", "note", "appendix", "chapter", "roman", "letter", "control", "none", *ZH_KINDS)
CONTROL_FAMILIES = frozenset(
    "AC AT AU CA CM CP IA IR MA MP PE PL PM PS PT RA SA SC SI SR".split())
WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen twenty".split())}
_ROMAN_OK = ("", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
             "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX")


@dataclass(frozen=True, slots=True)
class Num:
    family: str
    key: tuple[str, ...]
    raw: str
    title: str
    weak: bool = False
    note: str = ""
    kind: str = "dotted"

    @property
    def depth(self) -> int:
        return len(self.key)


def roman(s: str) -> int | None:
    s = s.upper()
    return _ROMAN_OK.index(s) if s and s in _ROMAN_OK else None


def to_roman(n: int) -> str | None:
    return _ROMAN_OK[n] if 0 < n < len(_ROMAN_OK) else None


_PREFIX = re.compile(r"^(item|part|note|appendix|annex|attachment|c\s?h\s?a\s?p\s?t\s?e\s?r|section)\s*", re.I)


def _segments(s: str) -> list[str] | None:
    parts = [p for p in re.split(r"\s*[.\-]\s*", s) if p]
    return parts if parts and all(re.fullmatch(r"\d+[A-Za-z]?|[A-Za-z]+", p) for p in parts) else None


def _component(p: str) -> str:
    p = p.upper()
    m = re.fullmatch(r"(\d+)([A-Z]?)", p)
    return str(int(m.group(1))) + m.group(2) if m else p


def number_key(kind: str, raw: str) -> str | None:
    """Canonical section key, e.g. ("dotted", "03.01.01") -> "3.1.1", ("part", "PART 2") -> "II",
    ("chapter", "CHAPTER ONE") -> "1", ("control", "AC-02(03)") -> "AC-2(3)". None if unparseable.

    `raw` is the number only, optionally with its prefix word ("Item 7A"); a whole heading such as
    "2.2.4 Title" returns None. It only normalises: it does not judge whether "2023" is a section."""
    s = norm(raw, lower=False).strip().rstrip(".:–—-|").strip()
    s = _PREFIX.sub("", s).strip()
    if not s:
        return None
    if kind == "control":
        m = re.fullmatch(r"([A-Za-z]{2})\s*-\s*(\d{1,3})\s*(?:\(\s*(\d{1,3})\s*\))?", s)
        if not m:
            return None
        return f"{m.group(1).upper()}-{int(m.group(2))}" + (f"({int(m.group(3))})" if m.group(3) else "")
    if kind == "item":
        m = re.fullmatch(r"(\d{1,2})\s*([A-Za-z]?)", s)
        return f"{int(m.group(1))}{m.group(2).upper()}" if m else None
    if kind == "note":
        return str(int(s)) if s.isdigit() else None
    if kind in ("part", "chapter"):
        tok = s.upper()
        val = int(tok) if tok.isdigit() else roman(tok) or WORDS.get(tok.lower())
        if not val:
            return None
        return to_roman(val) if kind == "part" else str(val)
    if kind == "appendix":
        tok = s.upper().replace(" ", "")
        return str(int(tok)) if tok.isdigit() else tok if re.fullmatch(r"[A-Z]{1,5}", tok) else None
    parts = _segments(s)
    if not parts:
        return None
    root, rest = parts[0].upper(), [_component(p) for p in parts[1:]]
    if any(not re.fullmatch(r"\d+[A-Z]?", p) for p in rest):
        return None
    if kind == "dotted" and root.isdigit():
        return ".".join([str(int(root))] + rest)
    if kind == "roman" and roman(root):
        return ".".join([root] + rest)
    if kind == "letter" and re.fullmatch(r"[A-Z]", root):
        return ".".join([root] + rest)
    return None


_SEP = r"(?:\s*[.:\-–—|]\s*|\s+)"
_UP_ROMAN = r"(?-i:[IVX]{1,5})"
_CHAPTER = re.compile(rf"^(c\s?h\s?a\s?p\s?t\s?e\s?r\s+([0-9]{{1,2}}|{_UP_ROMAN}|[a-z]+)\b\.?){_SEP}?(.*)$", re.I)
_SECTION_ROMAN = re.compile(rf"^(section\s+({_UP_ROMAN})\b(?![.\-]\d)){_SEP}?(.*)$", re.I)
_APPENDIX = re.compile(
    rf"^((?:appendix|annex|attachment)\s+((?-i:[A-Z]{{1,4}})|\d{{1,2}})\b(?![.\-]\d)){_SEP}?(.*)$", re.I)
_PART = re.compile(rf"^(part\s+({_UP_ROMAN}|\d{{1,2}})\b){_SEP}?(.*)$", re.I)
_ITEM = re.compile(rf"^(item\s+(\d{{1,2}}[A-C]?)\b\.?){_SEP}?(.*)$", re.I)
_NOTE = re.compile(rf"^(note\s+(\d{{1,2}})\b){_SEP}?(.*)$", re.I)
_CONTROL = re.compile(r"^(([A-Z]{2})-(\d{1,2})(?:\s?\((\d{1,2})\))?)(?=\s|$)\s*(.*)$")
_ENH = re.compile(r"^\((\d{1,2})\)\s+(.+\|.+)$")
_SECTION_PREFIX = re.compile(r"^section\s+(?=[IVX0-9])", re.I)
_DEC = re.compile(
    r"^((?:\d{1,2}|[A-Z]|[IVX]{2,5})(?:\s?[.\-]\s?\d{1,3}[A-Z]?)*)"
    r"(\.?)(\s*[:–—|-]\s*|\s+)(.*)$")


_OCR_COLON = re.compile(r"^(\d{1,2}(?:\.\d{1,3})+:\d{1,3})\s+(?=[A-Za-z])")  # NASA scan: "2.2.4:1 Elements ..."


def _title_ok(title: str) -> bool:
    return bool(re.match(r"^[\"'(\[]?[A-Za-z]", title))


def _dec(text: str) -> Num | None:
    body = _SECTION_PREFIX.sub("", text)
    note = ""
    if re.match(r"^Z[.\-]\d", body):
        body, note = "2" + body[1:], "ocr: Z -> 2"
    if m := _OCR_COLON.match(body):
        fixed = m.group(1).replace(":", ".")
        body, note = fixed + body[m.end(1):], f"ocr: {m.group(1)} -> {fixed}"
    m = _DEC.match(body)
    if not m:
        return None
    raw, dot, sep, title = m.group(1), m.group(2), m.group(3), m.group(4).strip()
    if not _title_ok(title):
        return None
    parts = [p.strip() for p in re.split(r"[.\-]", raw)]
    root, rest = parts[0], parts[1:]
    delimited = bool(rest or dot or sep.strip())
    weak = False
    if root.isdigit():
        key0, kind = str(int(root)), "dotted"
    elif roman(root) is not None and delimited:
        key0, kind = str(roman(root)), "roman"
    elif len(root) == 1:
        if not rest and not title[:1].isupper():
            return None
        key0, kind, weak = root, "letter", not rest
    else:
        return None
    key = (key0,) + tuple(_component(p) for p in rest)
    if len(key) == 2 and key[1] == "0":
        key = key[:1]
    return Num("dec", key, raw.replace(" ", ""), title, weak=weak, note=note, kind=kind)


def parse(text: str) -> Num | None:
    """Return the section number at the start of a heading, or None."""
    s = norm(text, lower=False)
    if not s:
        return None
    m = _ENH.match(s)
    if m:
        return Num("enh", (str(int(m.group(1))),), f"({m.group(1)})", m.group(2).strip(), kind="control")
    m = _CONTROL.match(s)
    if m and m.group(2) in CONTROL_FAMILIES:
        key = (m.group(2), str(int(m.group(3)))) + ((str(int(m.group(4))),) if m.group(4) else ())
        return Num("ctl", key, m.group(1), m.group(5).strip(), kind="control")
    m = _CHAPTER.match(s)
    if m:
        tok = m.group(2)
        val = int(tok) if tok.isdigit() else WORDS.get(tok.lower()) or roman(tok)
        if val:
            return Num("dec", (str(val),), m.group(1), m.group(3).strip(), kind="chapter")
    for rx, family, kind in ((_SECTION_ROMAN, "dec", "roman"), (_APPENDIX, "app", "appendix"),
                             (_PART, "part", "part"), (_ITEM, "item", "item"), (_NOTE, "note", "note")):
        m = rx.match(s)
        if not m:
            continue
        tok = m.group(2).upper()
        if family in ("dec", "part"):
            val = int(tok) if tok.isdigit() else roman(tok)
            if not val:
                continue
            tok = str(val)
        elif family == "note":
            tok = str(int(tok))
        return Num(family, (tok,), m.group(1), m.group(3).strip(), kind=kind)
    return _dec(s)


def parse_zh(text: str) -> Num | None:
    """parse(), then task ZH's Chinese forms (第三章, 第十三条, 附录一, 一、, （一）, 1、, 1.1 + Chinese title);
    for Chinese documents only. Families are the ZH styles ("zh_chapter" ...), 1.1-style numbers "dec"."""
    from tglw.zh import headings

    n = parse(text)
    if n is not None:
        return n
    z = headings.parse(text)
    return headings.to_num(z) if z is not None else None


def num_key(n: Num, parent_key: str | None = None) -> str | None:
    """Sidecar number_key of a parsed heading number; enhancements need their control's key."""
    if n.family == "enh":
        return f"{parent_key}({n.key[0]})" if parent_key else None
    if n.family == "ctl":
        return f"{n.key[0]}-{n.key[1]}" + (f"({n.key[2]})" if len(n.key) > 2 else "")
    if n.kind in ("part", "roman"):
        root = to_roman(int(n.key[0]))
        return ".".join([root or n.key[0]] + list(n.key[1:]))
    return ".".join(n.key)
