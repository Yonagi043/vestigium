"""Word tokens shared by the layer and Docling sides: normalised, Han split per character."""

from __future__ import annotations

import regex

from tglw.common.text import norm

_TOKEN = regex.compile(r"\p{Han}|[\p{L}\p{N}]+")


def tokens(s: str) -> list[str]:
    return _TOKEN.findall(norm(s))
