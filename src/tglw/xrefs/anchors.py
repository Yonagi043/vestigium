"""Where reference targets live in a document: captions, numbered headings, notes, items, controls,
footnotes, and outline chapters. Temporary heading index until task A's structure sidecars arrive."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field

from tglw.common import pdfmeta
from tglw.common.bundle import Block, Doc
from tglw.common.paths import DATA
from tglw.common.text import norm
from tglw.zh import is_cjk_doc

from .detect import FAMILIES
from .keys import caption_key, control_key, note_key, number_key, section_key

SKIP_LABELS = {"page_header", "page_footer", "document_index"}
CONT = re.compile(r"\b(?:continued|concluded|cont['’]?d)\b", re.I)
TOC_LINE = re.compile(r"\.{4,}|…{2,}|\s\d{1,3}\s*$")

_CAPTION = re.compile(
    r"^\s*(?P<label>Table|TABLE|Figure|FIGURE|Fig\.|FIG\.)\s*(?P<key>(?:[A-Z]{1,2}[-.]?)?\d+(?:(?:\s\.\s|[.\-])\d+)*(?:-[A-Za-z]{1,4})?[a-z]?)"
    r"(?P<sep>\s*[:.\-–—|]|\s+(?=[A-Z(\[“\"']))")
_APPENDIX = re.compile(r"^\s*(?:APPENDIX|Appendix|ANNEX|Annex)\s+(?P<key>[IVX]+[AB]?|[A-Z]|\d+)(?!\w|-\d)")
_NUMBERED = re.compile(
    r"^\s*(?:(?:Section|SECTION)\s+)?(?P<num>(?:\d{1,2}|[A-Z])(?:\.\d{1,3})*)(?P<dot>\.?)\s+(?P<title>[\"'(\[“A-Za-z].*)$")
_CHAPTER = re.compile(r"^\s*CHAPTER\s+(?P<num>\d{1,2}|[A-Za-z]+)\b", re.I)
_ARXIV_APPENDIX = re.compile(r"^\s*(?P<key>[A-H])\s+[A-Z][A-Za-z]")
INLINE_CAPTION = re.compile(
    r"(?<=\s)(?P<label>Table|Figure|Fig\.)\s+(?P<key>(?:[A-Z]{1,2}[-.]?)?\d+(?:[.\-]\d+)*[a-z]?):\s+[A-Z]")
_NOTE_HEAD = re.compile(r"^\s*NOTE\s+(?P<key>\d{1,2})\s*[-–—:.]", re.I)
_FS_NOTE = re.compile(r"^\s*(?P<key>\d{1,2}(?:\.\d{1,2})?)\.?\s+[A-Z][A-Z'’ ,/&()\-]{3,}")
_ITEM_HEAD = re.compile(r"^\s*ITEM\s+(?P<key>\d{1,2}[A-C]?)\s*[.:\-–—]", re.I)
_PART_HEAD = re.compile(r"^\s*PART\s+(?P<part>[IVX]+|\d)\b", re.I)
_CONTROL_HEAD = re.compile(rf"^\s*(?P<id>(?:{FAMILIES})-\d{{1,2}})\s+[A-Z]")
_ENH_HEAD = re.compile(r"^\s*\((?P<enh>\d{1,2})\)\s+[A-Z][A-Z0-9 ,/\-–()&'’.]+\|")
_REQ_HEAD = re.compile(r"^\s*(?P<key>0[1-9]\.\d{2}\.\d{2})\s+\S")
_FOOTNOTE = re.compile(r"^\s*(?P<key>\d{1,3})\s+\S")


@dataclass(frozen=True, slots=True)
class Anchor:
    kind: str
    key: str
    ref: str
    page: int | None
    order: int
    title: str
    source: str
    scope: str = ""
    cont: bool = False
    depth: int = 0
    top: str = ""


@dataclass(frozen=True, slots=True)
class Chapter:
    title: str
    start: int
    end: int
    page: int | None
    ref: str


@dataclass
class Index:
    doc: Doc
    anchors: dict[str, dict[str, list[Anchor]]]
    chapters: list[Chapter]
    headings: list[Block]
    parts: list[tuple[int, str]]
    toc_pages: set[int]
    block_top: dict[str, str] = field(default_factory=dict)

    def get(self, kind: str, key: str | None) -> list[Anchor]:
        """Task A's nodes first; then this module's body anchors; page-header anchors (Docling sometimes files a
        real heading there) last."""
        found = self.anchors.get(kind, {}).get(key, []) if key else []
        found = ([a for a in found if a.source.startswith("A:")]
                 or [a for a in found if not a.source.endswith("-furniture")] or found)
        titled = [a for a in found if simple(a.title) not in ("", simple(a.key))]
        return titled or found

    def chapter_at(self, order: int) -> Chapter | None:
        return next((c for c in reversed(self.chapters) if c.start <= order < c.end), None)

    def part_at(self, order: int) -> str:
        return next((p for o, p in reversed(self.parts) if o <= order), "I")

    def all(self) -> list[Anchor]:
        return [a for by_key in self.anchors.values() for lst in by_key.values() for a in lst]


HEAD_LABELS = ("section_header", "title")


def is_heading_like(b: Block, labels: tuple[str, ...] = HEAD_LABELS) -> bool:
    return b.label in labels


def content_blocks(doc: Doc) -> list[Block]:
    return [b for b in doc.blocks if b.layer == "body" and b.label not in SKIP_LABELS]


def toc_pages(doc: Doc) -> set[int]:
    """Front-matter pages holding a real table of contents (an index block with page-number lines, or a
    'Contents' heading). Docling also labels other tables 'document_index', so one such block is not enough."""
    limit = max(20, doc.page_count // 10)
    pages = set()
    for b in doc.blocks:
        if not b.page or b.page > limit or b.layer != "body":
            continue
        if b.label == "document_index" and len(re.findall(r"(?:\||\.{3,}|\s)\s*[ivxlc\d]{1,4}\s*(?:\n|$)", b.text)) >= 3:
            pages.add(b.page)
        elif is_heading_like(b) and re.match(r"^\s*(table of )?contents\s*$", b.text, re.I):
            pages.add(b.page)
    return pages


def _target_of(b: Block) -> str:
    if b.parent and b.parent.split("/")[1] in ("tables", "pictures"):
        return b.parent
    return b.ref


def _captions(blocks: list[Block], toc: set[int]) -> list[Anchor]:
    out = []
    for b in blocks:
        m = _CAPTION.match(b.text)
        if not m or b.kind != "text" or re.search(r"\.{4,}", b.text):
            continue
        strong = b.label in ("caption", "section_header", "title")
        titled = re.match(r"\s*[^\w\s]*\s*[A-Z(\[“\"']", b.text[m.end():]) is not None
        if not strong and not ((m.group("sep").strip() and titled) or b.text[m.end():m.end() + 30].isupper()
                               or (titled and len(b.text) <= 150)):
            continue
        kind = "table" if m.group("label").upper().startswith("TAB") else "figure"
        key = caption_key(m.group("key"))
        keys = [key]
        if (r := re.match(r"\s*(?:-|–|through|to)\s*([a-z])\b", b.text[m.end("key"):])) and re.search(r"[A-Z]$", key):
            keys += [key[:-1] + chr(c).upper() for c in range(ord(key[-1].lower()) + 1, ord(r.group(1)) + 1)]
        for k in keys:
            out.append(Anchor(kind, k, _target_of(b), b.page, b.order, b.text[:120],
                              "caption" if b.label == "caption" else f"{b.label}-title",
                              cont=bool(CONT.search(b.text[:80]))))
    for b in blocks:
        if b.kind == "table" and (cell := _first_cell(b)) and (m := _CAPTION.match(cell)):
            kind = "table" if m.group("label").upper().startswith("TAB") else "figure"
            out.append(Anchor(kind, caption_key(m.group("key")), b.ref, b.page, b.order, cell[:120], "table-first-row"))
        if b.kind != "text":
            continue
        for m in INLINE_CAPTION.finditer(b.text):
            kind = "table" if m.group("label") == "Table" else "figure"
            out.append(Anchor(kind, caption_key(m.group("key")), b.ref, b.page, b.order,
                              b.text[m.start():m.start() + 120], "inline-caption"))
    return out


def _letter_appendices(heads: list[Block]) -> set[str]:
    """Refs of 'A Title', 'B Title', ... headings that form an A, B, C... run (arXiv-style appendices)."""
    cands = [(b, m.group("key")) for b in heads if (m := _ARXIV_APPENDIX.match(b.text))]
    for i, (b, k) in enumerate(cands):
        if k != "A":
            continue
        run, want = [b.ref], "B"
        for b2, k2 in cands[i + 1:]:
            if k2 == want:
                run.append(b2.ref)
                want = chr(ord(want) + 1)
        if len(run) >= 2:
            return set(run)
    return set()


def _first_cell(b: Block) -> str:
    """First non-empty cell of a table's first row (Docling sometimes keeps the table title there)."""
    first = b.text.split("\n", 1)[0]
    return next((c.strip() for c in first.split(" | ") if c.strip()), "")


