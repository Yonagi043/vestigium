"""Chinese references (task ZH's rules D5) resolved against task A's Chinese-numbered nodes.

Mentions come from `tglw.zh.xrefs.find_mentions` (Chinese documents only). Targets:
- 第X章/节/条/编, and 本节X、 -> A's node of that unit and number (anchor kind "zh", key "<number_kind>:<key>");
- “第三节 管理层讨论与分析” / 「代價及付款條款」一節 -> by the quoted heading's number, else its title;
- 附注五、12 / 附註2 -> down A's tree: 五、 then its 12 (in the financial statements when the mention says so);
- 表 3 / 图 2-1 -> Chinese captions (anchor kinds "table" / "figure", like English captions).
Appendix and page mentions go through the English resolvers (A's 附錄 nodes key as "appendix").
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz

from tglw.common.bundle import Block, Doc
from tglw.structure.numbering import to_roman
from tglw.structure.roles import text_key
from tglw.zh import headings

from .anchors import Anchor, Index, raw_text, structure
from .detect import Mention

UNIT_KIND = {"章": "chapter", "节": "zh_section", "条": "zh_article", "编": "part", "部分": "part", "篇": "part",
             "、": "zh_list"}
ZH_NODE_KINDS = frozenset({"chapter", "part", "appendix", "dotted", *(k for k in headings.KIND.values())})
NOTE_KINDS = ("zh_list", "zh_paren", "zh_arabic", "zh_arabic_paren", "zh_circled", "dotted")
_CAPTION = re.compile(r"^\s*(附表|表|图|圖)\s*([0-9]{1,3}(?:\s*[-.－]\s*[0-9]{1,3})*)(?![0-9])\s*[:：.、]?\s*(?=\S)")
_FS_TITLE = re.compile(r"财务报告|財務報告|财务报表|財務報表|附注|附註|注释|註釋")
_ACCOUNTANTS_TITLE = re.compile(r"会计师报告|會計師報告")


def anchors(doc: Doc, blocks: list[Block]) -> list[Anchor]:
    """A's Chinese-numbered nodes keyed by number kind and key, and Chinese table/figure captions."""
    out = []
    side = structure(doc.name)
    for n in (side or {}).get("nodes", [])[1:]:
        nk, key = n.get("number_kind"), n.get("number_key")
        ref = n.get("heading_ref") or (doc.blocks[n["start_order"]].ref if n["start_order"] < len(doc.blocks) else None)
        if nk in ZH_NODE_KINDS and key and ref in doc.by_ref:
            b = doc.by_ref[ref]
            out.append(Anchor("zh", f"{nk}:{key}", ref, (n.get("pages") or [b.page])[0], b.order, (n.get("title") or b.text)[:120],
                              f"A:{n.get('source')}", depth=n.get("depth") or 0, top=n.get("number_scope") or ""))
    for b in blocks:
        if b.kind == "text" and (m := _CAPTION.match(b.text)) and (b.label in ("caption", "section_header") or len(b.text) <= 80):
            kind = "figure" if m.group(1) in ("图", "圖") else "table"
            out.append(Anchor(kind, re.sub(r"[\s－]", lambda x: "-" if x.group() == "－" else "", m.group(2)), b.ref,
                              b.page, b.order, b.text[:120], "zh-caption"))
    return out


def resolve(m: Mention, src: Block, idx: Index):
    """Resolution for a Chinese section or note mention, or None to use the English resolvers."""
    from .resolve import Resolution, _pick, target

    if m.kind == "note" and m.qual.get("label", "").startswith("附"):
        return _note(m, src, idx)
    if m.kind != "section" or m.qual.get("external"):
        return None
    method = "zh-number"
    if m.qual.get("label") == "quoted":
        if not m.qual.get("zh_number"):
            return _title(m, src, idx)
        style, key = m.qual["zh_number"].split(":", 1)
        nk, method = headings.KIND[style], "zh-quoted-number"
    else:
        nk, key = UNIT_KIND.get(m.qual.get("zh_unit", "")), m.key
        if nk is None:
            return Resolution("unresolved", method, reason=f"no {m.qual.get('zh_unit')} units in task A")
    if nk == "part" and key.isdigit():
        key = to_roman(int(key)) or key
    cands = idx.get("zh", f"{nk}:{key}")
    if m.qual.get("scope") == "本节":
        side = structure(idx.doc.name) or {}
        here = _top(side, side.get("block_section", {}).get(src.ref))
        cands = [a for a in cands if here and _top(side, side.get("block_section", {}).get(a.ref)) == here]
    precision = "parent" if {"clause", "item", "sub"} & set(m.qual) else "exact"
    if not cands:
        return Resolution("unresolved", method, reason=f"no {nk} {key} in task A")
    return _pick(idx.doc.name, cands, src, method, precision, idx=idx)


