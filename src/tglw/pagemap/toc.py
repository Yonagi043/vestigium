"""Find table-of-contents pages and parse their entries into data/B/<doc>.toc.json.

Text pieces of a page are put back into lines: cells of TOC tables by their table row, other Docling
body blocks by vertical position. Each line is read as [number] title [leader] page; layouts that print
the page first are read block by block.
The printed page is mapped to a PDF page through the page map; the title is matched to a Docling block.
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import pdfplumber
from rapidfuzz import fuzz

from tglw.common.bundle import Block, Doc
from tglw.common.text import norm

from tglw.zh.headings import parse as zh_heading

from .labels import normalize_label, parse

TOC_TABLE_LABELS = ("document_index", "table")
MIN_ENTRIES = 3
LEADER_PAGE_MIN = 5
MATCH_MIN = 60
GLOBAL_MATCH_MIN = 90
OUTLINE_MATCH_MIN = 85
INDENT_GAP = 6.0
TEXTLAYER_FALLBACK = True
TEXT_EDGE = 0.06
PAGE_COLUMN_SLACK = 25.0
DIGIT_GAP = 6.0

_LEADER = re.compile(r"(?:\s*[.·•…_‥]\s*){3,}")
_TOC_TITLE = re.compile(r"^(?:table\s+of\s+)?contents?$|^目\s*[录錄]$", re.I)
_LIST_TITLE = re.compile(r"^(?:list\s+of\s+)?(figures|tables|illustrations|exhibits|charts)$", re.I)
_SKIP = re.compile(r"^(?:page|pages|section|chapter|item|title|section\s+page|chapter\s+page|page\s+no\.?"
                   r"|[页頁]\s*[次码碼]|[页頁])$", re.I)
_KEYWORD = r"(?:part|chapter|section|appendix|annex|attachment|item|note|schedule|volume|exhibit|figure|table)"
_NUMBER = re.compile(
    rf"^(?P<num>{_KEYWORD}\s+[0-9A-Z][\w.\-]*|(?:[A-Z]\.)?\d+(?:\.\d+)*\.?|[A-Z]\.(?:\d+(?:\.\d+)*)?|[IVX]+\.)"
    r"(?=[\s:]|$)[.:\-–]?\s*", re.I)
_WORD_NUM = re.compile(rf"^(?P<num>{_KEYWORD}\s+(?:one|two|three|four|five|six|seven|eight|nine|ten))\b\s*", re.I)
_ZH_NUMBER_START = set("第附一二三四五六七八九十（(")
_WORDY = re.compile(r"[A-Za-z]{3}|[\u4e00-\u9fff]{2}")
_PAGE_TOKEN = re.compile(r"^-?[A-Za-z0-9]+(?:-[A-Za-z0-9]+)?$")
_SPLIT_PREFIX = re.compile(r"\s([A-Z]{1,3}|\d{1,2})$")
FRONT_SHARE = 0.2
MONOTONIC_MIN = 0.7
_DIVISIONS = ("part", "chapter", "section", "appendix", "annex", "volume")
_KEYWORD_HEADS = _DIVISIONS + ("attachment", "item", "note", "schedule", "exhibit", "figure", "table")


@dataclass(slots=True)
class Atom:
    text: str
    l: float
    b: float
    r: float
    t: float
    ref: str
    row: int | None = None


@dataclass(slots=True)
class Line:
    page: int
    atoms: list[Atom]

    @property
    def text(self) -> str:
        return " ".join(a.text for a in self.atoms).strip()

    @property
    def x(self) -> float:
        return self.atoms[0].l

    @property
    def refs(self) -> set[str]:
        return {a.ref for a in self.atoms}


@dataclass(slots=True)
class Entry:
    title: str
    number: str | None
    page_token: str | None
    toc_page: int
    x: float
    kind: str
    has_leader: bool
    refs: list[str]
    printed_page: str | None = None
    repaired: bool = False
    pdf_page: int | None = None
    pdf_page_source: str | None = None
    level_hint: int | None = None
    level_cues: list[str] = field(default_factory=list)
    match: dict[str, Any] | None = None
    layout: str = "page_last"
    source: str = "docling"

    @property
    def full_title(self) -> str:
        return f"{self.number} {self.title}" if self.number else self.title


def split_number(text: str, alone: bool = False) -> tuple[str | None, str]:
    """("2.2.4", "Title") from "2.2.4 Title"; also "Item 7A", "Appendix I", "CHAPTER ONE".
    A bare number stays text unless `alone` (a number on its own line before the title)."""
    m = _WORD_NUM.match(text) or _NUMBER.match(text)
    if not m:
        z = zh_heading(text) if text[:1] in _ZH_NUMBER_START else None
        return (z.raw, z.title) if z and (z.title or alone) else (None, text)
    rest = text[m.end():].strip()
    return (m.group("num").rstrip("."), rest) if rest or alone else (None, text)


def read_line(text: str) -> tuple[str, str | None, bool]:
    """(body, trailing page token, has dot leader)."""
    has_leader = bool(_LEADER.search(text))
    text = _LEADER.sub(" ", text).strip()
    parts = text.rsplit(None, 1)
    if (len(parts) == 2 and _PAGE_TOKEN.match(parts[1]) and parse(parts[1]) is not None
            and parts[0].lower() not in _KEYWORD_HEADS):
        body, tok = parts[0].strip(), parts[1]
        m = _SPLIT_PREFIX.search(body)
        if tok.startswith("-") and m:
            body, tok = body[:m.start()].strip(), m.group(1) + tok
        return body, tok, has_leader
    return text, None, has_leader


class TocReader:
    def __init__(self, doc: Doc, label_to_pdf: dict[str, list[int]]):
        self.doc = doc
        self.labels = set(label_to_pdf)
        self.first_pdf = {k: v[0] for k, v in label_to_pdf.items()}
        self.keys = page_keys(label_to_pdf)
        self.by_page: dict[int, list[Block]] = defaultdict(list)
        for b in doc.blocks:
            if b.page is not None and len(b.pages) == 1 and b.layer == "body":
                self.by_page[b.page].append(b)
        self._lines: dict[int, list[Line]] = {}
        self._pdf: Any = None

    def atoms(self, page: int) -> list[Atom]:
        height = self.doc.raw["pages"][str(page)]["size"]["height"]
        cells: list[Atom] = []
        tables: list[tuple[float, float, float, float]] = []
        for b in self.by_page.get(page, []):
            if b.kind == "table" and b.label in TOC_TABLE_LABELS and b.bbox:
                found = False
                for c in self.doc.item(b.ref)["data"].get("table_cells", []):
                    text = (c.get("text") or "").replace("\n", " ").strip()
                    bb = c.get("bbox")
                    if text and bb:
                        cells.append(Atom(text, bb["l"], height - bb["b"], bb["r"], height - bb["t"], b.ref,
                                          c.get("start_row_offset_idx")))
                        found = True
                if found:
                    tables.append(b.bbox)
        texts = []
        for b in self.by_page.get(page, []):
            if b.kind == "text" and b.text and b.bbox:
                l, t, r, bot = b.bbox
                cx, cy = (l + r) / 2, (t + bot) / 2
                if not any(tl <= cx <= tr and tb <= cy <= tt for tl, tt, tr, tb in tables):
                    texts.append(Atom(b.text, l, bot, r, t, b.ref))
        return texts + cells

    def lines(self, page: int) -> list[Line]:
        """Table cells form lines by their table row; other pieces by vertical position, where a wrapped
        (tall) piece sits on the line of its last row."""
        if page in self._lines:
            return self._lines[page]
        atoms = self.atoms(page)
        groups: list[tuple[float, list[Atom]]] = []
        rows: dict[tuple[str, int], list[Atom]] = defaultdict(list)
        for a in atoms:
            if a.row is not None:
                rows[(a.ref, a.row)].append(a)
        loose = [a for a in atoms if a.row is None]
        if loose:
            unit = statistics.median(a.t - a.b for a in loose) or 10.0

            def y(a: Atom) -> float:
                return a.b + min(a.t - a.b, unit) / 2

            for a in sorted(loose, key=lambda a: -y(a)):
                if groups and abs(groups[-1][0] - y(a)) <= unit * 0.6:
                    groups[-1][1].append(a)
                else:
                    groups.append((y(a), [a]))
        groups += [(max(a.t for a in g), g) for g in rows.values()]
        out = [Line(page, sorted(g, key=lambda a: a.l)) for _, g in sorted(groups, key=lambda x: -x[0])]
        self._lines[page] = out
        return out

    def text_lines(self, page: int) -> list[Line]:
        """Lines of the PDF text layer (pdfplumber words), for pages where Docling's TOC table is broken."""
        if self._pdf is None:
            self._pdf = pdfplumber.open(str(self.doc.info.pdf_path))
        pg = self._pdf.pages[page - 1]
        band = pg.height * TEXT_EDGE
        words = [w for w in pg.extract_words() if w.get("upright", True) and band < w["top"] and w["bottom"] < pg.height - band]
        if not words:
            return []
        unit = statistics.median(w["bottom"] - w["top"] for w in words) or 10.0
        groups: list[tuple[float, list[Atom]]] = []
        for w in sorted(words, key=lambda w: (w["top"] + w["bottom"]) / 2):
            y = (w["top"] + w["bottom"]) / 2
            atom = Atom(w["text"], w["x0"], pg.height - w["bottom"], w["x1"], pg.height - w["top"], "textlayer")
            if groups and abs(groups[-1][0] - y) <= unit * 0.5:
                groups[-1][1].append(atom)
            else:
                groups.append((y, [atom]))
        return [Line(page, _join_digits(sorted(g, key=lambda a: a.l))) for _, g in groups]

    def close(self) -> None:
        if self._pdf is not None:
            self._pdf.close()
            self._pdf = None

    def parse_page(self, page: int) -> list[Entry]:
        """The better of the Docling reading and the text-layer reading: more entries with known pages."""
        best = self._parse_lines(self.lines(page), page, "docling")
        if TEXTLAYER_FALLBACK and self.doc.info.pdf_path.exists():
            alt = self._parse_lines(self.text_lines(page), page, "textlayer")
            if self._score_entries(alt) > self._score_entries(best):
                best = alt
        return best

    def _score_entries(self, entries: list[Entry]) -> int:
        return sum(1 for e in entries if self._known(e.page_token)) if self._ordered(entries) else 0

    def _parse_lines(self, page_lines: list[Line], page: int, source: str) -> list[Entry]:
        entries: list[Entry] = []
        pending: list[Line] = []
        kind = "section"
        right = max((ln.atoms[-1].r for ln in page_lines if read_line(ln.text)[1]), default=0.0)
        for line in page_lines:
            text = line.text
            if not text or _SKIP.match(text) or _TOC_TITLE.match(text):
                pending = []
                continue
            m = _LIST_TITLE.match(text)
            if m:
                kind, pending = m.group(1).lower().rstrip("s"), []
                continue
            body, tok, leader = read_line(text)
            if body and _TOC_TITLE.match(body):
                pending = []
                continue
            aligned = source != "textlayer" or leader or line.atoms[-1].r >= right - PAGE_COLUMN_SLACK
            if tok is None or not _WORDY.search(body) or not (leader or self._known(tok)) or not aligned:
                pending.append(line)
                continue
            number, title = split_number(body)
            if _TOC_TITLE.match(title):
                pending = []
                continue
            x, refs = line.x, set(line.refs)
            if pending and number is None:
                number, head = split_number(" ".join(p.text for p in pending), alone=True)
                title, x = f"{head} {title}".strip(), pending[0].x
                refs |= set().union(*(p.refs for p in pending))
            else:
                entries += _headless(pending, kind)
            entries.append(Entry(title, number, tok, page, x, _kind(number, kind), leader,
                                 sorted(refs - {"textlayer"}), source=source))
            pending = []
        entries = _unique(entries + _headless(pending, kind))
        if source == "textlayer":
            return entries
        leading = self._leading_page_entries(page)
        paged = sum(1 for e in entries if e.page_token)
        return leading if len(leading) >= MIN_ENTRIES and len(leading) > 2 * paged else entries

    def _leading_page_entries(self, page: int) -> list[Entry]:
        """Layouts that print the page first ("82 Greenhouse gas emissions"), often in several columns:
        read single Docling blocks in reading order instead of merged lines."""
        out = []
        for b in self.by_page.get(page, []):
            parts = b.text.split(None, 1) if b.kind == "text" and b.bbox else []
            if (len(parts) == 2 and _PAGE_TOKEN.match(parts[0]) and self._known(parts[0])
                    and _WORDY.search(parts[1]) and read_line(b.text)[1] is None):
                out.append(Entry(parts[1], None, parts[0], page, b.bbox[0], "section", False, [b.ref],
                                 layout="page_first"))
        return out

    def headed(self) -> set[int]:
        return {b.page for bs in self.by_page.values() for b in bs
                if b.kind == "text" and _TOC_TITLE.match(norm(b.text).rstrip(":"))}

    def seeds(self) -> set[int]:
        """Pages with a Contents heading or a Docling index table, and front pages with many dot leaders."""
        index = {b.page for bs in self.by_page.values() for b in bs if b.label == "document_index"}
        front = min(self.doc.page_count, max(3, int(self.doc.page_count * FRONT_SHARE)))
        leaders = {p for p in range(1, front + 1)
                   if sum(1 for ln in self.lines(p) if read_line(ln.text)[2]) >= LEADER_PAGE_MIN}
        return self.headed() | index | leaders

    def _known(self, token: str | None) -> list[str]:
        return [k for k in self.keys(token) if k in self.first_pdf]

    def _ordered(self, entries: list[Entry]) -> bool:
        pages = [min(self.first_pdf[k] for k in ks) for e in entries if (ks := self._known(e.page_token))]
        if len(pages) < MIN_ENTRIES:
            return False
        steps = list(zip(pages, pages[1:]))
        return sum(b >= a for a, b in steps) >= MONOTONIC_MIN * len(steps)

    def toc_pages(self) -> dict[int, list[Entry]]:
        """Entries must mostly run in page order (tables of numbers can look like a TOC). A page under a
        Contents heading needs 2 entries with known pages; other seeds and following pages need 3."""
        seeds, headed = self.seeds(), self.headed()
        pages: dict[int, list[Entry]] = {}
        for p in range(1, self.doc.page_count + 1):
            if p not in seeds and p - 1 not in pages:
                continue
            es = self.parse_page(p)
            known = [e for e in es if self._known(e.page_token)]
            if (p in headed and len(known) == MIN_ENTRIES - 1) or (len(known) >= MIN_ENTRIES - 1 and self._ordered(es)
                                                                   and (p in headed or len(known) >= MIN_ENTRIES)):
                pages[p] = es
        return pages