def _headings(blocks: list[Block], toc: set[int], labels: tuple[str, ...] = HEAD_LABELS) -> list[Anchor]:
    out = []
    heads = [b for b in blocks if is_heading_like(b, labels) and b.page not in toc
             and not (TOC_LINE.search(b.text) and re.search(r"\.{4,}", b.text))]
    letters = _letter_appendices(heads)
    for b in heads:
        t = b.text
        if m := _APPENDIX.match(t):
            if k := number_key("appendix", m.group("key")):
                out.append(Anchor("appendix", k, b.ref, b.page, b.order, t[:120], "heading"))
            continue
        if b.ref in letters:
            out.append(Anchor("appendix", t.strip()[0], b.ref, b.page, b.order, t[:120], "heading-letter"))
            continue
        if m := _CHAPTER.match(t):
            if k := number_key("chapter", m.group("num")):
                out.append(Anchor("section", k, b.ref, b.page, b.order, t[:120], "heading-chapter"))
            continue
        if m := _REQ_HEAD.match(t):
            out.append(Anchor("section", section_key(m.group("key")) or m.group("key"), b.ref, b.page, b.order,
                              t[:120], "heading-requirement"))
            continue
        if (m := _NUMBERED.match(t)) and not _CAPTION.match(t):
            if len(m.group("num")) == 1 and m.group("num").isalpha() and not m.group("dot"):
                continue
            if k := section_key(m.group("num")):
                out.append(Anchor("section", k, b.ref, b.page, b.order, t[:120], "heading"))
    return out


