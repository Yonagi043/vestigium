"""In-text definitions: 'Long Form (LF)', '... (collectively, the "Company")', '"X" means ...', 'the term X refers to ...'."""

from __future__ import annotations

import re
from typing import Callable

from tglw.common.bundle import Block, Doc

from .model import Definition
from .parse import clean, is_acronym, is_termlike

_PAREN = re.compile(r"\(([^()]{1,160})\)")
_QUOTED = re.compile(r"[\"']([^\"']{1,70})[\"']")
_GLUE = {
    "the", "a", "an", "our", "or", "and", "collectively", "together", "each", "individually", "jointly",
    "hereinafter", "referred", "to", "as", "also", "known", "herein", "below", "after", "this", "report",
    "document", "in", "we", "use", "called", "respectively",
}
_SENT_START = re.compile(r"(?:^|[.;!?][\"']?(?:\[[\d,\s\-]+\])?\s+|:\s+)(?=[A-Z\"'(\d])")
_ABBREV_END = re.compile(r"(?:\b(?:Mr|Ms|Mrs|Dr|Prof|No|Nos|Inc|Co|Ltd|Corp|St|vs|Jr|Sr|e\.g|i\.e|etc|al|U\.S|U\.K|Fig|Sec)"
                         r"|\b[A-Z])\.\s+$")
_PREV_DEF = re.compile(r"^.*[\"']\s*\)\s*(?:,\s*and|and|,)\s+", re.S)
_MEANS = re.compile(
    r"(?:^|[\s(])[\"']([^\"']{2,60})[\"']\s+(?:means|refers to|shall mean|is defined as|is used to (?:mean|refer to))"
    r"\s+(.{3,400}?)(?=\.\s|;|$)")
_THE_TERM = re.compile(
    r"\b[Tt]he terms?\s+[\"']?([A-Za-z][\w\- ]{1,50}?)[\"']?,?\s+(?:refers? to|means|is defined as|is used to "
    r"(?:mean|refer to|describe))\s+(.{3,400}?)(?=\.\s|;|$)")
_REFER_AS = re.compile(r"\b(?:we|which we)\s+(?:refer to|call)\s+(.{3,120}?)\s+as\s+(?:the\s+)?[\"']([^\"']{2,60})[\"']")
_ROMAN = re.compile(r"^(?:[ivxlc]+|[IVXLC]+)$")
_TOC = re.compile(r"\.{4,}|(?:\. ){4,}")
_LABELS = {"text", "list_item", "footnote", "caption", "paragraph"}
_CJK = re.compile(r"[\u3000-\u303f\u3400-\u9fff\uff00-\uffef]")
_QUALIFIER = re.compile(r",?\s+as (?:amended|supplemented|modified|restated|revised|updated)\b.*$")
_DETERMINERS = {"the", "a", "an", "our", "its", "their", "his", "her", "this", "these", "those", "such", "each",
                "any", "all", "certain", "that"}
_NAME_GLUE = {"of", "and", "for", "on", "in", "de", "du", "&", "the", "due"}
_CORP_SUFFIX = re.compile(r"^(?:Inc|Ltd|LLC|L\.P|LP|Corp|Co|Limited|PLC|S\.A|AG|N\.V)\.?,?$")
_STOP = {
    "by", "to", "with", "from", "into", "under", "pursuant", "against", "as", "at", "for", "in", "on", "about",
    "between", "through", "during", "including", "via", "per", "upon", "within", "without", "over", "after",
    "before", "is", "are", "was", "were", "be", "been", "being", "has", "have", "had", "will", "may", "can",
    "shall", "which", "who", "whom", "whose", "where", "when", "while", "if", "than", "then", "also", "not",
    "require", "requires", "include", "includes", "called", "named", "known", "it", "we", "they", "he", "she",
}
MAX_NP = 10