def _join_digits(atoms: list[Atom]) -> list[Atom]:
    """OCR text layers split numbers ("3 6" for 36); join digit words that nearly touch."""
    out: list[Atom] = []
    for a in atoms:
        prev = out[-1] if out else None
        if prev and prev.text.isdigit() and a.text.isdigit() and a.l - prev.r <= DIGIT_GAP:
            out[-1] = Atom(prev.text + a.text, prev.l, min(prev.b, a.b), a.r, max(prev.t, a.t), prev.ref)
        else:
            out.append(a)
    return out


def _kind(number: str | None, current: str) -> str:
    head = number.split()[0].lower() if number else ""
    return head if head in ("figure", "table", "exhibit") else current


def _unique(entries: list[Entry]) -> list[Entry]:
    """Drop repeats of the same entry (a TOC table and an overlapping text block)."""
    seen: set[tuple[str, str | None, str | None]] = set()
    out = []
    for e in entries:
        key = (e.title, e.number, e.page_token)
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def _headless(pending: list[Line], kind: str) -> list[Entry]:
    """Lines without a page that still open a division, e.g. "Part II" in a 10-K."""
    out = []
    for line in pending:
        number, title = split_number(_LEADER.sub(" ", line.text).strip(), alone=True)
        if number and len(line.text) <= 80 and number.split()[0].lower() in _DIVISIONS:
            out.append(Entry(title, number, None, line.page, line.x, kind, False, sorted(line.refs)))
    return out