def _notes(blocks: list[Block], chapters: list[Chapter], labels: tuple[str, ...] = HEAD_LABELS) -> list[Anchor]:
    out = []
    reports = [c for c in chapters if re.search(r"ACCOUNTANT", c.title, re.I)]
    for b in blocks:
        if b.kind != "text":
            continue
        if (m := _NOTE_HEAD.match(b.text)) and (is_heading_like(b, labels) or len(b.text) < 120):
            out.append(Anchor("note", number_key("note", m.group("key")) or m.group("key"), b.ref, b.page, b.order,
                              b.text[:120], "note-heading"))
            continue
        scope = next((c for c in reports if c.start <= b.order < c.end), None)
        if scope and is_heading_like(b, labels) and (m := _FS_NOTE.match(b.text)):
            key, _ = note_key(m.group("key"))
            if key:
                out.append(Anchor("note", key, b.ref, b.page, b.order, b.text[:120], "accountants-report-note",
                                  scope=appendix_id(scope.title)))
    return out


def appendix_id(title: str) -> str:
    m = re.match(r"\s*APPENDIX\s+([IVX]+[AB]?)\b", title, re.I)
    return m.group(1).upper() if m else ""


def _items(blocks: list[Block], toc: set[int], labels: tuple[str, ...] = HEAD_LABELS
           ) -> tuple[list[Anchor], list[tuple[int, str]]]:
    out, parts = [], []
    for b in blocks:
        if not is_heading_like(b, labels) or b.page in toc:
            continue
        if m := _PART_HEAD.match(b.text):
            parts.append((b.order, number_key("part", m.group("part")) or m.group("part").upper()))
        elif m := _ITEM_HEAD.match(b.text):
            part = next((p for o, p in reversed(parts) if o <= b.order), "I")
            out.append(Anchor("item", number_key("item", m.group("key")) or m.group("key").upper(), b.ref, b.page,
                              b.order, b.text[:120], "item-heading", scope=part))
    return out, parts


def raw_text(doc: Doc, b: Block) -> str:
    """Docling drops list markers from `text` ('(1) X | Y' -> 'X | Y'); Block.marker keeps them."""
    return f"{b.marker} {b.text}" if b.marker else b.text


