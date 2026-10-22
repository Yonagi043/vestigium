"""Resolve a mention against the document's target index (and 53r5's controls for other NIST docs)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache

from rapidfuzz import fuzz

from tglw.common.bundle import Block
from tglw.common.paths import DATA
from tglw.pagemap.build import pdf_pages
from tglw.common.text import norm

from .anchors import Anchor, Index, appendix_id, raw_text, same_title, simple
from .detect import Mention
from .keys import caption_key, control_key, note_key, number_key, section_key

CONTROL_DOC = "nist__NIST.SP.800-53r5"
CONTROL_FRAMEWORK = "SP80053"
_ENUM = re.compile(r"^\s*(?:[A-Za-z0-9]{1,3}[.)]|\([A-Za-z0-9]{1,4}\))\s+")
_DASH_SPLIT = re.compile(r"\s+-\s*|\s*-\s+")


@dataclass
class Resolution:
    status: str
    method: str
    targets: list[dict] = field(default_factory=list)
    precision: str = ""
    reason: str = ""


def target(doc: str, a: Anchor) -> dict:
    return {"doc": doc, "ref": a.ref, "page": a.page, "title": a.title, "source": a.source}


def _unique(cands: list[Anchor]) -> list[Anchor]:
    seen, out = set(), []
    for a in cands:
        if a.ref not in seen:
            seen.add(a.ref)
            out.append(a)
    return out


def _same_part(cands: list[Anchor], src: Block, idx: Index) -> list[Anchor]:
    """Candidates in the source's numbering scope in task A's tree (e.g. GPT-4's System Card, 61r2's FAQ)."""
    top = idx.block_top.get(src.ref)
    return [a for a in cands if top and a.top == top]


def _pick(doc: str, cands: list[Anchor], src: Block, method: str, precision: str = "exact",
          idx: Index | None = None) -> Resolution:
    """One target, or repeats of one title (multi-page captions, running headings): resolved to the first.
    Several distinct targets: the only one in the source's top-level part, else ambiguous, nearest first."""
    cands = _unique(cands)
    if len({simple(a.title) for a in cands}) == 1:
        return Resolution("resolved", method, [target(doc, cands[0])], precision)
    if idx is not None and len(same := _same_part(cands, src, idx)) == 1:
        return Resolution("resolved", method + "-same-part", [target(doc, same[0])], precision)
    near = sorted(cands, key=lambda a: (a.source != "A:outline", abs(a.order - src.order)))
    return Resolution("ambiguous", method, [target(doc, a) for a in near], precision,
                      f"{len(cands)} candidates")


def resolve(m: Mention, src: Block, idx: Index, controls: Index | None) -> Resolution:
    if m.kind == "external":
        return Resolution("external", "document-id", reason=m.key)
    if "external" in m.qual:
        return Resolution("external", "external-qualifier", reason=m.qual["external"][:80])
    fn = {"table": _caption, "figure": _caption, "section": _section, "appendix": _appendix, "note": _note,
          "item": _item, "page": _page, "control": _control}[m.kind]
    return fn(m, src, idx, controls)


def _caption(m: Mention, src: Block, idx: Index, _: Index | None) -> Resolution:
    key = caption_key(m.key)
    cands = idx.get(m.kind, key)
    precision = "exact"
    if not cands and re.search(r"\d[A-Z]$", key):
        cands, precision = idx.get(m.kind, key[:-1]), "parent"
    if not cands and (r := re.fullmatch(r"(\d+)-(\d+)", m.key)) and int(r.group(1)) < int(r.group(2)):
        cands, precision = idx.get(m.kind, r.group(1)), "range-start"
    if not cands:
        return Resolution("unresolved", "caption-number", reason=f"no {m.kind} titled {key}")
    main = [a for a in cands if not a.cont] or cands
    main = _unique([a for a in main if a.source != "inline-caption"] or main)
    pages = [a.page or 0 for a in main]
    if len(main) > 1 and max(pages) - min(pages) <= 1:
        best = min(main, key=lambda a: (a.ref.split("/")[1] == "texts", a.source != "caption", a.order))
        return Resolution("resolved", "caption-number", [target(idx.doc.name, best)], precision)
    return _pick(idx.doc.name, main, src, "caption-number", precision)


