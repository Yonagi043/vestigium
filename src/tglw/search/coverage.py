"""`add coverage` (stage 3, on saturated): what this conversation has already done in one document, as facts
(LANG_SPEC 11.4). A search most of whose hits were listed before gets, instead of another round, a summary:

    this conversation in this document (355 pages): 14 searches (12 default, 2 literal), 14 pages read
    searched words, whole-word matches in the document: "80 billion" 0 · "280" 4 · "expeditions" 96 · "cost" 123
    literal searches with 0 matches: "80 billion Case Study 2"
    pages read: p1-5 p45 p51-54 p73-74 p296 · whole sections read: s53 2.2.7 Case Study Synthesis
    hit pages listed, not read: p24 p26 p36 p41 p44 p59-60 … +6 more

No conclusion ("not in the document") is drawn. The searched words are each query's parts (absence's
query_parts; a query of up to 3 words counts whole), least frequent first.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Any

from tglw.common.text import count_tokens

from .absence import PART_WORDS, count_literal, pages_with, query_parts
from .tokens import tokens

COVERAGE_MAX = 250
TERMS_MAX, TERMS_KEEP = 10, 4
ZEROS_MAX = 3
SECTIONS_MAX, SECTION_CHARS = 4, 28
RANGES_MAX = 8
UNREAD_REPEAT = "new"  # a repeated summary lists the unread hit pages that are "new", or "full": all, when any changed
UNREAD_RUNS = 10  # stage 0, answerable runs that saturate: evidence among the first 10 unread pages 18/18, first 6: 13/18
_PAGE = re.compile(r"^p(\d+)$")


@dataclass
class Coverage:
    lines: list[str]
    terms: list[dict[str, Any]] = field(default_factory=list)  # {text, n}: whole-word matches in the document
    zeros: list[str] = field(default_factory=list)  # literal searches with 0 matches
    pages: list[int] = field(default_factory=list)  # pages read
    sections: list[str] = field(default_factory=list)  # sections read whole
    unread: list[int] = field(default_factory=list)  # hit pages listed, not read
    tokens: int = 0
    trimmed: int = 0
    new_pages: list[int] = field(default_factory=list)  # pages read since the previous summary (all, for a first one)
    searches: int = 0  # searches summarised
    shown: dict[str, list] = field(default_factory=dict)  # what the model was shown, for the next summary (previous=)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _runs(pages: Iterable[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for p in sorted(set(pages)):
        if runs and p == runs[-1][1] + 1:
            runs[-1][1] = p
        else:
            runs.append([p, p])
    return runs


def shown_pages(pages: Iterable[int], most: int = RANGES_MAX) -> list[int]:
    """The pages page_ranges(pages, most) writes out (not those behind "+N more")."""
    return [p for a, b in _runs(pages)[:most] for p in range(a, b + 1)]


def page_ranges(pages: Iterable[int], most: int = RANGES_MAX) -> str:
    """p1-5 p45 p51-54 … (+N more runs)."""
    runs = _runs(pages)
    shown = [f"p{a}" if a == b else f"p{a}-{b}" for a, b in runs[:most]]
    return " ".join(shown) + (f" +{len(runs) - most} more" if len(runs) > most else "")


@lru_cache(maxsize=4096)
def _whole(name: str, text: str, cjk_state: tuple) -> int:
    from tglw.views import api

    return count_literal(api.view(name), text, whole=True)[0]


COUNTED_MAX = TERMS_MAX + 2  # candidates counted (each count reads the document once)


def searched_terms(v: Any, queries: Iterable[str]) -> list[dict[str, Any]]:
    """Each query's parts (a short query whole), deduplicated, with whole-word matches, least frequent first.
    Only the COUNTED_MAX candidates on the fewest pages of G's index (a phrase: its rarest word) are counted."""
    from tglw.search.tokens import doc_cjk_mode

    texts: list[str] = []
    df = pages_with(v.name)
    for q in queries:
        q = " ".join(q.split())
        for t in ([q] if len(q.split()) <= PART_WORDS else query_parts(q, df)):
            if t and t.lower() not in (x.lower() for x in texts):
                texts.append(t)
    rank = {t: min((df(w) for w in tokens(t, "none", doc_cjk_mode(v.doc))), default=0) for t in texts}
    picked = sorted(texts, key=lambda t: rank[t])[:COUNTED_MAX]
    state = (doc_cjk_mode(v.doc),)
    terms = [{"text": t, "n": _whole(v.name, t, state)} for t in texts if t in picked]
    return sorted(terms, key=lambda x: x["n"])  # stable: equal counts keep the order searched


def whole_sections(v: Any, read: set[int]) -> list[str]:
    """Outermost sections all of whose pages were read (the root left out)."""
    full = []
    for sid, n in v.sections.items():
        span = v.section_pages(sid) if n.get("kind") != "root" else None
        if span and all(p in read for p in range(span[0], span[1] + 1)):
            full.append(sid)
    keep = set(full)
    out = []
    for sid in full:
        parent = v.sections[sid].get("parent")
        while parent and parent not in keep:
            parent = v.sections.get(parent, {}).get("parent")
        if not parent:
            out.append(sid)
    return out


def _addr_page(v: Any, addr: str) -> int | None:
    from tglw.views.docview import long_ref

    if m := _PAGE.match(addr):
        return int(m.group(1))
    b = v.doc.by_ref.get(long_ref(addr) or "")
    return b.page if b is not None else None


