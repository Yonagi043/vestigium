"""context(doc, addr): where an addressed range sits and what it points to, in one compact text.

Parts, in order: path (each level with number, title, pages), the parent's opening paragraphs, sibling
and child sections with sizes, references out of the range (C2) and into it, terms used in it (C1;
uncertain ones only as their source sentence), and footnotes. Stops at max_tokens with "next: part=…".
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Callable

from tglw.common.text import count_tokens

from .api import _block_tokens, _tok, _view, resolve
from .docview import DocView, Segment, short_ref

CONTEXT_MAX_TOKENS = 1500
LEAD_PARAGRAPHS = 2
LEAD_WORDS = 60
SIBLINGS = 12
CHILDREN = 12
REFS_OUT = 12
REFS_IN = 6
TERMS = 10
FOOTNOTES = 6
SNIPPET_WORDS = 14
SCOPE_MAX_BLOCKS = 200
PARTS = ("path", "parent", "siblings", "children", "refs_out", "refs_in", "terms", "footnotes")


def _words(text: str, n: int) -> str:
    w = text.split()
    return " ".join(w[:n]) + (" …" if len(w) > n else "")


def _sec_line(v: DocView, sid: str) -> str:
    n = v.sections[sid]
    pages = v.section_pages(sid)
    where = v.page_range_tag(*pages) if pages else "no pages"
    tok = sum(_block_tokens(v.name)[n["start_order"]:n["end_order"]])
    return f"{sid} {v.section_label(sid)} · {where} · {_tok(tok)}"


def _kids(v: DocView, sid: str | None) -> list[str]:
    return [c for c, n in v.sections.items() if n.get("parent") == sid]


def _primary(v: DocView, addr: str, segs: list[Segment]) -> str | None:
    base = addr.split(" from ")[0].strip()
    if base in v.sections:
        return base
    counts = Counter(v.section_of(s.block) for s in segs if v.section_of(s.block))
    return counts.most_common(1)[0][0] if counts else None


class _Range:
    """The addressed blocks, and the scope used for references: the range, its deepest section when
    that is small, and the footnotes the range points to."""

    def __init__(self, v: DocView, addr: str):
        r = resolve(v, addr)
        self.note = r.note
        self.segs = [s for s in r.segments if v.visible(s.block, s.page) or s.block.in_picture]
        self.refs = {s.block.ref for s in self.segs}
        self.pages = sorted({s.page for s in self.segs})
        self.text = "\n".join(s.text for s in self.segs)
        self.sid = _primary(v, addr, self.segs)
        self.scope = set(self.refs)
        if self.sid:
            n = v.sections[self.sid]
            if n["end_order"] - n["start_order"] <= SCOPE_MAX_BLOCKS:
                self.scope |= {b.ref for b in v.doc.blocks[n["start_order"]:n["end_order"]]}
        for x in (v.side.xrefs or {}).get("refs", []):
            if x["kind"] == "footnote" and x["src_ref"] in self.refs and x["status"] == "resolved":
                self.scope |= {t["ref"] for t in x["targets"] if t.get("doc") in (None, v.name) and t.get("ref")}


def _path(v: DocView, rg: _Range) -> list[str]:
    chain, sid = [], rg.sid
    while sid and v.sections[sid].get("kind") != "root":
        chain.append(sid)
        sid = v.sections[sid].get("parent")
    tables = _table_parts(v, rg)
    if not chain:
        return ["path: (no section)"] + tables
    return ["path: " + " > ".join(f"{s} {v.section_label(s)}" for s in reversed(chain))] + tables


def _table_parts(v: DocView, rg: _Range) -> list[str]:
    """Where the rest of a table split over pages is (D2's links)."""
    chains = {tuple(v.table_parts(s.block.ref)) for s in rg.segs if s.block.kind == "table"}
    return ["table parts: " + ", ".join(f"{short_ref(r)} p{v.doc.by_ref[r].page}" for r in parts)
            for parts in sorted(chains, key=lambda c: v.doc.by_ref[c[0]].order) if len(parts) > 1]


def _parent(v: DocView, rg: _Range) -> list[str]:
    if not rg.sid:
        return []
    parent = v.sections[rg.sid].get("parent")
    if not parent or v.sections[parent].get("kind") == "root":
        return []
    n = v.sections[parent]
    first_child = min((v.sections[c]["start_order"] for c in _kids(v, parent)), default=n["end_order"])
    lead = [b for b in v.doc.blocks[n["start_order"]:first_child]
            if v.visible(b) and b.layer == "body" and b.kind == "text" and not b.is_heading and b.ref != n.get("heading_ref")]
    lead = lead[:LEAD_PARAGRAPHS]
    if not lead:
        return [f"parent {parent} has no text before its first subsection"]
    return [f"parent {parent} opens ({v.page_tag(lead[0].page)}):"] + [f'  "{_words(v.display(b.text), LEAD_WORDS)}"' for b in lead]


def _listing(v: DocView, title: str, sids: list[str], here: str | None, cap: int) -> list[str]:
    if not sids:
        return []
    if here in sids and len(sids) > cap:
        i = sids.index(here)
        start = max(0, min(i - cap // 2, len(sids) - cap))
        shown = sids[start:start + cap]
    else:
        shown = sids[:cap]
    lines = [f"{title} ({len(sids)}):"]
    lines += [f"  {'◀ ' if s == here else ''}{_sec_line(v, s)}" for s in shown]
    if len(shown) < len(sids):
        lines.append(f"  … +{len(sids) - len(shown)} more (outline(section=…))")
    return lines


def _siblings(v: DocView, rg: _Range) -> list[str]:
    if not rg.sid:
        return []
    return _listing(v, "siblings", _kids(v, v.sections[rg.sid].get("parent")), rg.sid, SIBLINGS)


def _children(v: DocView, rg: _Range) -> list[str]:
    return _listing(v, "subsections", _kids(v, rg.sid), None, CHILDREN) if rg.sid else []


def _target(v: DocView, t: dict[str, Any]) -> str:
    if t.get("doc") not in (None, v.name):
        return f"{t['doc']} p{t['page']}" + (f" ({v.display(t['title'])[:40]})" if t.get("title") else "")
    ref = t.get("ref")
    block = v.doc.by_ref.get(ref) if ref else None
    sid = v.section_of(block) if block else None
    head = short_ref(ref) if block else ""
    return " ".join(x for x in (head, v.page_tag(t["page"]) if t.get("page") else "", sid or "") if x)


def _refs_out(v: DocView, rg: _Range) -> list[str]:
    """References from the range first, then from the rest of its scope (tagged with their source)."""
    seen, lines = set(), []
    refs = (v.side.xrefs or {}).get("refs", [])
    ordered = [r for r in refs if r["src_ref"] in rg.refs] + [r for r in refs if r["src_ref"] in rg.scope - rg.refs]
    for r in ordered:
        if (r["kind"], r["key"]) in seen:
            continue
        seen.add((r["kind"], r["key"]))
        if r["status"] == "resolved" and r["targets"]:
            dest = "; ".join(_target(v, t) for t in r["targets"][:2])
        elif r["status"] == "external":
            dest = "external" + (f": {r.get('reason', '')[:40]}" if r.get("reason") else "")
        else:
            dest = "? unresolved"
        src = "" if r["src_ref"] in rg.refs else f" (from {short_ref(r['src_ref'])})"
        lines.append(f'  {r["kind"]} "{r["span"]}" → {dest}{src}')
    if not lines:
        return []
    out = [f"refs out ({len(lines)}):"] + lines[:REFS_OUT]
    return out + ([f"  … +{len(lines) - REFS_OUT} more"] if len(lines) > REFS_OUT else [])


def _refs_in(v: DocView, rg: _Range) -> list[str]:
    hits = [r for r in (v.side.xrefs or {}).get("refs", [])
            if r["status"] == "resolved" and r["src_ref"] not in rg.scope
            and any(t.get("doc") in (None, v.name) and t.get("ref") in rg.scope for t in r["targets"])]
    hits.sort(key=lambda r: not any(t.get("ref") in rg.refs for t in r["targets"]))
    if not hits:
        return []
    lines = [f"refs in ({len(hits)}):"]
    for r in hits[:REFS_IN]:
        src = v.doc.by_ref.get(r["src_ref"])
        sid = v.section_of(src) if src else None
        snippet = _words(v.display(r["context"].strip("…")), SNIPPET_WORDS)
        lines.append(f'  {short_ref(r["src_ref"])} {v.page_tag(r["src_page"])}{" " + sid if sid else ""}: "{snippet}"')
    return lines + ([f"  … +{len(hits) - REFS_IN} more"] if len(hits) > REFS_IN else [])


def _terms(v: DocView, rg: _Range) -> list[str]:
    lines = []
    for t in (v.side.terms or {}).get("terms", []):
        term = t["term"]
        if len(term) < 2 or t.get("def_ref") in rg.refs:
            continue
        flags = 0 if t["kind"] == "abbreviation" or term.isupper() else re.I
        if not re.search(rf"(?<!\w){re.escape(term)}(?!\w)", rg.text, flags):
            continue
        ref = t.get("def_ref")
        where = (short_ref(ref) + " " if ref in v.doc.by_ref else "") + f"p{t['def_page']}"
        if t.get("uncertain"):
            src = v.doc.by_ref.get(ref)
            sent = next((s for s in re.split(r"(?<=[.;])\s+", src.text) if term.lower() in s.lower()), "") if src else ""
            lines.append(f'  {term} (uncertain; source sentence only, {where}): "{_words(sent, 30)}"')
        else:
            lines.append(f"  {term} = {_words(v.display(t['definition']), 25)} ({where})")
        if len(lines) >= TERMS:
            break
    return [f"terms ({len(lines)}):"] + lines if lines else []


def _footnotes(v: DocView, rg: _Range) -> list[str]:
    notes: dict[str, str] = {}
    for r in (v.side.xrefs or {}).get("refs", []):
        if r["kind"] == "footnote" and r["src_ref"] in rg.refs and r["status"] == "resolved":
            for t in r["targets"]:
                b = v.doc.by_ref.get(t.get("ref"))
                if b is not None and b.ref not in rg.refs:
                    notes.setdefault(b.ref, f'  [{r["span"]}] {short_ref(b.ref)} {v.page_tag(b.page)}: "{_words(v.display(b.text), 40)}"')
    for p in rg.pages:
        for b in v.by_page.get(p, []):
            if b.label == "footnote" and b.layer == "body" and b.ref not in notes and b.ref not in rg.refs:
                notes[b.ref] = f'  {short_ref(b.ref)} {v.page_tag(b.page)}: "{_words(v.display(b.text), 40)}"'
    lines = list(notes.values())
    return [f"footnotes ({len(lines)}):"] + lines[:FOOTNOTES] if lines else []


_BUILDERS: dict[str, Callable[[DocView, _Range], list[str]]] = {
    "path": _path, "parent": _parent, "siblings": _siblings, "children": _children,
    "refs_out": _refs_out, "refs_in": _refs_in, "terms": _terms, "footnotes": _footnotes,
}


def context(doc: str | DocView, addr: str, max_tokens: int = CONTEXT_MAX_TOKENS, part: str | None = None) -> str:
    """Section path, parent opening, siblings, children, refs out/in, terms and footnotes of a range."""
    v = _view(doc)
    rg = _Range(v, addr)
    if not rg.segs:
        return f"[note] {rg.note or f'{addr}: nothing to show'}"
    span = v.page_range_tag(rg.pages[0], rg.pages[-1])
    head = f"context {addr} · {span} · {len(rg.refs)} blocks"
    lines = [head] + ([f"[note] {rg.note}"] if rg.note else [])
    used = count_tokens("\n".join(lines))
    start = PARTS.index(part) if part in PARTS else 0
    if start:
        lines += _path(v, rg)
        used = count_tokens("\n".join(lines))
    for name in PARTS[start:]:
        if start and name == "path":
            continue
        chunk = _BUILDERS[name](v, rg)
        if not chunk:
            continue
        cost = count_tokens("\n".join(chunk))
        if used + cost > max_tokens and len(lines) > 1:
            lines.append(f"next: part={name}")
            break
        lines += chunk
        used += cost
    return "\n".join(lines)