def _section(m: Mention, src: Block, idx: Index, _: Index | None) -> Resolution:
    if m.qual.get("label") == "quoted":
        return _title_match(m, src, idx)
    chapter = "chapter" in m.qual
    key = number_key("chapter", m.key) if chapter else section_key(m.key)
    if not key:
        return Resolution("unresolved", "heading-number", reason=f"unparseable number {m.key}")
    cands = idx.get("section", key)
    if chapter and (c := [a for a in cands if a.source == "heading-chapter"]):
        cands = c
    elif not chapter and (c := [a for a in cands if a.source != "heading-chapter"]):
        cands = c
    method = "requirement-number" if m.qual.get("label") == "requirement" else "heading-number"
    if len(_unique(cands)) > 1 and len(same := _same_part(_unique(cands), src, idx)) == 1:
        return Resolution("resolved", method + "-same-part", [target(idx.doc.name, same[0])], "exact")
    if len(_unique(cands)) > 1 and len(fit := [a for a in _unique(cands) if a.depth == key.count(".") + 1]) == 1:
        return Resolution("resolved", method + "-depth", [target(idx.doc.name, fit[0])], "exact")
    if cands:
        return _pick(idx.doc.name, cands, src, method)
    if re.fullmatch(r"\d{3,}", key) and not chapter:
        return Resolution("external", "statute-number",
                          reason=f"section {key}: statute-style number, no such section in this document")
    parent = key
    while "." in parent:
        parent = parent.rsplit(".", 1)[0]
        if cands := idx.get("section", parent):
            return _pick(idx.doc.name, cands, src, method, "parent", idx=idx)
    return Resolution("unresolved", method, reason=f"no heading numbered {key}")


def _appendix(m: Mention, src: Block, idx: Index, _: Index | None) -> Resolution:
    doc = idx.doc.name
    if "." in m.key:
        if (k := section_key(m.key)) and (cands := idx.get("section", k)):
            return _pick(doc, cands, src, "appendix-section", idx=idx)
        key, precision = number_key("appendix", m.key.split(".")[0]), "parent"
    else:
        key, precision = number_key("appendix", m.key), "exact"
    cands = idx.get("appendix", key)
    if not cands and key and (subs := sorted(k for k in idx.anchors.get("appendix", {}) if re.fullmatch(rf"{key}[AB]", k))):
        firsts = [next((a for a in idx.get("appendix", k) if a.source == "outline"), idx.get("appendix", k)[0]) for k in subs]
        return Resolution("ambiguous", "appendix-heading", [target(doc, a) for a in firsts],
                          reason=f"no appendix {key}; the document has {', '.join(subs)}")
    if not cands:
        return Resolution("unresolved", "appendix-heading", reason=f"no appendix {key}")
    if len(same := _same_part(_unique(cands), src, idx)) == 1 and len(_unique(cands)) > 1:
        return Resolution("resolved", "appendix-heading-same-part", [target(doc, same[0])], precision)
    first = next((a for a in cands if a.source in ("outline", "A:outline")), cands[0])
    return Resolution("resolved", "appendix-heading", [target(doc, first)], precision)


def _note(m: Mention, src: Block, idx: Index, _: Index | None) -> Resolution:
    doc = idx.doc.name
    key, parent = note_key(m.key)
    precision = "parent" if "(" in m.key else "exact"
    scope = m.qual.get("appendix", "")
    if not scope and (ch := idx.chapter_at(src.order)) and re.search("ACCOUNTANT", ch.title, re.I):
        scope = appendix_id(ch.title)
    cands = idx.get("note", key)
    if not cands and parent:
        cands, precision = idx.get("note", parent), "parent"
    if cands and all(a.scope for a in cands) and not scope and "scope" not in m.qual:
        if local := _local_note(m, src, idx):
            return Resolution("resolved", "local-note", [{"doc": doc, "ref": local.ref, "page": local.page,
                                                           "title": local.text[:120]}])
        if m.qual.get("paren"):
            return Resolution("unresolved", "local-note",
                              reason=f"chart/table note ({key}) not found nearby; not sent to the accountants' report")
    scopes = {a.scope for lst in idx.anchors.get("note", {}).values() for a in lst if a.scope}
    if scope and scopes:
        named = {scope} if scope in scopes else {x for x in scopes if x.startswith(scope)}
        if named and not (c := [a for a in cands if a.scope in named]):
            return Resolution("unresolved", "note-heading",
                              reason=f"no note {key} heading in Appendix {scope} (only in another report)")
        cands = c if named else cands
    if cands:
        scopes = {a.scope for a in cands}
        if len(scopes) == 1:
            return Resolution("resolved", "note-heading", [target(doc, cands[0])], precision)
        return Resolution("ambiguous", "note-heading", [target(doc, a) for a in cands], precision,
                          f"note {key} in {sorted(scopes)}")
    if local := _local_note(m, src, idx):
        return Resolution("resolved", "local-note", [{"doc": doc, "ref": local.ref, "page": local.page,
                                                       "title": local.text[:120]}])
    return Resolution("unresolved", "note-heading", reason=f"no note {key} heading or nearby note list")


