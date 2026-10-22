"""Quality report written when a document is ingested (A4): DATA/ING/<name>.qc.json.

What it measures, per document and per page:
- text coverage: share of the PDF text layer's words Docling extracted (task D1's census), and
  after D1's patch, which puts lost words back into what the reader sees;
- tables (and those task D2 flagged), pictures, headings; structure (A) and printed page labels (B);
- OCR: page kinds from pageplan, pages OCR'd in full, words from OCR, mean OCR confidence;
- suspect pages, each with reasons: no_text, text_missing (D1 flag partial or misattributed, or
  coverage < MIN_COVERAGE, and D1 did not patch that page), low_ocr_confidence, undecodable_layer
  (garbled: text came from OCR), rotated.

render() gives the short text form for people and for the reading model.
"""

from __future__ import annotations

import json
import os
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium

from tglw.common import bundle, paths, registry

from . import pageplan

PRODUCER = "tglw.ingest.qc v1"
MIN_COVERAGE = 0.8
MIN_LAYER_WORDS = 30
LOW_OCR_CONF = 0.75
MIN_OCR_WORDS = 20
POOR_COVERAGE = 0.85
POOR_SHARE = 0.10
TABLE_FLAGS = ("garbled", "column_shift", "header_misflag", "scanned")  # task D2's flags that mean "may be misread"


def path(name: str) -> Path:
    return paths.DATA / "ING" / f"{name}.qc.json"


def _json(p: Path) -> dict[str, Any]:
    return json.loads(p.read_text()) if p.exists() else {}


def _words(text: str) -> int:
    return len(text.split())


def _plan(name: str, manifest: dict[str, Any]) -> dict[int, str]:
    """Page kind per page: from the bundle manifest when the ingest command made it, else from the PDF."""
    saved = manifest.get("page_plan")
    if saved:
        kinds = {p: kind for kind, pages in saved.items() if isinstance(pages, list) and kind != "cjk" for p in pages}
        return {p: kinds.get(p, "text") for p in range(1, manifest["page_count"] + 1)}
    return {p.page: p.kind for p in pageplan.plan(str(registry.get(name).pdf_path))}


def _rotations(name: str) -> dict[int, int]:
    pdf = pdfium.PdfDocument(str(registry.get(name).pdf_path))
    try:
        return {i + 1: pdf[i].get_rotation() for i in range(len(pdf)) if pdf[i].get_rotation()}
    finally:
        pdf.close()


def _ocr_pages(pages: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    out = {}
    for pg in pages:
        ocr = [c for c in pg["textline_cells"] if c["from_ocr"]]
        words = sum(_words(c["text"]) for c in ocr)
        if words:
            conf = sum(c["confidence"] * _words(c["text"]) for c in ocr) / words
            out[pg["page_no"]] = {"ocr_words": words, "ocr_conf": round(conf, 3)}
    return out


def build(name: str) -> dict[str, Any]:
    d = registry.get(name)
    manifest = _json(d.bundle_dir / "manifest.json")
    doc = bundle.load(name)
    kinds = _plan(name, manifest)
    ocr = _ocr_pages(bundle.load_pages(name))
    loss = _json(paths.DATA / "D1" / f"{name}.textloss.json")
    d1 = {p["pdf_page"]: p for p in loss.get("pages", [])}
    patch = _json(paths.DATA / "D1" / f"{name}.patch.json")
    patched = {r["pdf_page"] for key in ("table_residue", "replacements") for r in patch.get(key, [])}
    tables = _json(paths.DATA / "D2" / f"{name}.tables.json").get("tables", [])
    rotated = _rotations(name)
    full_page = set((manifest.get("ocr") or {}).get("full_page_pages") or [])
    rows, suspects = [], []
    for page in range(1, doc.page_count + 1):
        words = sum(_words(t) for _, t in doc.page_segments(page))
        p1 = d1.get(page, {})
        kind = kinds.get(page, "text")
        row = {"page": page, "kind": kind, "words": words, "layer_words": p1.get("layer_words"),
               "coverage": p1.get("coverage"), **ocr.get(page, {})}
        if page in full_page:
            row["full_page_ocr"] = True
        if page in patched:
            row["patched"] = True
        rows.append(row)
        why = []
        if words == 0 and kind not in ("sparse", "blank"):
            why.append("no_text")
        elif page not in patched and (p1.get("flag") in ("partial", "misattributed") or (
                (p1.get("layer_words") or 0) >= MIN_LAYER_WORDS and (p1.get("coverage") or 1.0) < MIN_COVERAGE)):
            why.append(f"text_missing (coverage {p1.get('coverage')}, D1 {p1.get('flag')})")
        if row.get("ocr_words", 0) >= MIN_OCR_WORDS and row["ocr_conf"] < LOW_OCR_CONF:
            why.append(f"low_ocr_confidence ({row['ocr_conf']})")
        if kind == "garbled":
            why.append("undecodable_layer (text from OCR)")
        if page in rotated:
            why.append(f"rotated {rotated[page]}")
        if why:
            suspects.append({"page": page, "why": why})
    summary = loss.get("summary", {})
    ocr_words = sum(r.get("ocr_words", 0) for r in rows)
    conf = (sum(r["ocr_conf"] * r["ocr_words"] for r in rows if r.get("ocr_words")) / ocr_words) if ocr_words else None
    counts = {k: sum(1 for v in kinds.values() if v == k) for k in ("text", "scan_text", "scan", "blank", "garbled", "sparse")}
    flagged = [t["ref"] for t in tables if any(f.get("kind") in TABLE_FLAGS for f in t.get("flags", []))]
    side_a = _json(paths.DATA / "A" / f"{name}.structure.json").get("verdict", {})
    side_b = _json(paths.DATA / "B" / f"{name}.pagemap.json").get("verdict", {})
    coverage = summary.get("coverage")
    share = len(suspects) / max(1, doc.page_count)
    status = ("poor" if (coverage is not None and coverage < POOR_COVERAGE) or share > POOR_SHARE
              or (conf is not None and conf < LOW_OCR_CONF) else "check" if suspects else "ok")
    return {
        "doc": name, "producer": PRODUCER, "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pages": doc.page_count,
        "docling": {"version": (manifest.get("versions") or {}).get("docling"),
                    "ocr_engine": (manifest.get("options") or {}).get("ocr_engine"),
                    "converted_at": manifest.get("converted_at")},
        "text": {"coverage": coverage, "coverage_patched": ((patch.get("summary") or {}).get("after") or {}).get("coverage"),
                 "layer_words": summary.get("layer_words"),
                 "docling_words": summary.get("docling_words"), "words": sum(r["words"] for r in rows)},
        "ocr": {"page_kinds": counts, "full_page_pages": sorted(full_page), "pages_with_ocr_text": len(ocr),
                "ocr_words": ocr_words, "mean_confidence": None if conf is None else round(conf, 3)},
        "tables": {"count": len(doc.raw.get("tables", [])), "flagged": flagged},
        "pictures": len(doc.raw.get("pictures", [])),
        "headings": len(doc.headings()),
        "structure": side_a.get("status", "none"),
        "printed_labels": side_b.get("status", "none"),
        "verdict": {"status": status, "suspect_pages": [s["page"] for s in suspects]},
        "suspects": suspects,
        "page_rows": rows,
    }


def write(name: str) -> dict[str, Any]:
    report = build(name)
    out = path(name)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    os.replace(tmp, out)
    man = out.parent / "MANIFEST.json"
    files = sorted(p.name for p in out.parent.glob("*.qc.json"))
    tmp = man.with_name(f".{man.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps({"task": "ING", "version": 1, "producers": [PRODUCER], "docs": len(files),
                               "files": files, "written_at": report["written_at"]}, indent=1))
    os.replace(tmp, man)
    return report


