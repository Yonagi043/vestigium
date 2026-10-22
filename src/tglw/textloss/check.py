"""Per-document text-loss check: PDF text layer vs. Docling output, page by page."""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Collection, Sequence

import regex

from tglw.common import bundle

from .docside import DocPage, Seg, contains, doc_pages, overlaps
from .layer import BBox, LayerPage, Word, read_layer
from .match import consume
from .tokens import tokens

PRODUCER = "tglw.textloss v1"
MIN_LAYER_WORDS = 5
MIN_PAGE_WORDS = 30
PAGE_LOST = 0.3
MISATTRIBUTED = 0.5
PARTIAL_WORDS = 20
EXTRA_HIGH = 0.3
JUNK = 0.3
LOST_WORD_FRAC = 0.5
GAP = 2
MIN_FRAGMENT_TOKENS = 3
ELSEWHERE = 0.6
SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}
TEXT_KINDS = ("paragraph", "table", "footnote", "formula")
SHORT_TOKEN = 3
FOOTNOTE_MARK = regex.compile(r"^(\d{1,3}|[*†‡§])\D")


@dataclass
class PageResult:
    record: dict[str, Any]
    losses: list[dict[str, Any]]
    extras: list[dict[str, Any]]


def layer_tokens(words: Sequence[Word], bag: Counter[str]) -> tuple[list[str], list[tuple[int, ...]]]:
    """Tokens of the layer words, joining a line-end hyphen split when Docling has the joined word."""
    wt = [tokens(w.text) for w in words]
    toks: list[str] = []
    owners: list[tuple[int, ...]] = []
    skip = False
    for w, word in enumerate(words):
        ts = wt[w][1:] if skip else wt[w]
        skip = False
        nxt = wt[w + 1] if w + 1 < len(words) else []
        if (ts and nxt and word.text.endswith("-") and words[w + 1].line != word.line
                and bag[ts[-1] + nxt[0]] > 0):
            toks += ts[:-1]
            owners += [(w,)] * (len(ts) - 1)
            toks.append(ts[-1] + nxt[0])
            owners.append((w, w + 1))
            skip = True
        else:
            toks += ts
            owners += [(w,)] * len(ts)
    return toks, owners


def word_fracs(n: int, owners: Sequence[tuple[int, ...]], hit: Sequence[bool]) -> list[float | None]:
    tot = [0] * n
    got = [0] * n
    for own, h in zip(owners, hit):
        for w in own:
            tot[w] += 1
            got[w] += h
    return [g / t if t else None for t, g in zip(tot, got)]


def _center(b: BBox) -> tuple[float, float]:
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def _height(b: BBox) -> float:
    return max(b[1] - b[3], 1.0)


def _near(a: Word, b: Word) -> bool:
    if 0 <= b.line - a.line <= 2:
        return True
    return abs(_center(a.bbox)[1] - _center(b.bbox)[1]) <= 3 * max(_height(a.bbox), _height(b.bbox))


def lost_runs(words: Sequence[Word], fracs: Sequence[float | None],
              anchored: Sequence[bool] | None = None) -> list[list[int]]:
    """Groups of lost word indices. Matched words may sit inside a group when at most GAP of them
    are anchored (share a token bigram with the page's Docling text); the rest are chance hits."""
    runs: list[list[int]] = []
    for i, f in enumerate(fracs):
        if f is None or f >= LOST_WORD_FRAC:
            continue
        if runs and _near(words[runs[-1][-1]], words[i]):
            gap = range(runs[-1][-1] + 1, i)
            n_anchored = sum(anchored[j] for j in gap) if anchored is not None else len(gap)
            if n_anchored <= GAP:
                runs[-1].append(i)
                continue
        runs.append([i])
    return runs


def anchored_tokens(toks: Sequence[str], grams: set[tuple[str, ...]]) -> list[bool]:
    out = [False] * len(toks)
    for k in range(len(toks) - 1):
        if (toks[k], toks[k + 1]) in grams:
            out[k] = out[k + 1] = True
    return out


def anchored_words(n: int, toks: Sequence[str], owners: Sequence[tuple[int, ...]],
                   grams: set[tuple[str, ...]]) -> list[bool]:
    out = [False] * n
    for k in range(len(toks) - 1):
        if (toks[k], toks[k + 1]) in grams:
            for own in owners[k:k + 2]:
                for w in own:
                    out[w] = True
    return out