def _controls(doc: Doc, blocks: list[Block]) -> list[Anchor]:
    """53r5 controls and enhancements. An enhancement whose '(n)' Docling lost entirely ('ACCESS ENFORCEMENT |
    RESTRICTED ...' with no marker) gets the previous number + 1, marked as inferred."""
    out = []
    base, base_title, last = None, "", 0
    for b in blocks:
        if not is_heading_like(b, HEAD_LABELS + ("list_item",)):
            continue
        t = raw_text(doc, b)
        if (m := _CONTROL_HEAD.match(t)) and b.label != "list_item":
            base, last = control_key(m.group("id")), 0
            base_title = t[m.end("id"):].strip().split("[")[0].strip()
            if base:
                out.append(Anchor("control", base, b.ref, b.page, b.order, t[:120], "control-heading"))
        elif base and (m := _ENH_HEAD.match(t)):
            last = int(m.group("enh"))
            out.append(Anchor("control", f"{base}({last})", b.ref, b.page, b.order, t[:120], "enhancement-heading"))
        elif base and base_title and t.startswith(base_title + " |"):
            last += 1
            out.append(Anchor("control", f"{base}({last})", b.ref, b.page, b.order, f"({last}?) {t}"[:120],
                              "enhancement-heading-inferred"))
    return out


def _footnotes(blocks: list[Block]) -> list[Anchor]:
    return [Anchor("footnote", str(int(m.group("key"))), b.ref, b.page, b.order, b.text[:120], "footnote")
            for b in blocks if b.label == "footnote" and (m := _FOOTNOTE.match(b.text))]


def _chapters(doc: Doc, blocks: list[Block]) -> list[Chapter]:
    """Top-level chapters from the PDF outline (level 1), each anchored to its first matching heading."""
    outline = [o for o in pdfmeta.outline(doc.name) if o.get("level") == 1 and o.get("page")]
    starts = []
    for o in outline:
        page, title = o["page"], norm(o["title"])
        near = [b for b in blocks if b.page in (page, page + 1)]
        hit = next((b for b in near if is_heading_like(b) and same_title(norm(b.text), title)), None)
        hit = hit or next((b for b in blocks if b.page == page), None)
        if hit is not None:
            starts.append((hit.order, o["title"], hit.page, hit.ref))
    starts.sort()
    end_all = doc.blocks[-1].order + 1 if doc.blocks else 0
    return [Chapter(t, o, starts[i + 1][0] if i + 1 < len(starts) else end_all, p, r)
            for i, (o, t, p, r) in enumerate(starts)]


def simple(s: str) -> str:
    s = re.sub(r"\[\s*redacted\s*\]", " ", norm(s))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def same_title(a: str, b: str) -> bool:
    a2, b2 = simple(a), simple(b)
    return bool(a2) and bool(b2) and (a2 == b2 or a2.startswith(b2) or b2.startswith(a2))


def structure(name: str) -> dict | None:
    """Task A's structure sidecar, only once A has delivered (MANIFEST present)."""
    path = DATA / "A" / f"{name}.structure.json"
    if not (DATA / "A" / "MANIFEST.json").exists() or not path.exists():
        return None
    return json.loads(path.read_text())


def a_anchors(doc: Doc) -> list[Anchor]:
    """Section/appendix/item/note targets from task A's nodes, keyed by A's number_key."""
    side = structure(doc.name)
    if side is None:
        return []
    nodes = {n["id"]: n for n in side["nodes"]}

    def top_of(n: dict) -> str:
        """A's number_scope (numbers are unique within it); without it, the depth-1 ancestor."""
        if n.get("number_scope"):
            return n["number_scope"]
        while n.get("parent") and nodes[n["parent"]].get("depth", 0) >= 1:
            n = nodes[n["parent"]]
        return n["id"]

    def part_of(n: dict) -> str:
        while n.get("parent"):
            n = nodes[n["parent"]]
            if n.get("number_kind") == "part":
                return n["number_key"]
        return "I"

    out = []
    has_appendix = any(n.get("number_kind") == "appendix" for n in side["nodes"])
    for n in side["nodes"]:
        nk, key = n.get("number_kind"), n.get("number_key")
        if not key or nk in (None, "none", "part", "control"):
            continue
        start = doc.blocks[n["start_order"]] if n.get("start_order") is not None and n["start_order"] < len(doc.blocks) else None
        ref = n.get("heading_ref") or (start.ref if start else None)
        if ref is None or ref not in doc.by_ref:
            continue
        b = doc.by_ref[ref]
        page = (n.get("pages") or [b.page])[0]
        src = f"A:{n.get('source')}"
        common = dict(ref=ref, page=page, order=b.order, title=(n.get("title") or b.text)[:120], source=src,
                      depth=n.get("depth") or 0, top=top_of(n))
        if nk in ("dotted", "chapter", "roman", "letter"):
            out.append(Anchor("section", key, **common))
            if nk == "letter" and "." not in key and not has_appendix and not re.match(r"^\s*[A-Z]\.\s", n.get("title") or ""):
                out.append(Anchor("appendix", key, **common))
        elif nk == "appendix":
            out.append(Anchor("appendix", key, **common))
        elif nk == "item":
            ok = bool(_ITEM_HEAD.match(b.text))
            out.append(Anchor("item", key, **{**common, "source": src if ok else src + "-offanchor"}, scope=part_of(n)))
        elif nk == "note":
            out.append(Anchor("note", key, **common))
    items = defaultdict(list)
    for a in out:
        if a.kind == "item":
            items[(a.scope, a.key)].append(a)
    drop = {id(a) for group in items.values() if any(not x.source.endswith("-offanchor") for x in group)
            for a in group if a.source.endswith("-offanchor")}
    return [a for a in out if id(a) not in drop]


