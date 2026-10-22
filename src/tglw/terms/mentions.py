"""Count where terms are used: token-sequence matching, leftmost-longest, with per-term case rules.

Case rules: acronym-shaped surfaces match exactly ("IT" never matches "it" or "It"); a capitalised
defined term from a definitions chapter or an in-text quote matches exactly ("Board" not "board");
every other surface ignores the case of its first letter only ("access control" ~ "Access control").
A capitalised defined term directly followed by another capitalised word is part of a longer name
("Mr. Yuan" in "Mr. Yuan Jianjun") and is not counted.
A trailing "s"/"es" on the last token counts as the same term. Single-character surfaces are not counted.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from tglw.common.bundle import Block, Doc

from .parse import clean, is_acronym

_TOKEN = re.compile(r"[A-Za-z0-9]+|[^A-Za-z0-9\s]")
MAX_TOKENS = 12


def tokens(s: str) -> list[str]:
    return _TOKEN.findall(clean(s))


def case_mode(surface: str, source: str, quoted: bool) -> str:
    if is_acronym(surface):
        return "exact"
    if surface[:1].isupper() and (source == "definitions_chapter" or quoted):
        return "exact"
    return "first"


def _same(text_toks: list[str], form: list[str], mode: str) -> bool:
    if mode == "exact":
        return text_toks == form
    return text_toks[0][:1].lower() == form[0][:1].lower() and text_toks[0][1:] == form[0][1:] and \
        text_toks[1:] == form[1:]


def _plurals(form: list[str]) -> list[list[str]]:
    last = form[-1]
    if not last[-1:].isalpha():
        return []
    out = [form[:-1] + [last + "s"]]
    if re.search(r"(?:s|x|ch|sh)$", last, re.I):
        out.append(form[:-1] + [last + "es"])
    return out


@dataclass
class Counts:
    body: int = 0
    pictures: int = 0
    pages: dict[int, int] = field(default_factory=lambda: defaultdict(int))


class Matcher:
    """Index of surface forms -> term ids; `scan` counts non-overlapping leftmost-longest matches."""

    def __init__(self) -> None:
        self._index: dict[tuple[str, ...], list[tuple[int, list[str], str]]] = defaultdict(list)
        self._lengths: dict[str, set[int]] = defaultdict(set)

    def add(self, term_id: int, surface: str, mode: str) -> bool:
        form = tokens(surface)
        if not form or len(form) > MAX_TOKENS or len("".join(form)) < 2:
            return False
        for f in [form] + _plurals(form):
            key = tuple(t.lower() for t in f)
            self._index[key].append((term_id, f, mode))
            self._lengths[key[0]].add(len(key))
        return True

    def scan(self, toks: list[str]) -> list[set[int]]:
        low = [t.lower() for t in toks]
        hits: list[set[int]] = []
        i = 0
        while i < len(toks):
            step = 1
            for n in sorted(self._lengths.get(low[i], ()), reverse=True):
                longer = i + n < len(toks) and toks[i + n][:1].isupper() and toks[i + n].isalpha()
                ids = {tid for tid, form, mode in self._index.get(tuple(low[i:i + n]), ())
                       if _same(toks[i:i + n], form, mode) and not (longer and mode == "exact"
                                                                    and not is_acronym(" ".join(form)))}
                if ids:
                    hits.append(ids)
                    step = n
                    break
            i += step
        return hits


def _segments(doc: Doc, b: Block) -> list[tuple[int, str]]:
    """(page, text) pieces of a block; multi-page text blocks are cut by their charspans."""
    if len(b.pages) > 1 and b.spans and b.spans[0].start is not None:
        raw = doc.item(b.ref).get("text") or ""
        return [(s.page, raw[s.start:s.end]) for s in b.spans]
    return [(b.page, b.text)] if b.page is not None else []


def count(doc: Doc, matcher: Matcher, exclude: set[str]) -> dict[int, Counts]:
    """Mentions per term id over body text and tables, skipping furniture and the excluded blocks
    (the definition collections themselves); text inside pictures is counted apart."""
    out: dict[int, Counts] = defaultdict(Counts)
    for b in doc.blocks:
        if b.layer != "body" or b.ref in exclude or b.kind == "picture":
            continue
        for page, text in _segments(doc, b):
            for ids in matcher.scan(tokens(text)):
                for tid in ids:
                    c = out[tid]
                    if b.in_picture:
                        c.pictures += 1
                    else:
                        c.body += 1
                        c.pages[page] += 1
    return out
