"""Build data/B/<doc>.toc.json from the TOC reader and the page map (run after tglw.pagemap.build)."""

from __future__ import annotations

import json
import sys
from typing import Any

from tglw.common import bundle, pdfmeta, registry

from .build import OUT_DIR, sidecar_path, write_json
from .toc import Entry, TocReader, letter_numbers, level_cues, match_blocks, outline_check, resolve_pages

PRODUCER = "tglw.pagemap.toc v2"


def _entry(e: Entry) -> dict[str, Any]:
    return {
        "title": e.title,
        "number": e.number,
        "printed_page": e.printed_page,
        "pdf_page": e.pdf_page,
        "level_hint": e.level_hint,
        "level_cues": e.level_cues,
        "match": e.match,
        "kind": e.kind,
        "layout": e.layout,
        "source": e.source,
        "toc_page": e.toc_page,
        "indent": round(e.x, 1),
        "printed_raw": e.page_token,
        "pdf_page_source": e.pdf_page_source,
        "repaired": e.repaired,
        "has_leader": e.has_leader,
        "toc_refs": e.refs,
    }


def build_toc(name: str) -> dict[str, Any]:
    doc = bundle.load(name)
    pagemap = json.loads(sidecar_path(name, ".pagemap.json").read_text())
    reader = TocReader(doc, pagemap["label_to_pdf"])
    pages = reader.toc_pages()
    reader.close()
    entries = [e for p in sorted(pages) for e in pages[p]]
    resolve_pages(entries, pagemap["label_to_pdf"])
    letter_numbers(entries)
    level_cues(entries)
    match_blocks(entries, doc, set(pages))
    check = outline_check(entries, pdfmeta.outline(name))
    sections = [e for e in entries if e.kind == "section"]
    note = (f"{len(entries)} entries ({len(sections)} sections) on {len(pages)} pages; "
            f"{sum(e.printed_page is not None for e in entries)} with printed page, "
            f"{sum(e.pdf_page is not None for e in entries)} with PDF page, "
            f"{sum(e.match is not None for e in entries)} matched to a block")
    if check:
        note += f"; bookmarks: same page {check['same_page']}/{check['matched_bookmarks']}"
    return {
        "doc": name,
        "producer": PRODUCER,
        "toc_pages": sorted(pages),
        "entries": [_entry(e) for e in entries],
        "outline_check": check,
        "verdict": {"status": "found" if entries else "none", "note": note if entries else "no TOC pages found"},
    }


def main(names: list[str]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name in names or [d.name for d in registry.docs()]:
        side = build_toc(name)
        write_json(OUT_DIR / f"{name}.toc.json", side)
        c = side["outline_check"] or {}
        es = side["entries"]
        print(f"{name:58s} {side['verdict']['status']:5s} pages={side['toc_pages'][:6]} n={len(es):3d} "
              f"pdf={sum(e['pdf_page'] is not None for e in es):3d} match={sum(e['match'] is not None for e in es):3d} "
              f"bm={c.get('same_page', '-')}/{c.get('matched_bookmarks', '-')}")


if __name__ == "__main__":
    main(sys.argv[1:])