_BARE_NUMBER = re.compile(r"^\s*\(\d{1,2}\)\s*$")


def a_controls(doc: Doc) -> list[Anchor]:
    """53r5 controls and enhancements from task A's nodes (A v3 has all 1,189 numbers of the control summary
    table; _controls misses PT-7(2), SA-8(22), SA-8(25), SA-19(2)), each at its title block rather than a
    bare "(2)" block A starts it at. Kind "control_a": a fallback, used only for numbers _controls lacks."""
    side = structure(doc.name)
    if side is None:
        return []
    out = []
    for n in side["nodes"]:
        if n.get("number_kind") != "control" or not n.get("number_key"):
            continue
        refs = [r for r in [n.get("heading_ref"), *(n.get("title_refs") or [])] if r in doc.by_ref]
        b = next((doc.by_ref[r] for r in refs if not _BARE_NUMBER.match(doc.by_ref[r].text)), None)
        if b is not None:
            out.append(Anchor("control_a", n["number_key"], b.ref, b.page, b.order, (n.get("title") or b.text)[:120],
                              f"A:{n.get('source')}"))
    return out


def block_tops(doc: Doc) -> dict[str, str]:
    """Block ref -> numbering scope it sits in: the nearest ancestor node that some number_scope names
    (in task A's structure sidecar), e.g. GPT-4's System Card. Without number_scope, the depth-1 ancestor."""
    side = structure(doc.name)
    if side is None:
        return {}
    nodes = {n["id"]: n for n in side["nodes"]}
    scopes = {n["number_scope"] for n in side["nodes"] if n.get("number_scope")}
    out = {}
    for ref, nid in side["block_section"].items():
        n = nodes.get(nid)
        if scopes:
            while n and n["id"] not in scopes and n.get("parent"):
                n = nodes[n["parent"]]
            if n:
                out[ref] = n["id"]
            continue
        while n and n.get("parent") and nodes[n["parent"]].get("depth", 0) >= 1:
            n = nodes[n["parent"]]
        if n and n.get("depth", 0) >= 1:
            out[ref] = n["id"]
    return out


def build_index(doc: Doc, controls: bool = False) -> Index:
    blocks = content_blocks(doc)
    toc = toc_pages(doc)
    chapters = _chapters(doc, blocks)
    items, parts = _items(blocks, toc)
    found = _captions(blocks, toc) + _headings(blocks, toc) + _notes(blocks, chapters) + items + _footnotes(blocks)
    for c in chapters:
        if k := appendix_id(c.title):
            found.append(Anchor("appendix", k, c.ref, c.page, c.start, c.title, "outline"))
    if controls:
        found += _controls(doc, blocks) + a_controls(doc)
    found += a_anchors(doc)
    if is_cjk_doc(doc):
        from .zh import anchors as zh_anchors

        found += zh_anchors(doc, blocks)
    furn = [b for b in doc.blocks if b.label == "page_header" and b.kind == "text"]
    fl = ("page_header",)
    fallback = _headings(furn, toc, fl) + _notes(furn, chapters, fl) + _items(furn, toc, fl)[0]
    found += [Anchor(a.kind, a.key, a.ref, a.page, a.order, a.title, a.source + "-furniture", a.scope, a.cont)
              for a in fallback]
    anchors: dict[str, dict[str, list[Anchor]]] = defaultdict(lambda: defaultdict(list))
    for a in sorted(found, key=lambda a: (a.order, a.source != "outline")):
        anchors[a.kind][a.key].append(a)
    return Index(doc, anchors, chapters, [b for b in blocks if is_heading_like(b)], parts, toc, block_tops(doc))
