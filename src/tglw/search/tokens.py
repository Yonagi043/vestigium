"""Tokens for search: E's tokenizer (normalised, alphanumeric, stop words dropped), optionally stemmed.

With a CJK mode ("uni", "bi", "unibi"; off by default, see `cjk_mode`), Han characters are indexed too:
the spaces Docling leaves between them ("中 醫 藥") are removed, and each run of Han characters gives
its single characters and/or adjacent pairs. Text between the runs is tokenized exactly as before, so
a text without Han characters gives the same tokens in every mode. A mode ending in "+fold"
(TGLW_SEARCH_FOLD) maps traditional and simplified characters to one form first (tglw.zh.fold), so a
query in either script finds both; shown text is never folded. TGLW_SEARCH_CJK_AUTO turns the CJK mode
on per document for Chinese documents (tglw.zh.is_cjk_doc) while TGLW_CJK is unset. Both switches are on
unless set to 0, off or false (the configuration of the Chinese pilot); other documents are unaffected.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache

from tglw.common.text import norm

_TERM = re.compile(r"[a-z0-9]+")
_STOP = frozenset("""
a an and are as at be been being both but by can could did do does each for from had has have how
i if in into is it its may might more most no not of on or other our over same should so such than
that the their them then there these they this those through to under up was we were what when
where which while who whom why will with within would you your
""".split())


def terms(text: str) -> list[str]:
    """Task E's tokenizer (tglw.evalcov.search.terms, copied so search does not import the evaluation
    package): normalised alphanumeric runs without stop words."""
    return [t for t in _TERM.findall(norm(text)) if t not in _STOP and (len(t) > 1 or t.isdigit())]


STEMS = ("none", "plural", "snowball")
_PLURAL = (("ies", "y"), ("sses", "ss"), ("ches", "ch"), ("shes", "sh"), ("xes", "x"))


def _plural(t: str) -> str:
    if len(t) <= 3 or not t.isalpha():
        return t
    for suffix, repl in _PLURAL:
        if t.endswith(suffix):
            return t[: -len(suffix)] + repl
    if t.endswith("s") and not t.endswith(("ss", "us", "is")):
        return t[:-1]
    return t


@lru_cache(maxsize=1)
def _snowball():
    import snowballstemmer

    return snowballstemmer.stemmer("english")


@lru_cache(maxsize=200_000)
def stem(t: str, mode: str) -> str:
    if mode == "plural":
        return _plural(t)
    if mode == "snowball" and t.isalpha():
        return _snowball().stemWord(t)
    return t


CJK_BASES = ("off", "uni", "bi", "unibi")
FOLD = "+fold"  # suffix of a CJK mode: Han text is folded to simplified characters before it is split
CJK_MODES = CJK_BASES + tuple(m + FOLD for m in CJK_BASES[1:])
CJK_DEFAULT = "bi"
HAN = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_HAN_GAP = re.compile(rf"(?<=[{HAN}])\s+(?=[{HAN}])")
_HAN_RUN = re.compile(rf"[{HAN}]+")
AUTO_ENV = "TGLW_SEARCH_CJK_AUTO"  # on unless 0/off/false: a Chinese document (tglw.zh.is_cjk_doc) gets CJK_DEFAULT
FOLD_ENV = "TGLW_SEARCH_FOLD"  # on unless 0/off/false: CJK modes fold traditional and simplified together (tglw.zh.fold)
_OFF = ("0", "off", "false", "no")


def auto_enabled() -> bool:
    """TGLW_SEARCH_CJK_AUTO, on unless set to 0, off, false or no (the Chinese pilot ran with it on)."""
    return os.environ.get(AUTO_ENV, "").strip().lower() not in _OFF


def fold_enabled() -> bool:
    """TGLW_SEARCH_FOLD, on unless set to 0, off, false or no (the Chinese pilot ran with it on)."""
    return os.environ.get(FOLD_ENV, "").strip().lower() not in _OFF


def _with_fold(mode: str) -> str:
    return mode + FOLD if mode != "off" and not mode.endswith(FOLD) and fold_enabled() else mode


def cjk_mode() -> str:
    """The CJK mode from the environment, read at every call: TGLW_CJK unset/0 -> "off", 1 -> CJK_DEFAULT,
    or a mode name; unless TGLW_SEARCH_FOLD is off, a mode other than "off" also folds."""
    v = os.environ.get("TGLW_CJK", "").strip().lower()
    if v in ("", "0", "off"):
        return "off"
    return _with_fold(CJK_DEFAULT if v == "1" else v if v in CJK_MODES else "off")


def doc_cjk_mode(doc) -> str:
    """cjk_mode(), or while TGLW_CJK is unset and TGLW_SEARCH_CJK_AUTO is not off, CJK_DEFAULT (folded
    unless TGLW_SEARCH_FOLD is off) for a Chinese document (`doc`: a registered name or a bundle.Doc).
    Every other document stays "off"."""
    mode = cjk_mode()
    if mode != "off" or not auto_enabled():
        return mode
    from tglw.zh import is_cjk_doc

    return _with_fold(CJK_DEFAULT) if is_cjk_doc(doc) else "off"


def squeeze_han(text: str) -> str:
    """Remove whitespace between two Han characters: "中 醫 藥" -> "中醫藥"."""
    return _HAN_GAP.sub("", text)


def _fold(text: str) -> str:
    from tglw.zh import fold

    return fold(text)


def _cjk_terms(text: str, cjk: str) -> list[str]:
    out: list[str] = []
    text = norm(text)
    if cjk.endswith(FOLD):
        text, cjk = _fold(text), cjk[: -len(FOLD)]
    for i, part in enumerate(re.split(rf"([{HAN}]+)", squeeze_han(text))):
        if i % 2 == 0:
            out += terms(part)
            continue
        if cjk in ("uni", "unibi") or len(part) == 1:
            out += list(part)
        if cjk in ("bi", "unibi"):
            out += [part[j:j + 2] for j in range(len(part) - 1)]
    return out


def tokens(text: str, mode: str = "none", cjk: str = "off") -> list[str]:
    if cjk == "off":
        toks = terms(text)
        return toks if mode == "none" else [stem(t, mode) for t in toks]
    toks = _cjk_terms(text, cjk)
    return toks if mode == "none" else [t if _HAN_RUN.match(t) else stem(t, mode) for t in toks]


_SPLIT = re.compile(r"\?|;|,?\s+and\s+(?=(?:what|which|how|why|when|where|who|whether|does|do|did|is|are)\b)|"
                    r",\s+(?=(?:what|which|how|why|when|where|who|whether)\b)", re.I)


def facets(query: str) -> list[str]:
    """Clauses of a multi-part question: 'What X, how Y, and what Z?' -> ['What X', 'how Y', 'what Z']."""
    return [p.strip(" ,") for p in _SPLIT.split(query) if p and len(terms(p)) >= 2]
