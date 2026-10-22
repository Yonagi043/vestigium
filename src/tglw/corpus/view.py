"""Library search as the reader's tools show it: one line per hit, its address naming the document.

    search "query" · library: 59 documents · top 5 · engine G
    documents by best match: nist__NIST.SP.800-53r5 · nist__NIST.SP.800-53B (+7 more with matches)
    1. nist__NIST.SP.800-53r5:t1201 · p120 · s45 IR-4 INCIDENT HANDLING | excerpt …
"""

from __future__ import annotations

from pathlib import Path

from . import rank

DOCS_SHOWN = 5
PATH_DEPTH, PART_CHARS, LEAF_CHARS = 2, 32, 60
NO_WORDS = "query has no searchable words in this index; try other wording"


def _cut(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n - 1] + "…"


def short_path(titles: list[str]) -> str:
    """Last PATH_DEPTH titles, parents cut at PART_CHARS and the hit's own section at LEAF_CHARS (as the
    reader's single-document search shows them)."""
    parts = [" ".join(t.split()) for t in titles[-PATH_DEPTH:]]
    return " > ".join(_cut(p, LEAF_CHARS if i == len(parts) - 1 else PART_CHARS) for i, p in enumerate(parts))


def page_tag(page: int, label: str | None) -> str:
    return f"p{page}" if label in (None, str(page)) else f"p{page} (printed {label})"


def hit_line(rank_no: int, h: rank.Hit) -> str:
    path = short_path(h.section)
    note = (" · figure" if h.figure else "") + (" · fuzzy" if h.fuzzy else "") \
        + (f" · same text in {', '.join(h.same)}" if h.same else "")
    return (f"{rank_no}. {h.address} · {page_tag(h.page, h.label)}" + (f" · {h.sid} {path}" if path else "")
            + note + f" | {h.excerpt}")


def search_text(query: str, k: int = 5, unit: str = "block", docs: list[str] | None = None,
                path: Path | None = None) -> str:
    """The tool text of a library search (over `docs` when given, e.g. a server's allowlist)."""
    lib = rank.library(path)
    scope = len(docs) if docs is not None else len(lib.docs)
    head = f'search "{query}" · library: {scope} documents · top {k} · {unit}s · engine G'
    res = rank.query(query, k=k, docs=docs, unit=unit, path=path)
    if not res.words:
        return f"{head}\n{NO_WORDS}"
    if not res.hits:
        return f'{head}\n0 matches for "{query}" in the library'
    names = [d for d, _ in res.docs]
    more = len(names) - DOCS_SHOWN
    lines = [head, "documents by best match: " + " · ".join(names[:DOCS_SHOWN])
             + (f" (+{more} more with matches)" if more > 0 else "")]
    lines += [hit_line(i, h) for i, h in enumerate(res.hits, 1)]
    return "\n".join(lines)