def _local_note(m: Mention, src: Block, idx: Index) -> Block | None:
    """A chart/table note: '(Note 5)' -> a later block 'Note 5 ...' or '(5) ...' after a 'Notes:' label."""
    n = re.match(r"\d+(?:\.\d+)?", m.key).group(0)
    labelled = False
    for b in idx.doc.blocks[src.order + 1:]:
        if b.page is None or src.page is None or b.page > src.page + 2:
            break
        if b.layer != "body":
            continue
        if re.match(rf"^\s*Notes?\s*[:.]?\s*\(?{re.escape(n)}\)?(?!\d|\.\d)", b.text, re.I):
            return b
        if re.match(r"^\s*Notes?\s*:?\s*$", b.text, re.I) or re.match(r"^\s*Notes?\s*:", b.text, re.I):
            labelled = True
        if (labelled or "." in n) and re.match(rf"^\s*\(?{re.escape(n)}[.):]?\s+\S", raw_text(idx.doc, b)):
            return b
    return None


def _item(m: Mention, src: Block, idx: Index, _: Index | None) -> Resolution:
    key = number_key("item", m.key)
    cands = idx.get("item", key)
    if not cands:
        return Resolution("unresolved", "item-heading", reason=f"no Item {key} heading")
    part = number_key("part", m.qual["part"]) if "part" in m.qual else None
    if part:
        cands = [a for a in cands if a.scope == part]
        if not cands:
            return Resolution("unresolved", "item-heading", reason=f"no Part {part} Item {key}")
        return _pick(idx.doc.name, cands, src, "item-heading")
    if len(cands) > 1 and (same := [a for a in cands if a.scope == idx.part_at(src.order)]):
        return _pick(idx.doc.name, same, src, "item-heading-same-part")
    return _pick(idx.doc.name, cands, src, "item-heading")


def _page(m: Mention, src: Block, idx: Index, _: Index | None) -> Resolution:
    side = pagemap(idx.doc.name)
    if side is None:
        return Resolution("unresolved", "page-label", reason="awaiting_B")
    pages = pdf_pages(side, m.key)
    if not pages:
        return Resolution("unresolved", "page-label", reason=f"printed page {m.key} not in task B pagemap")
    if len(pages) > 1:
        run = next((r for r in side["runs"] if src.page is not None and r["pdf_first"] <= src.page <= r["pdf_last"]), None)
        ctx = ((run or {}).get("run_context") or {}).get("text")
        same = pdf_pages(side, m.key, context=ctx) if ctx else []
        if len(same) == 1:
            return Resolution("resolved", "page-label-context", [_page_target(idx, same[0], m.key)],
                              reason=f"same numbering run as the source: {ctx}")
        return Resolution("ambiguous", "page-label", [_page_target(idx, p, m.key) for p in pages],
                          reason=f"printed page {m.key} on PDF pages {pages}")
    return Resolution("resolved", "page-label", [_page_target(idx, pages[0], m.key)])


def _page_target(idx: Index, page: int, label: str) -> dict:
    first = next((b for b in idx.doc.blocks if b.page == page and b.layer == "body"), None)
    return {"doc": idx.doc.name, "ref": first.ref if first else None, "page": page,
            "title": f"printed page {label}"}


@lru_cache(maxsize=None)
def pagemap(name: str) -> dict | None:
    """Task B's pagemap sidecar, only once B has delivered (MANIFEST present)."""
    path = DATA / "B" / f"{name}.pagemap.json"
    if not (DATA / "B" / "MANIFEST.json").exists() or not path.exists():
        return None
    return json.loads(path.read_text())


