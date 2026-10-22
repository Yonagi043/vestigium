"""Assemble the per-document term sidecar and write data/C1/<doc>.terms.json.

    python -m tglw.terms.build                 # all 59 cached documents -> data/C1/wip/
    python -m tglw.terms.build --doc NAME ...  # selected documents
    python -m tglw.terms.build --out DIR       # another directory (delivered versions live in data/C1/)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from tglw.common import bundle, registry
from tglw.common.bundle import Doc
from tglw.common.paths import DATA
from tglw.common.text import norm
from tglw.zh import is_cjk_doc

from .inline import find_inline
from .mentions import Matcher, case_mode, count
from .model import Definition
from .parse import clean, is_acronym, is_redirect
from .sections import Section, find_sections
from .zh import find_zh_inline, find_zh_sections

PRODUCER = "tglw.terms v2"
OUT = DATA / "C1"
WIP = OUT / "wip"
SIMILAR = 85
_QUOTED_VIA = {"quoted", "means", "the_term", "refer_as"}
_HAN = re.compile(r"[\u4e00-\u9fff]")


def term_key(s: str) -> str:
    s = clean(s)
    return s if is_acronym(s) else s.lower()


def _plain(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", norm(s)).strip()


def _similar(a: Definition, b: Definition) -> bool:
    for x, y in ((a.definition, b.definition), (a.definition, b.term), (a.term, b.definition)):
        nx, ny = _plain(x), _plain(y)
        if nx and ny and (nx == ny or fuzz.token_set_ratio(nx, ny) >= SIMILAR):
            return True
    return False


def _groups(defs: list[Definition]) -> list[list[Definition]]:
    """Definitions whose term or variants share a key (union-find over keys)."""
    parent: dict[str, str] = {}

    def find(k: str) -> str:
        while parent.setdefault(k, k) != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for d in defs:
        keys = [term_key(d.term)] + [term_key(v) for v in d.variants if d.source != "inline"]
        for k in keys[1:]:
            parent[find(k)] = find(keys[0])
    out: dict[str, list[Definition]] = defaultdict(list)
    for d in defs:
        out[find(term_key(d.term))].append(d)
    return list(out.values())


def _clusters(group: list[Definition]) -> list[list[Definition]]:
    """Senses of one term: collection entries first, then in-text ones, joined when definitions agree."""
    ranked = sorted(group, key=lambda d: (d.source == "inline", d.order))
    clusters: list[list[Definition]] = []
    for d in ranked:
        for c in clusters:
            if _similar(c[0], d):
                c.append(d)
                break
        else:
            clusters.append([d])
    return [_fullest_first(c) for c in clusters]


def _fullest_first(c: list[Definition]) -> list[Definition]:
    """A duplicate OCR layer can hold a truncated copy; lead with the copy that extends the others."""
    best = c[0]
    for d in c[1:]:
        if d.source == best.source and len(d.definition) > len(best.definition) and \
                _plain(d.definition).startswith(_plain(best.definition)):
            best = d
    return [best] + [d for d in c if d is not best]


def _kind(d: Definition) -> str:
    if d.source == "abbrev_table" or d.extra.get("via") == "acronym":
        return "abbreviation"
    if is_acronym(d.term) and len(d.definition.split()) <= 8:
        return "abbreviation"
    return "glossary" if d.source == "glossary" else "defined_term"


def _heading_before(doc: Doc, order: int) -> str | None:
    ref = None
    for b in doc.blocks:
        if b.order >= order:
            break
        if b.is_heading and b.layer == "body" and not b.in_picture:
            ref = b.ref
    return ref


def _scopes(doc: Doc, clusters: list[list[Definition]]) -> list[Any]:
    """'doc' for collection senses and lone senses; a page range for each of several in-text senses."""
    inline = [c for c in clusters if c[0].source == "inline"]
    if len(clusters) < 2 or not inline:
        return ["doc"] * len(clusters)
    starts = sorted(min(d.page for d in c) for c in inline)
    out: list[Any] = []
    for c in clusters:
        if c[0].source != "inline":
            out.append("doc")
            continue
        p0 = min(d.page for d in c)
        later = [s for s in starts if s > p0]
        p1 = later[0] - 1 if later else doc.page_count
        out.append({"heading_ref": _heading_before(doc, c[0].order), "pages": [p0, p1]})
    return out


def _variants(c: list[Definition]) -> list[str]:
    out: list[str] = []
    for d in c:
        for v in [d.term] + (d.variants if d.source != "inline" or c[0].source == "inline" else []):
            if v != c[0].term and v not in out:
                out.append(v)
    return out


def _entry(c: list[Definition], scope: Any, ambiguous: int) -> dict[str, Any]:
    d = c[0]
    e: dict[str, Any] = {
        "term": d.term, "variants": _variants(c), "kind": _kind(d), "definition": d.definition,
        "def_ref": d.ref, "def_page": d.page, "source": d.source, "scope": scope,
        "mentions": None, "first_mention_page": None,
    }
    for k in ("term_ref", "row", "def_refs", "def_pages", "uncertain", "context"):
        if k in d.extra:
            e[k] = d.extra[k]
    e["via"] = d.extra.get("via")
    if d.section:
        e["section_ref"] = d.section
    if d.cite:
        e["cite"] = d.cite
    if is_redirect(d.definition):
        e["redirect"] = True
    if ambiguous > 1:
        e["ambiguous"] = True
        e["senses"] = ambiguous
    also = [{"ref": x.ref, "page": x.page, "source": x.source} for x in c[1:]]
    if also:
        e["also_at"] = also
    return e


def _sections_out(sections: list[Section]) -> list[dict[str, Any]]:
    out = []
    for s in sections:
        via: dict[str, int] = defaultdict(int)
        for d in s.pairs:
            via[d.extra["via"]] += 1
        out.append({"title": s.title, "title_ref": s.title_ref, "source": s.source, "pages": list(s.page_range),
                    "pairs": len(s.pairs), "table_rows": s.rows_seen, "via": dict(via)})
    return out


def build_doc(name: str) -> dict[str, Any]:
    doc = bundle.load(name)
    cjk = is_cjk_doc(doc)
    sections = find_sections(doc) + (find_zh_sections(doc) if cjk else [])
    collected = {r for s in sections for r in s.refs}
    defs = [d for s in sections for d in s.pairs] + find_inline(doc, collected)
    if cjk:
        defs = [d for d in defs if not (d.source == "inline" and _HAN.search(d.term))] + find_zh_inline(doc, collected)
    entries: list[dict[str, Any]] = []
    matcher = Matcher()
    for group in _groups(defs):
        clusters = _clusters(group)
        for c, scope in zip(clusters, _scopes(doc, clusters)):
            e = _entry(c, scope, len(clusters))
            tid = len(entries)
            quoted = c[0].extra.get("via") in _QUOTED_VIA
            counted = [s for s in [e["term"]] + e["variants"] if matcher.add(tid, s, case_mode(s, c[0].source, quoted))]
            e["counted"] = bool(counted)
            entries.append(e)
    counts = count(doc, matcher, collected)
    for tid, e in enumerate(entries):
        if not e.pop("counted"):
            e["mentions_note"] = "single-character term, not counted"
            continue
        c = counts.get(tid)
        pages = dict(c.pages) if c else {}
        if isinstance(e["scope"], dict):
            p0, p1 = e["scope"]["pages"]
            pages = {p: n for p, n in pages.items() if p0 <= p <= p1}
        e["mentions"] = sum(pages.values())
        e["first_mention_page"] = min(pages) if pages else None
        if c and c.pictures:
            e["mentions_in_pictures"] = c.pictures
    entries.sort(key=lambda e: (e["def_page"], e["term"].lower()))
    return {"doc": name, "producer": PRODUCER, "collections": _sections_out(sections), "terms": entries}


def writable(path: Path, delivered: Path = OUT) -> Path:
    """Refuse output paths inside a delivered directory other than its wip/ (delivered data is moved into
    place by hand, never written over by a generator)."""
    p, root = path.resolve(), delivered.resolve()
    if (p == root or root in p.parents) and not (p == root / "wip" or (root / "wip") in p.parents):
        raise SystemExit(f"refusing to write into delivered {root}: use {root / 'wip'} or a directory outside it")
    return path


def write_json(path: Path, obj: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1))
    os.replace(tmp, path)


def write_manifest(names: list[str], out: Path = WIP) -> None:
    files = sorted(p.name for p in out.glob("*.terms.json"))
    write_json(out / "MANIFEST.json", {"producer": PRODUCER, "version": "v2", "docs": len(names),
                                       "written": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "files": files})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", nargs="*")
    ap.add_argument("--manifest", action="store_true")
    ap.add_argument("--out", type=Path, default=WIP)
    args = ap.parse_args()
    names = args.doc or [d.name for d in registry.docs()]
    writable(args.out).mkdir(parents=True, exist_ok=True)
    for name in names:
        t = time.time()
        side = build_doc(name)
        write_json(args.out / f"{name}.terms.json", side)
        print(f"{name}: {len(side['collections'])} collections, {len(side['terms'])} terms, {time.time() - t:.1f}s")
    if args.manifest:
        write_manifest(names, args.out)


if __name__ == "__main__":
    main()
