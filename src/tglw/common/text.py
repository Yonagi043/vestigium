"""Text normalisation, token counting, and a plain page-tagged rendering of a Doc."""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Iterable

from .bundle import Block, Doc

_PUNCT = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2212": "-",
    "\u00a0": " ", "\u00ad": "",
})
_WS = re.compile(r"\s+")


def norm(s: str, lower: bool = True) -> str:
    s = _WS.sub(" ", unicodedata.normalize("NFKC", s).translate(_PUNCT)).strip()
    return s.lower() if lower else s


@lru_cache(maxsize=1)
def _encoder():
    import tiktoken

    return tiktoken.get_encoding("o200k_base")


def count_tokens(s: str) -> int:
    """o200k_base token count: a proxy, not the reading model's own tokenizer."""
    return len(_encoder().encode(s, disallowed_special=()))


_TAGS = {"caption", "footnote", "formula", "code", "reference", "page_header", "page_footer"}


def render_block(b: Block, with_refs: bool = False) -> str:
    ref = f"<{b.ref}> " if with_refs else ""
    if b.kind == "table":
        return f"[TABLE {ref}]\n{b.text}\n[/TABLE]".replace(" ]", "]")
    if b.kind == "picture":
        return f"[PICTURE {ref}]".replace(" ]", "]")
    if b.is_heading:
        return f"[H] {ref}{b.text}"
    if b.label == "list_item":
        return f"{b.marker or '-'} {ref}{b.text}"
    if b.label in _TAGS:
        return f"[{b.label}] {ref}{b.text}"
    return f"{ref}{b.text}"


def render(doc: Doc, blocks: Iterable[Block] | None = None, furniture: bool = True,
           with_refs: bool = False) -> str:
    """Page-tagged plain text in reading order (same layout idea as the T1 renders)."""
    out: list[str] = []
    page = None
    for b in doc.blocks if blocks is None else blocks:
        if not furniture and b.layer != "body":
            continue
        if b.page is not None and b.page != page:
            out.append(f"=== PAGE {b.page} ===")
            page = b.page
        out.append(render_block(b, with_refs))
    return "\n".join(out)