def _control(m: Mention, src: Block, idx: Index, controls: Index | None) -> Resolution:
    fw = m.qual.get("framework")
    if fw and fw != CONTROL_FRAMEWORK:
        return Resolution("external", "framework-label", reason=f"{fw} (not SP 800-53)")
    key = control_key(m.key)
    cidx = idx if idx.doc.name == CONTROL_DOC else controls
    if cidx is None or not key:
        return Resolution("unresolved", "control-heading", reason="no control index")
    cands, precision = cidx.get("control", key), "parent" if m.qual.get("part") else "exact"
    method = "control-heading"
    if not cands and (cands := cidx.get("control_a", key)):
        method = "control-node"
    if not cands and "(" in key:
        cands, precision = cidx.get("control", key.split("(")[0]), "parent"
    if not cands:
        return Resolution("unresolved", "control-heading", reason=f"{key} not found in 53r5")
    method += "" if cidx is idx else "-53r5"
    return Resolution("resolved", method, [target(cidx.doc.name, cands[0])], precision)


def _title_match(m: Mention, src: Block, idx: Index) -> Resolution:
    doc = idx.doc.name
    raw = norm(m.key)
    rel = raw.startswith("-")
    parts = [p for p in _DASH_SPLIT.split(raw.lstrip("- ")) if p.strip()]
    if not rel and (whole := next((c for c in idx.chapters if same_title(norm(c.title), raw)), None)):
        return Resolution("resolved", "title-match", [_chapter_target(doc, whole)])
    chapter = None
    if rel:
        chapter = idx.chapter_at(src.order)
    elif "appendix" in m.qual:
        chapter = next((c for c in idx.chapters if appendix_id(c.title) == m.qual["appendix"]), None)
    if chapter is None and not rel and parts:
        chapter = next((c for c in idx.chapters if _chapter_title_match(c.title, parts[0])), None)
        if chapter is not None:
            parts = parts[1:]
    lo, hi = (chapter.start, chapter.end) if chapter else (0, len(idx.doc.blocks))
    if not parts:
        if chapter is None:
            return Resolution("unresolved", "title-match", reason="empty title")
        return Resolution("resolved", "title-match", [_chapter_target(doc, chapter)])
    hits = _find_heading(idx, parts, lo, hi)
    if hits:
        if chapter is not None or len({simple(h.text) for h in hits}) == 1 or len(hits) == 1:
            return Resolution("resolved", "title-match", [_block_target(doc, hits[0])])
        near = sorted(hits, key=lambda h: abs(h.order - src.order))
        return Resolution("ambiguous", "title-match", [_block_target(doc, h) for h in near],
                          reason=f"{len(hits)} headings")
    if chapter is not None:
        return Resolution("resolved", "title-match", [_chapter_target(doc, chapter)], "parent",
                          f"sub-heading '{parts[-1]}' not found in chapter")
    return Resolution("unresolved", "title-match", reason="no heading or chapter with this title")


def _chapter_title_match(chapter_title: str, part: str) -> bool:
    t = re.sub(r"^\s*appendix\s+[ivx]+[ab]?\s*-?\s*", "", norm(chapter_title))
    return same_title(t, part) or same_title(norm(chapter_title), part)


def _find_heading(idx: Index, parts: list[str], lo: int, hi: int) -> list[Block]:
    start = lo
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        hits = [h for h in idx.headings if start <= h.order < hi and _heading_match(h.text, part)]
        if not hits:
            want = simple(_ENUM.sub("", part))
            hits = [b for b in idx.doc.blocks[start:hi] if b.kind == "text" and b.layer == "body"
                    and len(b.text) <= 150 and simple(_ENUM.sub("", b.text)) == want]
        if not hits:
            if last:
                return []
            continue
        if last:
            return hits
        start = hits[0].order
    return []


def _heading_match(text: str, part: str) -> bool:
    a, b = simple(_ENUM.sub("", text)), simple(_ENUM.sub("", part))
    if not a or not b:
        return False
    if a == b or (len(b) >= 12 and a.startswith(b)) or (len(a) >= 12 and b.startswith(a)):
        return True
    return min(len(a), len(b)) >= 12 and fuzz.ratio(a, b) >= 92


def _chapter_target(doc: str, c) -> dict:
    return {"doc": doc, "ref": c.ref, "page": c.page, "title": c.title}


def _block_target(doc: str, b: Block) -> dict:
    return {"doc": doc, "ref": b.ref, "page": b.page, "title": b.text[:120]}
