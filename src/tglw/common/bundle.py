"""Read a cached Docling bundle as blocks in body reading order.

Traversal matches the earlier T1 text renderer: depth-first over
`body`, children first, then an item's captions and footnotes. Groups are walked but are not
blocks. Items unreachable from `body` are appended at the end (listed in `Doc.orphans`).
Page headers/footers stay in place with `layer == "furniture"`.

Formulas: 31 formula items have empty `text`; `Block.text` falls back to the raw `orig`.

List items: Docling strips the enumeration ("(1)", "[3]", "a.") from `text`; it is kept in
`Block.marker` (and in the raw item's `orig`).

Coordinates: `Block.bbox` and `Span.bbox` are (l, t, r, b) in PDF points, bottom-left origin.
Table cell bboxes in the raw item and the cells in pages.json use a top-left origin.
"""

from __future__ import annotations

import json
import os
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, TypeVar

from .registry import DocInfo, get

T = TypeVar("T")
CONTENT_KINDS = ("texts", "tables", "pictures")
HEADING_LABELS = ("title", "section_header")


@dataclass(frozen=True, slots=True)
class Span:
    """One provenance entry: where part of a block sits on one page.

    `start`/`end` index the raw item text (`Doc.item(ref)["text"]`), end exclusive; None for
    tables and pictures.
    """

    page: int
    bbox: tuple[float, float, float, float] | None
    start: int | None
    end: int | None


@dataclass(frozen=True, slots=True)
class Block:
    ref: str
    kind: str
    label: str
    text: str
    page: int | None
    pages: tuple[int, ...]
    bbox: tuple[float, float, float, float] | None
    order: int
    parent: str | None
    layer: str
    spans: tuple[Span, ...]
    in_picture: bool
    marker: str | None

    @property
    def is_heading(self) -> bool:
        return self.label in HEADING_LABELS


@dataclass
class Doc:
    info: DocInfo
    raw: dict[str, Any]
    page_count: int
    blocks: list[Block]
    by_ref: dict[str, Block]
    orphans: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.info.name

    def item(self, ref: str) -> dict[str, Any]:
        return resolve(self.raw, ref)

    def headings(self) -> list[Block]:
        return [b for b in self.blocks if b.is_heading]

    def body_blocks(self) -> list[Block]:
        return [b for b in self.blocks if b.layer == "body"]

    def page_blocks(self, page: int) -> list[Block]:
        """Every block touching `page`; a multi-page block appears on each of its pages, whole."""
        return [b for b in self.blocks if page in b.pages]

    def page_segments(self, page: int) -> list[tuple[Block, str]]:
        """The text each block contributes to `page`, in reading order.

        Multi-page text blocks are cut by their provenance charspans, so each character lands
        on exactly one page. Tables and pictures have no charspans; they count once, on their
        first page.
        """
        out: list[tuple[Block, str]] = []
        for b in self.blocks:
            if page not in b.pages:
                continue
            if b.kind != "text" or len(b.pages) == 1:
                if b.page == page:
                    out.append((b, b.text))
                continue
            raw = self.item(b.ref).get("text") or ""
            parts = [raw[s.start:s.end] for s in b.spans if s.page == page and s.start is not None]
            text = " ".join(p.replace("\n", " ").strip() for p in parts).strip()
            if text:
                out.append((b, text))
        return out


def resolve(raw: dict[str, Any], ref: str) -> dict[str, Any]:
    _, kind, idx = ref.split("/")
    return raw[kind][int(idx)]


def table_grid(item: dict[str, Any]) -> list[list[str]]:
    """Rows of cell texts; a spanning cell's text sits in its top-left slot only."""
    data = item.get("data") or {}
    nr, nc = data.get("num_rows", 0), data.get("num_cols", 0)
    grid = [[""] * nc for _ in range(nr)]
    for c in data.get("table_cells", []):
        r0, c0 = c.get("start_row_offset_idx", 0), c.get("start_col_offset_idx", 0)
        if r0 < nr and c0 < nc:
            grid[r0][c0] = (c.get("text") or "").replace("\n", " ").strip()
    return grid


def _text(kind: str, item: dict[str, Any]) -> str:
    if kind == "texts":
        text = item.get("text") or ""
        if not text.strip() and item.get("label") == "formula":
            text = item.get("orig") or ""
        return text.replace("\n", " ").strip()
    if kind == "tables":
        return "\n".join(" | ".join(row) for row in table_grid(item))
    return ""


def _bbox(p: dict[str, Any]) -> tuple[float, float, float, float] | None:
    b = p.get("bbox")
    return (b["l"], b["t"], b["r"], b["b"]) if b else None


def _span(kind: str, p: dict[str, Any]) -> Span:
    cs = p.get("charspan") if kind == "texts" else None
    return Span(page=p["page_no"], bbox=_bbox(p), start=cs[0] if cs else None, end=cs[1] if cs else None)


def _block(ref: str, item: dict[str, Any], order: int, in_picture: bool) -> Block:
    kind = ref.split("/")[1]
    prov = [p for p in item.get("prov") or [] if p.get("page_no") is not None]
    pages = tuple(sorted({p["page_no"] for p in prov}))
    bbox = _bbox(prov[0]) if prov else None
    return Block(
        ref=ref,
        kind=kind[:-1],
        label=item.get("label", kind[:-1]),
        text=_text(kind, item),
        page=prov[0].get("page_no") if prov else None,
        pages=pages,
        bbox=bbox,
        order=order,
        parent=(item.get("parent") or {}).get("$ref"),
        layer=item.get("content_layer", "body"),
        spans=tuple(_span(kind, p) for p in prov),
        in_picture=in_picture,
        marker=(item.get("marker") or "").strip() or None if item.get("label") == "list_item" else None,
    )


