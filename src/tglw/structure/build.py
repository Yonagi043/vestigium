"""Assemble the section tree of one document and write data/A/<doc>.structure.json.

Sources: bookmarks (outline.py), heading numbers (numtree.py), and well-known front/back-matter
names (convention). `block_section` maps each block to the deepest node that certainly contains it:
an unexplained heading or a numbering gap between a node's start and the block means the block may
already sit in a later sibling, so it climbs to the nearest node that also contains the next known
section start (or to a bookmark node, whose children are taken as complete).
"""

from __future__ import annotations

import bisect
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from tglw.common import bundle, registry
from tglw.common.bundle import Block, Doc
from tglw.common.paths import DATA
from tglw.zh import is_cjk_doc

from . import headerchapters, numtree, outline, pdfoutline, roles, toc
from .numbering import Num, num_key, parse, parse_zh
from .roles import text_key

PRODUCER = "tglw.structure v4"
DELIVERED = DATA / "A"
OUT = DELIVERED / "wip"


def current() -> Path:
    """Where to read sidecars: work in progress if there is any, else the delivered set."""
    return OUT if any(OUT.glob("*.structure.json")) else DELIVERED
SHORT_PAGES = 10
FRONT = {
    "abstract", "executive summary", "foreword", "preface", "contents", "table of contents",
    "list of figures", "list of tables", "list of illustrations", "list of figures and tables",
    "acknowledgments", "acknowledgements", "keywords", "authority", "disclaimer", "errata",
    "abbreviations", "acronyms", "reports on computer systems technology",
}
BACK = {
    "acknowledgments", "acknowledgements", "acknowledgment", "acknowledgement", "references",
    "bibliography", "glossary", "glossary of terms", "acronyms", "abbreviations",
    "acronyms and abbreviations", "list of acronyms", "index", "appendix", "appendices", "endnotes",
    "change log",
}


@dataclass(eq=False)
class Node:
    title: str
    source: str
    start: int
    parent: Node | None = None
    heading_ref: str | None = None
    anchor: str | None = None
    num: Num | None = None
    number_key: str | None = None
    confidence: str = "high"
    complete: bool = False
    flags: list[str] = field(default_factory=list)
    title_refs: list[str] = field(default_factory=list)
    id: str = ""
    depth: int = 0
    end: int = 0
    leaf_of: Node | None = None
    scope: Node | None = None

    def ancestors(self) -> list[Node]:
        out, x = [], self.parent
        while x is not None:
            out.append(x)
            x = x.parent
        return out

    def contains(self, other: Node) -> bool:
        return other is self or self in other.ancestors()

    def contains_ref(self, b: Block) -> bool:
        """Whether a heading block is this node's own title (same text), not front matter inside it."""
        return text_key(b.text) == text_key(self.title)


def _words(b: Block) -> int:
    return len(b.text.split())


def _orig(doc: Doc, b: Block) -> str:
    item = doc.item(b.ref)
    return (item.get("orig") or b.text).replace("\n", " ").strip() if b.kind == "text" else b.text


# ---------------------------------------------------------------- numbering candidates

def _first_cell(doc: Doc, b: Block) -> str:
    grid = bundle.table_grid(doc.item(b.ref))
    return grid[0][0] if grid and grid[0] else ""


def numbering_candidates(doc: Doc, role: dict[str, str]) -> list[numtree.Cand]:
    return _candidates(doc, role)[0]


def parser(doc: Doc):
    """Number parser for the document: Chinese forms too in a Chinese document (task ZH's is_cjk_doc)."""
    return parse_zh if is_cjk_doc(doc) else parse


def _candidates(doc: Doc, role: dict[str, str]) -> tuple[list[numtree.Cand], list[numtree.Cand]]:
    out, loose = [], []
    pn = parser(doc)
    for b in doc.blocks:
        if b.layer != "body" or b.in_picture:
            continue
        if b.kind == "table":
            n = parse(_first_cell(doc, b))
            if n is not None and n.family == "dec" and n.depth >= 2:
                out.append(numtree.Cand(b.ref, b.order, b.page, _first_cell(doc, b), n, False))
            continue
        if b.kind != "text":
            continue
        if b.is_heading:
            if role.get(b.ref) not in ("unknown", "running_header", "label"):
                continue
        elif b.label not in ("text", "list_item") or len(b.text.split()) > 12 and not _zh_unit(pn, b.text):
            continue
        text = _orig(doc, b)
        n = pn(text)
        if n is not None and n.family == "enh":
            continue
        if n is not None and (b.is_heading or _relabel_ok(n)):
            out.append(numtree.Cand(b.ref, b.order, b.page, text, n, b.is_heading))
        elif n is not None and n.family == "dec" and not n.weak:
            loose.append(numtree.Cand(b.ref, b.order, b.page, text, n, False))
    out += enhancement_candidates(doc, out)
    return sorted(out, key=lambda c: c.order), loose


_ENH_TITLE = re.compile(r"^(?:\((\d{1,2})\)\s*)?(\S.*?)\s*\|\s*(\S.*)$")
_NUM_ONLY = re.compile(r"^\((\d{1,2})\)(\s*\[Withdrawn[^]]*\]\.?)?$")


@dataclass
class _EnhTitle:
    pos: int
    block: Block
    number: int | None
    text: str
    via: Block | None = None
    note: str = ""


