"""Heuristic answer strings per facet, to see whether delivered text carries the answer.

A facet's keys are its strong numbers: those with a thousands separator or a decimal point, or
with at least three digits that are not a year (a currency prefix such as USD12,345 still
yields 12,345). Without any, integer percentages (the 40 of "40%") serve as weak keys, since
tables often print them bare under a "%" column. A facet without numbers falls back to
acronym-like tokens: at least three characters, every hyphen part has two or more capitals or
is all digits, and at least one part has letters (ABC, QR7, ABC-123; not Long-Name or
2020-01-01).
Facets with neither stay unchecked. A facet is found once all of its keys have appeared in the
delivered text, matched on `norm` text with digit/letter boundaries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from tglw.common.text import norm

_NUM = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
_WORD = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")
_YEAR = re.compile(r"(?:19|20)\d\d")


def strong(key: str) -> bool:
    return key[0].isdigit() and ("," in key or "." in key or (len(key) >= 3 and not _YEAR.fullmatch(key)))


def _numbers(text: str) -> list[str]:
    found, weak = [], []
    for m in _NUM.finditer(text):
        s, end = m.group(), m.end()
        if m.start() and (text[m.start() - 1].isdigit() or text[m.start() - 1] in ".,"):
            continue
        if strong(s):
            found.append(s)
        elif text[end:end + 1] == "%":
            weak.append(s)
    return found or weak


def _acronym(word: str) -> bool:
    parts = word.split("-")
    caps = [sum(c.isupper() for c in p) >= 2 for p in parts]
    return len(word) >= 3 and any(caps) and all(ok or p.isdigit() for ok, p in zip(caps, parts))


def _acronyms(text: str) -> list[str]:
    return [w for w in _WORD.findall(text) if _acronym(w)]


def facet_keys(requirement: str) -> tuple[str, ...]:
    keys = _numbers(requirement) or _acronyms(requirement)
    return tuple(dict.fromkeys(norm(k) for k in keys))


def key_pattern(key: str) -> re.Pattern[str]:
    """Numbers tolerate a dropped thousands separator and spaces around the decimal point
    ("1000" for 1,000; Docling's "0 . 01" for 0.01)."""
    if key[0].isdigit():
        body = re.escape(key).replace(",", ",?").replace(r"\.", r"\s*\.\s*")
        return re.compile(rf"(?<![\d.,]){body}(?![\d]|[.,]\d)")
    return re.compile(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])")


@dataclass
class KeyTracker:
    """Feeds delivered texts in order and counts facets whose keys have all appeared."""

    facets: list[tuple[str, ...]]
    seen: set[str] = field(default_factory=set)

    @classmethod
    def for_facets(cls, requirements: tuple[str, ...]) -> KeyTracker:
        return cls([facet_keys(r) for r in requirements])

    @property
    def checked(self) -> int:
        return sum(bool(k) for k in self.facets)

    @property
    def strong(self) -> int:
        """Facets with at least one strong number among their keys."""
        return sum(any(strong(x) for x in k) for k in self.facets)

    @property
    def found(self) -> int:
        return sum(bool(k) and all(x in self.seen for x in k) for k in self.facets)

    @property
    def done(self) -> bool:
        return self.found == self.checked

    def feed(self, text: str) -> None:
        pending = {k for keys in self.facets for k in keys} - self.seen
        if not pending or not text:
            return
        t = norm(text)
        self.seen.update(k for k in pending if key_pattern(k).search(t))

    def missing(self) -> list[str]:
        return sorted({k for keys in self.facets for k in keys} - self.seen)
