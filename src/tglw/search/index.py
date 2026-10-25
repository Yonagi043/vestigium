"""Per-document search index: page and block texts, section paths (task A), printed page labels (task B),
and abbreviation expansions (task C1). Only delivered sidecars are read: a MANIFEST no older than any
sidecar in that directory."""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tglw.common import bundle
from tglw.common.bundle import Block, Doc
from tglw.common.paths import DATA



def delivered(task: str, suffix: str) -> Path | None:
    root = DATA / task
    manifest = root / "MANIFEST.json"
    sidecars = list(root.glob(f"*{suffix}"))
    if not manifest.exists() or not sidecars:
        return None
    return root if manifest.stat().st_mtime >= max(p.stat().st_mtime for p in sidecars) else None


def _sidecar(task: str, suffix: str, name: str) -> dict[str, Any] | None:
    root = delivered(task, suffix)
    path = root / f"{name}{suffix}" if root else None
    return json.loads(path.read_text()) if path and path.exists() else None


@dataclass
class Sections:
    """Task A's tree: node id -> title and parent; block ref -> deepest node."""

    title: dict[str, str] = field(default_factory=dict)
    parent: dict[str, str | None] = field(default_factory=dict)
    block: dict[str, str | None] = field(default_factory=dict)

    @classmethod
    def load(cls, name: str) -> Sections:
        sc = _sidecar("A", ".structure.json", name)
        if sc is None:
            return cls()
        nodes = sc.get("nodes") or []
        return cls({n["id"]: (n.get("title") or "").strip() for n in nodes},
                   {n["id"]: n.get("parent") for n in nodes}, dict(sc.get("block_section") or {}))

    def chain(self, node: str | None) -> list[str]:
        """Ids from the outermost titled section down to `node` (the untitled root is left out)."""
        out: list[str] = []
        while node is not None and node in self.parent:
            if self.title.get(node):
                out.append(node)
            node = self.parent[node]
        return out[::-1]

    def path(self, node: str | None) -> list[str]:
        return [self.title[n] for n in self.chain(node)]


def page_labels(name: str) -> dict[int, str]:
    pm = _sidecar("B", ".pagemap.json", name)
    return {p["pdf_page"]: p["label"] for p in (pm or {}).get("pages", []) if p.get("label")}


def expansions(name: str) -> dict[str, list[str]]:
    """Abbreviation <-> expansion pairs from C1: certain, single-sense abbreviation entries only."""
    side = _sidecar("C1", ".terms.json", name)
    out: dict[str, list[str]] = defaultdict(list)
    if side is None:
        return out
    for e in side["terms"]:
        if e.get("kind") != "abbreviation" or e.get("uncertain") or e.get("ambiguous") or e.get("redirect"):
            continue
        short, full = e["term"], e["definition"]
        if not (2 <= len(short) <= 15) or len(full.split()) > 12:
            continue
        for s in [short] + [v for v in e.get("variants", []) if len(v.split()) == 1]:
            out[s].append(full)
        out[full.lower()].append(short)
    return out


PASSAGE_WORDS = 60
RUNNING_HEADERS_ENV = "TGLW_SRC_RUNNING_HEADERS"  # 1: page headers Docling put in the body (A v4) split passages only


TABLE_ROWS_ENV = "TGLW_SRC_TABLE_ROWS"  # 1: rows of scanned tables Docling took for headings (task TR) are text


def switched(env: str) -> bool:
    return os.environ.get(env, "").strip().lower() in ("1", "true", "on", "yes")


def running_headers(name: str) -> frozenset[str]:
    """Refs of page headers that Docling put in the body as headings (task A's running_headers), when
    TGLW_SRC_RUNNING_HEADERS is on; read when an index is built."""
    if not switched(RUNNING_HEADERS_ENV):
        return frozenset()
    return frozenset((_sidecar("A", ".structure.json", name) or {}).get("running_headers") or [])


def table_rows(name: str) -> frozenset[str]:
    """Refs of scanned-table rows that Docling labelled headings (task TR's tablerows sidecar), when
    TGLW_SRC_TABLE_ROWS is on; read when an index is built."""
    if not switched(TABLE_ROWS_ENV):
        return frozenset()
    return frozenset((_sidecar("TR", ".tablerows.json", name) or {}).get("table_rows") or [])


@dataclass
class Passage:
    """Consecutive body blocks of one section: a heading with what follows it, or short blocks joined
    until about PASSAGE_WORDS words, so a lone heading or list item does not outrank a paragraph."""

    blocks: list[Block]
    node: str | None
    heading: str | None
    figure: str | None = None

    @property
    def text(self) -> str:
        return " ".join(b.text for b in self.blocks)

    @property
    def pages(self) -> list[int]:
        return sorted({p for b in self.blocks for p in b.pages})