def enhancement_candidates(doc: Doc, cands: list[numtree.Cand]) -> list[numtree.Cand]:
    """NIST control enhancements, "(N) CONTROL NAME | ENHANCEMENT NAME". Docling often splits the
    "(N)" into its own block (before or after the title), or loses it; and long titles are body text.
    Within each control, a title without its number takes the free numbers between its numbered
    neighbours when they fit exactly, else the separate "(N)" blocks of that stretch, else nothing."""
    controls = sorted((c for c in cands if c.num.family == "ctl" and c.num.depth == 2), key=lambda c: c.order)
    blocks = [b for b in doc.blocks if b.layer == "body" and not b.in_picture and b.kind == "text"]
    out = []
    for i, ctl in enumerate(controls):
        end = controls[i + 1].order if i + 1 < len(controls) else len(doc.blocks)
        region = [b for b in blocks if ctl.order < b.order < end]
        out += _enhancements_of(doc, ctl, region)
    return out


def _enhancements_of(doc: Doc, ctl: numtree.Cand, region: list[Block]) -> list[numtree.Cand]:
    titles: list[_EnhTitle] = []
    numbers: list[tuple[int, Block, int]] = []
    names = {text_key(ctl.num.title)}
    for pos, b in enumerate(region):
        orig = _orig(doc, b)
        m = _NUM_ONLY.match(orig)
        if m:
            numbers.append((pos, b, int(m.group(1))))
            continue
        m = _ENH_TITLE.match(orig)
        if not m:
            continue
        explicit = m.group(1) is not None
        if explicit:
            names.add(text_key(m.group(2)))
        elif max((fuzz.ratio(text_key(m.group(2)), n) for n in names), default=0) < 80:
            continue
        title = orig[m.start(2):] if explicit else orig
        titles.append(_EnhTitle(pos, b, int(m.group(1)) if explicit else None, title))
    _fill_numbers(titles, numbers)
    out = []
    for t in titles:
        if t.number is None:
            continue
        bare = t.via is not None and _NUM_ONLY.match(_orig(doc, t.via)).group(2) is None
        anchor, extra = t.block, (t.via.ref,) if bare else ()
        if t.via is not None and t.via.order == t.block.order - 1:
            anchor, extra = t.via, (t.block.ref,)
        num = Num("enh", (str(t.number),), f"({t.number})", t.text, note=t.note, kind="control")
        out.append(numtree.Cand(anchor.ref, anchor.order, anchor.page, f"({t.number}) {t.text}", num, True, extra))
    return out


def _fill_numbers(titles: list[_EnhTitle], numbers: list[tuple[int, Block, int]]) -> None:
    """Give each run of unnumbered titles the numbers that fit between its numbered neighbours."""
    i = 0
    while i < len(titles):
        if titles[i].number is not None:
            i += 1
            continue
        j = i
        while j < len(titles) and titles[j].number is None:
            j += 1
        before = titles[i - 1] if i else None
        after = titles[j] if j < len(titles) else None
        low, high = (before.number if before else 0), (after.number if after else None)
        run = titles[i:j]
        lo_pos = before.pos if before else -1
        hi_pos = after.pos if after else 10 ** 9
        loose = [(p, b, n) for p, b, n in numbers if lo_pos < p < hi_pos and n > low and (high is None or n < high)]
        if len(loose) == len(run):
            for t, (_, b, n) in zip(run, sorted(loose, key=lambda x: x[2])):
                t.number, t.via, t.note = n, b, "number from separate block"
        elif high is not None and high - low - 1 == len(run):
            for k, t in enumerate(run, 1):
                t.number, t.note = low + k, "number inferred from neighbours"
        i = j


def number_tree(doc: Doc, role: dict[str, str]) -> tuple[list[numtree.Cand], list[numtree.NumNode], dict[str, str]]:
    cands, loose = _candidates(doc, role)
    nodes, rejected = numtree.build(cands, loose)
    return cands, nodes, rejected


ZH_UNITS = frozenset({"zh_part", "zh_chapter", "zh_section", "zh_article", "zh_appendix"})


def _relabel_ok(n: Num) -> bool:
    """Body text may stand in for a heading only when its number cannot be a plain list counter. In
    Chinese, 第X章/节/条 and 附录X can (Docling files a law's 第十三条 ... as text); 一、 and （一） cannot."""
    if n.weak:
        return False
    if n.family.startswith("zh_"):
        return n.family in ZH_UNITS
    return n.family != "dec" or n.depth >= 2 or n.kind in ("chapter", "roman")


def _zh_unit(pn, text: str) -> bool:
    """A long body block that opens with 第X条 (a whole article in one block) is still a candidate."""
    n = pn(text) if pn is parse_zh else None
    return n is not None and n.family in ZH_UNITS


def numbering_quality(cands: list[numtree.Cand], nodes: list[numtree.NumNode]) -> dict[str, Any]:
    accepted = {n.ref for n in nodes}
    dups = {c.ref for c in cands if c.ref not in accepted and _is_dup(c, nodes)}
    heads = [c for c in cands if c.heading and c.ref not in dups and not c.num.weak]
    acc_heads = sum(1 for c in heads if c.ref in accepted)
    flags: dict[str, int] = {}
    for n in nodes:
        for f in n.flags:
            k = f.split(":")[0]
            flags[k] = flags.get(k, 0) + 1
    parents = [n for n in nodes if n.num.family == "dec" and n.num.depth > 1]
    return {
        "numbered_headings": len(heads),
        "accepted_headings": acc_heads,
        "accept_rate": round(acc_heads / len(heads), 3) if heads else 0.0,
        "nodes": len(nodes),
        "relabeled": sum(1 for n in nodes if "relabeled" in n.flags),
        "flags": flags,
        "dotted_with_parent": len(parents),
        "max_depth": _num_depth(nodes),
    }


