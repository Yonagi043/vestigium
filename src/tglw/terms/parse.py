"""String-level parsing: term cells, variants, term shapes, and one-line "T - D" / "T: D" entries."""

from __future__ import annotations

import re

from tglw.common.text import norm

_MARKS = re.compile(r"[®™©]")
_APOS_S = re.compile(r"(\w) ' s\b")
_APOS = re.compile(r"(\w) ' (?=\w)")
_SLASH = re.compile(r"\s*/\s*")
_CJK_GAP = re.compile(r"(?<=[\u3000-\u303f\u3400-\u9fff\uff00-\uffef]) (?=[\u3000-\u303f\u3400-\u9fff\uff00-\uffef])")
_CITE = re.compile(r"\s*(\[[^\[\]]{2,60}\])\s*$")
_LEAD_CITE = re.compile(r"^\s*(\[[^\[\]]{2,60}\])\s*")
_REDACTED = re.compile(r"\[\s*REDACTED\s*\]")
_QUOTE_DOUBLE = re.compile(r"''\s*(.+?)\s*''")
_QUOTE_SINGLE = re.compile(r"[\"'](.+?)[\"'](?=\s*(?:,|;|\bor\b|\band\b|$))")
_CONNECTORS = re.compile(r"^(?:\s|,|;|\bor\b|\band\b|\bthe\b|\bour\b)*$", re.I)
_PAREN_ACRO = re.compile(r"^(.{3,}?)\s*\(([^()]{2,15})\)$")
_PLURAL_S = re.compile(r"\((s|es)\)")
_PLURAL_IES = re.compile(r"y\(ies\)")
_HEADER_WORDS = {
    "acronym", "acronyms", "abbreviation", "abbreviations", "term", "terms", "definition", "definitions",
    "meaning", "description", "full name", "full form", "explanation",
}


def clean(s: str) -> str:
    """Whitespace/quote normalisation without lowercasing; drops ®/™ and Docling's spaced apostrophes."""
    s = norm(_MARKS.sub("", s), lower=False)
    s = _CJK_GAP.sub("", s)
    return _SLASH.sub("/", _APOS.sub(r"\1' ", _APOS_S.sub(r"\1's", s)))


def is_acronym(s: str) -> bool:
    """Abbreviation-shaped: one token (or 'X Y' with an acronym part), uppercase beyond the first char or digits."""
    s = s.strip()
    if not s or len(s) > 16 or not any(c.isalpha() for c in s):
        return False
    toks = s.split()
    if len(toks) > 2:
        return False
    if len(toks) == 2:
        return any(is_acronym(t) for t in toks)
    upper = sum(c.isupper() for c in s)
    return upper >= 2 or any(c.isupper() for c in s[1:]) or (upper >= 1 and any(c.isdigit() for c in s))


def is_garbled(s: str) -> bool:
    """OCR debris: letters spaced apart ('M i s s f i s s'), or nothing left once [REDACTED] is removed."""
    toks = s.split()
    if len(toks) >= 3 and sum(len(t) == 1 for t in toks) >= 0.5 * len(toks):
        return True
    return not any(c.isalpha() for c in _REDACTED.sub("", s))


def is_termlike(s: str, max_words: int = 10) -> bool:
    s = s.strip()
    if not s or len(s) > 90 or not any(c.isalpha() for c in s) or is_garbled(s) or re.search(r"[?*]|^\[", s):
        return False
    if len(s.split()) > max_words:
        return False
    if s.endswith(".") and not is_acronym(s.rstrip(".")) and len(s.split()) > 1 and \
            not re.search(r"\b(?:Inc|Ltd|Co|Corp|Bros|No|St|Jr|Sr)\.$", s):
        return False
    return s.lower() not in _HEADER_WORDS


def is_definitionlike(s: str) -> bool:
    """Has words; not a bare page number or a table-of-contents leader line."""
    return sum(c.isalpha() for c in s) >= 2 and not re.search(r"\.{4,}|(?:\. ){4,}", s) and \
        not re.fullmatch(r"[\divxlc\s.\-]+", s, re.I)


def is_header_row(term: str, definition: str) -> bool:
    return term.strip().lower() in _HEADER_WORDS and definition.strip().lower() in _HEADER_WORDS


def _quoted(cell: str) -> list[str] | None:
    cell = re.sub(r"\s*,\s*(''|['\"])(?=\s|$)", r"\1,", cell)
    for pat in (_QUOTE_DOUBLE, _QUOTE_SINGLE):
        found = [m.group(1).strip() for m in pat.finditer(cell)]
        if found and _CONNECTORS.match(pat.sub(" ", cell)):
            return found
    return None