def _title(m: Mention, src: Block, idx: Index):
    """A quoted heading without a number: a heading (A node or Docling heading) whose title reads the same
    ("「代價及付款條款」一節" -> the heading 代價及付款條款), else nearly the same."""
    from .resolve import Resolution, _pick

    want = text_key(m.key)
    nodes = [n for n in (structure(idx.doc.name) or {}).get("nodes", [])[1:] if n.get("heading_ref") in idx.doc.by_ref]
    found = [(n["heading_ref"], n["title"], n.get("number_scope") or "") for n in nodes]
    found += [(b.ref, b.text, "") for b in idx.headings]
    keyed = [(ref, title, top, text_key(headings.parse(title).title if headings.parse(title) else title))
             for ref, title, top in found]
    hits = [x for x in keyed if x[3] == want or text_key(x[1]) == want]
    hits = hits or [x for x in keyed if len(want) >= 4 and fuzz.ratio(x[3], want) >= 90]
    if not hits:
        return Resolution("unresolved", "zh-quoted-title", reason="no heading with this title")
    cands = [Anchor("zh", m.key, ref, idx.doc.by_ref[ref].page, idx.doc.by_ref[ref].order, title[:120], "heading", top=top)
             for ref, title, top, _ in hits]
    cands = sorted({a.ref: a for a in cands}.values(), key=lambda a: a.order)
    return _pick(idx.doc.name, cands, src, "zh-quoted-title", idx=idx)


_NOTE_LABEL = re.compile(r"^\s*附\s*[註注]\s*[:：]?\s*$")


def _note_patterns(key: str) -> tuple[re.Pattern[str], re.Pattern[str]]:
    n = re.escape(key.split(".")[0])
    return re.compile(rf"^\s*(?:附\s*)?[註注]\s*{n}\s*[:：.、)）]"), re.compile(rf"^\s*[(（]?\s*{n}\s*[)）.、]")


def _local_note(m: Mention, src: Block, idx: Index, pages: int) -> Block | None:
    """A table or chart note printed near the source: "附註1： ..." / "註1：", or "(1) ..." / "1. ..." after a
    "附註：" label. After the source on its page and the `pages` after it; before it, on its page and the
    `pages` before, when the mention says 上文 (qual where=above)."""
    own, item = _note_patterns(m.key)
    above = m.qual.get("where") == "above"
    blocks = idx.doc.blocks[src.order - 1::-1] if above else idx.doc.blocks[src.order + 1:]
    labelled = False
    for b in blocks:
        if b.page is None or src.page is None or abs(b.page - src.page) > pages:
            break
        if b.layer != "body" or b.kind != "text":
            continue
        if own.match(b.text):
            return b
        if above and item.match(raw_text(idx.doc, b)):
            return b
        if _NOTE_LABEL.match(b.text):
            labelled = True
        elif labelled and item.match(raw_text(idx.doc, b)):
            return b
    return None


_NOTES_TITLE = re.compile(r"(?:附\s*[註注]|注\s*释|註\s*釋)\s*$")


def _notes_section(m: Mention, src: Block, idx: Index) -> Block | None:
    """The note under the nearest heading after the source, in the same top-level part, whose title ends
    in 附註 ("未經審核備考財務資料附註" seven pages after the table that cites it): its item numbered
    like the mention ("(1)" list item, or "1." text)."""
    side = structure(idx.doc.name) or {}
    top = _top(side, side.get("block_section", {}).get(src.ref))
    node = nodes_by(side.get("nodes", [])).get(top or "")
    end = node["end_order"] if node else len(idx.doc.blocks)
    head = next((b for b in idx.headings if src.order < b.order < end and _NOTES_TITLE.search(b.text)), None)
    if head is None:
        return None
    own, item = _note_patterns(m.key)
    for b in idx.doc.blocks[head.order + 1:end]:
        if b.layer != "body" or b.kind != "text":
            continue
        if b.is_heading and b is not head and not item.match(raw_text(idx.doc, b)):
            break
        if own.match(b.text) or item.match(raw_text(idx.doc, b)):
            return b
    return None