def page_keys(label_to_pdf: dict[str, list[int]]):
    """Token -> candidate label keys: the label itself, and for a bare number the same number under each
    prefix the document uses (TOC "6" for footer "2010-6"; OCR "-5" for "1-5")."""
    prefixes = sorted({k.rsplit("-", 1)[0] for k in label_to_pdf if (n := parse(k)) and n.scheme == "prefixed"})

    def keys(token: str | None) -> list[str]:
        k = normalize_label(token) if token else None
        if not k:
            return []
        alts = [f"{p}-{k}" for p in prefixes if k.isdigit() and f"{p}-{k}" in label_to_pdf]
        return [x for x in [k, *alts] if x in label_to_pdf] or [k]

    return keys


def resolve_pages(entries: list[Entry], label_to_pdf: dict[str, list[int]]) -> None:
    """Printed page -> PDF page: among all candidate keys and repeated labels, the first PDF page at or
    after the previous entry's page wins (the first entry starts from the TOC page). A division line
    without a page ("Part IV") takes the page of the entry right after it."""
    prev_pdf = entries[0].toc_page if entries else 0
    keys = page_keys(label_to_pdf)
    for e in entries:
        ks = keys(e.page_token)
        if not ks:
            continue
        options = sorted((pdf, k) for k in ks for pdf in label_to_pdf.get(k, []))
        e.printed_page = ks[0]
        if options:
            after = [o for o in options if o[0] >= prev_pdf]
            e.pdf_page, e.printed_page = after[0] if after else options[-1]
            e.pdf_page_source = "pagemap"
            prev_pdf = e.pdf_page
        e.repaired = e.printed_page != normalize_label(e.page_token)
    for e, nxt in zip(entries, entries[1:]):
        if e.page_token is None and e.pdf_page is None and nxt.pdf_page is not None:
            e.pdf_page, e.pdf_page_source = nxt.pdf_page, "next_entry"


