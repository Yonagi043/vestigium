"""Build data/B/<doc>.pagemap.json: printed page label per PDF page, and the reverse lookup."""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from tglw.common import bundle, pdfmeta, registry
from tglw.common.paths import DATA

from .candidates import docling_candidates, textlayer_candidates
from .context import divisions, matches, run_contexts
from .labels import normalize_label, parse
from .solve import Assigned, assign

PRODUCER = "tglw.pagemap v3"
DELIVERED_DIR = DATA / "B"
WIP_DIR = DELIVERED_DIR / "wip"
OUT_DIR = WIP_DIR
PAGELABELS_TRUST = 0.9
PAGELABELS_MIN_COMPARED = 3
RELIABLE_SHARE = 0.8
PRINTED = ("footer", "header", "textlayer")


def compare_pagelabels(assigned: list[Assigned], labels: list[str] | None) -> dict[str, Any] | None:
    """Agreement of the printed labels with /PageLabels, on pages where both exist."""
    if labels is None:
        return None
    pairs = [(a.num.label, normalize_label(pl) or pl) for a, pl in zip(assigned, labels) if a.num]
    same = sum(x == y for x, y in pairs)
    same_ci = sum(x.lower() == y.lower() for x, y in pairs)
    mismatches = [{"pdf_page": a.pdf_page, "printed": a.num.label, "pagelabel": pl}
                  for a, pl in zip(assigned, labels) if a.num and a.num.label != (normalize_label(pl) or pl)]
    return {
        "compared": len(pairs),
        "agree": same,
        "agree_rate": round(same / len(pairs), 4) if pairs else None,
        "agree_rate_ignore_case": round(same_ci / len(pairs), 4) if pairs else None,
        "mismatch_examples": mismatches[:10],
    }


def _fill_from_pagelabels(assigned: list[Assigned], labels: list[str]) -> int:
    filled = 0
    for a, pl in zip(assigned, labels):
        num = parse(pl)
        if a.num is None and num is not None:
            a.num, a.source, a.confidence = num, "pagelabels", "low"
            filled += 1
    return filled


def _runs_summary(assigned: list[Assigned]) -> list[dict[str, Any]]:
    by_run: dict[int, list[Assigned]] = defaultdict(list)
    for a in assigned:
        if a.run is not None:
            by_run[a.run].append(a)
    out = []
    for i, pages in sorted(by_run.items()):
        out.append({
            "run": i,
            "pdf_first": pages[0].pdf_page,
            "pdf_last": pages[-1].pdf_page,
            "label_first": pages[0].num.label,
            "label_last": pages[-1].num.label,
            "scheme": pages[0].num.scheme,
            "evidence_pages": sum(a.source in PRINTED for a in pages),
        })
    return out


def _verdict(assigned: list[Assigned], runs: list[dict[str, Any]], dupes: dict[str, list[int]],
             pl_check: dict[str, Any] | None, pl_used: int) -> dict[str, Any]:
    n = len(assigned)
    labeled = [a for a in assigned if a.num]
    printed = [a for a in labeled if a.source in PRINTED]
    share = len(labeled) / n if n else 0.0
    if not labeled:
        status = "none"
    elif share >= RELIABLE_SHARE and len(printed) >= len(labeled) / 2:
        status = "reliable"
    else:
        status = "partial"
    notes = [f"{len(printed)} pages printed, {len(labeled) - len(printed)} inferred/filled, {n - len(labeled)} unlabeled"]
    if runs:
        notes.append("runs: " + "; ".join(
            f"p{r['pdf_first']}-{r['pdf_last']}={r['label_first']}..{r['label_last']}" for r in runs[:8])
            + (f"; +{len(runs) - 8} more" if len(runs) > 8 else ""))
    if dupes:
        notes.append(f"{len(dupes)} labels map to several pages")
    if pl_check is not None:
        rate = pl_check["agree_rate"]
        notes.append(f"/PageLabels agree on {pl_check['agree']}/{pl_check['compared']} printed pages"
                     + (f"; used to fill {pl_used} pages" if pl_used else "; not used"))
        if not printed:
            notes.append("no printed page numbers found; /PageLabels are not visible on the page")
        elif rate is not None and rate < PAGELABELS_TRUST:
            notes.append("/PageLabels contradict the printed numbers")
    return {
        "status": status,
        "labeled_share": round(share, 4),
        "printed_share": round(len(printed) / n, 4) if n else 0.0,
        "schemes": sorted({a.num.scheme for a in labeled}),
        "note": "; ".join(notes),
    }