def _note(m: Mention, src: Block, idx: Index):
    """附注五、12 -> the 五、 node, then its 12 below it; inside the financial statements (or the
    accountants' report) when the mention or the source says so, else anywhere. Fewer levels found than
    asked: the deepest one found, precision parent."""
    from .resolve import Resolution, target

    local = "." not in m.key and _local_note(m, src, idx, 3 if m.qual.get("paren") or m.qual.get("where") else 2)
    if local:
        return Resolution("resolved", "zh-local-note", [{"doc": idx.doc.name, "ref": local.ref, "page": local.page,
                                                         "title": local.text[:120]}])
    if m.qual.get("paren") and m.qual.get("where") != "above" and (b := _notes_section(m, src, idx)) is not None:
        return Resolution("resolved", "zh-notes-section", [{"doc": idx.doc.name, "ref": b.ref, "page": b.page,
                                                            "title": b.text[:120]}])
    if m.qual.get("paren") or m.qual.get("where") == "above":
        return Resolution("unresolved", "zh-local-note", reason=f"note ({m.key}) not found near the reference")
    side = structure(idx.doc.name) or {}
    nodes = side.get("nodes", [])
    kids: dict[str, list[dict]] = {}
    for n in nodes[1:]:
        kids.setdefault(n["parent"], []).append(n)
    parts = m.key.split(".")
    scope = _scope_roots(nodes, side.get("block_section", {}).get(src.ref), m.qual.get("scope"))
    level = [n for root in scope for n in _descendants(root, kids) if n.get("number_kind") in NOTE_KINDS
             and n.get("number_key") == parts[0]]
    if not level and len(parts) == 1:
        return _heading_note(m, idx, [by for by in (nodes_by(nodes).get(r) for r in scope) if by])
    if not level:
        return Resolution("unresolved", "zh-note", reason=f"no note {parts[0]} nearby or in this part's numbered sections")
    path, found = level, 1
    for p in parts[1:]:
        nxt = [c for n in path for c in _descendants(n["id"], kids) if c.get("number_kind") in NOTE_KINDS
               and c.get("number_key") == p and c is not n]
        if not nxt:
            break
        path, found = nxt, found + 1
    precision = "exact" if found == len(parts) else "parent"
    best = path[0]
    b = idx.doc.by_ref.get(best.get("heading_ref") or "")
    if b is None:
        return Resolution("unresolved", "zh-note", reason="note node without a heading block")
    a = Anchor("note", m.key, b.ref, (best.get("pages") or [b.page])[0], b.order, best["title"][:120], f"A:{best.get('source')}")
    status = "resolved" if len(path) == 1 else "ambiguous"
    return Resolution(status, "zh-note", [target(idx.doc.name, a)], precision,
                      "" if status == "resolved" else f"{len(path)} candidates")


def _top(side: dict, node: str | None) -> str | None:
    """The top-level section (depth 1) holding a node; None at the root."""
    by_id = {n["id"]: n for n in side.get("nodes", [])}
    while node in by_id and by_id[node].get("parent") not in (None, "s0"):
        node = by_id[node]["parent"]
    return node if node in by_id and node != "s0" else None


def nodes_by(nodes: list[dict]) -> dict[str, dict]:
    return {n["id"]: n for n in nodes}


def _heading_note(m: Mention, idx: Index, parts: list[dict]):
    """A note numbered by a heading A did not make a section ("2. 目標集團之未經審核財務資料之呈列及
    編製基準" under its appendix): the one heading of the source's part with that Chinese number."""
    from .resolve import Resolution

    hits = [b for top in parts for b in idx.headings if top["start_order"] <= b.order < top["end_order"]
            and (z := headings.parse(b.text)) is not None and z.key == m.key]
    if len(hits) != 1:
        reason = f"{len(hits)} headings numbered {m.key} in this part" if hits else f"no note {m.key} nearby or in this part"
        return Resolution("unresolved", "zh-note", reason=reason)
    b = hits[0]
    return Resolution("resolved", "zh-note-heading", [{"doc": idx.doc.name, "ref": b.ref, "page": b.page,
                                                      "title": b.text[:120]}])


def _scope_roots(nodes: list[dict], src_node: str | None, scope: str | None) -> list[str]:
    """Where a note is looked for: the top-level parts named like financial statements (or the
    accountants' report) when the mention says so; else the top-level part the source sits in."""
    by_id = {n["id"]: n for n in nodes}
    if scope:
        rx = _ACCOUNTANTS_TITLE if scope == "accountants" else _FS_TITLE
        named = [n["id"] for n in nodes[1:] if n.get("parent") == "s0" and rx.search(n.get("title") or "")]
        if named:
            return named
    top = src_node
    while top and top in by_id and by_id[top].get("parent") not in (None, "s0"):
        top = by_id[top]["parent"]
    return [top] if top and top != "s0" else []


def _descendants(root: str, kids: dict[str, list[dict]]) -> list[dict]:
    out, stack = [], list(reversed(kids.get(root, [])))
    while stack:
        n = stack.pop()
        out.append(n)
        stack.extend(reversed(kids.get(n["id"], [])))
    return out