def _depth(number: str | None) -> int | None:
    """2.2.4 -> 3, A.1 -> 2."""
    if number and re.fullmatch(r"(?:[A-Z]\.)?\d+(?:\.\d+)*", number):
        return number.count(".") + 1
    return None


_TIERS = {"volume": 0, "part": 1, "chapter": 2, "appendix": 2, "annex": 2, "section": 3, "attachment": 3,
          "item": 4, "note": 4, "schedule": 4}


def _keyword(number: str | None) -> str | None:
    head = number.split()[0].lower() if number else None
    return head if head in _TIERS else None


_LETTER = re.compile(r"^([A-Z])\s+(?=[A-Z])")


def letter_numbers(entries: list[Entry]) -> None:
    """Unnumbered titles opening with consecutive capital letters ("A Details ...", "B Details ...")
    are lettered appendices; a lone "A Brief History" is left alone."""
    lettered = [(e, m.group(1)) for e in entries if e.number is None and (m := _LETTER.match(e.title))]
    for (e, a), (f, b) in zip(lettered, lettered[1:]):
        if ord(b) == ord(a) + 1:
            for x, letter in ((e, a), (f, b)):
                if x.number is None:
                    x.number, x.title = letter, _LETTER.sub("", x.title, count=1)


def level_cues(entries: list[Entry]) -> None:
    """Record cues only: dotted-number depth, keyword tier (Part above Item), indent rank, all caps."""
    tiers = sorted({_TIERS[k] for e in entries if e.kind == "section" and (k := _keyword(e.number))})
    xs = sorted({round(e.x) for e in entries if e.kind == "section" and e.layout == "page_last"})
    ranks: dict[int, int] = {}
    rank = 0
    for i, x in enumerate(xs):
        if i == 0 or x - xs[i - 1] > INDENT_GAP:
            rank += 1
        ranks[x] = rank
    for e in entries:
        cues = []
        depth = _depth(e.number)
        if depth:
            cues.append("number")
        if e.number and e.number.split()[0].lower() in _KEYWORD_HEADS:
            cues.append("keyword")
        if rank > 1 and e.kind == "section" and e.layout == "page_last":
            cues.append("indent")
        letters = [c for c in e.title if c.isalpha()]
        if letters and all(c.isupper() for c in letters) and len(letters) > 3:
            cues.append("caps")
        e.level_cues = cues
        kw = _keyword(e.number) if e.kind == "section" else None
        tier = tiers.index(_TIERS[kw]) + 1 if kw else None
        e.level_hint = depth or tier or (ranks.get(round(e.x)) if "indent" in cues else None) or 1