SHARED = True  # M8: views and search share one parse per process (each parsed document.json itself)
CACHE_MB_ENV = "TGLW_CACHE_MB"
CACHE_MB = 2048  # default memory budget of the per-document caches
BYTES_PER_JSON_BYTE = 3.5  # process memory per byte of document.json: its Doc, view and search index (RS-ARCH coldstart: 3.6-3.8 on the largest)


class _DocLRU:
    """Documents in least-recently-used order with their estimated memory, shared by every doc_cache:
    when the estimate passes the budget, the oldest document leaves all of them at once (one cache
    keeping a document alive would keep the memory too). The newest document always stays."""

    def __init__(self) -> None:
        self.cost: OrderedDict[str, int] = OrderedDict()
        self.stores: list[dict[tuple, Any]] = []
        self.lock = threading.RLock()

    @staticmethod
    def budget() -> int:
        raw = os.environ.get(CACHE_MB_ENV, "").strip()
        return int(float(raw) * 2**20) if raw else CACHE_MB * 2**20

    @staticmethod
    def estimate(name: str) -> int:
        try:
            return int((get(name).bundle_dir / "document.json").stat().st_size * BYTES_PER_JSON_BYTE)
        except (KeyError, OSError):
            return 0

    def admit(self, name: str) -> None:
        if name in self.cost:
            self.cost.move_to_end(name)
            return
        self.cost[name] = self.estimate(name)
        budget = self.budget()
        while len(self.cost) > 1 and sum(self.cost.values()) > budget:
            old, _ = self.cost.popitem(last=False)
            for store in self.stores:
                for key in [k for k in store if k[0] == old]:
                    del store[key]

    def forget_unused(self) -> None:
        held = {k[0] for store in self.stores for k in store}
        for name in [n for n in self.cost if n not in held]:
            del self.cost[name]


DOCS = _DocLRU()


def doc_cache(fn: Callable[..., T]) -> Callable[..., T]:
    """Like functools.lru_cache for a function whose first argument is a registered document name, but
    every such cache shares one memory budget (TGLW_CACHE_MB, default CACHE_MB) counted per document,
    and evicts by document. Has cache_clear()."""
    store: dict[tuple, Any] = {}
    DOCS.stores.append(store)

    @wraps(fn)
    def cached(name: str, *args: Any, **kwargs: Any) -> T:
        key = (name, *args, *sorted(kwargs.items()))
        with DOCS.lock:
            if key in store:
                DOCS.admit(name)
                return store[key]
        value = fn(name, *args, **kwargs)
        with DOCS.lock:
            store[key] = value
            DOCS.admit(name)
        return value

    def cache_clear() -> None:
        with DOCS.lock:
            store.clear()
            DOCS.forget_unused()

    cached.cache_clear = cache_clear  # type: ignore[attr-defined]
    cached.cache_store = store  # type: ignore[attr-defined]
    return cached


def shared(name: str) -> Doc:
    """load(name), parsed once per process when SHARED; callers only read the Doc."""
    return _shared(name) if SHARED else load(name)


@doc_cache
def _shared(name: str) -> Doc:
    return load(name)


def load(name: str) -> Doc:
    info = get(name)
    if not info.cached:
        raise ValueError(f"{name} has no Docling cache")
    raw = json.loads((info.bundle_dir / "document.json").read_text())
    manifest = json.loads((info.bundle_dir / "manifest.json").read_text())
    blocks: list[Block] = []
    seen: set[str] = set()

    def visit(ref: str, in_picture: bool) -> None:
        if ref in seen:
            return
        seen.add(ref)
        item = resolve(raw, ref)
        kind = ref.split("/")[1]
        if kind in CONTENT_KINDS:
            blocks.append(_block(ref, item, len(blocks), in_picture))
        for ch in item.get("children") or []:
            visit(ch["$ref"], in_picture or kind == "pictures")
        for key in ("captions", "footnotes"):
            for ch in item.get(key) or []:
                visit(ch["$ref"], in_picture)

    for ch in raw["body"].get("children", []):
        visit(ch["$ref"], False)
    for ch in (raw.get("furniture") or {}).get("children", []):
        visit(ch["$ref"], False)
    orphans = []
    for kind in CONTENT_KINDS:
        for i, item in enumerate(raw.get(kind, [])):
            ref = f"#/{kind}/{i}"
            if ref not in seen:
                orphans.append(ref)
                blocks.append(_block(ref, item, len(blocks), False))
    return Doc(
        info=info,
        raw=raw,
        page_count=manifest["page_count"],
        blocks=blocks,
        by_ref={b.ref: b for b in blocks},
        orphans=orphans,
    )


def load_pages(name: str) -> list[dict[str, Any]]:
    """Docling's per-page parse cells from pages.json: textline_cells and word_cells (with
    bbox in top-left origin, confidence, from_ocr), plus page size and confidence."""
    return json.loads((get(name).bundle_dir / "pages.json").read_text())["pages"]