def unmark_gaps(runs: Sequence[list[int]], anchored: Sequence[bool],
                owners: Sequence[tuple[int, ...]], hit: list[bool]) -> None:
    """Matched but unanchored words inside a lost group count as lost."""
    inside = {j for run in runs for j in range(run[0], run[-1] + 1) if not anchored[j]}
    for k, own in enumerate(owners):
        if own[0] in inside:
            hit[k] = False


def shingles(s: str, n: int = 8) -> set[str]:
    return {s[i:i + n] for i in range(max(len(s) - n + 1, 1))} if s else set()


def _found_despaced(s: str, page: str, page_shingles: set[str]) -> bool:
    if len(s) < 8:
        return s in page
    sh = shingles(s)
    return sum(x in page_shingles for x in sh) >= 0.8 * len(sh)


def rescue_spaced(words: Sequence[Word], toks: Sequence[str], owners: Sequence[tuple[int, ...]],
                  hit: list[bool], page: str, anchored: Sequence[bool]) -> None:
    """Mark lost runs found in the page's Docling text once spacing is ignored (e.g. "u s e r")."""
    page_shingles = shingles(page)
    fracs = word_fracs(len(words), owners, hit)
    for run in lost_runs(words, fracs, anchored):
        idx = set(range(run[0], run[-1] + 1))
        ks = [k for k, o in enumerate(owners) if o[0] in idx]
        if ks and _found_despaced("".join(toks[k] for k in ks), page, page_shingles):
            for k in ks:
                hit[k] = True


def line_pieces(words: Sequence[Word], gutter: float = 15.0) -> list[list[int]]:
    """Consecutive words on one line with no column-sized gap between them."""
    pieces: list[list[int]] = []
    for i, w in enumerate(words):
        prev = words[i - 1] if i else None
        if prev and prev.line == w.line and 0 <= w.bbox[0] - prev.bbox[2] < gutter:
            pieces[-1].append(i)
        else:
            pieces.append([i])
    return pieces


def rescue_lines(words: Sequence[Word], toks: Sequence[str], owners: Sequence[tuple[int, ...]],
                 hit: list[bool], page: str) -> None:
    """Mark line pieces holding lost words when the piece, spacing ignored, is in Docling's page text."""
    page_shingles = shingles(page)
    fracs = word_fracs(len(words), owners, hit)
    by_word: dict[int, list[int]] = {}
    for k, own in enumerate(owners):
        by_word.setdefault(own[0], []).append(k)
    for piece in line_pieces(words):
        if not any(fracs[i] is not None and fracs[i] < LOST_WORD_FRAC for i in piece):
            continue
        ks = [k for i in piece for k in by_word.get(i, [])]
        text = "".join(toks[k] for k in ks)
        if len(text) >= 8 and _found_despaced(text, page, page_shingles):
            for k in ks:
                hit[k] = True


def rescue_short(toks: Sequence[str], hit: list[bool], page: str) -> None:
    """Short tokens split or joined differently by Docling ("r v" vs "rv", "4.7M" vs "4.7 M"):
    mark them when a 3-4 token window around them holding a digit, spacing ignored, is in
    Docling's page text. The digit keeps plain prose (which overlaps by chance) out."""
    for k, t in enumerate(toks):
        if hit[k] or len(t) > SHORT_TOKEN:
            continue
        for size in (3, 4):
            for start in range(max(k - size + 1, 0), min(k, len(toks) - size) + 1):
                window = "".join(toks[start:start + size])
                if len(window) >= 5 and any(c.isdigit() for c in window) and window in page:
                    hit[k] = True
                    break
            if hit[k]:
                break


def _union(boxes: Sequence[BBox]) -> BBox:
    return (min(b[0] for b in boxes), max(b[1] for b in boxes),
            max(b[2] for b in boxes), min(b[3] for b in boxes))


def _inside(words: Sequence[Word], boxes: Sequence[tuple[str, BBox]]) -> str | None:
    """Ref of the box holding at least half of `words` (by centre), if any."""
    for ref, box in boxes:
        n = sum(contains(box, *_center(w.bbox)) for w in words)
        if n * 2 >= len(words):
            return ref
    return None


