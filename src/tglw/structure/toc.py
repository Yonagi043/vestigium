"""Table-of-contents route (task B, data/B/<doc>.toc.json): the top-level sections of documents
without usable bookmarks, plus titles for numbered parents that have no heading block.

Only the top tier of a contents page is used as structure. Deeper contents levels come from
indentation, which is unreliable on scanned pages (two-column rows merge, tiers jitter).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from tglw.common.bundle import Doc
from tglw.common.paths import DATA

from . import outline
from .numbering import Num, num_key, parse
from .roles import text_key

_LEADER = re.compile(r"\.\s?\.\s?\.|·\s?·|…")


@dataclass
class TocTop:
    title: str
    num: Num | None
    page: int
    ref: str | None = None
    order: int | None = None
    method: str | None = None
    score: float = 0.0


def _load(name: str) -> dict[str, Any]:
    manifest = DATA / "B" / "MANIFEST.json"
    path = DATA / "B" / f"{name}.toc.json"
    if not manifest.exists() or not path.exists():
        return {}
    if json.loads(manifest.read_text()).get("parts", {}).get("toc", {}).get("status") != "delivered":
        return {}
    return json.loads(path.read_text())


def toc_pages(name: str) -> list[int]:
    return _load(name).get("toc_pages") or []


def section_entries(name: str) -> list[dict[str, Any]]:
    manifest = DATA / "B" / "MANIFEST.json"
    path = DATA / "B" / f"{name}.toc.json"
    if not manifest.exists() or not path.exists():
        return []
    if json.loads(manifest.read_text()).get("parts", {}).get("toc", {}).get("status") != "delivered":
        return []
    toc = json.loads(path.read_text())
    return [e for e in toc.get("entries", []) if e.get("kind") == "section" and e.get("pdf_page")]


def _caps(title: str) -> bool:
    letters = [c for c in title if c.isalpha()]
    return len(letters) >= 4 and sum(c.isupper() for c in letters) >= 0.85 * len(letters)


def _merged(title: str) -> bool:
    return bool(_LEADER.search(title)) or len(title.split()) > 14


def listing_noise(title: str) -> bool:
    """Rows of a list of tables or figures that leaked into the section list ('3.2.3-1V 3.3.2-I ...')."""
    first = title.split()[0] if title.split() else ""
    return _merged(title) or bool(re.fullmatch(r"[\dIVXLivxl.\-]+", first)) or bool(
        re.search(r"\b(table|figure|page)\b", title, re.I))


def _num(e: dict[str, Any]) -> Num | None:
    if e.get("number"):
        n = parse(f"{e['number']} {e['title']}")
        if n is not None:
            return n
    return parse(e["title"])


def top_tier(ents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Entries of the top level: depth-1 numbers in a numbered contents page, else the upper-case
    tier when capitals separate chapters from sections, else the shallowest level hint."""
    good = [e for e in ents if not _merged(e["title"])]
    nums = [(e, _num(e)) for e in good]
    numbered = [(e, n) for e, n in nums if n is not None]
    if len(numbered) >= 0.5 * len(good):
        return [e for e, n in nums if (n is not None and n.depth == 1 and n.family in ("dec", "app", "part"))]
    caps = [e for e in good if _caps(e["title"])]
    if 2 <= len(caps) < len(good):
        return caps
    low = min((e["level_hint"] for e in good), default=1)
    return [e for e in good if e["level_hint"] == low]


def anchor_top(doc: Doc, ents: list[dict[str, Any]]) -> list[TocTop]:
    out = []
    after = 0
    for e in ents:
        t = TocTop(e["title"].strip(), _num(e), e["pdf_page"])
        m = e.get("match") or {}
        if m.get("ref") in doc.by_ref and m.get("score", 0) >= 90 and abs((m.get("page") or 0) - t.page) <= 1:
            b = doc.by_ref[m["ref"]]
            t.ref, t.order, t.method, t.score = b.ref, b.order, "toc_match", m["score"]
        else:
            probe = outline.Entry(0, 1, t.title, t.page, None)
            outline.anchor(doc, probe, after)
            if probe.ref and probe.method not in ("position", "page"):
                t.ref, t.order, t.method, t.score = probe.ref, probe.order, probe.method, probe.score
        if t.ref:
            after = max(after, t.order or 0)
        out.append(t)
    return out


def _consecutive(tier: list[TocTop]) -> bool:
    """Numbered top entries must run 1, 2, 3 (and A, B, C) without gaps or repeats."""
    series: dict[bool, list[int]] = {}
    for t in tier:
        if t.num is None:
            continue
        k = t.num.key[0]
        val = ord(k) - 64 if k.isalpha() and len(k) == 1 else int(k) if k.isdigit() else None
        if val is None:
            return False
        series.setdefault(k.isalpha(), []).append(val)
    return all(vals == list(range(vals[0], vals[0] + len(vals))) and vals[0] <= 1 for vals in series.values())


def tops(doc: Doc, name: str) -> tuple[list[TocTop], dict[str, Any]]:
    """Top-level contents entries and their quality numbers. Entries that cannot be anchored to a
    block keep their PDF page (method 'page'), so they still close the previous section."""
    ents = section_entries(name)
    tier = anchor_top(doc, top_tier(ents))
    anchored = [t for t in tier if t.ref]
    keep = outline._lis([t.order for t in anchored])
    kept = [t for k, t in enumerate(anchored) if k in keep]
    titles = [(text_key(t.title), t.page // 2) for t in tier]
    q = {"sections": len(ents), "top_tier": len(tier), "anchored": len(anchored), "order_kept": len(kept),
         "anchor_rate": round(len(anchored) / len(tier), 3) if tier else 0.0,
         "consecutive": _consecutive(tier), "duplicate_titles": len(titles) - len(set(titles))}
    q["ok"] = (len(kept) >= 3 and q["anchor_rate"] >= 0.7 and len(kept) >= 0.9 * len(anchored)
               and q["consecutive"] and not q["duplicate_titles"])
    out, seen = [], set()
    for t in tier:
        if t.ref and t not in kept:
            continue
        if not t.ref:
            body = [b for b in doc.page_blocks(t.page) if b.layer == "body" and not b.in_picture and b.page == t.page]
            if not body:
                continue
            t.ref, t.order, t.method = body[0].ref, body[0].order, "page"
        if t.ref not in seen:
            seen.add(t.ref)
            out.append(t)
    return sorted(out, key=lambda t: t.order), q


def numbered_titles(name: str) -> dict[tuple[str, str], tuple[str, int]]:
    """(number kind, number key) -> (contents title, PDF page), for naming implied parents."""
    out = {}
    for e in section_entries(name):
        n = _num(e)
        if n is not None and n.family != "enh":
            out.setdefault((n.kind, num_key(n) or ""), (e["title"].strip(), e["pdf_page"]))
    return out


def same_title(a: str, b: str) -> bool:
    return text_key(a) == text_key(b)