def _is_dup(c: numtree.Cand, nodes: list[numtree.NumNode]) -> bool:
    return any(n.num.family == c.num.family and n.num.key == c.num.key and n.order < c.order for n in nodes)


def _num_depth(nodes: list[numtree.NumNode]) -> int:
    depth: dict[int, int] = {}
    for i, n in enumerate(nodes):
        depth[i] = depth.get(n.parent, 0) + 1 if n.parent is not None else 1
    return max(depth.values(), default=0)


def numbering_ok(q: dict[str, Any]) -> bool:
    return q["accepted_headings"] >= 3 and q["accept_rate"] >= 0.6


# ---------------------------------------------------------------- node construction

def _title_after(doc: Doc, b: Block) -> Block | None:
    """The title block of a bare 'CHAPTER ONE' heading: a following heading, else upper-case text."""
    nxt = [x for x in doc.blocks[b.order + 1:b.order + 3]
           if x.page == b.page and x.kind == "text" and x.layer == "body" and parse(x.text) is None
           and len(x.text.split()) <= 12]
    return next((x for x in nxt if x.is_heading), None) or next((x for x in nxt[:1] if x.text.isupper()), None)


def _from_numbering(doc: Doc, nn: numtree.NumNode, confidence: str) -> Node:
    if not nn.ref:
        return Node(title=num_key(nn.num) or nn.num.raw, source="numbering", start=nn.order, anchor="virtual",
                    num=nn.num, flags=list(nn.flags), confidence="medium" if confidence == "high" else confidence)
    b = doc.by_ref[nn.ref]
    title = nn.num.title
    anchor = "heading" if b.is_heading else "relabeled" if b.kind == "text" else "cell"
    node = Node(title="", source="numbering", start=nn.order, heading_ref=nn.ref,
                anchor=anchor, num=nn.num, flags=list(nn.flags))
    node.title_refs.extend(nn.extra)
    if not title:
        t = _title_after(doc, b)
        if t is not None:
            title = t.text
            node.title_refs.append(t.ref)
    if nn.num.family == "zh_article" and anchor == "relabeled":
        title = _first_clause(title)
    node.title = f"{nn.num.raw} {title}".strip() if nn.num.family != "enh" else f"{nn.num.raw} {title}"
    node.confidence = confidence if not nn.flags else "medium" if confidence == "high" else confidence
    return node


def _first_clause(text: str, limit: int = 24) -> str:
    """The opening of an article that is all one block ("第十三条 年度报告应当记载以下内容：..."): up to
    its first full stop, colon or semicolon, at most `limit` characters."""
    cut = min((i for i in (text.find(c) for c in "。：；:;") if i > 0), default=len(text))
    return text[:min(cut, limit)] + ("…" if min(cut, limit) < len(text) and cut > limit else "")


def numbering_nodes(doc: Doc, nnodes: list[numtree.NumNode], confidence: str) -> list[Node]:
    made: list[Node] = []
    for nn in nnodes:
        node = _from_numbering(doc, nn, confidence)
        node.parent = made[nn.parent] if nn.parent is not None else None
        made.append(node)
    return made


def _entry_num(doc: Doc, e: outline.Entry) -> Num | None:
    pn = parser(doc)
    if e.ref and e.method in ("text", "prefix"):
        n = pn(_orig(doc, doc.by_ref[e.ref])) if doc.by_ref[e.ref].kind == "text" else None
        if n:
            return n
    return pn(e.title)


def outline_agreement(doc: Doc, entries: list[outline.Entry]) -> tuple[int, int]:
    live = {e.i: e for e in entries if not e.junk and not e.drop}
    agree = total = 0
    for e in live.values():
        n = _entry_num(doc, e)
        if n is None or n.family != "dec":
            continue
        p = live.get(e.parent) if e.parent is not None else None
        pn = _entry_num(doc, p) if p else None
        if p is not None and pn is None:
            continue
        if n.depth == 1:
            ok = pn is None or pn.family != "dec" or pn.kind != n.kind
        else:
            ok = pn is not None and pn.family in ("dec", "app") and pn.key == n.key[:-1]
        total += 1
        agree += ok
    return agree, total


def outline_nodes(doc: Doc, entries: list[outline.Entry]) -> list[Node]:
    by_i: dict[int, Node] = {}
    made = []
    conf = {"text": "high", "prefix": "high", "substring": "medium", "cell": "medium",
            "position": "medium", "page": "low"}
    for e in sorted((e for e in entries if not e.junk and not e.drop), key=lambda x: (x.order, x.i)):
        heading = e.method not in ("position", "page")
        title = (outline.volume_title(e.title) or e.title.strip()) if "filename" in e.flags else e.title.strip()
        node = Node(title=title, source="outline", start=e.order, heading_ref=e.ref if heading else None,
                    anchor=e.method, num=_entry_num(doc, e) if heading else parser(doc)(e.number or e.title),
                    confidence="high" if "running_header" in e.flags else conf[e.method or "page"],
                    complete=True, flags=list(e.flags))
        if e.ref and heading and doc.by_ref[e.ref].kind == "text":
            t = _title_after(doc, doc.by_ref[e.ref])
            if t is not None and node.num is not None and not node.num.title:
                node.title_refs.append(t.ref)
        by_i[e.i] = node
        made.append(node)
    for e in entries:
        if e.i in by_i and e.parent is not None:
            by_i[e.i].parent = by_i.get(e.parent)
    return made


