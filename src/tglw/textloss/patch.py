"""Repair sidecar <doc>.patch.json for what the text-loss check found fixable without reconverting:
table text Docling left out of its cells, layer text for undecodable fonts, glyph-code junk to
filter, and misplaced OCR duplicates to skip.

python -m tglw.textloss.patch [doc ...] [--jobs N] [--out DIR]   (default DIR data/D1/wip)
python -m tglw.textloss.patch --remaining [--src DIR]             (losses left after the patch; reads only)
"""

from __future__ import annotations

import argparse
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np
import pypdfium2 as pdfium
import regex
from PIL import Image

from tglw.common import bundle, registry
from tglw.common.bundle import Block, Doc
from tglw.common.paths import DATA
from tglw.common.text import count_tokens, render

from .check import MIN_LAYER_WORDS, check_doc, check_page, shingles
from .docside import DocPage, Seg, _cell_bbox, _span_bbox, contains, doc_pages, overlaps
from .layer import BBox, LayerPage, Word, read_layer
from .match import consume
from .tokens import tokens

PRODUCER = "tglw.textloss.patch v2"
OUT = DATA / "D1" / "wip"
RESIDUE_FRAC = 0.5
PLACED = 0.6
GHOST_LOCATED = 0.3
GHOST_COVERED = 0.8
MOJIBAKE = 0.2
MOJIBAKE_MIN = 10
JUNK_MIN_COUNT = 5
GLYPH_CODE = regex.compile(r"(?<![\w-])[A-Za-z]\d{2,5}(?![\w-])")
DARK = 160
BLANK = 0.01


@dataclass(frozen=True, slots=True)
class Line:
    text: str
    bbox: BBox
    ocr: bool


@dataclass(frozen=True, slots=True)
class GridCell:
    row: int
    col: int
    bbox: BBox
    text: str


def _center(b: BBox) -> tuple[float, float]:
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def _union(boxes: Sequence[BBox]) -> BBox:
    return (min(b[0] for b in boxes), max(b[1] for b in boxes),
            max(b[2] for b in boxes), min(b[3] for b in boxes))


def _box(b: BBox) -> list[float]:
    return [round(x, 1) for x in b]


def parse_lines(pp: dict[str, Any]) -> list[Line]:
    return [Line(c.get("text") or "", _cell_bbox(c, pp["height"]), bool(c.get("from_ocr")))
            for c in pp.get("textline_cells") or []]


def grid_cells(item: dict[str, Any], height: float) -> list[GridCell]:
    out = []
    for c in (item.get("data") or {}).get("table_cells", []):
        b = c.get("bbox")
        if not b:
            continue
        if b.get("coord_origin", "TOPLEFT") == "TOPLEFT":
            box = (b["l"], height - b["t"], b["r"], height - b["b"])
        else:
            box = (b["l"], b["t"], b["r"], b["b"])
        out.append(GridCell(c.get("start_row_offset_idx", 0), c.get("start_col_offset_idx", 0), box,
                            (c.get("text") or "").strip()))
    return out


def reading_order(lines: Sequence[Line], tol: float = 3.0) -> list[Line]:
    """Top to bottom by visual row (centres within `tol`), left to right inside a row."""
    rows: list[list[Line]] = []
    for ln in sorted(lines, key=lambda l: -_center(l.bbox)[1]):
        if rows and abs(_center(rows[-1][0].bbox)[1] - _center(ln.bbox)[1]) <= tol:
            rows[-1].append(ln)
        else:
            rows.append([ln])
    return [ln for row in rows for ln in sorted(row, key=lambda l: l.bbox[0])]


def _x_overlap(a: BBox, b: BBox) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) / max(a[2] - a[0], 1.0)


def position(line: Line, cells: Sequence[GridCell]) -> tuple[GridCell | None, str]:
    """The grid cell the line belongs to: the cell holding it, else the nearest cell above in the
    same column, else the nearest below."""
    cx, cy = _center(line.bbox)
    inside = [c for c in cells if contains(c.bbox, cx, cy, pad=1.0)]
    if inside:
        return inside[0], "inside"
    column = [c for c in cells if _x_overlap(line.bbox, c.bbox) >= 0.5]
    above = [c for c in column if c.bbox[3] >= line.bbox[1] - 2]
    if above:
        return min(above, key=lambda c: c.bbox[3]), "below"
    below = [c for c in column if c.bbox[1] <= line.bbox[3] + 2]
    if below:
        return max(below, key=lambda c: c.bbox[1]), "above"
    return None, "none"


