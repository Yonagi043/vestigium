"""Chapters from running headers, for documents without PDF bookmarks.

A Hong Kong circular prints each chapter's name at the top of its pages ("釋 義", "董 事 會 函 件",
"附錄一 本集團之財務資料") and has neither bookmarks nor chapter headings Docling keeps: the chapter
title is the running header itself, as page-header furniture or as a body heading (task A's
running_headers). Runs of pages under one header become level-1 bookmark entries, so the outline route
builds the chapters and numbers each chapter's own "1. 2. 3." under it.
"""

from __future__ import annotations

from tglw.common.bundle import Block, Doc
from tglw.zh import normalize_display

from .outline import Entry
from .roles import page_height, text_key

DOC_WIDE = 0.6  # a header line on more pages than this share names the document, not a chapter
TOP = 0.85  # share of the page height above which a block counts as a header


def _top(doc: Doc, b: Block) -> bool:
    return b.bbox is not None and b.bbox[1] >= TOP * page_height(doc, b.page)


def page_headers(doc: Doc, margin: set[str]) -> dict[int, list[Block]]:
    """Header lines per page: page-header furniture and task A's running headers, top to bottom."""
    out: dict[int, list[Block]] = {}
    for b in doc.blocks:
        furniture = b.layer != "body" and b.label == "page_header"
        if b.page and b.kind == "text" and text_key(b.text) and (furniture or b.ref in margin) and _top(doc, b):
            out.setdefault(b.page, []).append(b)
    common = {k for k in {text_key(b.text) for bs in out.values() for b in bs}
              if sum(any(text_key(b.text) == k for b in bs) for bs in out.values()) > DOC_WIDE * doc.page_count}
    return {p: sorted((b for b in bs if text_key(b.text) not in common), key=lambda b: (-b.bbox[1], b.bbox[0]))
            for p, bs in out.items()}


def _title(blocks: list[Block]) -> str:
    return " ".join(normalize_display(b.text.strip()) for b in blocks)


def entries(doc: Doc, margin: set[str]) -> list[Entry]:
    """One level-1 entry per run of pages sharing a header; [] unless there are at least two chapters.
    A run starts one page earlier when that page opens with a heading reading as the header (the
    chapter's own title page, which Docling does not file as a running header). A chapter takes its
    whole first page, whatever order Docling read that page's blocks in; its number is read from the
    header's first line alone ("附錄五", not "附錄五 一般資料", whose 一 would join the number)."""
    heads = {p: bs for p, bs in page_headers(doc, margin).items() if bs}
    runs: list[list] = []
    for p in sorted(heads):
        key = text_key(_title(heads[p]))
        if runs and runs[-1][2] == key and p - runs[-1][1] <= 2:
            runs[-1][1] = p
        else:
            runs.append([p, p, key, heads[p]])
    if len({r[2] for r in runs}) < 2:
        return []
    out = []
    for i, (first, _, key, lines) in enumerate(runs):
        own = _opening(doc, first - 1, key) if first > 1 else None
        page = own.page if own else first
        body = [b for b in doc.blocks if b.page == page and b.layer == "body" and not b.in_picture]
        if not body:
            continue
        anchor = own or body[0]
        out.append(Entry(i, 1, _title(lines), page, None, ref=anchor.ref, order=min(b.order for b in body),
                         method="text" if own else "page", score=100.0, depth=1, flags=["running_header"],
                         number=normalize_display(lines[0].text)))
    return out


def _opening(doc: Doc, page: int, key: str) -> Block | None:
    """A heading at the top of `page` that reads as the header `key` (or one of its lines)."""
    for b in doc.blocks:
        if b.page == page and b.layer == "body" and b.is_heading and _top(doc, b):
            k = text_key(b.text)
            if k and (k == key or k in key):
                return b
    return None