def _expand(name: str) -> list[str]:
    """'affiliate(s)' -> ['affiliate', 'affiliates']; 'Long Name (LN)' -> ['Long Name', 'LN']."""
    if _PLURAL_IES.search(name):
        return [_PLURAL_IES.sub("y", name).strip(), _PLURAL_IES.sub("ies", name).strip()]
    if _PLURAL_S.search(name):
        return [_PLURAL_S.sub("", name).strip(), _PLURAL_S.sub(r"\1", name).strip()]
    m = _PAREN_ACRO.match(name)
    if m and is_acronym(m.group(2)):
        return [m.group(1).strip(), m.group(2).strip()]
    return [name]


def cite_only(cell: str) -> str | None:
    """A term cell holding nothing but a source citation ('[ISO 15288]'): a further definition of the previous term."""
    m = re.fullmatch(r"\s*(\[[^\[\]]{2,60}\])\s*", clean(cell))
    return m.group(1) if m else None


def split_term_cell(cell: str) -> tuple[str, list[str], str | None]:
    """Parse a term cell into (term, other variants, citation like '[FIPS 201-2]'); several trailing
    citations are joined, a leading one (Docling shifted it from the row above) is dropped."""
    cell = clean(cell)
    cites = []
    while (m := _CITE.search(cell)) and m.start() > 0:
        cites.insert(0, m.group(1))
        cell = cell[: m.start()].strip()
    if (m := _LEAD_CITE.match(cell)) and m.end() < len(cell):
        cell = cell[m.end():]
    cite = " ".join(cites) or None
    names = _quoted(cell)
    if names is None:
        parts = re.split(r"\s+or\s+", cell)
        names = parts if len(parts) == 2 and all(len(p.split()) <= 3 for p in parts) else [cell]
    out: list[str] = []
    for n in names:
        for v in _expand(n.strip(" ,;:")):
            if v and v not in out:
                out.append(v)
    if not out:
        return cell, [], cite
    return out[0], out[1:], cite


_DASH_SEP = re.compile(r"(?:\s+-|(?<=[\w)\].,])-)\s+|\s+-(?=[A-Z])")
_COLON = re.compile(r"^([^:]{1,90}?)\s*:\s+(\S.*)$", re.S)
_PERIOD = re.compile(r"^([A-Z][^.:]{1,60}?)\.\s+([A-Z].*)$", re.S)


def _tail_term(left: str) -> tuple[str, str]:
    """Split the text before a dash into (previous definition tail, the next entry's term)."""
    toks = left.split()
    if not toks:
        return "", ""
    k = 1
    if len(toks) >= 3 and toks[-2] == "or":
        k = 3
    elif len(toks) >= 2 and toks[-2] in ("Code",):
        k = 2
    return " ".join(toks[:-k]), " ".join(toks[-k:])


def split_dash_entries(text: str, max_words: int = 4) -> list[tuple[str, str]]:
    """'A&R - automation and robotics AAS - American Astronautical Society' -> [(A&R, ...), (AAS, ...)]."""
    text = clean(text)
    seps = list(_DASH_SEP.finditer(text))
    if not seps:
        return []
    out: list[tuple[str, str]] = []
    term = text[: seps[0].start()].strip()
    for i, m in enumerate(seps):
        end = seps[i + 1].start() if i + 1 < len(seps) else len(text)
        chunk = text[m.end(): end]
        if i + 1 < len(seps):
            definition, nxt = _tail_term(chunk)
        else:
            definition, nxt = chunk, ""
        definition = definition.strip(" ,;")
        if term and definition and is_termlike(term, max_words=max_words):
            out.append((term.rstrip(" ,"), definition))
        term = nxt
    return out


def split_colon_entry(text: str) -> tuple[str, str] | None:
    m = _COLON.match(clean(text))
    if m and is_termlike(m.group(1), max_words=10) and len(m.group(2)) > 3:
        return m.group(1).strip(), m.group(2).strip()
    return None


def split_period_entry(text: str) -> tuple[str, str] | None:
    m = _PERIOD.match(clean(text))
    if m and is_termlike(m.group(1), max_words=6):
        return m.group(1).strip(), m.group(2).strip()
    return None


def is_redirect(definition: str) -> bool:
    return bool(re.match(r"^see\b", definition.strip(), re.I))