def subdoc_numbering(leaf: Node, cands: list[numtree.Cand], loose: list[numtree.Cand]) -> list[numtree.NumNode]:
    """A bookmark leaf holding its own document (GPT-4's appended System Card: Abstract, 1, 2, 3 ...)
    gets a separate numbering pass, so its sections keep their own number range."""
    first = leaf.start if "running_header" in leaf.flags and leaf.heading_ref is None else leaf.start + 1
    inside = [c for c in cands if first <= c.order < leaf.end]
    firsts = [c for c in inside if c.heading and _restartable(c.num) and not c.num.weak]
    if not firsts or firsts[0].num.key != ("1",):
        return []
    nodes, _ = numtree.build(inside, [c for c in loose if first <= c.order < leaf.end])
    tops = [n for n in nodes if n.parent is None and _restartable(n.num)]
    return nodes if len(tops) >= 2 and len(nodes) >= 3 else []


def _restartable(n: Num) -> bool:
    """Top numbers of a part that numbers its sections afresh: 1, 2, 3 (or a Chinese circular's 1. 2. 3.,
    一、 二、 under each chapter)."""
    return n.family == "dec" and n.depth == 1 or n.family.startswith("zh_")


def extend_with_numbering(doc: Doc, base: list[Node], nnodes: list[numtree.NumNode],
                          cands: list[numtree.Cand], loose: list[numtree.Cand]) -> list[Node]:
    """Hang numbered headings found inside bookmark nodes. Inside a leaf any consistent numbered
    heading goes under the leaf; inside a node with bookmarked children (a gap the bookmarks skip,
    e.g. PT-1 before the first bookmarked PT-2) only a heading whose numbering parent is that node or
    an ancestor. Numbers the bookmarks already carry are not added again."""
    spans(base, len(doc.blocks))
    anchors = {n.heading_ref: n for n in base if n.heading_ref}
    has_child = {id(n.parent) for n in base if n.parent is not None}
    leaves = sorted((n for n in base if id(n) not in has_child), key=lambda n: n.start)
    known = {(n.num.family, n.num.key) for n in base if n.num is not None}
    out: list[Node] = []
    for leaf in leaves:
        sub = subdoc_numbering(leaf, cands, loose)
        if sub:
            leaf.flags.append("subdoc")
            out += _hang(doc, sub, anchors, set(), has_child, lambda nn: leaf)
    done = {id(n.leaf_of) for n in out}

    def container(nn: numtree.NumNode) -> Node | None:
        inside = [n for n in base if n.start < nn.order < n.end]
        box = max(inside, key=lambda n: n.depth, default=None)
        return None if box is None or id(box) in done else box

    return out + _hang(doc, nnodes, anchors, known, has_child, container, base)


def _hang(doc: Doc, nnodes: list[numtree.NumNode], anchors: dict[str, Node],
          known: set[tuple[str, tuple[str, ...]]], has_child: set[int], container: Any,
          base: list[Node] = ()) -> list[Node]:
    """Attach numbered headings inside bookmark nodes. A heading whose numbering parent is itself a
    bookmark node (CA-2's parent is section 3.4; the PDF bookmarks list CA-1 and CA-3 but not CA-2)
    goes under that node, as a sibling of the bookmark it was found in, not under it. Chinese numbers
    restart under every parent (Maotai's notes have a 26、 under 五、 and another under 七、), so a
    bookmark carrying the same number only counts inside the same container, and they are placed by rank
    (_hang_zh) -- except a leaf's own fresh numbering (`base` empty), which goes under the leaf."""
    ext: dict[int, Node] = {}
    for i, nn in enumerate(nnodes):
        zh = nn.num.family.startswith("zh_") and bool(base)
        if nn.ref in anchors or not nn.ref or not zh and (nn.num.family, nn.num.key) in known:
            continue
        box = container(nn)
        if box is None or any(_same_number(x.num, nn.num) for x in [box, *box.ancestors()]):
            continue
        if zh and any(_same_number(x.num, nn.num) and box.contains(x) for x in anchors.values()):
            continue
        if zh:
            if (node := _hang_zh(doc, nn, box, list(base), [x for x in ext.values() if x.num.family.startswith("zh_")])) is not None:
                ext[i] = node
            continue
        p = ext.get(nn.parent) if nn.parent is not None else None
        marked = anchors.get(nnodes[nn.parent].ref) if nn.parent is not None and p is None else None
        node = _from_numbering(doc, nn, "high")
        if p is not None and p.leaf_of is box:
            node.parent = p
        elif marked is not None and (marked is box or marked in box.ancestors()):
            node.parent = marked
        elif id(box) not in has_child:
            node.parent = box
            if nn.parent is not None and nnodes[nn.parent].order != box.start:
                node.flags.append("parent_by_containment")
                node.confidence = "medium"
        else:
            continue
        node.leaf_of = box
        ext[i] = node
    return list(ext.values())


def _hang_zh(doc: Doc, nn: numtree.NumNode, box: Node, base: list[Node], hung: list[Node]) -> Node | None:
    """A Chinese number goes under the nearest open node of higher rank (二、 > （二） > 2、), or without a
    number: starting from the last number already hung in the same bookmark (else the bookmark itself),
    climbing past nodes of equal or lower rank. "2、" found inside the bookmark "1、 股份变动情况表" is that
    bookmark's sibling; "(2)" after a hung "11、" goes under "11、". It climbs only past bookmarks it can
    close (none of their bookmarked children comes after it), and not next to one with the same number."""
    up = next((h for h in reversed(hung) if h.leaf_of is box), None) or box
    while up is not None and up.num is not None and numtree.zh_rank(up.num.family) >= numtree.zh_rank(nn.num.family):
        if any(x.parent is up and x.start > nn.order for x in base):
            return None
        up = up.parent
    if up is None or any(x.parent is up and _same_number(x.num, nn.num) for x in base + hung):
        return None
    node = _from_numbering(doc, nn, "high")
    node.parent, node.leaf_of = up, box
    return node


