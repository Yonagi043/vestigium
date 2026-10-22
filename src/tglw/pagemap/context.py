"""Name each run of consecutive numbering (run_context), so a repeated label can be told apart:
"page 3 of Exhibit 10.21", "page 5 of NIST SP 1800-11B".

Two signals: a division marker where the run starts ("Exhibit 10.21" on the run's first page or on the
unnumbered cover page just before it; a lettered "EXHIBIT A" hangs under the last numbered exhibit), and
a running header/footer printed on most pages of the run, preferring one that other runs do not carry.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from tglw.common.bundle import Block, Doc
from tglw.common.text import norm

from .labels import normalize_label

_MARKER = re.compile(
    r"^(?P<kind>exhibit|appendix|annex|attachment|schedule|volume|part|book)\s+"
    r"(?P<id>[0-9]+(?:\.[0-9]+)*[A-Z]?|[IVXLC]+[A-Z]?|[A-Z])\b[.:]?", re.I)
_ATTACHMENTS = ("exhibit", "appendix", "annex", "attachment", "schedule")
MARKER_TOP = 0.3
MAX_COVER_PAGES = 2
RUNNING_SHARE = 0.8
MIN_LETTERS = 4


@dataclass(slots=True)
class Marker:
    page: int
    text: str
    ref: str
    major: bool


def _marker(b: Block, height: float) -> Marker | None:
    if b.kind != "text" or b.bbox is None or b.page is None or len(b.text) > 120:
        return None
    if b.label not in ("page_header", "section_header", "title") and b.bbox[1] < height * (1 - MARKER_TOP):
        return None
    m = _MARKER.match(b.text.strip())
    if not m:
        return None
    ident = m.group("id")
    major = not (len(ident) == 1 and ident.isalpha() and m.group("kind").lower() == "exhibit")
    return Marker(b.page, m.group(0).rstrip(".:").strip(), b.ref, major)


def markers(doc: Doc) -> dict[int, list[Marker]]:
    heights = {int(k): v["size"]["height"] for k, v in doc.raw["pages"].items()}
    out: dict[int, list[Marker]] = defaultdict(list)
    for b in doc.blocks:
        if b.page is not None and (m := _marker(b, heights[b.page])):
            out[b.page].append(m)
    return out


def divisions(doc: Doc) -> list[Marker]:
    """Where each division starts: the first page of every streak of the same marker (running headers
    repeat "APPENDIX I" on every page of the appendix)."""
    out: list[Marker] = []
    last = None
    for page, ms in sorted(markers(doc).items()):
        for m in ms:
            key = norm(m.text)
            if key != last:
                out.append(m)
            last = key
    return out


def strip_label(text: str, label: str | None) -> str:
    """A header/footer string without the page's own number: "Apple Inc. | 2024 Form 10-K | 26" ->
    "Apple Inc. | 2024 Form 10-K"."""
    parts = [p.strip() for p in re.split(r"\s*\|\s*|\s{2,}", text) if p.strip()]
    words = " | ".join(p for p in parts if not label or normalize_label(p) != label).split()
    if words and label and normalize_label(words[-1]) == label:
        words = words[:-1]
    if words and label and normalize_label(words[0]) == label:
        words = words[1:]
    return " ".join(words).strip(" |")


def _running_texts(doc: Doc, page: int, label: str | None) -> set[str]:
    texts = {strip_label(b.text, label) for b in doc.page_blocks(page) if b.layer == "furniture" and b.text}
    return {t for t in texts if sum(c.isalpha() for c in t) >= MIN_LETTERS}


def _window(first: int, labels: dict[int, str | None]) -> list[int]:
    """The run's first page and the unnumbered cover pages right before it."""
    pages = [first]
    for p in range(first - 1, max(0, first - 1 - MAX_COVER_PAGES), -1):
        if labels.get(p) is not None:
            break
        pages.insert(0, p)
    return pages


def _labels_of(run: dict[str, Any], labels: dict[int, str | None]) -> set[str]:
    return {labels[p] for p in range(run["pdf_first"], run["pdf_last"] + 1) if labels[p]}


def run_contexts(doc: Doc, pages: list[dict[str, Any]], runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One context per run: {"text", "heading", "parent", "running", "page", "ref"}.

    text: an attachment marker (Exhibit/Appendix/Annex/...) at the run's start, else a running
    header/footer that the runs repeating this run's labels do not carry, else any marker."""
    marks = markers(doc)
    majors = sorted((m.page, m.ref, m) for ms in marks.values() for m in ms if m.major)
    labels = {p["pdf_page"]: p["label"] for p in pages}
    counts: list[Counter[str]] = []
    for r in runs:
        c: Counter[str] = Counter()
        for p in range(r["pdf_first"], r["pdf_last"] + 1):
            c.update(_running_texts(doc, p, labels[p]))
        counts.append(c)
    whole = sum(counts, Counter())
    common: list[list[tuple[str, int]]] = []
    for r, c in zip(runs, counts):
        n = r["pdf_last"] - r["pdf_first"] + 1
        # ties: a real running header recurs across the document, an OCR scrap does not; then text order
        common.append(sorted(((t, k) for t, k in c.items() if k >= RUNNING_SHARE * n),
                             key=lambda tk: (-tk[1], -whole[tk[0]], tk[0])))
    spread = Counter(t for c in common for t, _ in c)
    everywhere = {t for t, k in spread.items() if len(runs) > 1 and (k == len(runs) or (len(runs) >= 3 and k > len(runs) / 2))}
    run_labels = [_labels_of(r, labels) for r in runs]
    out = []
    for i, r in enumerate(runs):
        found = [m for p in _window(r["pdf_first"], labels) for m in marks.get(p, [])]
        majors_found = [m for m in found if m.major]
        heading = majors_found[-1] if majors_found else (found[-1] if found else None)
        parent = None
        if heading and not heading.major:
            before = [m for page, _, m in majors if page <= heading.page]
            parent = before[-1] if before else None
        partners = {t for j, c in enumerate(common) if j != i and run_labels[j] & run_labels[i] for t, _ in c}
        own = [t for t, _ in common[i] if t not in everywhere]
        running = next((t for t in own if t not in partners), own[0] if own else None)
        marker = (f"{parent.text} / {heading.text}" if parent else heading.text) if heading else None
        attachment = heading is not None and _MARKER.match(heading.text).group("kind").lower() in _ATTACHMENTS
        out.append({
            "text": marker if attachment else (running or marker),
            "heading": heading.text if heading else None,
            "parent": parent.text if parent else None,
            "running": running,
            "page": heading.page if heading else None,
            "ref": heading.ref if heading else None,
        })
    return out


def matches(context: dict[str, Any] | None, query: str) -> bool:
    """Whether a run's context fits a query such as "Exhibit 10.21" or "1800-11B"."""
    if not context:
        return False
    q = norm(query)
    return any(q in norm(context[k] or "") for k in ("text", "heading", "running"))