def _strip_number(text: str) -> str:
    return split_number(text)[1]


def _score(title: str, block_text: str) -> float:
    """Title similarity; a block carrying a different number ("PART I" for "Part IV") scores 0."""
    (n1, t1), (n2, t2) = split_number(title, alone=True), split_number(block_text, alone=True)
    if n1 and n2 and n1.lower() != n2.lower():
        return 0.0
    a, b = norm(t1 or title), norm(t2 or block_text)
    if not a or not b or len(b) > 2.5 * len(a) + 30:
        return 0.0
    return fuzz.ratio(a, b)


def match_blocks(entries: list[Entry], doc: Doc, toc_pages: set[int]) -> None:
    """Best Docling block for each entry near its PDF page; without a page, a unique strong heading."""
    by_page: dict[int, list[Block]] = defaultdict(list)
    for b in doc.blocks:
        if b.kind == "text" and b.layer == "body" and b.page and b.page not in toc_pages:
            by_page[b.page].append(b)
    headings = [b for bs in by_page.values() for b in bs if b.is_heading]
    for e in entries:
        if e.pdf_page is not None:
            best = None
            for page in (e.pdf_page, e.pdf_page + 1, e.pdf_page - 1, e.pdf_page + 2):
                for b in by_page.get(page, []):
                    s = _score(e.full_title, b.text) + (3 if b.is_heading else 0)
                    if best is None or s > best[0]:
                        best = (s, b)
                if best and best[0] >= 95:
                    break
            if best and best[0] >= MATCH_MIN:
                e.match = {"ref": best[1].ref, "score": round(min(best[0], 100)), "page": best[1].page}
        else:
            scored = sorted(((_score(e.full_title, b.text), b) for b in headings), key=lambda x: -x[0])
            if scored and scored[0][0] >= GLOBAL_MATCH_MIN and (len(scored) == 1 or scored[1][0] < scored[0][0]):
                s, b = scored[0]
                e.match = {"ref": b.ref, "score": round(s), "page": b.page}
                e.pdf_page, e.pdf_page_source = b.page, "match"