def build(name: str) -> dict[str, Any]:
    info = registry.get(name)
    doc = bundle.load(name)
    cands = docling_candidates(doc) + textlayer_candidates(str(info.pdf_path))
    assigned, _ = assign(doc.page_count, cands)
    labels = pdfmeta.page_labels(name)
    pl_check = compare_pagelabels(assigned, labels)
    pl_used = 0
    if (labels is not None and pl_check["compared"] >= PAGELABELS_MIN_COMPARED
            and pl_check["agree_rate"] >= PAGELABELS_TRUST):
        pl_used = _fill_from_pagelabels(assigned, labels)
    label_to_pdf: dict[str, list[int]] = defaultdict(list)
    pages = []
    for a in assigned:
        row: dict[str, Any] = {
            "pdf_page": a.pdf_page,
            "label": a.num.label if a.num else None,
            "source": a.source,
            "confidence": a.confidence,
        }
        if a.raw is not None:
            row["raw"] = a.raw
        if a.ref is not None:
            row["ref"] = a.ref
        if a.run is not None:
            row["run"] = a.run
        if labels is not None:
            row["pagelabel"] = labels[a.pdf_page - 1]
        pages.append(row)
        if a.num:
            label_to_pdf[a.num.label].append(a.pdf_page)
    dupes = {k: v for k, v in label_to_pdf.items() if len(v) > 1}
    runs = _runs_summary(assigned)
    for r, ctx in zip(runs, run_contexts(doc, pages, runs)):
        r["run_context"] = ctx
    run_of = {p["pdf_page"]: p.get("run") for p in pages}
    ambiguous_contexts = {
        k: [{"pdf_page": p, "run": run_of[p], "context": runs[run_of[p]]["run_context"]["text"] if run_of[p] is not None else None}
            for p in v]
        for k, v in dupes.items()}
    return {
        "doc": name,
        "producer": PRODUCER,
        "page_count": doc.page_count,
        "pages": pages,
        "label_to_pdf": dict(label_to_pdf),
        "ambiguous_labels": dupes,
        "ambiguous_contexts": ambiguous_contexts,
        "runs": runs,
        "divisions": [{"pdf_page": m.page, "text": m.text, "ref": m.ref, "major": m.major} for m in divisions(doc)],
        "pagelabels_check": pl_check,
        "verdict": _verdict(assigned, runs, dupes, pl_check, pl_used),
    }


def pdf_pages(sidecar: dict[str, Any], text: str, context: str | None = None) -> list[int]:
    """PDF pages printed with this label ("Page 45", "A–3", "iii"); exact case first. With a context
    ("Exhibit 10.21", "1800-11B"), only pages whose run context fits it."""
    key = normalize_label(text) or text.strip()
    table = sidecar["label_to_pdf"]
    found = table.get(key) or sorted(p for k, v in table.items() if k.lower() == key.lower() for p in v)
    if context is None:
        return found
    runs = sidecar["runs"]
    run_of = {p["pdf_page"]: p.get("run") for p in sidecar["pages"]}
    return [p for p in found if run_of[p] is not None and matches(runs[run_of[p]].get("run_context"), context)]


def sidecar_path(name: str, suffix: str) -> Path:
    """The work-in-progress sidecar when there is one, else the delivered one."""
    wip = WIP_DIR / f"{name}{suffix}"
    return wip if wip.exists() else DELIVERED_DIR / f"{name}{suffix}"


def write_json(path: Path, obj: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False))
    os.replace(tmp, path)


def main(names: list[str]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name in names or [d.name for d in registry.docs()]:
        side = build(name)
        write_json(OUT_DIR / f"{name}.pagemap.json", side)
        v = side["verdict"]
        print(f"{name:58s} {v['status']:8s} labeled={v['labeled_share']:.2f} printed={v['printed_share']:.2f} "
              f"runs={len(side['runs']):3d} dup={len(side['ambiguous_labels']):3d}")


if __name__ == "__main__":
    main(sys.argv[1:])
