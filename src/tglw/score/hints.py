"""Automatic hints for a human grader; they never decide a verdict.

Keys are E's answer strings for a facet (`tglw.evalcov.keys`); keys that already occur in the
question are reported apart as echoes, since restating the question finds them. Cited pages are read
from the answer: "p. 45", "pp. 45-47", "pp. 45-7", "page 45", "pages 12, 14", "p45" (the MCP form),
"第 45 页", and Docling refs such as "#/tables/4"; numbers with thousands separators, "printed page N",
and pages beyond the document are ignored. A cross-document question passes all its documents: refs
are looked up in each, pages up to the longest are kept, and a page hint compares page numbers only,
so it cannot tell which document a cited page belongs to. Abstention phrases and the numbers an
answer states help with unanswerable questions. Known blind spots are listed on every sheet.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from tglw.common import bundle, registry
from tglw.common.text import norm
from tglw.evalcov.keys import facet_keys, key_pattern

from .questions import Facet

_NUM_PAGE = r"\d{1,4}(?![\d,.]\d)"
_PAGES = re.compile(
    r"(?<![a-z])(?<!printed )(?<!printed\s)(?:pdf\s*)?(?:pp?|pages?|pgs?)\.?\s*("
    + _NUM_PAGE + r"(?:\s*(?:[-–—]|to)\s*" + _NUM_PAGE + r")?"
    r"(?:\s*(?:,|and|&)\s*(?!\d{1,3},\d{3})" + _NUM_PAGE + r"(?:\s*(?:[-–—]|to)\s*" + _NUM_PAGE + r")?)*)", re.I)
_PRINTED = re.compile(r"printed\s+(?:page|p\.?)\s*[\w-]+", re.I)
_CN_PAGE = re.compile(r"第\s*(\d{1,4})\s*页")
_REF = re.compile(r"#/(?:texts|tables|pictures|groups)/\d+")
_SPAN = re.compile(r"(\d{1,4})\s*(?:[-–—]|to)\s*(\d{1,4})", re.I)
_MAX_SPAN = 30
_SEGMENT = re.compile(r"\n+|(?<=[.;!?])\s+(?=[A-Z(\[*-])")
_ABSTAIN = re.compile(
    r"\b(?:(?:is|are|was|were)?\s*(?:not|n['’]t)\s+(?:explicitly\s+|directly\s+|separately\s+|specifically\s+)?"
    r"(?:stated|given|specified|mentioned|provided|reported|disclosed|found|available|included|listed|"
    r"addressed|defined|set|broken\s+out|quantified|identified|shown|covered)"
    r"|(?:does|do|did)\s*(?:n['’]t|\s+not)\s+(?:explicitly\s+|separately\s+|specifically\s+)?"
    r"(?:state|say|give|specify|mention|provide|report|disclose|contain|include|list|set|establish|define|"
    r"fix|break\s+out|quantify|identify|show|cover|appear)"
    r"|(?:sets|gives|provides|reports|discloses|contains|states|specifies)\s+no\b"
    r"|no\s+(?:such\s+|specific\s+|explicit\s+|separate\s+|\w+\s+)?(?:figure|number|value|information|data|"
    r"mention|statement|maximum|threshold|amount|cost|limit|period|rate|percentage|estimate|breakdown)s?\b"
    r"|cannot\s+be\s+(?:determined|found|answered)|could\s*(?:n['’]t|\s+not)\s+(?:find|locate|identify)"
    r"|unable\s+to\s+(?:find|locate|identify)|is\s+silent\s+on|unavailable|nowhere\s+in|neither\s+\w+\s+"
    r"(?:is|appears|figure)|is\s+not\s+in\s+the\s+(?:document|report))"
    r"|未(?:给出|提及|说明|列出|披露|规定)|没有(?:给出|提到|说明|披露|规定)|文档中(?:没有|未)", re.I)
_VALUE = re.compile(r"(?<![\w.-])[$€£¥]?\d[\d,]*(?:\.\d+)?(?![\w-]|\.\w)"
                    r"(?:\s*(?:%|percent|million|billion|thousand|hours?|years?|days?))?", re.I)


@dataclass(frozen=True, slots=True)
class KeyHint:
    keys: tuple[str, ...]
    found: tuple[str, ...]
    echo: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        if not self.keys:
            return "echo" if self.echo else "unchecked"
        if len(self.found) == len(self.keys):
            return "all"
        return "some" if self.found else "none"

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(k for k in self.keys if k not in self.found)


def key_hint(facet_text: str, answer: str, question: str = "") -> KeyHint:
    """Keys found in the answer; keys that the question itself contains are moved to `echo`."""
    keys = facet_keys(facet_text)
    q = norm(question)
    echo = tuple(k for k in keys if q and key_pattern(k).search(q))
    keys = tuple(k for k in keys if k not in echo)
    t = norm(answer)
    return KeyHint(keys, tuple(k for k in keys if key_pattern(k).search(t)), echo)


def _expand(group: str) -> set[int]:
    pages: set[int] = set()
    for part in re.split(r"\s*(?:,|and|&)\s*", group):
        m = _SPAN.fullmatch(part.strip())
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            if hi < lo and len(m.group(2)) < len(m.group(1)):
                hi = int(m.group(1)[: len(m.group(1)) - len(m.group(2))] + m.group(2))
            if lo <= hi <= lo + _MAX_SPAN:
                pages.update(range(lo, hi + 1))
                continue
        if part.strip().isdigit():
            pages.add(int(part.strip()))
    return pages


def _docs(doc: str | Sequence[str] | None) -> tuple[str, ...]:
    return tuple(d for d in ((doc,) if isinstance(doc, str) else doc or ()) if d)


def ref_pages(doc: str | Sequence[str] | None, answer: str) -> set[int]:
    refs = set(_REF.findall(answer))
    pages: set[int] = set()
    for d in _docs(doc) if refs else ():
        by_ref = bundle.load(d).by_ref
        pages |= {p for r in refs if r in by_ref for p in by_ref[r].pages}
    return pages


def cited_pages(answer: str, doc: str | Sequence[str] | None = None) -> set[int]:
    text = _PRINTED.sub(" ", answer)
    pages: set[int] = set()
    for m in _PAGES.finditer(text):
        pages |= _expand(m.group(1))
    pages.update(int(m.group(1)) for m in _CN_PAGE.finditer(text))
    docs = _docs(doc)
    last = max(registry.get(d).pages for d in docs) if docs else 10_000
    return {p for p in pages if 0 < p <= last} | ref_pages(docs, answer)


def _match(facet: Facet, cited: set[int]) -> str:
    if cited & (set(facet.pages) | {p for _, p in facet.elsewhere}):
        return "pdf"
    if {str(p) for p in cited} & set(facet.printed):
        return "printed?"
    return "no"


def segments(answer: str) -> list[str]:
    return [s for s in _SEGMENT.split(answer) if s.strip()]


def page_hint(facet: Facet, answer: str, found: tuple[str, ...], doc: str | Sequence[str] | None = None) -> str:
    """Pages cited next to the facet's keys (same sentence or line) when there are any, else pages
    cited anywhere in the answer (suffix '~'). 'pdf': an evidence page is cited; 'printed?': a cited
    number only equals a printed page label; 'no': pages cited but none is an evidence page;
    'none': no page cited."""
    local: set[int] = set()
    if found:
        pats = [key_pattern(k) for k in found]
        for seg in segments(answer):
            if any(p.search(norm(seg)) for p in pats):
                local |= cited_pages(seg, doc)
    if local:
        return _match(facet, local)
    everywhere = cited_pages(answer, doc)
    return _match(facet, everywhere) + "~" if everywhere else "none"


_STOP = frozenset("""about above after again also among and another any are because been before being below
between both but can could does doing down during each either from further have having here into its itself
just more most must other over same should some such than that their them then there these they this those
through under until very what when where which while will with within would your
identify identifies state states stated report reports reported describe describes explain explains
mention mentions include includes provide provides specify specifies list lists name names give gives
note notes""".split())
_WORDS = re.compile(r"[a-z0-9]{4,}")


def content_words(text: str) -> set[str]:
    """Words of four or more characters outside a stop list, cut to six characters as a crude stem
    (archive/archived, require/required)."""
    return {w[:6] for w in _WORDS.findall(norm(text)) if w not in _STOP}


def word_overlap(facet_text: str, answer: str) -> tuple[int, int]:
    """(facet content words found in the answer, facet content words); a hint for text facets that
    have no number or acronym key."""
    fw = content_words(facet_text)
    return len(fw & content_words(answer)), len(fw)


def abstains(answer: str) -> bool:
    return bool(_ABSTAIN.search(answer))


def stated_values(answer: str) -> list[str]:
    """Numbers the answer states, minus page citations; a fabrication check for graders."""
    body = _CN_PAGE.sub(" ", _PAGES.sub(" ", _PRINTED.sub(" ", answer)))
    body = _REF.sub(" ", body)
    vals = [m.group().strip().rstrip(".,") for m in _VALUE.finditer(body)]
    keep = [v for v in vals if len(re.sub(r"\D", "", v)) >= 2 or not v[-1].isdigit()]
    return list(dict.fromkeys(keep))