def _clause_before(text: str, end: int, limit: int = 300) -> str:
    """The sentence fragment that ends right before a parenthesis (its definiens). A preceding quoted
    definition joined by 'and' or a comma ("(the 'Stock Exchange') and the SFC") is cut off."""
    starts = [m.end() for m in _SENT_START.finditer(text, 0, end) if not _ABBREV_END.search(text[:m.end()])]
    start = max([s for s in starts if s < end] or [0])
    frag = _PREV_DEF.sub("", text[start:end]).strip(" ,")
    if len(frag) > limit:
        frag = frag[-limit:]
        frag = frag[frag.find(" ") + 1:]
    return frag


def _strip_trailing_parens(s: str) -> str:
    """Drop parentheses that close the clause and hold a Chinese name or a bare year: '... Measures (2021) ( 《...》 )'."""
    while s.endswith(")"):
        depth, i = 0, len(s) - 1
        while i >= 0:
            depth += {")": 1, "(": -1}.get(s[i], 0)
            if depth == 0:
                break
            i -= 1
        inner = s[i + 1:-1]
        if i < 0 or not (_CJK.search(inner) or re.fullmatch(r"\s*\d{4}\s*", inner)):
            break
        s = s[:i].rstrip()
    return s


def _is_name_word(w: str) -> bool:
    w = w.strip(",;()")
    return bool(w) and (w[0].isupper() or w[0].isdigit() or w == "&")


def _verbish(words: list[str], i: int) -> bool:
    """words[i] ends in -ed/-ing and is used as a verb (not right after a determiner or a possessive)."""
    w = words[i].strip(",;").lower()
    if not re.search(r"(?:ed|ing)$", w):
        return False
    prev = words[i - 1].lower() if i > 0 else ""
    return not (prev in _DETERMINERS or prev.endswith(("'s", "s'")))


_PREPS = {"of", "in", "for", "on", "at"}


def definiens(clause: str, extend: bool = True) -> tuple[str, bool]:
    """The noun phrase right before a '(the "X")' parenthesis, and whether the cut is trustworthy.

    A capitalised name is taken as far back as its words stay capitalised (allowing 'of', 'and', ... inside
    and a 'Name, Inc.' suffix); a 'Name, a California corporation' apposition keeps the name; a phrase
    after 'noun of/in/for the' takes in that noun. A common noun phrase runs back to a determiner, a
    possessive, a preposition, or a verb-like word.
    """
    text = _QUALIFIER.sub("", _strip_trailing_parens(clause.strip(" ,'\""))).strip(" ,")
    words = text.split()
    if not words:
        return clause, False
    i = len(words)
    name = _is_name_word(words[-1])
    while i > 0:
        w = words[i - 1]
        bare = w.strip(",;()'\"").lower()
        if name:
            if (bare in _STOP and bare not in _NAME_GLUE) or (bare in _DETERMINERS and bare != "the"):
                break
            if _is_name_word(w) and not (w.endswith(",") and i < len(words) and not _CORP_SUFFIX.match(words[i])):
                i -= 1
            elif bare in _NAME_GLUE and i < len(words) and not w.endswith(","):
                k = i - 1
                while k > 0 and words[k - 1].lower() in _NAME_GLUE and not words[k - 1].endswith(","):
                    k -= 1
                if k == 0 or not _is_name_word(words[k - 1]):
                    break
                i = k
            else:
                break
        else:
            if w.endswith((",", ";", ":")) or bare in _STOP or bare in _DETERMINERS or bare in ("or", "and", "nor") \
                    or _verbish(words, i - 1):
                break
            i -= 1
            if bare.endswith(("'s", "'")):
                break
    while i < len(words) and words[i].strip(",").lower() in _NAME_GLUE | _DETERMINERS:
        i += 1
    np_words = words[i:]
    if not np_words:
        return clause, False
    j = i - 1 if i > 0 and words[i - 1].lower() in _DETERMINERS else i
    if extend and j > 1 and words[j - 1].lower() in ("and", "or"):
        head, conf = definiens(" ".join(words[:j - 1]), extend=False)
        if conf and _is_name_word(head.split()[0]):
            return f"{head} {' '.join(words[j - 1:])}", True
    if extend and j > 1 and len(np_words) <= 3 and words[j - 1].lower() in _PREPS and not _is_name_word(words[j - 2]) and \
            words[j - 2].lower() not in _STOP and not _verbish(words, j - 2) and not words[j - 2].endswith(","):
        head, conf = definiens(" ".join(words[:j - 1]), extend=False)
        if conf:
            return f"{head} {' '.join(words[j - 1:])}", True
    if not name and i > 1 and words[i - 1].lower() in ("a", "an") and words[i - 2].endswith(","):
        head, conf = definiens(" ".join(words[:i - 1]).rstrip(","), extend=False)
        if conf and _is_name_word(head.split()[0]):
            return f"{head}, {' '.join(words[i - 1:])}", True
    np = " ".join(np_words).strip(" ,;")
    after_relative = i > 1 and words[i - 2].lower() in ("require", "requires", "that", "which", "who")
    coordinated = i > 0 and (words[i - 1].lower() in ("or", "and") or words[i - 1].endswith(","))
    adverb = len(np_words) == 1 and np.endswith("ly")
    limit = MAX_NP + 6 if name else MAX_NP
    sure = 1 <= len(np_words) <= limit and (i > 0 or len(words) <= limit) and not (after_relative or coordinated
                                                                                     or adverb)
    return np, sure