def _same_number(a: Num | None, b: Num) -> bool:
    return a is not None and a.family == b.family and a.key == b.key


def enh_keys(nodes: list[Node]) -> None:
    for n in nodes:
        if n.num is None:
            continue
        parent_key = None
        if n.num.family == "enh" and n.parent is not None and n.parent.num is not None:
            parent_key = num_key(n.parent.num)
        n.number_key = num_key(n.num, parent_key)


def _restarts(doc: Doc, start: int, end: int) -> bool:
    """An embedded document between two blocks: its own Abstract or a section numbered 1."""
    for b in doc.blocks[start + 1:end]:
        if b.is_heading:
            n = parse(b.text)
            if text_key(b.text) == "abstract" or (n is not None and n.family == "dec" and n.key == ("1",)):
                return True
    return False


def _main_chapter(n: Node) -> bool:
    """A top-level section of the numbered main sequence (chapter 7), which back matter cannot precede."""
    return n.parent is None and n.num is not None and n.num.family == "dec" and n.num.kind in ("dotted", "chapter")


def _appendix_like(n: Node) -> bool:
    return n.num is not None and (n.num.family == "app" or n.num.kind == "letter") \
        or text_key(n.title).startswith(("appendix", "annex"))


def convention_nodes(doc: Doc, nodes: list[Node], role: dict[str, str], used: set[str]) -> list[Node]:
    """Unnumbered front matter before the first section, and back matter after the last one."""
    spans(nodes, len(doc.blocks))
    tops = sorted((n for n in nodes if n.parent is None), key=lambda n: n.start)
    first = tops[0].start if tops else len(doc.blocks)
    main = next((t.start for t in tops if t.num is not None and t.num.family in ("dec", "part")), first)
    out: list[Node] = []
    for b in doc.headings():
        if b.ref in used or b.in_picture or role.get(b.ref) not in ("unknown", "label"):
            continue
        key = text_key(b.text)
        top = next((t for t in sorted(tops + out, key=lambda n: n.start, reverse=True) if t.start <= b.order), None)
        front = key in FRONT and (b.order < first or (b.order < main and top is not None and not top.contains_ref(b)))
        later = [n for n in nodes if n.start > b.order]
        back = (key in BACK and top is not None and key != text_key(top.title)
                and not (_appendix_like(top) and _restarts(doc, top.start, b.order))
                and not any(top.contains(n) or _main_chapter(n) or (n.parent is None and not _appendix_like(n))
                            for n in later))
        if front or back or (not tops and key in FRONT | BACK):
            out.append(Node(title=b.text, source="convention", start=b.order, heading_ref=b.ref,
                            anchor="heading", confidence="high"))
    return out


def name_implied(nodes: list[Node], titles: dict[tuple[str, str], tuple[str, int]]) -> None:
    """Give implied parents (II, 2.3.4) the title the contents page prints for that number."""
    for n in nodes:
        if "virtual" in n.flags and n.num is not None:
            hit = titles.get((n.num.kind, n.number_key or ""))
            if hit:
                own = parse(hit[0])
                n.title = f"{n.number_key} {own.title if own is not None and own.title else hit[0]}"
                n.flags.append("title_from_toc")


def confirm_top_level(doc: Doc, nodes: list[Node]) -> dict[str, Any]:
    """Numbered top sections are complete when the contents page lists the same numbers, all of
    them, consecutively: then an unnumbered heading inside chapter 3 cannot be a top-level sibling."""
    tier, q = toc.tops(doc, doc.name)
    listed = {(t.num.family, t.num.key) for t in tier if t.num is not None}
    top = [n for n in nodes if n.parent is None and n.num is not None and n.num.family in ("dec", "app")]
    keys = {(n.num.family, n.num.key) for n in top}
    seq: dict[bool, list[int]] = {}
    for n in sorted(top, key=lambda x: x.start):
        k = n.num.key[0]
        seq.setdefault(k.isalpha(), []).append(ord(k) - 64 if k.isalpha() else int(k) if k.isdigit() else -99)
    consecutive = all(v == list(range(v[0], v[0] + len(v))) and v[0] <= 1 for v in seq.values())
    pages = [t.page for t in tier if t.num is not None]
    loose = [e for e in toc.section_entries(doc.name) if e.get("level_hint", 1) <= 1 and toc._num(e) is None
             and pages and min(pages) < e["pdf_page"] < max(pages) and text_key(e["title"]) not in FRONT | BACK
             and not toc.listing_noise(e["title"])]
    q.update({"numbered_listed": len(listed), "listed_found": len(listed & keys), "numbering_consecutive": consecutive,
              "unnumbered_top_inside": len(loose)})
    q["used"] = len(listed) >= 3 and listed <= keys and consecutive and not loose
    if q["used"]:
        for n in top:
            n.complete = True
            n.flags.append("toc_confirmed")
    return q


def is_attachment(n: Node) -> bool:
    return bool(roles.ATTACH.match(n.title.strip()))


def _pagemap(name: str) -> dict[str, Any]:
    manifest = DATA / "B" / "MANIFEST.json"
    path = DATA / "B" / f"{name}.pagemap.json"
    if not manifest.exists() or not path.exists():
        return {}
    if json.loads(manifest.read_text()).get("parts", {}).get("pagemap", {}).get("status") != "delivered":
        return {}
    return json.loads(path.read_text())


