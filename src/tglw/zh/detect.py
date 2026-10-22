"""Is this a Chinese document? Han share among the letters of its text blocks."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from tglw.common import bundle, registry
from tglw.common.bundle import Doc

from .chars import HAN

MIN_RATIO = 0.2
MIN_HAN = 20
_HAN = re.compile(f"[{HAN}]")
_LETTER = re.compile(r"[^\W\d_]")


@dataclass(frozen=True, slots=True)
class Profile:
    han: int
    letters: int
    ratio: float
    cjk: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def text_profile(texts: list[str]) -> Profile:
    han = sum(len(_HAN.findall(t)) for t in texts)
    letters = sum(len(_LETTER.findall(t)) for t in texts)
    ratio = round(han / letters, 4) if letters else 0.0
    return Profile(han, letters, ratio, han >= MIN_HAN and ratio >= MIN_RATIO)


_CACHE: dict[tuple[str, str], Profile] = {}


def profile(doc: Doc | str) -> Profile:
    """Han characters / all letters (Han included) over the document's text blocks; cached per
    (name, PDF sha256), so a document loaded or named again is not rescanned."""
    if isinstance(doc, str):
        key = (doc, registry.get(doc).sha256)
        if key not in _CACHE:
            _CACHE[key] = _scan(bundle.load(doc))
        return _CACHE[key]
    key = (doc.name, doc.info.sha256)
    if key not in _CACHE:
        _CACHE[key] = _scan(doc)
    return _CACHE[key]


def _scan(d: Doc) -> Profile:
    return text_profile([b.text for b in d.blocks if b.kind == "text" and b.text])


def is_cjk_doc(doc: Doc | str) -> bool:
    """True when at least 20% of the letters are Chinese characters (and there are at least 20).
    The 59 research documents peak at 1.1% (English prospectuses quoting Chinese names)."""
    return profile(doc).cjk