def _kind(words: Sequence[Word], lp: LayerPage, dp: DocPage, med_h: float) -> tuple[str, str | None]:
    formula = _inside(words, dp.formulas)
    if formula:
        return "formula", formula
    pic = _inside(words, dp.pictures)
    if pic:
        return "figure_text", pic
    box = _union([w.bbox for w in words])
    hs = statistics.median(_height(w.bbox) for w in words)
    if box[1] < lp.height * 0.4 and hs < 0.85 * med_h and FOOTNOTE_MARK.match(words[0].text):
        return "footnote", _inside(words, dp.tables)
    tab = _inside(words, dp.tables)
    if tab:
        return "table", tab
    if len(words) <= 15 and (box[3] > lp.height * 0.92 or box[1] < lp.height * 0.08):
        return "margin", None
    if box[1] < lp.height * 0.4 and hs < 0.85 * med_h:
        return "footnote", None
    return "paragraph", None


def _severity(kind: str, n: int) -> str:
    if kind == "page" or (kind in TEXT_KINDS and n >= 30):
        return "high"
    if kind in TEXT_KINDS and n >= 8 or kind == "figure_text" and n >= 40:
        return "medium"
    return "low"


def trigrams(toks: Sequence[str]) -> set[tuple[str, ...]]:
    if len(toks) < 3:
        return {tuple(toks)} if toks else set()
    return {tuple(toks[i:i + 3]) for i in range(len(toks) - 2)}


def _loss(lp: LayerPage, dp: DocPage, words: Sequence[Word], lost: Sequence[Word], toks: Sequence[str],
          kind: str, ref: str | None, doc_grams: set[tuple[str, ...]]) -> dict[str, Any]:
    grams = trigrams(toks)
    elsewhere = sum(g in doc_grams for g in grams) / len(grams) if grams else 0.0
    in_cells = sum(dp.cells[t] > 0 for t in toks) / len(toks) if toks else 0.0
    box = _union([w.bbox for w in lost])
    garbled = [s.ref for s in dp.segs if s.garbled and s.bbox and overlaps(s.bbox, box)]
    status = ("in_orig" if kind == "formula" else "garbled" if garbled
              else "elsewhere" if elsewhere >= ELSEWHERE else "absent")
    return {
        "pdf_page": lp.page,
        "kind": kind,
        "text": " ".join(w.text for w in words)[:600],
        "bbox": [round(x, 1) for x in box],
        "severity": _severity(kind, len(toks)),
        "words": len(toks),
        "status": status,
        "in_docling_cells": round(in_cells, 2),
        "ref": ref,
        "garbled_refs": garbled[:5],
    }


def _reverse(lp: LayerPage, dp: DocPage, layer_toks: Sequence[str]) -> tuple[int, int, list[dict[str, Any]]]:
    """Docling tokens missing from the layer, split into duplicates (the word is on the page) and
    extras (it is not); returns (dup, extra, notable segments)."""
    segs = sorted(dp.segs, key=lambda s: s.ocr)
    flat = [t for s in segs for t in s.tokens]
    hit = consume(flat, Counter(layer_toks))
    vocab = Counter(set(layer_toks))
    layer_shingles = shingles("".join(layer_toks))
    dup = extra = 0
    out: list[dict[str, Any]] = []
    i = 0
    for s in segs:
        miss = [t for t, h in zip(s.tokens, hit[i:i + len(s.tokens)]) if not h]
        i += len(s.tokens)
        is_dup = consume(miss, Counter({t: len(miss) for t in vocab}))
        d = sum(is_dup)
        dup += d
        extra += len(miss) - d
        if len(miss) >= 5 and len(miss) * 2 >= len(s.tokens):
            out.append(_extra(lp, s, miss, d, len(layer_toks), layer_shingles))
    return dup, extra, out


def _extra(lp: LayerPage, s: Seg, miss: Sequence[str], dup: int, n_layer: int,
           layer_shingles: set[str]) -> dict[str, Any]:
    sh = shingles("".join(s.tokens))
    if s.garbled:
        kind = "garbled"
    elif dup >= 0.7 * len(miss):
        kind = "duplicate"
    elif n_layer >= MIN_LAYER_WORDS and sum(x in layer_shingles for x in sh) >= 0.8 * len(sh):
        kind = "spacing"
    elif n_layer < MIN_LAYER_WORDS:
        kind = "no_layer"
    elif s.ocr:
        kind = "ocr"
    else:
        kind = "other"
    return {
        "pdf_page": lp.page,
        "ref": s.ref,
        "label": s.label,
        "kind": kind,
        "text": " ".join(s.tokens)[:300],
        "bbox": [round(x, 1) for x in s.bbox] if s.bbox else None,
        "words": len(miss),
    }