def _divisions(name: str) -> list[dict[str, Any]]:
    """Starts of exhibits, appendices, volumes and parts, from the divisions in task B's page map."""
    return _pagemap(name).get("divisions") or []


def _page_runs(name: str) -> list[dict[str, Any]]:
    """Runs of consecutive printed page numbers from task B, if its pagemap is delivered."""
    manifest = DATA / "B" / "MANIFEST.json"
    path = DATA / "B" / f"{name}.pagemap.json"
    if not manifest.exists() or not path.exists():
        return []
    if json.loads(manifest.read_text()).get("parts", {}).get("pagemap", {}).get("status") != "delivered":
        return []
    return json.loads(path.read_text()).get("runs") or []


def attachment_nodes(doc: Doc, nodes: list[Node], role: dict[str, str], used: set[str]) -> list[Node]:
    """Material appended after the last section: exhibit and certification headings in the tail, and
    pages where the printed numbering restarts at 1 in the second half of the document."""
    spans(nodes, len(doc.blocks))
    tops = [n for n in nodes if n.parent is None and not is_attachment(n)]
    last = max((n.start for n in tops), default=-1)
    out: list[Node] = []
    page_top = {}
    for b in doc.blocks:
        if b.layer == "body" and not b.in_picture and b.page is not None:
            page_top.setdefault(b.page, []).append(b.order)
    for b in doc.blocks:
        if b.order <= last or b.ref in used or b.layer != "body" or b.in_picture or b.kind != "text":
            continue
        if b.order not in page_top.get(b.page, [])[:3]:
            continue
        if (b.is_heading and role.get(b.ref) in ("unknown", "label")) or len(b.text.split()) <= 8:
            if roles.ATTACH.match(b.text.strip()) and not any(n.start == b.order for n in nodes + out):
                out.append(Node(title=b.text.strip(), source="convention", start=b.order, heading_ref=b.ref,
                                anchor="heading" if b.is_heading else "relabeled", confidence="high"))
    starts = {doc.blocks[n.start].page for n in nodes + out}
    toc_pages = set(toc.toc_pages(doc.name))
    for d in _divisions(doc.name):
        b = doc.by_ref.get(d.get("ref") or "")
        if b is None or d["pdf_page"] in toc_pages or d["pdf_page"] in starts or b.order <= last:
            continue
        if roles.ATTACH.match((d.get("text") or "").strip()):
            out.append(Node(title=d["text"].strip(), source="pagemap", start=b.order, heading_ref=b.ref,
                            anchor="division", confidence="high"))
            starts.add(d["pdf_page"])
    for r in _page_runs(doc.name):
        p = r["pdf_first"]
        if r["label_first"] not in ("1", "i", "I") or p <= doc.page_count / 2 or {p - 1, p} & starts:
            continue
        body = [b for b in doc.page_blocks(p) if b.layer == "body" and not b.in_picture and b.page == p]
        if body and body[0].order > last:
            head = next((b for b in body if b.is_heading), body[0])
            out.append(Node(title=head.text.strip()[:120] or f"p{p}", source="pagemap", start=body[0].order,
                            heading_ref=head.ref if head is body[0] else None, anchor="page_restart",
                            confidence="medium", flags=["attachment_by_page_restart"]))
    return out


def is_boundary(b: Block) -> bool:
    """Could this unexplained heading open a new section? Sentences and greetings cannot."""
    t = b.text.strip()
    return bool(t) and not t[-1] in ".,;!?。，；！？" and len(t.split()) <= 20 and not t[0].islower()


# ---------------------------------------------------------------- geometry of the tree

def spans(nodes: list[Node], n_blocks: int) -> list[Node]:
    for n in nodes:
        n.depth = len(n.ancestors()) + 1
    ordered = sorted(nodes, key=lambda n: (n.start, n.depth))
    stack: list[Node] = []
    for n in ordered:
        while stack and stack[-1].depth >= n.depth:
            stack.pop().end = n.start
        stack.append(n)
    for n in stack:
        n.end = n_blocks
    for n in ordered:
        if n.parent is not None:
            n.end = min(n.end, n.parent.end)
    return ordered


def picture_of(doc: Doc, b: Block) -> str | None:
    ref = b.parent
    while ref and not ref.startswith("#/pictures/"):
        if ref == "#/body":
            return None
        ref = (doc.item(ref).get("parent") or {}).get("$ref")
    return ref


def assign_blocks(doc: Doc, ordered: list[Node], boundaries: list[float]) -> tuple[dict[str, Node | None], dict[str, list[Node]]]:
    starts = [n.start for n in ordered]
    by_start: dict[int, list[Node]] = {}
    for n in ordered:
        by_start.setdefault(n.start, []).append(n)
    out: dict[str, Node | None] = {}
    shared: dict[str, list[Node]] = {}
    stack: list[Node] = []
    k = 0
    for b in doc.blocks:
        while k < len(ordered) and ordered[k].start <= b.order:
            n = ordered[k]
            while stack and not (stack[-1].start <= n.start < stack[-1].end and stack[-1].contains(n)):
                stack.pop()
            stack.append(n)
            k += 1
        while stack and not (stack[-1].start <= b.order < stack[-1].end):
            stack.pop()
        if b.layer != "body":
            out[b.ref] = None
            continue
        if b.in_picture:
            continue
        here = by_start.get(b.order, [])
        if len(here) > 1:
            lca = here[0]
            while not all(lca.contains(x) for x in here):
                lca = lca.parent
                if lca is None:
                    break
            out[b.ref] = lca
            shared[b.ref] = here
            continue
        p = stack[-1] if stack else None
        if p is not None and p.start < b.order:
            j = bisect.bisect_right(boundaries, p.start)
            if j < len(boundaries) and boundaries[j] < b.order:
                nxt_i = bisect.bisect_right(starts, b.order)
                nxt = ordered[nxt_i] if nxt_i < len(ordered) else None
                while p is not None and not p.complete and not (nxt is not None and p.contains(nxt)):
                    p = p.parent
        while p is not None and p.confidence == "low":
            p = p.parent
        out[b.ref] = p
    for b in doc.blocks:
        if b.layer == "body" and b.in_picture:
            pic = picture_of(doc, b)
            out[b.ref] = out.get(pic) if pic else None
    return out, shared


