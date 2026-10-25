"""One document with its delivered sidecars, indexed for compact views.

Addresses (stable):
  s12        section s12 of task A's structure
  p45        PDF page 45;  p45-47  PDF pages 45..47
  t123 tab4 fig5   block #/texts/123, #/tables/4, #/pictures/5 (Docling refs are accepted too)
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import cached_property, lru_cache
from typing import Any

from tglw.common import bundle
from tglw.common.bundle import Block, Doc
from tglw.common.paths import DATA
from tglw.common.text import count_tokens, norm

from . import sidecars
from .switches import V1

_SHORT = {"texts": "t", "tables": "tab", "pictures": "fig"}
_LONG = {v: k for k, v in _SHORT.items()}
TABLE_WHOLE_TOKENS = 3000  # a table split over pages is read whole up to this size

# Page headers/footers shown as content (see DocView.furniture_content).
FURNITURE_MAX_PAGES = 2
FURNITURE_MIN_WORDS = 4
FURNITURE_MIN_REAL_WORDS = 3  # words of 3+ letters: OCR debris such as "Q 0 a W" is not content
FURNITURE_SIMILAR = 0.8
PRECOMPUTED = True  # M8: furniture_content as computed when F was built (check precompute), if task B is unchanged
PRECOMPUTED_FILE = "precomputed.json"
_WITHDRAWN = re.compile(r"^\W*withdrawn\b", re.I)
MOVE_STUBS = True  # M6.1: blocks printed above their section's heading go back to where they are printed (switchable)
_REAL_WORD = re.compile(r"[^\W\d_]{3,}")
_WORD_OR_NUMBER = re.compile(r"[^\W\d_]+|\d+")
_ADDR = re.compile(r"^(?:(?P<sec>s\d+)|p(?P<p1>\d+)(?:-p?(?P<p2>\d+))?|(?P<kind>tab|fig|t)(?P<idx>\d+)|(?P<ref>#/(?:texts|tables|pictures)/\d+))$")


@dataclass(frozen=True, slots=True)
class Segment:
    """The part of one block that lies on one page."""
    block: Block
    page: int
    text: str


def _masked_words(text: str) -> list[str]:
    return ["#" if w.isdigit() else w for w in _WORD_OR_NUMBER.findall(norm(text))]


def _word_bag(text: str) -> str:
    """Words in sorted order, numbers as '#': running headers repeat with other page numbers and,
    in navigation bars, other word order."""
    return " ".join(sorted(_masked_words(text)))


@dataclass(frozen=True, slots=True)
class _HeadBox:
    """A section heading block with the box of its whole heading (number block and title lines)."""
    block: Block
    bbox: tuple[float, float, float, float]

    @property
    def order(self) -> int:
        return self.block.order

    @property
    def ref(self) -> str:
        return self.block.ref


def _overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    """Horizontal overlap of two boxes over the narrower one: whether they share a column."""
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) / max(1.0, min(a[2] - a[0], b[2] - b[0]))


def short_ref(ref: str) -> str:
    _, kind, idx = ref.split("/")
    return f"{_SHORT[kind]}{idx}"


def long_ref(addr: str) -> str | None:
    m = _ADDR.match(addr.strip())
    if not m:
        return None
    if m.group("ref"):
        return m.group("ref")
    if m.group("kind"):
        return f"#/{_LONG[m.group('kind')]}/{m.group('idx')}"
    return None


def precompute_inputs() -> dict[str, Any]:
    """What the precomputed results depend on besides the Docling bundle and F's code."""
    return {"B": sidecars.manifest("B").get("version") if sidecars.delivered("B") else None}


@lru_cache(maxsize=1)
def _precomputed() -> dict[str, Any]:
    for path in (DATA / "F" / PRECOMPUTED_FILE, DATA / "F" / "wip" / PRECOMPUTED_FILE):  # delivered copy first
        if path.exists():
            return json.loads(path.read_text())
    return {}


