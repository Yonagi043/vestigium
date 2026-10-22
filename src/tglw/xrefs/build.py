"""Per-document pipeline: scan body blocks (table cells one by one), resolve, write <doc>.xrefs.json.

    python -m tglw.xrefs.build [--out=DIR] [doc ...]   # all 59 cached docs when none given

Writes to data/C2/wip/ unless --out is given; the delivered data/C2/ is only written by an explicit --out.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

from tglw.common import bundle, registry
from tglw.common.bundle import Block, Doc, table_grid
from tglw.common.paths import DATA

from .anchors import Anchor, Index, build_index, content_blocks, is_heading_like
from .detect import Mention, find_mentions
from .keys import NUMBER_KEY_SOURCE
from .resolve import CONTROL_DOC, Resolution, resolve, target
from .zh import resolve as zh_resolve
from tglw.zh import is_cjk_doc
from tglw.zh.xrefs import find_mentions as zh_mentions

PRODUCER = "tglw.xrefs v5"
DELIVERED = DATA / "C2"
WIP = DELIVERED / "wip"
_DEF_TAIL = re.compile(r"^\s*(?:[:.|]|\.?\s*[-–—]\s*\S|\s+[A-Z][A-Z'’ ,&/()\-]{3,})")
_LABEL_WORDS = re.compile(r"\b(?:Section|Table|Figure|Appendix|Chapter|Note|Item)\b", re.I)
_NOT_MARKER_BEFORE = re.compile(
    r"(?:Table|Figure|Fig\.|Section|Sec\.|§|Note|Chapter|Item|Appendix|page|pages|p\.|pp\.|Part|No\.|Step|Phase|"
    r"Level|Volume|Vol\.|Rule|Article|Title|Tier|Class|Type|Category|Annex|Exhibit|Form|SP|FIPS|USC|CFR|Q[1-4]|FY|"
    r"\$|€|£|¥|RMB|HK\$|US\$)\s?$", re.I)


def texts_of(doc: Doc, b: Block) -> list[tuple[str, list[int] | None]]:
    if b.kind == "table":
        grid = table_grid(doc.item(b.ref))
        return [(cell, [r, c]) for r, row in enumerate(grid) for c, cell in enumerate(row) if cell]
    return [(b.text, None)] if b.kind == "text" and b.text else []


def is_definition(m: Mention, b: Block, text: str, cell: list[int] | None) -> bool:
    """'Table 3: Title', 'Note 2 - Revenue', 'AC-12 SESSION TERMINATION': the target itself, not a reference."""
    if m.qual.get("label") == "quoted" or m.kind in ("page", "external"):
        return False
    if m.kind in ("table", "figure") and re.match(r":\s+[A-Z]", text[m.end:]) and (m.start == 0 or text[m.start - 1] == " "):
        return True
    if m.start > 3:
        return False
    if cell is None and re.fullmatch(rf"\s*{re.escape(m.span)}\s*[.:]?\s*", text, re.I):
        return True
    if m.kind in ("table", "figure") and len(text) <= 150 and re.match(r"\s+[A-Z]", text[m.end:]):
        return True
    if cell is None and (is_heading_like(b) or b.label == "caption"):
        return True
    return bool(_DEF_TAIL.match(text[m.end:]))


def running_headers(doc: Doc) -> list[str]:
    seen = Counter(b.text.strip() for b in doc.blocks if b.label in ("page_header", "page_footer"))
    return [t for t, n in seen.items() if n >= 2 and len(t) >= 6 and _LABEL_WORDS.search(t)]


def in_running_header(m: Mention, text: str, headers: list[str]) -> bool:
    for h in headers:
        i = text.find(h)
        while i >= 0:
            if i <= m.start < i + len(h):
                return True
            i = text.find(h, i + 1)
    return False


def context(text: str, start: int, end: int, width: int = 90) -> str:
    lo, hi = max(0, start - width), min(len(text), end + width)
    return ("…" if lo else "") + text[lo:hi] + ("…" if hi < len(text) else "")


def entry(b: Block, cell: list[int] | None, text: str, m: Mention, res: Resolution) -> dict:
    e = {"src_ref": b.ref, "src_page": b.page}
    if cell is not None:
        e["cell"] = cell
    e.update({"span": m.span, "context": context(text, m.start, m.end), "kind": m.kind, "key": m.key,
              "status": res.status, "targets": res.targets, "method": res.method})
    if res.precision and res.status == "resolved":
        e["precision"] = res.precision
    if res.reason:
        e["reason"] = res.reason
    qual = {k: v for k, v in m.qual.items() if k not in ("label",)}
    if qual:
        e["qual"] = qual
    return e


def footnote_refs(doc: Doc, idx: Index) -> tuple[list[dict], int]:
    """Anchor-driven: for each numbered footnote block, find its marker in text or a table cell on the same or
    previous page. At most one marker per footnote, preferring markers right after punctuation or glued to a word."""
    out, missing = [], 0
    pieces = [(b, cell, text) for b in content_blocks(doc) if b.label != "footnote" and b.kind in ("text", "table")
              for text, cell in texts_of(doc, b)]
    for key, anchors in idx.anchors.get("footnote", {}).items():
        for a in anchors:
            best = _best_marker(pieces, a, key)
            if best is None:
                missing += 1
                continue
            b, cell, text, mt = best
            res = Resolution("resolved", "footnote-marker", [target(doc.name, a)], "exact")
            m = Mention("footnote", key, text[max(0, mt.start() - 12):mt.end()].strip(), mt.start(), mt.end())
            out.append(entry(b, cell, text, m, res))
    return out, missing


def _best_marker(pieces: list[tuple[Block, list[int] | None, str]], a: Anchor, key: str):
    rx = re.compile(rf"(?<=[A-Za-z.,;:?)\]”\"'’®™])(?P<sp>\s?){key}(?=\s|$|[,;:)]|\.(?!\d))")
    best, best_score = None, None
    for b, cell, text in pieces:
        if b.order >= a.order or not b.pages or a.page is None or not set(b.pages) & {a.page, a.page - 1}:
            continue
        for mt in rx.finditer(text):
            before = text[:mt.start()]
            if _NOT_MARKER_BEFORE.search(before):
                continue
            glued = not mt.group("sp")
            punct = before[-1:] in ".,;:?)]”\"'’"
            score = (2 if punct else 1 if glued else 0, a.page in b.pages, b.order)
            if best_score is None or score > best_score:
                best, best_score = (b, cell, text, mt), score
    return best


def build(name: str, controls: Index | None = None) -> dict:
    doc = bundle.load(name)
    nist = doc.info.cls == "nist"
    idx = build_index(doc, controls=name == CONTROL_DOC)
    if name == CONTROL_DOC:
        controls = idx
    requirements = any(a.source == "heading-requirement" for a in idx.all())
    headers = running_headers(doc)
    cjk = is_cjk_doc(doc)
    refs = []
    for b in content_blocks(doc):
        for text, cell in texts_of(doc, b):
            found = find_mentions(text, controls=nist, requirements=requirements)
            zh = [m for m in zh_mentions(text) if not any(m.start < x.end and x.start < m.end for x in found)] if cjk else []
            for m in found + zh:
                if is_definition(m, b, text, cell) or in_running_header(m, text, headers):
                    continue
                res = zh_resolve(m, b, idx) if m in zh else None
                refs.append(entry(b, cell, text, m, res or resolve(m, b, idx, controls)))
    fn_refs, fn_missing = footnote_refs(doc, idx)
    refs += fn_refs
    refs.sort(key=lambda e: (doc.by_ref[e["src_ref"]].order, e.get("cell") or [0, 0]))
    counts = Counter((e["kind"], e["status"]) for e in refs)
    return {
        "doc": name,
        "producer": PRODUCER,
        "inputs": inputs(name),
        "stats": {"refs": len(refs), "by_kind_status": {f"{k}/{s}": n for (k, s), n in sorted(counts.items())},
                  "footnotes_without_marker": fn_missing,
                  "anchors": {k: sum(len(v) for v in by.values()) for k, by in idx.anchors.items()}},
        "refs": refs,
    }


def inputs(name: str | None = None) -> dict:
    """Which upstream deliveries were used (only those with a MANIFEST count), with per-file fingerprints."""
    a, b = DATA / "A" / "MANIFEST.json", DATA / "B" / "MANIFEST.json"
    out = {"headings": "C2 temporary heading index + PDF outline chapters",
           "pages": (f"task B pagemap v{json.loads(b.read_text()).get('version', '?')} "
                     f"({json.loads(b.read_text()).get('updated', '?')})") if b.exists() else "awaiting task B",
           "number_key": NUMBER_KEY_SOURCE}
    if a.exists():
        man = json.loads(a.read_text())
        out["headings"] = (f"task A structure {man.get('version')} (MANIFEST {man.get('written_at')}) first; "
                           "C2 temporary heading index where A has no node")
    for task, suffix in (("A", "structure"), ("B", "pagemap")):
        f = DATA / task / f"{name}.{suffix}.json"
        if name and f.exists():
            out[f"{task}_file"] = {"sha1": hashlib.sha1(f.read_bytes()).hexdigest()[:12],
                                   "mtime": time.strftime("%H:%M:%S", time.gmtime(f.stat().st_mtime))}
    return out


def write_json(path: Path, obj: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1))
    os.replace(tmp, path)


def out_dir(args: list[str], default: Path = WIP) -> Path:
    """`--out=DIR` if given, else the work-in-progress directory (never the delivered one by default)."""
    return Path(next((a.split("=", 1)[1] for a in args if a.startswith("--out=")), default))


def main(args: list[str]) -> None:
    out = out_dir(args)
    out.mkdir(parents=True, exist_ok=True)
    names = [a for a in args if not a.startswith("--")] or [d.name for d in registry.docs()]
    controls = build_index(bundle.load(CONTROL_DOC), controls=True)
    files = []
    for name in names:
        t0 = time.time()
        side = build(name, controls)
        path = out / f"{name}.xrefs.json"
        write_json(path, side)
        files.append(path.name)
        print(f"{name}: {side['stats']['refs']} refs in {time.time() - t0:.1f}s", flush=True)
    if len(names) == len(registry.docs()):
        write_json(out / "MANIFEST.json", {
            "task": "C2", "producer": PRODUCER, "version": "v5", "docs": len(files),
            "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "files": sorted(files),
            "inputs": inputs()})


if __name__ == "__main__":
    main(sys.argv[1:])