def _section_label(v: Any, sid: str) -> str:
    label = v.section_label(sid)
    return f"{sid} " + (label if len(label) <= SECTION_CHARS else label[:SECTION_CHARS - 1] + "…")


def coverage(v: Any, searches: Iterable[tuple[str, str]], pages: Iterable[int] = (), listed: Iterable[str] = (),
             budget: int = COVERAGE_MAX, previous: Coverage | None = None) -> Coverage | None:
    """The coverage summary of a document `v` (a views DocView or a registered name) for this conversation:
    `searches` (query, mode) in order, mode "literal" or "bm25"; `pages` delivered by read or context;
    `listed` hit addresses shown. Capped at min(budget, COVERAGE_MAX) tokens; None when not even the
    first line fits. With `previous` (the last summary this conversation got for the document), only what
    that summary did not show: new searched words, literal zeros, pages read and unread hit pages, or one
    line when nothing is new."""
    from tglw.views import api

    v = api.view(v) if isinstance(v, str) else v
    cap = min(budget, COVERAGE_MAX)
    searches = [(" ".join(q.split()), m) for q, m in searches if q and q.strip()]
    read = set(pages)
    with api._cjk_of(v):
        terms = searched_terms(v, [q for q, _ in searches])
        zeros = list(dict.fromkeys(q for q, m in searches if m == "literal" and count_literal(v, q)[0] == 0))
    all_unread = sorted({p for a in listed if (p := _addr_page(v, a)) is not None} - read)
    unread = all_unread
    literal = sum(m == "literal" for _, m in searches)
    old = previous.shown if previous is not None else {}
    if previous is not None:
        terms = [x for x in terms if x["text"] not in old.get("terms", [])]
        zeros = [z for z in zeros if z not in old.get("zeros", [])]
        if UNREAD_REPEAT == "full":  # the whole line again when what it would show changed, a page read included
            unread = all_unread if shown_pages(all_unread, UNREAD_RUNS) != old.get("unread_last", []) else []
        else:  # only pages no summary has written out yet ("+N more" ones were not)
            unread = [p for p in all_unread if p not in old.get("unread", [])]
        new_read = sorted(read - set(old.get("pages", [])))
        new_searches = len(searches) - previous.searches
        totals = f"{len(searches)} searches, {len(read)} pages read; "
        head = totals + (f"since the summary at search {previous.searches}: +{new_searches} searches, "
                         f"+{len(new_read)} pages read")
        if not (terms or zeros or unread or new_read):
            head = totals + f"nothing new since the summary at search {previous.searches}"
    else:
        new_read = sorted(read)
        head = (f"this conversation in this document ({v.doc.page_count} pages): {len(searches)} searches "
                f"({len(searches) - literal} default, {literal} literal), {len(read)} pages read")
    sections = whole_sections(v, read) if previous is None else [x for x in whole_sections(v, read)
                                                                 if x not in old.get("sections", [])]
    new = "new " if previous is not None else ""
    trimmed = 0

    def assemble() -> list[str]:
        lines = [head]
        if terms:
            lines.append(f"{new}searched words, whole-word matches in the document: "
                         + " · ".join(f'"{x["text"]}" {x["n"]}' for x in terms[:TERMS_MAX]))
        if zeros:
            lines.append(f"{new}literal searches with 0 matches: " + ", ".join(f'"{z}"' for z in zeros[:ZEROS_MAX])
                         + (f" +{len(zeros) - ZEROS_MAX}" if len(zeros) > ZEROS_MAX else ""))
        if new_read:
            lines.append(f"{new}pages read: " + page_ranges(new_read)
                         + (" · whole sections read: " + ", ".join(_section_label(v, s) for s in sections[:SECTIONS_MAX])
                            if sections else ""))
        if unread:
            lines.append(("" if UNREAD_REPEAT == "full" else new) + "hit pages listed, not read: " + page_ranges(unread, UNREAD_RUNS))
        return lines

    def size() -> int:
        return count_tokens("\n".join(assemble()))

    while size() > cap and len(terms) > TERMS_KEEP:
        terms.pop()
        trimmed += 1
    while size() > cap and sections:
        sections.pop()
        trimmed += 1
    while size() > cap and len(zeros) > 1:
        zeros.pop()
        trimmed += 1
    for drop in ("terms", "zeros"):
        while size() > cap and (terms if drop == "terms" else zeros):
            (terms if drop == "terms" else zeros).pop()
            trimmed += 1
    if size() > cap:
        if count_tokens(head) > cap:
            return None
        lines = [head]
    else:
        lines = assemble()
    shown_terms = [x["text"] for x in terms[:TERMS_MAX]] if len(lines) > 1 else []
    shown_zeros = zeros[:ZEROS_MAX] if len(lines) > 1 else []
    body = len(lines) > 1
    written = shown_pages(unread, UNREAD_RUNS) if body else []
    shown = {"terms": old.get("terms", []) + shown_terms, "zeros": old.get("zeros", []) + shown_zeros,
             "pages": sorted(set(old.get("pages", [])) | (set(new_read) if body else set())),
             "unread": sorted(set(old.get("unread", [])) | set(written)),
             "unread_last": shown_pages(all_unread, UNREAD_RUNS) if body else old.get("unread_last", []),
             "sections": old.get("sections", []) + (sections[:SECTIONS_MAX] if body else [])}
    return Coverage(lines, terms[:TERMS_MAX], zeros[:ZEROS_MAX], sorted(read), sections[:SECTIONS_MAX],
                    unread, count_tokens("\n".join(lines)), trimmed, new_read, len(searches), shown)