def outline_check(entries: list[Entry], outline: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Compare each section entry's PDF page with the bookmark of the same title."""
    if not outline:
        return None
    items = [(norm(o["title"]), o) for o in outline if o.get("page")]
    rows = []
    for e in entries:
        if e.kind != "section" or e.pdf_page is None:
            continue
        key, title = norm(e.full_title), norm(e.title)
        scored = [(max(fuzz.ratio(key, t), fuzz.ratio(title, _strip_number(t))), o) for t, o in items]
        top = max((sc for sc, _ in scored), default=0)
        if top >= OUTLINE_MATCH_MIN:
            same = [o for sc, o in scored if sc == top]
            rows.append((e, min(same, key=lambda o: abs(o["page"] - e.pdf_page)), top))
    exact = sum(1 for e, o, _ in rows if e.pdf_page == o["page"])
    near = sum(1 for e, o, _ in rows if abs(e.pdf_page - o["page"]) <= 1)
    misses = [{"title": e.full_title[:80], "pdf_page": e.pdf_page, "bookmark_page": o["page"],
               "bookmark": o["title"][:80]} for e, o, _ in rows if e.pdf_page != o["page"]]
    with_page = sum(1 for e in entries if e.kind == "section" and e.pdf_page is not None)
    return {
        "entries_with_pdf_page": with_page,
        "matched_bookmarks": len(rows),
        "same_page": exact,
        "within_one_page": near,
        "same_page_rate": round(exact / len(rows), 4) if rows else None,
        "mismatch_examples": misses[:10],
    }