def _bigrams(seqs: Sequence[Sequence[str]]) -> set[tuple[str, str]]:
    return {(a, b) for s in seqs for a, b in zip(s, s[1:])}


def residue_lines(lines: Sequence[Line], covered: Sequence[Sequence[str]]) -> list[Line]:
    """Lines under half of whose tokens are found in `covered` (token lists already in Docling)."""
    grams = _bigrams(covered)
    toks = [tokens(ln.text) for ln in lines]
    flat = [t for ts in toks for t in ts]
    first = [False] * len(flat)
    i = 0
    for ts in toks:
        for k in range(len(ts)):
            first[i + k] = (k + 1 < len(ts) and (ts[k], ts[k + 1]) in grams) or \
                (k > 0 and (ts[k - 1], ts[k]) in grams)
        i += len(ts)
    hit = consume(flat, Counter(t for s in covered for t in s), first=first)
    out, i = [], 0
    for ln, ts in zip(lines, toks):
        if ts and sum(hit[i:i + len(ts)]) < RESIDUE_FRAC * len(ts):
            out.append(ln)
        i += len(ts)
    return out


def join_lines(lines: Sequence[Line]) -> str:
    text = ""
    for ln in lines:
        t = ln.text.strip()
        text = text + t if text.endswith("-") or not text else f"{text} {t}"
    return text


def strip_codes(text: str, codes: set[str]) -> str:
    return GLYPH_CODE.sub(lambda m: "" if m.group(0) in codes else m.group(0), text) if codes else text


def has_layer(lp: LayerPage) -> bool:
    return sum(len(tokens(w.text)) for w in lp.words) >= MIN_LAYER_WORDS


def placed_lines(lines: Sequence[Line], lp: LayerPage) -> list[Line]:
    """Parse lines whose words the PDF text layer has at the same place (drops OCR text put at the
    wrong position); all lines when the page has no usable layer."""
    if not has_layer(lp):
        return list(lines)
    out = []
    for ln in lines:
        toks = tokens(ln.text)
        under = Counter(t for w in _words_in(lp, ln.bbox) for t in tokens(w.text))
        if toks and consume(toks, under).count(True) >= RESIDUE_FRAC * len(toks):
            out.append(ln)
    return out


def table_residue(doc: Doc, table: Block, dp: DocPage, pp: dict[str, Any], lp: LayerPage,
                  codes: set[str]) -> list[dict[str, Any]]:
    lines = [replace(ln, text=strip_codes(ln.text, codes)) for ln in parse_lines(pp)
             if contains(table.bbox, *_center(ln.bbox))]
    others = [s.tokens for s in dp.segs if s.ref != table.ref and s.bbox and overlaps(s.bbox, table.bbox)]
    lost = residue_lines(reading_order(placed_lines(lines, lp)), [tuple(tokens(table.text))] + others)
    cells = grid_cells(doc.item(table.ref), pp["height"])
    groups: dict[tuple[Any, ...], list[Line]] = defaultdict(list)
    anchors: dict[tuple[Any, ...], GridCell | None] = {}
    for ln in lost:
        cell, how = position(ln, cells)
        if cell and how == "inside" and _within(ln.text, cell.text):
            continue
        key = (cell.row, cell.col, how) if cell else (None, None, how)
        groups[key].append(ln)
        anchors[key] = cell
    return [_residue_entry(table, key, anchors[key], lns) for key, lns in groups.items()]


def _within(text: str, cell_text: str) -> bool:
    toks = tokens(text)
    return bool(toks) and all(consume(toks, Counter(tokens(cell_text))))