# ---------------------------------------------------------------- document

def build(name: str) -> dict[str, Any]:
    doc = bundle.load(name)
    info = registry.get(name)
    margin = roles.running_headers(doc)
    role = roles.classify(doc, margin)
    for b in doc.headings():
        if b.in_picture:
            role[b.ref] = "figure_text"

    raw_ol = pdfoutline.outline(name)
    entries = outline.clean(doc, raw_ol) if raw_ol else []
    oq = outline.quality(entries) if raw_ol else None
    headers = not raw_ol and is_cjk_doc(doc) and bool(entries := headerchapters.entries(doc, margin))
    if headers:
        oq = {**outline.quality(entries), "source": "running_headers"}
    if oq is not None:
        agree, total = outline_agreement(doc, entries)
        oq["numbering_agreement"] = [agree, total]
    ol_ok = bool(oq) and (headers and oq["used"] >= 2 or oq["used"] >= 3 and oq["match_rate"] >= 0.75 and (
        oq["numbering_agreement"][1] < 5 or oq["numbering_agreement"][0] >= 0.8 * oq["numbering_agreement"][1]))

    cands, nnodes, rejected = number_tree(doc, role)
    nq = numbering_quality(cands, nnodes)
    num_ok = numbering_ok(nq)

    tq: dict[str, Any] = {"used": False}
    if ol_ok:
        nodes = outline_nodes(doc, entries)
        _, loose = _candidates(doc, role)
        nodes += extend_with_numbering(doc, nodes, nnodes, cands, loose)
    else:
        nodes = numbering_nodes(doc, nnodes, "high" if num_ok else "low")
        if num_ok:
            tq = confirm_top_level(doc, nodes)
    enh_keys(nodes)
    name_implied(nodes, toc.numbered_titles(name))
    used = {n.heading_ref for n in nodes if n.heading_ref} | {r for n in nodes for r in n.title_refs}
    nodes += convention_nodes(doc, nodes, role, used)
    used |= {n.heading_ref for n in nodes if n.heading_ref}
    nodes += attachment_nodes(doc, nodes, role, used)
    used |= {n.heading_ref for n in nodes if n.heading_ref}

    ordered = spans(nodes, len(doc.blocks))
    for i, n in enumerate(ordered, 1):
        n.id = f"s{i}"
    assign_scopes(ordered)

    for ref in used:
        if ref in role or ref in doc.by_ref:
            role[ref] = "section"
    first = ordered[0].start if ordered else len(doc.blocks)
    for b in doc.headings():
        if role.get(b.ref) == "unknown" and b.order < first:
            role[b.ref] = "front_matter"
    boundaries = sorted(
        [float(b.order) for b in doc.headings() if role.get(b.ref) == "unknown" and not b.in_picture
         and b.layer == "body" and is_boundary(b)]
        + [_gap_boundary(ordered, n) for n in ordered if {"gap", "virtual"} & set(n.flags)])
    mapping, shared = assign_blocks(doc, ordered, boundaries)
    out = _sidecar(doc, info, ordered, mapping, shared, role, entries, oq, ol_ok, nq, num_ok, rejected)
    out["diagnostics"]["toc"] = tq
    out["running_headers"] = [b.ref for b in doc.blocks if b.ref in margin and b.ref not in used
                              and role.get(b.ref, "running_header") == "running_header"]
    return out


def _gap_boundary(ordered: list[Node], n: Node) -> float:
    prev = [m.start for m in ordered if m.start < n.start]
    return (max(prev) if prev else n.start - 1) + 0.5


def _base_scope(n: Node) -> Node | None:
    for a in n.ancestors():
        if {"subdoc", "filename"} & set(a.flags) or is_attachment(a) or "attachment_by_page_restart" in a.flags:
            return a
    return None


def assign_scopes(ordered: list[Node]) -> None:
    """number_scope: the node whose numbering a number belongs to (an embedded document, a volume, an
    attachment). Where a number still repeats inside one scope (10-Q items under PART I and PART II,
    FAQ items 1-12 inside Appendix F), each copy is scoped by its outermost ancestor not shared."""
    for n in ordered:
        n.scope = _base_scope(n)
    groups: dict[tuple, list[Node]] = {}
    for n in ordered:
        if n.num is not None and n.number_key:
            groups.setdefault((id(n.scope), n.num.kind, n.number_key), []).append(n)
    for group in groups.values():
        for n in group if len(group) > 1 else []:
            others = [m for m in group if m is not n]
            own = [a for a in n.ancestors() if not any(a.contains(m) for m in others)]
            if own:
                n.scope = own[-1]


def _scope(n: Node) -> str:
    return n.scope.id if n.scope is not None else "s0"