def passages(blocks: list[Block], node_of, skip: frozenset[str] = frozenset(),
             rows: frozenset[str] = frozenset()) -> list[Passage]:
    """`skip`: running page headers; each ends the passage before it, joins none and is no heading, so
    the topic it interrupts goes on under its own heading. `rows`: table rows labelled headings, read as
    text."""
    out: list[Passage] = []
    cur: list[Block] = []
    heading: str | None = None
    words = 0

    def flush() -> None:
        nonlocal cur, words
        if cur:
            out.append(Passage(cur, node_of(cur[0]), heading))
        cur, words = [], 0

    for b in blocks:
        if b.ref in skip:
            flush()
            continue
        new_node = cur and node_of(b) != node_of(cur[0])
        head = b.is_heading and b.ref not in rows
        if head or new_node or words >= PASSAGE_WORDS or (b.kind == "table" and cur):
            flush()
        if head:
            heading = b.text.strip()
        cur.append(b)
        words += len(b.text.split())
    flush()
    return out


def picture_of(doc: Doc, b: Block) -> str | None:
    """Nearest picture ancestor of a block (inner text can sit in groups inside the picture)."""
    ref = b.parent
    while ref and ref.startswith("#/"):
        if ref.startswith("#/pictures/"):
            return ref
        ref = (doc.item(ref).get("parent") or {}).get("$ref")
    return None


def figure_passages(doc: Doc, node_of, skip: frozenset[str] = frozenset(),
                    rows: frozenset[str] = frozenset()) -> list[Passage]:
    """One passage per picture: its caption and inner text (axis titles, labels, legends), addressed by
    the picture, as task M5's literal search does. Heading: the last body heading before the picture."""
    inner: dict[str, list[Block]] = {}
    heading_at: dict[str, str | None] = {}
    heading: str | None = None
    for b in doc.blocks:
        if b.layer != "body":
            continue
        if b.kind == "picture":
            heading_at[b.ref] = heading
        elif b.in_picture and b.kind == "text" and b.text.strip():
            pic = picture_of(doc, b)
            if pic:
                inner.setdefault(pic, []).append(b)
        elif b.is_heading and b.ref not in skip and b.ref not in rows:
            heading = b.text.strip()
    out = []
    for pic, blocks in inner.items():
        owner = doc.by_ref.get(pic)
        node = node_of(owner) if owner else node_of(blocks[0])
        out.append(Passage(blocks, node, heading_at.get(pic), pic))
    return out


@dataclass
class Index:
    doc: Doc
    sections: Sections
    labels: dict[int, str]
    expand: dict[str, list[str]]
    page_text: dict[int, str]
    page_nodes: dict[int, Counter]
    blocks: list[Block]
    cache: dict[Any, Any] = field(default_factory=dict)
    running: frozenset[str] = frozenset()
    rows: frozenset[str] = frozenset()

    @property
    def pages(self) -> list[int]:
        return list(self.page_text)

    def page_node(self, page: int) -> str | None:
        """The section holding most of the page's body words."""
        nodes = self.page_nodes.get(page)
        return nodes.most_common(1)[0][0] if nodes else None

    def titles(self, page: int) -> str:
        """Titles of every section (and its ancestors) that has body text on this page."""
        seen: dict[str, None] = {}
        for n in self.page_nodes.get(page, {}):
            for c in self.sections.chain(n):
                seen.setdefault(c, None)
        return " ".join(self.sections.title[c] for c in seen)

    def block_node(self, b: Block) -> str | None:
        return self.sections.block.get(b.ref)

    def passages(self) -> list[Passage]:
        """Text passages, then one passage per picture with inner text."""
        if "passages" not in self.cache:
            self.cache["passages"] = (passages(self.blocks, self.block_node, self.running, self.rows)
                                      + figure_passages(self.doc, self.block_node, self.running, self.rows))
        return self.cache["passages"]


def build(doc: Doc) -> Index:
    sections = Sections.load(doc.name)
    page_text: dict[int, str] = {}
    page_nodes: dict[int, Counter] = {}
    for p in range(1, doc.page_count + 1):
        segs = [(b, t) for b, t in doc.page_segments(p) if b.layer == "body"]
        page_text[p] = " ".join(t for _, t in segs)
        page_nodes[p] = Counter()
        for b, t in segs:
            if n := sections.block.get(b.ref):
                page_nodes[p][n] += len(t.split()) or 1
    blocks = [b for b in doc.blocks if b.layer == "body" and b.text and b.page is not None and not b.in_picture]
    return Index(doc, sections, page_labels(doc.name), expansions(doc.name), page_text, page_nodes, blocks,
                 running=running_headers(doc.name), rows=table_rows(doc.name))


@bundle.doc_cache
def load(name: str) -> Index:
    return build(bundle.shared(name))