def _residue_entry(table: Block, key: tuple[Any, ...], cell: GridCell | None,
                   lines: Sequence[Line]) -> dict[str, Any]:
    text = join_lines(reading_order(lines))
    return {
        "ref": table.ref,
        "pdf_page": table.page,
        "row": key[0],
        "col": key[1],
        "anchor": key[2],
        "anchor_text": cell.text[:80] if cell else None,
        "text": text,
        "bbox": _box(_union([ln.bbox for ln in lines])),
        "lines": len(lines),
        "words": len(tokens(text)),
        "ocr": any(ln.ocr for ln in lines),
    }


def mojibake(text: str) -> bool:
    """Symbol or private-use characters making up a fifth of the text, at least MOJIBAKE_MIN of them
    (a stray "°" or "♦" in a figure label is not an undecoded font)."""
    chars = [c for c in text if not c.isspace()]
    odd = sum(unicodedata.category(c) in ("So", "Co") for c in chars)
    return odd >= MOJIBAKE_MIN and odd >= MOJIBAKE * len(chars)


def _words_in(lp: LayerPage, box: BBox, pad: float = 2.0) -> list[Word]:
    return [w for w in lp.words if contains(box, *_center(w.bbox), pad=pad)]


def replacements(doc: Doc, page: int, lp: LayerPage) -> list[dict[str, Any]]:
    """Blocks whose font Docling could not decode, with the text layer under the same box."""
    out = []
    for b, text in doc.page_segments(page):
        box = _span_bbox(doc, b.ref, page)
        if b.kind != "text" or not box or not mojibake(text):
            continue
        layer = " ".join(w.text for w in _words_in(lp, box))
        if len(tokens(layer)) >= 3 and not mojibake(layer):
            spans = [(s.start, s.end) for s in b.spans if s.page == page and s.start is not None]
            out.append({"ref": b.ref, "pdf_page": page, "bbox": _box(box),
                        "charspan": [spans[0][0], spans[-1][1]] if spans else None,
                        "text": layer, "original": text[:160]})
    return out


def junk_filters(doc: Doc, layer_vocab: set[str], skip: set[str]) -> dict[str, Any]:
    """Glyph codes (e.g. "H1100" for a dot leader) that recur in Docling but never in the layer."""
    counts: Counter[str] = Counter()
    where: dict[str, list[Block]] = defaultdict(list)
    for b in doc.blocks:
        if b.ref in skip:
            continue
        for code in set(GLYPH_CODE.findall(b.text)):
            where[code].append(b)
        counts.update(GLYPH_CODE.findall(b.text))
    codes = sorted(c for c, n in counts.items() if n >= JUNK_MIN_COUNT and c.lower() not in layer_vocab)
    if not codes:
        return {"codes": [], "items": []}
    pattern = GLYPH_CODE.pattern
    items = {}
    for code in codes:
        for b in where[code]:
            rest = GLYPH_CODE.sub(lambda m: "" if m.group(0) in codes else m.group(0), b.text)
            items[b.ref] = {"ref": b.ref, "pdf_page": b.page, "kind": b.kind,
                            "action": "drop" if not tokens(rest) else "strip"}
    return {"codes": [{"code": c, "count": counts[c]} for c in codes], "pattern": pattern,
            "items": sorted(items.values(), key=lambda x: (x["pdf_page"] or 0, x["ref"]))}


def _located(seg: Seg, lp: LayerPage) -> float:
    """Share of the block's words the layer has under its box, by tokens or, spacing ignored, by
    6-character shingles (the layer sometimes runs words together)."""
    words = _words_in(lp, seg.bbox, pad=3.0)
    by_tokens = consume(seg.tokens, Counter(t for w in words for t in tokens(w.text))).count(True)
    mine = shingles("".join(seg.tokens), 6)
    under = shingles("".join(t for w in words for t in tokens(w.text)), 6)
    return max(by_tokens / len(seg.tokens), len(mine & under) / len(mine) if mine else 0.0)