class DocView:
    def __init__(self, name: str, drop_duplicates: bool = True):
        self.name = name
        self.doc: Doc = bundle.shared(name)
        self.side = sidecars.load(name)
        self.drop_duplicates = drop_duplicates
        self.by_page: dict[int, list[Block]] = defaultdict(list)
        self.children: dict[str, list[Block]] = defaultdict(list)
        for b in self.doc.blocks:
            for p in b.pages:
                self.by_page[p].append(b)
            if b.in_picture and (pic := self._picture_of(b)):
                self.children[pic].append(b)

    def _picture_of(self, b: Block) -> str | None:
        """Nearest picture ancestor (inner text can sit in groups inside the picture)."""
        ref = b.parent
        while ref and ref.startswith("#/"):
            if ref.startswith("#/pictures/"):
                return ref
            ref = (self.doc.item(ref).get("parent") or {}).get("$ref")
        return None

    # ---- sidecar indices -------------------------------------------------------------------

    @cached_property
    def sections(self) -> dict[str, dict[str, Any]]:
        s = self.side.structure
        return {n["id"]: n for n in s["nodes"]} if s else {}

    @cached_property
    def running_headers(self) -> frozenset[str]:
        """Page headers Docling labelled as headings or one-cell tables (task A v4)."""
        return frozenset((self.side.structure or {}).get("running_headers") or ())

    @cached_property
    def block_section(self) -> dict[str, str | None]:
        s = self.side.structure
        return s["block_section"] if s else {}

    @cached_property
    def section_heading(self) -> dict[str, str]:
        """heading block ref -> section id it opens."""
        return {n["heading_ref"]: sid for sid, n in self.sections.items() if n.get("heading_ref")}

    @cached_property
    def labels(self) -> dict[int, str | None]:
        pm = self.side.pagemap
        return {p["pdf_page"]: p["label"] for p in pm["pages"]} if pm else {}

    @cached_property
    def dropped(self) -> set[str]:
        """Blocks D1's filters drop as junk, on every page."""
        f = (self.side.patch or {}).get("filters") or {}
        return {x["ref"] for x in f.get("items", []) if x["action"] == "drop"}

    @cached_property
    def skipped_pages(self) -> set[tuple[int, str]]:
        """(PDF page, block) parts D1 finds repeated elsewhere (e.g. margin notices), hidden when
        drop_duplicates. D1 skips by page: a block over p39-40 can repeat on p40 only. Uses D1's patch
        sidecar when delivered, else v1 duplicates."""
        if not self.drop_duplicates:
            return set()
        if self.side.patch:
            skip = {(x["pdf_page"], x["skip"]) for x in self.side.patch.get("skip_duplicates", [])}
        else:
            skip = {(d["pdf_page"], d["ref"]) for d in (self.side.textloss or {}).get("duplicates", []) if d.get("ref")}
        return skip | self._f4_skips(skip)

    def _f4_skips(self, skip: set[tuple[int, str]]) -> set[tuple[int, str]]:
        """Task D1 v3 (boxfix): a misplaced copy of a line whose original block is shown on that page (V1
        "dup_blocks"; shown as decided before this rule), and the garbled text-layer blocks of rotated pages
        (V1 "rotated_pages")."""
        fix = self.boxfix if V1["dup_blocks"] or V1["rotated_pages"] else None
        if not fix:
            return set()
        out: set[tuple[int, str]] = set()
        if V1["dup_blocks"]:
            for f in fix.get("fixes", []):
                originals = [self.doc.by_ref.get(r) for r in f.get("duplicate_of") or []]
                if any(o is not None and (f["page"], o.ref) not in skip and self._kind_shown(o) for o in originals):
                    out.add((f["page"], f["ref"]))
        if V1["rotated_pages"]:
            out |= {(p["page"], j["ref"]) for p in (fix.get("rotated_pages") or {}).get("pages", []) for j in p.get("junk", [])}
        return out

    @cached_property
    def table_rows(self) -> frozenset[str]:
        """Rows of a scanned table Docling labelled as headings (task SRC's TR sidecar, read only by its switch; it
        is no sidecar version a return names)."""
        return frozenset((sidecars.extra("TR", ".tablerows.json", self.name) or {}).get("table_rows") or ())

    @cached_property
    def boxfix(self) -> dict[str, Any] | None:
        """Task D1 v3's box fixes, duplicate lines and rotated pages, read when an F4 switch is on."""
        return sidecars.extra("D1", ".boxfix.json", self.name)

    @cached_property
    def box_fixes(self) -> dict[str, tuple[float, float, float, float]]:
        """ref -> the block's true box where Docling misplaced it (V1 "box_fix"; region and preview use it)."""
        if not V1["box_fix"] or not self.boxfix:
            return {}
        return {f["ref"]: tuple(f["bbox"]) for f in self.boxfix.get("fixes", [])}

    @cached_property
    def skipped(self) -> set[str]:
        """Blocks hidden on at least one page (a count, and the pre-M3 block-level rule); to decide
        what is shown use hidden(page, ref) or visible(b, page)."""
        return self.dropped | {ref for _, ref in self.skipped_pages}

    def hidden(self, page: int, ref: str) -> bool:
        return ref in self.dropped or (page, ref) in self.skipped_pages

    @cached_property
    def furniture_content(self) -> set[str]:
        """furniture_live(), read from F's precomputed.json when it was built on the same task B version
        (the similarity test took up to 1.3 s at the first read of a scanned NASA document)."""
        pre = _precomputed()
        if PRECOMPUTED and pre.get("inputs") == precompute_inputs() and self.name in pre.get("furniture_content", {}):
            return set(pre["furniture_content"][self.name])
        return self.furniture_live()

    def furniture_live(self) -> set[str]:
        """Page headers/footers that are content Docling mislabelled (e.g. a table note set as a page
        footer): not the page number task B found; at least FURNITURE_MIN_WORDS words, of them
        FURNITURE_MIN_REAL_WORDS real words; similar text (same words in any order, numbers masked,
        similarity >= FURNITURE_SIMILAR) in at most FURNITURE_MAX_PAGES blocks, since running headers
        and footers repeat (counted by block: several printed pages can share one PDF page); and not
        starting with such a repeated header (OCR can glue a body line onto the running header)."""
        pm = self.side.pagemap or {}
        numbers = {p["ref"] for p in pm.get("pages", []) if p.get("ref")}
        labels = {p["pdf_page"]: norm(str(p["label"])) for p in pm.get("pages", []) if p.get("label")}
        cands = [b for b in self.doc.blocks
                 if b.layer != "body" and not b.in_picture and b.ref not in numbers and norm(b.text) != labels.get(b.page)
                 and len(b.text.split()) >= FURNITURE_MIN_WORDS and len(_REAL_WORD.findall(b.text)) >= FURNITURE_MIN_REAL_WORDS]
        if not cands:
            return set()
        bags = [(b.ref, _word_bag(b.text)) for b in self.doc.blocks if b.text.strip()]
        exact: dict[str, set[str]] = defaultdict(set)
        for ref, bag in bags:
            exact[bag].add(ref)
        furniture = [_masked_words(b.text) for b in self.doc.blocks if b.layer != "body" and b.text.strip()]
        return {b.ref for b in cands
                if not self._repeated(b, exact, bags) and not self._starts_with_repeated(b, furniture)}

    def _repeated(self, b: Block, exact: dict[str, set[str]], bags: list[tuple[str, str]]) -> bool:
        bag = _word_bag(b.text)
        similar = set(exact[bag])
        m = SequenceMatcher(None, autojunk=False)
        m.set_seq2(bag)
        for ref, other in bags:
            if len(similar) > FURNITURE_MAX_PAGES:
                return True
            if ref in similar:
                continue
            m.set_seq1(other)
            if m.real_quick_ratio() >= FURNITURE_SIMILAR and m.quick_ratio() >= FURNITURE_SIMILAR \
                    and m.ratio() >= FURNITURE_SIMILAR:
                similar.add(ref)
        return len(similar) > FURNITURE_MAX_PAGES

    @staticmethod
    def _starts_with_repeated(b: Block, furniture: list[list[str]]) -> bool:
        words = _masked_words(b.text)
        hits = 0
        for other in furniture:
            if FURNITURE_MIN_REAL_WORDS <= len(other) < len(words) and \
                    SequenceMatcher(None, " ".join(words[:len(other)]), " ".join(other), autojunk=False).ratio() >= FURNITURE_SIMILAR:
                hits += 1
                if hits > FURNITURE_MAX_PAGES:
                    return True
        return False

    @cached_property
    def _moves(self) -> dict[str, tuple[str, float]]:
        """Text blocks printed above the heading of the section they were read into (same page, same
        column): Docling read them after that heading -- 800-53r5's withdrawal stubs and "Discussion:
        None." lines of consecutive enhancements, e.g. CM-5(2)-(4) on p129, SI-3(6)-(10) on p363, SI-4(8)
        on p366. Each goes to the section of the nearest heading printed above it in its column (else the
        section open before that heading) and is placed after that section's last block printed above
        it. Only between control-numbered sections (A's number_kind "control"). ref -> (section id, reading order)."""
        out: dict[str, tuple[str, float]] = {}
        if not MOVE_STUBS:
            return out
        heads: dict[int, list[tuple[Block, str]]] = defaultdict(list)
        for ref, sid in self.section_heading.items():
            h = self.doc.by_ref.get(ref)
            if h is not None and h.bbox and h.page and self.sections[sid].get("kind") != "root":
                heads[h.page].append((_HeadBox(h, self._heading_box(sid, h)), sid))
        for page, hs in heads.items():
            for b in self.by_page.get(page, []):
                if b.page != page or b.kind != "text" or b.layer != "body" or b.in_picture or not b.bbox \
                        or b.ref in self.section_heading:
                    continue
                own = next((h for h, sid in hs if sid == self.block_section.get(b.ref)), None)
                if own is None or own.order > b.order or own.bbox[1] > b.bbox[3] + 1 or _overlap(own.bbox, b.bbox) < 0.3:
                    continue  # read after its heading and printed below it (or elsewhere): as it should be
                above = [(h, sid) for h, sid in hs if h.bbox[3] >= b.bbox[1] - 1 and _overlap(h.bbox, b.bbox) >= 0.3]
                if above:
                    head, sid = min(above, key=lambda x: x[0].bbox[3] - b.bbox[1])
                else:
                    prev = self.doc.blocks[own.order - 1] if own.order else None
                    sid = self.block_section.get(prev.ref) if prev is not None else None
                    head = self.doc.by_ref.get(self.sections[sid].get("heading_ref") or "") if sid in self.sections else None
                if not sid or sid == self.block_section.get(b.ref) or not all(
                        self.sections.get(x or "", {}).get("number_kind") == "control" for x in (sid, self.block_section.get(b.ref))):
                    continue  # only between numbered controls (800-53-style catalogues); elsewhere such blocks are
                    # chart labels, screenshot text and table headers, whose section is better left alone
                before = [x.order for x in self.by_page.get(page, []) if x.page == page and x.bbox and x.ref != b.ref
                          and self.block_section.get(x.ref) == sid and x.bbox[3] >= b.bbox[1] - 1]
                anchor = max(before + ([head.order] if head is not None else []), default=own.order - 1)
                out[b.ref] = (sid, anchor + 0.5 + (1000 - b.bbox[1]) / 1e6)  # several moved under one anchor: top first
        return out

    def _heading_box(self, sid: str, h: Block) -> tuple[float, float, float, float]:
        """The heading's box with its title blocks on the same page (A v3 starts an enhancement at a
        bare "(5)" block, 11 pt wide, and lists the title line beside it in title_refs)."""
        l, t, r, bot = h.bbox
        for ref in self.sections[sid].get("title_refs") or []:
            x = self.doc.by_ref.get(ref)
            if x is not None and x.bbox and x.page == h.page:
                l, t, r, bot = min(l, x.bbox[0]), max(t, x.bbox[1]), max(r, x.bbox[2]), min(bot, x.bbox[3])
        return l, t, r, bot

    @cached_property
    def moved(self) -> dict[str, str]:
        """Blocks given back to their section: printed above the heading they were read under (see
        _moves), or inside a numbered enhancement's range but filed under its parent control (A's block
        map and ranges disagree: 800-53r5 CA-7(1)'s text showed as "CA-7 (cont.)"). ref -> section id."""
        out: dict[str, str] = {}
        if MOVE_STUBS:
            for sid, n in self.sections.items():
                if n.get("number_kind") != "control" or not str(n.get("number") or "").startswith("("):
                    continue
                for b in self.doc.blocks[n["start_order"]:n["end_order"]]:
                    if b.ref != n.get("heading_ref") and self.block_section.get(b.ref) == n.get("parent"):
                        out[b.ref] = sid
        out.update({ref: sid for ref, (sid, _) in self._moves.items()})
        return out

    def _within(self, sid: str, top: str) -> bool:
        """Whether section sid is top or lies under it."""
        while sid:
            if sid == top:
                return True
            sid = self.sections.get(sid, {}).get("parent")
        return False

    def order_of(self, b: Block) -> float:
        """Reading order, with a moved block placed where it is printed in its section."""
        m = self._moves.get(b.ref)
        return m[1] if m else b.order

    def place(self, seg: Segment) -> tuple[int, int, float]:
        """Reading position: page, then page headers shown as content first and footers last."""
        b = seg.block
        slot = 1 if b.layer == "body" else 0 if b.label == "page_header" else 2
        return seg.page, slot, self.order_of(b)

    @cached_property
    def losses(self) -> dict[str | int, list[dict[str, Any]]]:
        """Text-layer words missing from Docling, by block: D1 patch table_residue, else D1 v1 losses."""
        out: dict[str | int, list[dict[str, Any]]] = defaultdict(list)
        if self.side.patch:
            for x in self.side.patch.get("table_residue", []):
                out[x["ref"]].append(x)
            return out
        for x in (self.side.textloss or {}).get("losses", []):
            out[x.get("ref") or x["pdf_page"]].append(x)
        return out

    @cached_property
    def replacements(self) -> dict[tuple[str, int], str]:
        """D1 text-layer text for garbled block pages."""
        return {(x["ref"], x["pdf_page"]): x["text"] for x in (self.side.patch or {}).get("replacements", [])}

    @cached_property
    def strip_codes(self) -> tuple[set[str], re.Pattern[str] | None]:
        """Blocks whose junk glyph codes (e.g. H1100) D1 says to strip, and a pattern for exactly those codes."""
        f = (self.side.patch or {}).get("filters", {})
        refs = {x["ref"] for x in f.get("items", []) if x["action"] == "strip"}
        codes = [c["code"] for c in f.get("codes", [])]
        pattern = re.compile(r"(?<![\w-])(?:" + "|".join(map(re.escape, codes)) + r")(?![\w-])") if codes else None
        return refs, pattern

    def clean(self, ref: str, text: str) -> str:
        refs, pattern = self.strip_codes
        return self.display(" ".join(pattern.sub(" ", text).split()) if pattern and ref in refs else text)

    @cached_property
    def chinese(self) -> bool:
        """Whether TGLW_ZH_DISPLAY turns task ZH's Chinese display on for this document."""
        from tglw import zh
        return zh.display_on(self.name)

    def display(self, text: str) -> str:
        """Text as shown: for a Chinese document (see chinese) without the spaces extraction puts between
        Chinese characters ("中 醫 藥" -> "中醫藥"); else unchanged."""
        if not self.chinese:
            return text
        from tglw.zh import normalize_display
        return normalize_display(text)

    @cached_property
    def tables(self) -> dict[str, dict[str, Any]]:
        return {t["ref"]: t for t in (self.side.tables or {}).get("tables", [])}

    @cached_property
    def loose_tables(self) -> tuple[dict[tuple[int, str], dict[str, Any]], set[tuple[int, str]]]:
        """Tables Docling did not recognise (task D2 v4): (page, first block in reading order) -> entry, and the
        (page, block) figures a rebuilt one shows in its own place."""
        first: dict[tuple[int, str], dict[str, Any]] = {}
        hidden: set[tuple[int, str]] = set()
        for e in (self.side.tables or {}).get("loose_tables", []):
            refs = [r for r in e["refs"] if r in self.doc.by_ref]
            if refs:
                first[(e["page"], min(refs, key=lambda r: self.order_of(self.doc.by_ref[r])))] = e
                hidden |= {(e["page"], r) for r in refs} if e["restored"] else set()
        return first, hidden

    @cached_property
    def xrefs(self) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in (self.side.xrefs or {}).get("refs", []):
            if r["status"] == "resolved" and r["targets"]:
                out[r["src_ref"]].append(r)
        return out

    # ---- pages and labels ------------------------------------------------------------------

    def page_tag(self, page: int) -> str:
        label = self.labels.get(page)
        return f"p{page}" if label in (None, str(page)) else f"p{page} (printed {label})"

    def page_range_tag(self, first: int, last: int) -> str:
        pdf = f"p{first}" if first == last else f"p{first}-{last}"
        a, b = self.labels.get(first), self.labels.get(last)
        if a in (None, str(first)) and b in (None, str(last)):
            return pdf
        printed = a if first == last or a == b else f"{a or '?'}–{b or '?'}"
        return f"{pdf} (printed {printed})"

    # ---- segments --------------------------------------------------------------------------

    def visible(self, b: Block, page: int | None = None) -> bool:
        """Body text (or a header/footer that is content) shown on `page`, or (no page) on at least
        one of its pages; with V1["running_headers"], not a page header Docling took for body text."""
        if not self._kind_shown(b):
            return False
        pages = [page] if page is not None else b.pages
        return not pages or any((p, b.ref) not in self.skipped_pages for p in pages)

    def _kind_shown(self, b: Block) -> bool:
        if b.in_picture or b.ref in self.dropped or (b.layer != "body" and b.ref not in self.furniture_content):
            return False
        return not (V1["running_headers"] and b.ref in self.running_headers)

    def block_segments(self, b: Block) -> list[Segment]:
        """The block's text per page, with D1's replacements and glyph-code stripping applied; every
        page part, hidden or not (filter with visible(b, seg.page))."""
        if b.kind != "text" or len(b.pages) <= 1:
            if b.page is None:
                return []
            text = self.replacements.get((b.ref, b.page), b.text)
            return [Segment(b, b.page, self.clean(b.ref, text))]
        raw = self.doc.item(b.ref).get("text") or ""
        out = []
        for p in b.pages:
            parts = [raw[s.start:s.end] for s in b.spans if s.page == p and s.start is not None]
            text = self.replacements.get((b.ref, p)) or " ".join(x.replace("\n", " ").strip() for x in parts).strip()
            if text:
                out.append(Segment(b, p, self.clean(b.ref, text)))
        return out

    def page_segments(self, page: int) -> list[Segment]:
        """Same cut as Doc.page_segments, from the page index (hidden parts included)."""
        return [s for b in self.by_page.get(page, []) for s in self.block_segments(b) if s.page == page]

    def resolve(self, addr: str) -> list[Segment]:
        """Segments an address covers; ValueError for an unknown address."""
        m = _ADDR.match(addr.strip())
        if not m:
            raise ValueError(f"bad address {addr!r}: use s12, p45, p45-47, t123, tab4, fig5")
        if m.group("sec"):
            n = self.sections.get(m.group("sec"))
            if n is None:
                raise ValueError(f"no section {addr!r}")
            sid = m.group("sec")
            blocks = [b for b in self.doc.blocks[n["start_order"]:n["end_order"]] if self._within(self.moved.get(b.ref, sid), sid)]
            have = {b.ref for b in blocks}
            blocks = sorted(blocks + [self.doc.by_ref[r] for r, to in self.moved.items() if r not in have and self._within(to, sid)],
                            key=self.order_of)
            if not blocks and n.get("heading_ref") in self.doc.by_ref:
                blocks = [self.doc.by_ref[n["heading_ref"]]]
            return [s for b in blocks for s in self.block_segments(b)]
        if m.group("p1"):
            first = int(m.group("p1"))
            last = int(m.group("p2") or first)
            if not 1 <= first <= last <= self.doc.page_count:
                raise ValueError(f"page out of range {addr!r} (1-{self.doc.page_count})")
            return [s for p in range(first, last + 1) for s in self.page_segments(p)]
        ref = long_ref(addr)
        if ref not in self.doc.by_ref:
            raise ValueError(f"no block {addr!r}")
        parts = self.table_parts(ref)
        if len(parts) > 1 and sum(count_tokens(self.doc.by_ref[r].text) for r in parts) > TABLE_WHOLE_TOKENS:
            parts = [ref]  # a long table (e.g. a glossary over many pages): this part, which names its neighbours
        return [s for r in parts for s in self.block_segments(self.doc.by_ref[r])]

    def table_parts(self, ref: str) -> list[str]:
        """A table split over pages, as task D2 links its parts (continues / continued_by), in order;
        any other block alone. Reading a part reads the whole table up to TABLE_WHOLE_TOKENS."""
        if ref not in self.tables:
            return [ref]
        head, seen = ref, {ref}
        while (prev := self.tables.get(head, {}).get("continues")) and prev in self.doc.by_ref and prev not in seen:
            head = prev
            seen.add(head)
        parts = [head]
        while (nxt := self.tables.get(parts[-1], {}).get("continued_by")) and nxt in self.doc.by_ref and nxt not in parts:
            parts.append(nxt)
        return parts

    # ---- sections --------------------------------------------------------------------------

    def section_of(self, b: Block) -> str | None:
        return self.moved.get(b.ref) or self.block_section.get(b.ref)

    def section_pages(self, sid: str) -> tuple[int, int] | None:
        """First and last PDF page of a section (from its blocks when A gives none)."""
        n = self.sections[sid]
        if n.get("pages"):
            return n["pages"][0], n["pages"][1]
        pages = [p for b in self.doc.blocks[n["start_order"]:n["end_order"]] for p in b.pages]
        return (min(pages), max(pages)) if pages else None

    def section_label(self, sid: str) -> str:
        n = self.sections[sid]
        title = " ".join((n.get("title") or "").split())
        num = " ".join((n.get("number") or "").split())
        return self.display(title if not num or title.startswith(num) else f"{num} {title}")

    def section_path(self, sid: str, width: int = 40) -> str:
        parts = []
        while sid and self.sections.get(sid, {}).get("kind") != "root":
            label = self.section_label(sid)
            parts.append(label if len(label) <= width else label[: width - 1] + "…")
            sid = self.sections[sid].get("parent")
        return " > ".join(reversed(parts))