def _flag(rec: dict[str, Any], junk: float) -> str:
    if rec["layer_words"] < MIN_LAYER_WORDS:
        return "no_layer"
    if junk > JUNK:
        return "layer_junk"
    if rec["body_words"] >= MIN_PAGE_WORDS and rec["body_coverage"] < PAGE_LOST:
        return "page_lost"
    if rec["layer_words"] >= MIN_PAGE_WORDS and rec["coverage_first_page"] < MISATTRIBUTED \
            and rec["coverage"] >= 0.8:
        return "misattributed"
    if rec["lost_words"] >= PARTIAL_WORDS:
        return "partial"
    if rec["docling_words"] >= MIN_PAGE_WORDS and rec["extra"] > EXTRA_HIGH:
        return "extra_high"
    return "ok"


def _ratio(a: int, b: int) -> float | None:
    return round(a / b, 3) if b else None


def _page_grams(dp: DocPage) -> set[tuple[str, ...]]:
    return {(a, b) for s in dp.segs for a, b in zip(s.tokens, s.tokens[1:])}


def _despaced(dp: DocPage) -> str:
    return "".join(t for s in dp.segs for t in s.tokens)


def check_page(lp: LayerPage, dp: DocPage, doc_grams: set[tuple[str, ...]]) -> PageResult:
    bag = Counter(t for s in dp.segs for t in s.tokens)
    toks, owners = layer_tokens(lp.words, bag)
    grams = _page_grams(dp)
    hit = consume(toks, bag, first=anchored_tokens(toks, grams))
    anchored = anchored_words(len(lp.words), toks, owners, grams)
    page = _despaced(dp)
    rescue_lines(lp.words, toks, owners, hit, page)
    rescue_spaced(lp.words, toks, owners, hit, page, anchored)
    rescue_short(toks, hit, page)
    unmark_gaps(lost_runs(lp.words, word_fracs(len(lp.words), owners, hit), anchored), anchored, owners, hit)
    first_hit = consume(layer_tokens(lp.words, dp.first)[0], dp.first)
    fracs = word_fracs(len(lp.words), owners, hit)
    in_pic = [any(contains(b, *_center(w.bbox)) for _, b in dp.pictures) for w in lp.words]
    body = [h for h, o in zip(hit, owners) if not in_pic[o[0]]]
    dup, extra, extras = _reverse(lp, dp, toks)
    n_doc = sum(bag.values())
    rec: dict[str, Any] = {
        "pdf_page": lp.page,
        "layer_words": len(toks),
        "docling_words": n_doc,
        "coverage": _ratio(hit.count(True), len(toks)),
        "extra": _ratio(extra, n_doc),
        "dup": _ratio(dup, n_doc),
        "flag": "",
        "coverage_first_page": _ratio(first_hit.count(True), len(toks)),
        "body_words": len(body),
        "body_coverage": _ratio(sum(body), len(body)),
        "figure_words": len(toks) - len(body),
        "marker_words": sum(s.marker_tokens for s in dp.segs),
        "lost_words": 0,
        "junk": round(lp.junk, 3),
    }
    losses = _page_losses(lp, dp, fracs, anchored, toks, owners, hit, doc_grams, rec)
    rec["flag"] = _flag(rec, lp.junk)
    if rec["flag"] == "page_lost":
        losses = [_page_loss(lp, dp, fracs, toks, owners, hit, doc_grams)] + \
            [x for x in losses if x["kind"] == "figure_text"]
    if rec["flag"] == "misattributed":
        losses = [_misattributed(lp, dp, rec)] + losses
    return PageResult(rec, losses, extras)


def _lost_tokens(idx: Sequence[int], toks: Sequence[str], owners: Sequence[tuple[int, ...]],
                 hit: Sequence[bool]) -> list[str]:
    keep = set(idx)
    return [t for t, o, h in zip(toks, owners, hit) if not h and o[0] in keep]


def _page_losses(lp: LayerPage, dp: DocPage, fracs: Sequence[float | None], anchored: Sequence[bool],
                 toks: Sequence[str], owners: Sequence[tuple[int, ...]], hit: Sequence[bool],
                 doc_grams: set[tuple[str, ...]], rec: dict[str, Any]) -> list[dict[str, Any]]:
    if not lp.words:
        return []
    med_h = statistics.median(_height(w.bbox) for w in lp.words)
    out: list[dict[str, Any]] = []
    for run in lost_runs(lp.words, fracs, anchored):
        lost_idx = [i for i in run if fracs[i] is not None and fracs[i] < LOST_WORD_FRAC]
        lost_words = [lp.words[i] for i in lost_idx]
        span = list(lp.words[run[0]:run[-1] + 1])
        kind, ref = _kind(lost_words, lp, dp, med_h)
        lt = _lost_tokens(lost_idx, toks, owners, hit)
        if kind not in ("figure_text", "margin"):
            rec["lost_words"] += len(lt)
        out.append(_loss(lp, dp, span, lost_words, lt, kind, ref, doc_grams))
    return out