def load(name: str) -> dict[str, Any] | None:
    p = path(name)
    try:
        return _load(p, p.stat().st_mtime_ns)
    except FileNotFoundError:
        return None


@lru_cache(maxsize=64)
def _load(p: Path, mtime_ns: int) -> dict[str, Any]:
    return json.loads(p.read_text())


def render(report: dict[str, Any], limit: int = 12) -> str:
    """A few lines: what was extracted, how, and which pages to distrust."""
    t, o, v = report["text"], report["ocr"], report["verdict"]
    cov = (f"{t['coverage'] * 100:.1f}% of the PDF text layer's words" + (
        f" ({t['coverage_patched'] * 100:.1f}% after restoring lost text)"
        if t.get("coverage_patched") not in (None, t["coverage"]) else "")) if t["coverage"] is not None else "no PDF text layer"
    kinds = ", ".join(f"{n} {k}" for k, n in o["page_kinds"].items() if n and k != "text")
    lines = [
        f"quality {v['status']} · {report['doc']} · {report['pages']} p · "
        + (f"Docling {report['docling']['version']}" if report["docling"]["version"] else "prebuilt Docling bundle")
        + (f", OCR {report['docling']['ocr_engine']}" if report["docling"]["ocr_engine"] else ""),
        f"text: {cov}; tables {report['tables']['count']}"
        + (f" ({len(report['tables']['flagged'])} flagged)" if report["tables"]["flagged"] else "")
        + f"; pictures {report['pictures']}; headings {report['headings']}",
        f"pages: {kinds or 'all born-digital'}"
        + (f"; OCR text on {o['pages_with_ocr_text']} p, mean confidence {o['mean_confidence']}" if o["ocr_words"] else ""),
        f"structure {report['structure']}; printed page labels {report['printed_labels']}",
    ]
    if report["suspects"]:
        lines.append(f"suspect pages ({len(report['suspects'])}): " + "; ".join(
            f"p{s['page']} {', '.join(s['why'])}" for s in report["suspects"][:limit])
            + (f"; +{len(report['suspects']) - limit} more" if len(report["suspects"]) > limit else ""))
    return "\n".join(lines)


def summary(name: str) -> str | None:
    """One phrase for list_documents, or None (no report: e.g. the research corpus)."""
    report = load(name)
    if report is None:
        return None
    v = report["verdict"]
    return f"quality {v['status']}" + (f", {len(v['suspect_pages'])} suspect p" if v["suspect_pages"] else "")


def page_note(name: str, page: int) -> str | None:
    """Why ingest QC distrusts this page, for a note next to the page's text; None when it does not."""
    report = load(name)
    hit = next((s for s in (report or {}).get("suspects", []) if s["page"] == page), None)
    return f"ingest QC flags p{page}: {'; '.join(hit['why'])}" if hit else None
