"""Every literal match of a document as addresses only, grouped by section in reading order: answers
"which clauses mention X" in one call where literal search shows at most 10 hits.

    37 matches for "SI-4" in 29 blocks, 21 sections · literal · every match, addresses only
    s139 AC-17(5) REMOTE ACCESS | MONITORING FOR UNAUTHORIZED CONNECTIONS: t1262 p76
    s988 SI-4 SYSTEM MONITORING: t7802 p352 (3×), t7805 p353, …

Matches are the reader's literal search (tglw.views.api._literal_hits: exact text, any case, tables
and text inside pictures included). Whole-word matches are listed; matches only inside a longer word
("SI-40") are counted, and listed only when there is no whole-word match. A list longer than
LIST_MAX_CHARS ends with "next: offset=N".
"""

from __future__ import annotations

from tglw.views import api
from tglw.views.docview import DocView, short_ref

LIST_MAX_CHARS = 6000
LABEL_CHARS = 60


def _section(v: DocView, sid: str | None) -> str:
    n = v.sections.get(sid or "", {})
    if not sid or n.get("kind") == "root":
        return "(no section)"
    label = v.section_label(sid)
    key = n.get("number_key") if n.get("number_kind") == "control" else None
    label = (f"{key} " if key and key not in label else "") + label
    return f"{sid} {label if len(label) <= LABEL_CHARS else label[:LABEL_CHARS - 1] + '…'}"


def list_literal(doc: str | DocView, query: str, offset: int = 0, max_chars: int = LIST_MAX_CHARS) -> str:
    """Every block holding `query` (from block `offset` on), one line per section: "sid label: t12 p3,
    tab4 p5 (2×)"."""
    v = api._view(doc)
    hits = api._literal_hits(v, query)
    if not hits:
        return (f'0 matches for "{query}" in this document (exact text, any case, tables included)\n'
                "try other wording, or mode='bm25'")
    whole = [h for h in hits if h[0]]
    shown, partial = (whole, len(hits) - len(whole)) if whole else (hits, 0)
    shown.sort(key=lambda h: v.place(h[2]))
    sections = {v.section_of(h[3]) for h in shown}
    total = sum(h[1] for h in shown)
    head = (f'{total} matches for "{query}" in {len(shown)} blocks, {len(sections)} sections · literal · '
            "every match, addresses only" + ("" if whole else " (all inside longer words)"))
    if partial:
        head += f'\n+{partial} more blocks hold "{query}" only inside a longer word, not listed'
    start = max(0, offset)
    if start >= len(shown):
        return f"{head}\n(no blocks from offset={start}; {len(shown)} in all)"
    lines = [head] + ([f"(from block {start + 1} of {len(shown)})"] if start else [])
    used, sid = sum(len(x) + 1 for x in lines), object()
    for i in range(start, len(shown)):
        _, count, seg, owner = shown[i]
        item = f"{short_ref(owner.ref)} p{seg.page}" + (f" ({count}×)" if count > 1 else "")
        new = v.section_of(owner) != sid
        piece = f"{_section(v, v.section_of(owner))}: {item}" if new else f", {item}"
        if i > start and used + len(piece) + 1 > max_chars:
            lines.append(f"next: offset={i} ({len(shown) - i} more blocks)")
            break
        if new:
            lines.append(piece)
            sid = v.section_of(owner)
        else:
            lines[-1] += piece
        used += len(piece) + new
    return "\n".join(lines)