def ghosts(dp: DocPage, lp: LayerPage, blank: Callable[[BBox], bool]) -> list[dict[str, Any]]:
    """Text blocks Docling put at the wrong place: under the block the layer has other words, or
    the page is blank there; and every word recurs in well-placed blocks on the page. Picture
    children and blocks over image content (OCR of a figure or screenshot) are left alone."""
    if not has_layer(lp):
        return []
    segs = [s for s in dp.segs if s.bbox and len(s.tokens) >= 3 and s.ref.startswith("#/texts")
            and not s.in_picture]
    loc = {s.ref: _located(s, lp) for s in segs}
    placed = [s for s in segs if loc[s.ref] >= PLACED]
    out = []
    for s in segs:
        if loc[s.ref] >= GHOST_LOCATED or not (_words_in(lp, s.bbox, pad=3.0) or blank(s.bbox)):
            continue
        pool = Counter(t for p in placed if p.ref != s.ref for t in p.tokens)
        if consume(s.tokens, pool).count(True) < GHOST_COVERED * len(s.tokens):
            continue
        mine = shingles("".join(s.tokens), 6)
        keep = sorted(placed, key=lambda p: -len(mine & shingles("".join(p.tokens), 6)))[:3]
        out.append({"pdf_page": dp.page, "skip": s.ref, "keep": [p.ref for p in keep],
                    "bbox": _box(s.bbox), "located": round(loc[s.ref], 2), "words": len(s.tokens)})
    return out


def blank_test(pdf: pdfium.PdfDocument, page: int) -> Callable[[BBox], bool]:
    """Whether a box on the page renders (almost) white; the page is rendered on first use."""
    img: list[Image.Image] = []

    def blank(box: BBox) -> bool:
        if not img:
            img.append(pdf[page - 1].render(scale=1.0).to_pil().convert("L"))
        h = img[0].height
        crop = img[0].crop((int(box[0]), int(h - box[1]), int(box[2]) + 1, int(h - box[3]) + 1))
        return int((np.asarray(crop) < DARK).sum()) <= BLANK * max(crop.width * crop.height, 1)
    return blank


def safe_ghosts(dp: DocPage, lp: LayerPage, blank: Callable[[BBox], bool]) -> list[dict[str, Any]]:
    """Ghosts whose removal leaves every layer word of the page covered as before; otherwise none."""
    found = ghosts(dp, lp, blank)
    if not found:
        return []
    gone = {x["skip"] for x in found}
    kept = replace(dp, segs=[s for s in dp.segs if s.ref not in gone])
    if _covered(check_page(lp, kept, set()).record) < _covered(check_page(lp, dp, set()).record):
        return []
    return found


def _covered(rec: dict[str, Any]) -> int:
    return round((rec["coverage"] or 0) * rec["layer_words"])


def _seg_text(doc: Doc, ref: str, page: int) -> str:
    return next((t for b, t in doc.page_segments(page) if b.ref == ref), "")


def build(name: str) -> dict[str, Any]:
    doc = bundle.load(name)
    pps = {pp["page_no"]: pp for pp in bundle.load_pages(name)}
    dps = doc_pages(doc, list(pps.values()))
    layer = {lp.page: lp for lp in read_layer(doc.info.pdf_path)}
    repl = [x for p, lp in layer.items() if p in dps for x in replacements(doc, p, lp)]
    vocab = {t for lp in layer.values() for w in lp.words for t in tokens(w.text)}
    filters = junk_filters(doc, vocab, {x["ref"] for x in repl})
    codes = {c["code"] for c in filters["codes"]}
    residue = [x for b in doc.blocks if b.kind == "table" and b.bbox and b.page in pps and b.page in layer
               for x in table_residue(doc, b, dps[b.page], pps[b.page], layer[b.page], codes)]
    pdf = pdfium.PdfDocument(str(doc.info.pdf_path))
    dups = [x for p, lp in layer.items() if p in dps for x in safe_ghosts(dps[p], lp, blank_test(pdf, p))]
    pdf.close()
    for x in dups:
        x["tokens"] = count_tokens(_seg_text(doc, x["skip"], x["pdf_page"]))
    patch = {"doc": name, "cls": doc.info.cls, "producer": PRODUCER, "table_residue": residue,
             "replacements": repl, "filters": filters, "skip_duplicates": dups}
    patch["summary"] = measure(name, patch, count_tokens(render(doc)))
    return patch