def _content(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", s.lower())} - _STOP - _DETERMINERS - _NAME_GLUE


def misplaced(term: str, np: str, clause: str) -> bool:
    """The term's words appear earlier in the clause but not in the cut phrase: the cut is probably wrong."""
    if is_acronym(term):
        return False
    t = _content(term)
    return bool(t) and not (t & _content(np)) and bool(t & _content(clause))


def _valid_short(s: str) -> bool:
    if not 2 <= len(s) <= 12 or len(s.split()) > 2 or not s[0].isalnum() or _ROMAN.match(s) or re.search(r"[\"']", s):
        return False
    letters = [c for c in s if c.isalpha()]
    return len(letters) >= 2 and any(c.isupper() for c in s) and not s.isdigit() and is_acronym(s)


def long_form(short: str, before: str) -> str | None:
    """Schwartz & Hearst (2003): shortest preceding phrase whose letters cover the short form in order."""
    words = before.split()
    words = words[-min(len(short) + 5, len(short) * 2):]
    cand = " ".join(words)
    letters = [c.lower() for c in short if c.isalnum()]
    si, li = len(letters) - 1, len(cand) - 1
    while si >= 0:
        c = letters[si]
        while li >= 0 and (cand[li].lower() != c or (si == 0 and li > 0 and cand[li - 1].isalnum())):
            li -= 1
        if li < 0:
            return None
        li -= 1
        si -= 1
    lf = re.sub(r"'s$", "", cand[cand.rfind(" ", 0, li + 1) + 1:].strip(" ,;:"))
    if len(lf) <= len(short) or len(re.split(r"[\s\-]+", lf)) < 2 or short.lower() in lf.lower().split():
        return None
    if lf[0].lower() != short[0].lower() or re.search(r"[()\[\]:;]", lf):
        return None
    return lf


PageAt = Callable[[int], int]


def _acronyms(b: Block, text: str, page_at: PageAt) -> list[Definition]:
    out = []
    for m in _PAREN.finditer(text):
        short = re.sub(r"'s?$", "", m.group(1).strip())
        if short.endswith("s") and _valid_short(short[:-1]) and short[:-1].isupper():
            short = short[:-1]
        if not _valid_short(short):
            continue
        lf = long_form(short, _clause_before(text, m.start(), limit=400))
        if lf:
            out.append(Definition(term=short, variants=[], definition=lf, ref=b.ref, page=page_at(m.start()),
                                  source="inline", order=b.order, extra={"via": "acronym"}))
    return out


def _quoted_definiens(term: str, clause: str) -> tuple[str, bool]:
    """An acronym-shaped quoted term takes its Schwartz-Hearst expansion; other terms the cut noun phrase.
    Lower-case terms (often scare quotes) and cuts that miss the term's own words stay uncertain."""
    acronym = is_acronym(term) and " " not in term
    if acronym and (lf := long_form(re.sub(r"[^A-Za-z0-9&]", "", term), clause)):
        return lf, True
    np, sure = definiens(clause)
    np = _trim_to_term(term, np)
    code = any(re.fullmatch(r"[a-z]+[A-Z]\w*", w) and len(w) >= 8 for w in np.split())
    return np, (sure and not misplaced(term, np, clause) and any(c.isupper() for c in term) and not acronym
                and clause[:1].isupper() and not code)


def _trim_to_term(term: str, np: str) -> str:
    """'Article 122 of the Food Safety Law of the PRC' for 'Food Safety Law' -> 'Food Safety Law of the PRC':
    start at the capitalised run holding the term's first word that occurs in the phrase."""
    words = np.split()
    t = _content(term)
    hit = next((k for k, w in enumerate(words) if _content(w) & t), None)
    if hit is None or not _is_name_word(words[hit]):
        return np
    while hit > 0 and _is_name_word(words[hit - 1]) and not words[hit - 1].endswith(","):
        hit -= 1
    return " ".join(words[hit:])


def _quoted_terms(b: Block, text: str, page_at: PageAt) -> list[Definition]:
    out = []
    for m in _PAREN.finditer(text):
        inner = m.group(1)
        names = [q.strip() for q in _QUOTED.findall(inner)]
        rest = set(re.findall(r"[a-z]+", _QUOTED.sub(" ", inner).lower()))
        if not names or not rest <= _GLUE or re.match(r"\s*(?:see|refer)\b", inner, re.I):
            continue
        names = [n for n in names if n and len(n.split()) <= 8 and is_termlike(n) and "/" not in n]
        clause = _clause_before(text, m.start())
        if names and len(clause.split()) >= 1:
            np, sure = _quoted_definiens(names[0], clause)
            extra = {"via": "quoted"} if sure else {"via": "quoted", "uncertain": True, "context": clause}
            out.append(Definition(term=names[0], variants=names[1:], definition=np, ref=b.ref, page=page_at(m.start()),
                                  source="inline", order=b.order, extra=extra))
    return out


def _phrases(b: Block, text: str, page_at: PageAt) -> list[Definition]:
    out = []
    for pat, ti, di, via in ((_MEANS, 1, 2, "means"), (_THE_TERM, 1, 2, "the_term"), (_REFER_AS, 2, 1, "refer_as")):
        for m in pat.finditer(text):
            term, definition = m.group(ti).strip(), m.group(di).strip()
            if term and definition and len(term.split()) <= 8 and is_termlike(term):
                out.append(Definition(term=term, variants=[], definition=definition, ref=b.ref, page=page_at(m.start()),
                                      source="inline", order=b.order, extra={"via": via}))
    return out


def find_inline(doc: Doc, exclude: set[str]) -> list[Definition]:
    out: list[Definition] = []
    for b in doc.blocks:
        if (b.kind != "text" or b.layer != "body" or b.in_picture or b.ref in exclude or b.label not in _LABELS
                or b.page is None):
            continue
        text = clean(b.text)
        if _TOC.search(text):
            continue
        page_at = _page_map(doc, b)
        quoted = [] if b.label == "caption" else _quoted_terms(b, text, page_at)
        out += _acronyms(b, text, page_at) + quoted + _phrases(b, text, page_at)
    return out


def _page_map(doc: Doc, b: Block) -> PageAt:
    """Offset in the cleaned block text -> page, using each provenance span's cleaned length."""
    if len(b.pages) < 2 or not b.spans or b.spans[0].start is None:
        return lambda _: b.page
    raw = doc.item(b.ref).get("text") or ""
    bounds, pos = [], 0
    for sp in b.spans:
        pos += len(clean(raw[sp.start:sp.end])) + 1
        bounds.append((pos, sp.page))
    return lambda i: next((p for end, p in bounds if i < end), bounds[-1][1])