def _node_json(n: Node, doc: Doc) -> dict[str, Any]:
    pages = [p for b in doc.blocks[n.start:n.end] if b.layer == "body" for p in b.pages]
    num = n.num
    out = {
        "id": n.id, "parent": n.parent.id if n.parent else "s0", "depth": n.depth,
        "kind": "attachment" if is_attachment(n) or "attachment_by_page_restart" in n.flags else "section",
        "title": n.title, "number": num.raw if num else None,
        "number_kind": num.kind if num else "none", "number_key": n.number_key if num else None,
        "source": n.source, "confidence": n.confidence, "heading_ref": n.heading_ref, "anchor": n.anchor,
        "start_order": n.start, "end_order": n.end, "pages": [min(pages), max(pages)] if pages else None,
    }
    if num is not None:
        out["number_scope"] = _scope(n)
    if num and num.note.startswith("ocr"):
        out["number_fixed_from"] = num.raw if "->" not in num.note else num.note.split(": ", 1)[1].split(" -> ")[0]
    if n.flags:
        out["flags"] = sorted(set(n.flags))
    if n.title_refs:
        out["title_refs"] = n.title_refs
    return out


def _sidecar(doc: Doc, info: Any, ordered: list[Node], mapping: dict[str, Node | None],
             shared: dict[str, list[Node]], role: dict[str, str], entries: list[outline.Entry],
             oq: dict | None, ol_ok: bool, nq: dict, num_ok: bool, rejected: dict[str, str]) -> dict[str, Any]:
    body = [b for b in doc.blocks if b.layer == "body" and not b.in_picture]
    total = sum(_words(b) for b in body) or 1
    max_depth = max((n.depth for n in ordered), default=0)
    certain_depth = {b.ref: (mapping[b.ref].depth if mapping.get(b.ref) else 0) for b in body}
    share = {str(d): round(sum(_words(b) for b in body if certain_depth[b.ref] >= d) / total, 3)
             for d in range(1, max_depth + 1)}
    sources = sorted({n.source for n in ordered if n.confidence != "low"})
    reliable = [s for s, ok in (("outline", ol_ok), ("numbering", num_ok)) if ok and s in sources]
    cover = share.get("1", 0.0)
    status = "unknown" if not reliable else "reliable" if cover >= 0.9 else "partial"
    verdict = {
        "status": status, "sources": sources, "reliable_sources": reliable, "max_depth": max_depth,
        "word_share_by_depth": share, "short_doc": info.pages <= SHORT_PAGES,
        "note": _note(status, reliable, oq, nq, cover, max_depth, info.pages <= SHORT_PAGES),
    }
    nodes_json = [{"id": "s0", "parent": None, "depth": 0, "kind": "root", "start_order": 0,
                   "end_order": len(doc.blocks), "pages": [1, doc.page_count]}]
    nodes_json += [_node_json(n, doc) for n in ordered]
    block_section = {b.ref: (mapping[b.ref].id if mapping.get(b.ref) else ("s0" if b.layer == "body" else None))
                     for b in doc.blocks}
    node_of_ref: dict[str, Node] = {}
    for n in ordered:
        for r in [n.heading_ref, *n.title_refs]:
            if r:
                node_of_ref.setdefault(r, n)
    headings = []
    for b in doc.blocks:
        if not (b.is_heading or b.ref in node_of_ref):
            continue
        r = role.get(b.ref, "section" if b.ref in node_of_ref else "unknown")
        h = {"ref": b.ref, "role": r, "node": node_of_ref[b.ref].id if r == "section" and b.ref in node_of_ref else None}
        if b.ref in rejected and r != "section":
            h["numbering"] = rejected[b.ref]
        headings.append(h)
    return {
        "doc": doc.name,
        "producer": PRODUCER,
        "verdict": verdict,
        "nodes": nodes_json,
        "block_section": block_section,
        "headings": headings,
        "multi_section_blocks": {ref: [n.id for n in ns] for ref, ns in shared.items()},
        "diagnostics": {"outline": oq, "outline_ok": ol_ok, "numbering": nq, "numbering_ok": num_ok,
                        "outline_entries": _entry_rows(entries)},
    }


def _entry_rows(entries: list[outline.Entry]) -> list[dict[str, Any]]:
    return [{"i": e.i, "level": e.level, "title": e.title[:120], "page": e.page, "junk": e.junk,
             "drop": e.drop, "ref": e.ref, "method": e.method, "depth": e.depth} for e in entries]


def _note(status: str, reliable: list[str], oq: dict | None, nq: dict, cover: float, depth: int,
          short: bool) -> str:
    parts = []
    if oq and oq.get("source") == "running_headers":
        parts.append(f"无书签；按页眉分出 {oq['used']} 章")
    elif oq:
        parts.append(f"书签 {oq['entries']} 条，去掉噪声后 {oq['kept']} 条，对上块 {oq['match_rate']:.0%}")
    else:
        parts.append("无书签")
    if nq["numbered_headings"]:
        parts.append(f"带编号标题 {nq['numbered_headings']} 个，通过一致性检查 {nq['accept_rate']:.0%}")
    else:
        parts.append("标题无编号")
    src = "+".join(reliable) if reliable else "无可靠来源"
    parts.append(f"可靠来源：{src}；第 1 层覆盖 {cover:.0%} 正文词，最深 {depth} 层")
    if short:
        parts.append("短文档（≤10 页），可整篇交付")
    return "；".join(parts)


def write(name: str) -> dict[str, Any]:
    data = build(name)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.structure.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    os.replace(tmp, path)
    return data


def main(argv: list[str] | None = None) -> None:
    import sys

    names = (argv if argv is not None else sys.argv[1:]) or [d.name for d in registry.docs()]
    for name in names:
        v = write(name)["verdict"]
        print(f"{name:58s} {v['status']:8s} d{v['max_depth']} {v['word_share_by_depth']}")


if __name__ == "__main__":
    main()