def apply(patch: dict[str, Any], residue: bool = True, repl: bool = True,
          skip: bool = True) -> Any:
    """An `adjust` hook for check_doc that applies the patch to the Docling side."""
    def adjust(dps: dict[int, DocPage]) -> None:
        if repl:
            new = {(x["pdf_page"], x["ref"]): tuple(tokens(x["text"])) for x in patch["replacements"]}
            for p, dp in dps.items():
                dp.segs = [replace(s, tokens=new[(p, s.ref)], garbled=False) if (p, s.ref) in new else s
                           for s in dp.segs]
        if skip:
            gone = {(x["pdf_page"], x["skip"]) for x in patch["skip_duplicates"]}
            for p, dp in dps.items():
                dp.segs = [s for s in dp.segs if (p, s.ref) not in gone]
        if residue:
            for x in patch["table_residue"]:
                dps[x["pdf_page"]].segs.append(Seg(x["ref"], "table_residue", "body", False,
                                                   x["pdf_page"], tuple(x["bbox"]), tuple(tokens(x["text"]))))
    return adjust


def _stats(r: dict[str, Any]) -> dict[str, Any]:
    s = r["summary"]
    return {"coverage": s["coverage"], "layer_words": s["layer_words"],
            "covered": sum(round((p["coverage"] or 0) * p["layer_words"]) for p in r["pages"]),
            "dup": s["dup"], "extra": s["extra"],
            "lost_words_by_kind": s["lost_words_by_kind"],
            "lost_words": sum(p["lost_words"] for p in r["pages"])}


def measure(name: str, patch: dict[str, Any], doc_tokens: int) -> dict[str, Any]:
    before = check_doc(name)
    after = check_doc(name, adjust=apply(patch))
    no_skip = check_doc(name, adjust=apply(patch, skip=False))
    saved = sum(x["tokens"] for x in patch["skip_duplicates"])
    return {
        "before": _stats(before),
        "after": _stats(after),
        "after_without_skip": _stats(no_skip),
        "residue_entries": len(patch["table_residue"]),
        "residue_words": sum(x["words"] for x in patch["table_residue"]),
        "replacements": len(patch["replacements"]),
        "junk_items": len(patch["filters"]["items"]),
        "skip_duplicates": len(patch["skip_duplicates"]),
        "skip_tokens": saved,
        "doc_tokens": doc_tokens,
    }


def remaining(name: str, src: Path = DATA / "D1", min_words: int = 5) -> list[dict[str, Any]]:
    """Losses still listed once the document's patch is applied."""
    import json

    patch = json.loads((src / f"{name}.patch.json").read_text())
    r = check_doc(name, adjust=apply(patch))
    return [x for x in r["losses"] if x["kind"] != "misattributed" and x["words"] >= min_words]


def write_json(path: Path, obj: object) -> None:
    import json

    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1))
    tmp.replace(path)


def _run(name: str, out: Path) -> tuple[str, dict[str, Any]]:
    patch = build(name)
    write_json(out / f"{name}.patch.json", patch)
    return name, patch["summary"]


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument("docs", nargs="*")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--remaining", action="store_true", help="list losses left after the patch in --src")
    ap.add_argument("--src", type=Path, default=DATA / "D1", help="patch.json to read for --remaining")
    return ap


def main() -> None:
    args = parser().parse_args()
    names = args.docs or [d.name for d in registry.docs()]
    if args.remaining:
        with ProcessPoolExecutor(args.jobs) as pool:
            for name, xs in zip(names, pool.map(remaining, names, [args.src] * len(names))):
                for x in xs:
                    print(name, x["pdf_page"], x["kind"], x["words"], x["in_docling_cells"], repr(x["text"][:100]))
        return
    args.out.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(args.jobs) as pool:
        rows = list(pool.map(_run, names, [args.out] * len(names)))
    if not args.docs:
        write_json(args.out / "patch_summary.json", [{"doc": n, **s} for n, s in rows])
    for n, s in rows:
        print(n, s["before"]["coverage"], "->", s["after"]["coverage"], s["residue_entries"],
              s["replacements"], s["junk_items"], s["skip_duplicates"], s["skip_tokens"])


if __name__ == "__main__":
    main()