def _page_loss(lp: LayerPage, dp: DocPage, fracs: Sequence[float | None], toks: Sequence[str],
               owners: Sequence[tuple[int, ...]], hit: Sequence[bool],
               doc_grams: set[tuple[str, ...]]) -> dict[str, Any]:
    idx = [i for i, f in enumerate(fracs) if f is not None and f < LOST_WORD_FRAC]
    words = [lp.words[i] for i in idx]
    return _loss(lp, dp, words, words, _lost_tokens(idx, toks, owners, hit), "page", None, doc_grams)


def _misattributed(lp: LayerPage, dp: DocPage, rec: dict[str, Any]) -> dict[str, Any]:
    """Page text is in Docling, but inside blocks whose first provenance page is another page."""
    carried = sorted((s for s in dp.segs if s.first_page != lp.page), key=lambda s: -len(s.tokens))
    return {
        "pdf_page": lp.page,
        "kind": "misattributed",
        "text": " ".join(w.text for w in lp.words)[:600],
        "bbox": [round(x, 1) for x in _union([w.bbox for w in lp.words])],
        "severity": "high",
        "words": rec["layer_words"],
        "status": "elsewhere",
        "in_docling_cells": None,
        "ref": carried[0].ref if carried else None,
        "refs": [s.ref for s in carried],
        "first_pages": sorted({s.first_page for s in carried if s.first_page is not None}),
        "coverage_first_page": rec["coverage_first_page"],
    }


def check_doc(name: str, pages: Collection[int] | None = None,
              adjust: Callable[[dict[int, DocPage]], None] | None = None) -> dict[str, Any]:
    """The D1 sidecar for one document; `pages` limits the check to some PDF pages, and `adjust`
    edits the Docling side first (used to measure a patch)."""
    doc = bundle.load(name)
    dps = doc_pages(doc, bundle.load_pages(name))
    if adjust:
        adjust(dps)
    grams = set().union(*(trigrams(s.tokens) for dp in dps.values() for s in dp.segs))
    results = [check_page(lp, dps[lp.page], grams) for lp in read_layer(doc.info.pdf_path, pages)
               if lp.page in dps]
    losses = sorted((x for r in results for x in r.losses if x["words"] >= MIN_FRAGMENT_TOKENS),
                    key=lambda x: (SEVERITY_RANK[x["severity"]], -x["words"]))
    extras = [x for r in results for x in r.extras]
    return {
        "doc": name,
        "cls": doc.info.cls,
        "producer": PRODUCER,
        "pages": [r.record for r in results],
        "losses": losses,
        "extras": [x for x in extras if x["kind"] != "duplicate"],
        "duplicates": [x for x in extras if x["kind"] == "duplicate"],
        "summary": summarize(results, losses),
    }


def summarize(results: Sequence[PageResult], listed: Sequence[dict[str, Any]]) -> dict[str, Any]:
    recs = [r.record for r in results]
    layer = sum(r["layer_words"] for r in recs)
    docling = sum(r["docling_words"] for r in recs)
    covered = sum(round((r["coverage"] or 0) * r["layer_words"]) for r in recs)
    by_kind: Counter[str] = Counter()
    for r in results:
        for x in r.losses:
            if x["kind"] != "misattributed":
                by_kind[x["kind"]] += x["words"]
    extra_kind: Counter[str] = Counter()
    for r in results:
        for x in r.extras:
            extra_kind[x["kind"]] += x["words"]
    return {
        "pages": len(recs),
        "layer_words": layer,
        "docling_words": docling,
        "coverage": _ratio(covered, layer),
        "dup": _ratio(sum(round((r["dup"] or 0) * r["docling_words"]) for r in recs), docling),
        "extra": _ratio(sum(round((r["extra"] or 0) * r["docling_words"]) for r in recs), docling),
        "marker_words": sum(r["marker_words"] for r in recs),
        "flags": dict(Counter(r["flag"] for r in recs)),
        "lost_words_by_kind": dict(by_kind),
        "listed_losses_by_severity": dict(Counter(x["severity"] for x in listed)),
        "extra_segment_words_by_kind": dict(extra_kind),
    }
