"""Plain-Python bodies of the MCP tools: document lookup, document list, region images."""

from __future__ import annotations

import ctypes
import difflib
import io
import math
import json
import os
import re
import threading
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from tglw.common import registry
from tglw.common.paths import DATA
from tglw.common.text import count_tokens
from tglw.trace import record
from tglw.views import view
from tglw.views.docview import long_ref, short_ref
from tglw.views.tracing import guarded, report

REGION_SCALE = 1.5
MAX_SIDE = 1200
MAX_PIXELS = 1_150_000  # the Claude API shrinks larger images to about this, so the token estimate below stays exact
PAD = 6.0
IMAGE_TOKEN_DIVISOR = 750
REGION_TEXT_TOKENS = 2000
INDEX = "doc_index.json"
ALLOW_ENV = "TGL_ALLOWED_DOCS"  # comma-separated registered names: the only documents served (experiment runs)
PDFIUM = threading.RLock()  # pdfium is not thread-safe; the server runs tool calls on worker threads
_BOX = re.compile(r"^p(?P<page>\d+)(?::(?P<box>[-\d.]+(?:\s*,\s*[-\d.]+){3}))?$")


class LookupFailed(ValueError):
    """A document or target the caller should correct; the message lists candidates."""


def allowed_docs() -> list[str] | None:
    """The documents ALLOW_ENV limits this server to, or None when it is not set (all documents)."""
    raw = os.environ.get(ALLOW_ENV)
    if raw is None:
        return None
    names = list(dict.fromkeys(n.strip() for n in raw.split(",") if n.strip()))
    known = {d.name for d in registry.docs()}
    bad = [n for n in names if n not in known]
    if bad or not names:
        raise LookupFailed(f"server setting {ALLOW_ENV} " + (f"names unregistered documents: {', '.join(bad)}" if bad
                                                             else "names no document"))
    return names


def resolve_doc(name: str) -> str:
    """Registered name from an exact name or a unique fragment ("800-53r5", "10-K-2025"), among the
    allowed documents when ALLOW_ENV is set."""
    allow = allowed_docs()
    names = allow if allow is not None else [d.name for d in registry.docs()]
    if name in names:
        return name
    key = name.strip().lower()
    hits = [n for n in names if key and key in n.lower()]
    if len(hits) == 1:
        return hits[0]
    if allow is not None:
        raise LookupFailed(f"unknown document {name!r}; this session can only use: {', '.join(allow)}")
    cands = hits[:8] or difflib.get_close_matches(name, names, n=5, cutoff=0.3)
    raise LookupFailed(f"unknown document {name!r}" + (f"; candidates: {', '.join(cands)}" if cands else "")
                       + "; see list_documents()")


@lru_cache(maxsize=1)
def doc_index() -> dict[str, dict[str, Any]]:
    """Per-document summary written by tglw.mcp_server.index (delivered copy first)."""
    for path in (DATA / "M" / INDEX, DATA / "M" / "wip" / INDEX):
        if path.exists():
            return {r["doc"]: r for r in json.loads(path.read_text())}
    return {}


def list_documents(filter: str = "", qc: bool = False) -> str:
    """One line per document; with qc, the verdict of its ingest quality report (task ING) when it has one."""
    idx = doc_index()
    allow = allowed_docs()
    lines, names, verdicts = [], [], {}
    for d in registry.docs():
        if allow is not None and d.name not in allow:
            continue
        if filter and filter.lower() not in d.name.lower() and filter.lower() != d.cls:
            continue
        r = idx.get(d.name, {})
        tok = r.get("tokens")
        size = f"{tok / 1000:.0f}k tok" if tok else "? tok"
        extra = " · toc" if r.get("toc") else ""
        if qc:
            from tglw.ingest import qc as ingest_qc
            verdict = ingest_qc.summary(d.name)
            extra += f" · {verdict}" if verdict else ""
            verdicts.update({d.name: verdict} if verdict else {})
        lines.append(f"{d.name} · {d.cls} · {d.pages} p · {size} · structure {r.get('structure', '?')}{extra}")
        names.append(d.name)
    report(record.sig, "struct", part="documents", units=names, n=len(names))
    if verdicts:
        guarded(_trace_verdicts, verdicts)
    return "\n".join(lines) if lines else f"no document matches {filter!r}"


def _trace_verdicts(verdicts: dict[str, str]) -> None:
    report(record.sig, "qc", docs=verdicts)
    report(record.dec, "qc_notes", src="TGLW_ING_QC_NOTES", tokens=sum(count_tokens(f" · {v}") for v in verdicts.values()))


@dataclass
class Region:
    caption: str
    png: bytes
    width: int
    height: int
    text: str = ""


Box = tuple[float, float, float, float]
CAPTION_GAP = 90.0  # pt between a picture or table and a caption or note Docling links to it
LABEL_GAP = 36.0  # pt beyond a picture within which its own text (axis titles, legends) is cropped with it
_NOT_OWN = ("footnote", "page_header", "page_footer")


def _gap(a: Box, b: Box) -> tuple[float, float]:
    """(horizontal, vertical) distance between two boxes, 0 where they overlap."""
    return max(0.0, a[0] - b[2], b[0] - a[2]), max(0.0, a[3] - b[1], b[3] - a[1])


def block_box(v: Any, block: Any) -> tuple[Box, set[str]]:
    """What region() crops for a block, and the blocks it takes in. A picture or table comes with the
    captions and notes Docling links to it (up to CAPTION_GAP away, overlapping it horizontally) and
    with its own text within LABEL_GAP (axis titles, legends; for a picture also unattached text
    straddling its edge, such as tick labels); page footnotes, headers and footers stay out. The PAD margin then stops short of any other text, so no neighbouring line is cut in half."""
    l, t, r, bot = base = v.box_fixes.get(block.ref, block.bbox)  # task SRC F4: a misplaced block's true box
    taken = {block.ref}
    if block.kind in ("picture", "table"):
        item = v.doc.item(block.ref)
        linked = {x["$ref"] for k in ("captions", "footnotes") for x in item.get(k, [])}
        cands = [v.doc.by_ref[x] for x in sorted(linked) if x in v.doc.by_ref] + v.children.get(block.ref, [])
        for c in cands:
            if c.bbox is None or c.page != block.page or c.ref in taken:
                continue
            dx, dy = _gap(c.bbox, base)
            if c.ref in linked or c.label == "caption":
                keep = dx == 0 and dy <= CAPTION_GAP
            else:
                keep = c.label not in _NOT_OWN and (c.bbox[0] >= base[0] - LABEL_GAP and c.bbox[2] <= base[2] + LABEL_GAP
                                                    and c.bbox[3] >= base[3] - LABEL_GAP and c.bbox[1] <= base[1] + LABEL_GAP)
            if keep:
                taken.add(c.ref)
                l, t, r, bot = min(l, c.bbox[0]), max(t, c.bbox[1]), max(r, c.bbox[2]), min(bot, c.bbox[3])
        if block.kind == "picture":
            for o in v.by_page.get(block.page, []):  # labels Docling did not attach, straddling the picture's edge
                if o.ref in taken or o.bbox is None or o.page != block.page or o.label in _NOT_OWN or not o.text.strip():
                    continue
                ol, ot, orr, ob = o.bbox
                if _gap(o.bbox, base) == (0.0, 0.0) and ol >= base[0] - LABEL_GAP and orr <= base[2] + LABEL_GAP \
                        and ob >= base[3] - LABEL_GAP and ot <= base[1] + LABEL_GAP:
                    taken.add(o.ref)
                    l, t, r, bot = min(l, ol), max(t, ot), max(r, orr), min(bot, ob)
    core = (l, t, r, bot)
    L, T, R, B = l - PAD, t + PAD, r + PAD, bot - PAD
    for o in v.by_page.get(block.page, []):
        if o.ref in taken or o.bbox is None or o.page != block.page or not o.text.strip():
            continue
        ol, ot, orr, ob = o.bbox
        if orr <= L or ol >= R or ot <= B or ob >= T:
            continue  # outside the padded box
        if ob >= t:
            T = min(T, ob - 0.5)
        elif ot <= bot:
            B = max(B, ot + 0.5)
        elif orr <= l:
            L = max(L, orr + 0.5)
        elif ol >= r:
            R = min(R, ol - 0.5)
    return (min(L, core[0]), max(T, core[1]), max(R, core[2]), min(B, core[3])), taken


def _target_box(name: str, target: str) -> tuple[int, Box | None, str]:
    """(page, the box to crop -- l,t,r,b bottom-left points, margins included -- or None for the
    whole page, label) for a target."""
    v = view(name)
    m = _BOX.match(target.strip())
    if m:
        page = int(m.group("page"))
        if not 1 <= page <= v.doc.page_count:
            raise LookupFailed(f"page out of range: {target!r} (1-{v.doc.page_count})")
        if not m.group("box"):
            report(record.addr, target, kind="page", pages=[page])
            return page, None, f"p{page} whole page"
        l, a, r, b = (float(x) for x in m.group("box").split(","))
        report(record.addr, target, kind="box", pages=[page])
        return page, (min(l, r) - PAD, max(a, b) + PAD, max(l, r) + PAD, min(a, b) - PAD), target
    ref = long_ref(target.strip())
    block = v.doc.by_ref.get(ref) if ref else None
    if block is None or block.bbox is None or block.page is None:
        raise LookupFailed(f"bad region target {target!r}: use a block id (tab4, fig5, t123), "
                           "a page box 'p45:l,t,r,b' (PDF points, origin bottom-left), or 'p45' for a whole page")
    note = f" (first of pages {block.pages[0]}-{block.pages[-1]})" if len(block.pages) > 1 else ""
    report(record.addr, target, kind={"table": "table", "picture": "figure"}.get(block.kind, "block"), pages=[block.page])
    return block.page, block_box(v, block)[0], f"{target}{note}"


PREVIEW_BLOCKS = 40
PREVIEW_WORDS = 8


def preview(doc: str, target: str) -> str:
    """Coordinates only, no image: the target box and the blocks on that page that overlap it, so a
    tighter box 'p45:l,t,r,b' can be asked for next."""
    name = resolve_doc(doc)
    page, box, label = _target_box(name, target)
    v = view(name)
    size = v.doc.raw["pages"][str(page)]["size"]
    l0, t0, r0, b0 = box or (0.0, size["height"], size["width"], 0.0)
    lines = [f"{label} · {v.page_tag(page)} · page {size['width']:.0f}×{size['height']:.0f} pt · "
             f"box {l0:.0f},{t0:.0f},{r0:.0f},{b0:.0f} (l,t,r,b; origin bottom-left)"]
    rows, refs = [], []
    for b in v.by_page.get(page, []):
        if b.bbox is None or not v.visible(b, page) or b.page != page:
            continue
        l, t, r, bot = v.box_fixes.get(b.ref, b.bbox)
        if r < l0 or l > r0 or t < b0 or bot > t0:
            continue
        words = " ".join(b.text.split()[:PREVIEW_WORDS])
        rows.append(f"{short_ref(b.ref)} {b.label} {l:.0f},{t:.0f},{r:.0f},{bot:.0f} {words}")
        refs.append(short_ref(b.ref))
    report(record.sig, "struct", part="preview", units=refs[:PREVIEW_BLOCKS], n=len(refs), page=page)
    lines += rows[:PREVIEW_BLOCKS] + ([f"… +{len(rows) - PREVIEW_BLOCKS} more"] if len(rows) > PREVIEW_BLOCKS else [])
    return "\n".join(lines)


def region(doc: str, target: str, scale: float = REGION_SCALE) -> Region:
    name = resolve_doc(doc)
    page, box, label = _target_box(name, target)
    with PDFIUM:
        pdf = pdfium.PdfDocument(str(registry.get(name).pdf_path))
        try:
            pg = pdf[page - 1]
            height = pg.get_height()
            img = pg.render(scale=scale).to_pil()
            text = _layer_text(pg, box)
            turn = _upright_turn(pg, box)
        finally:
            pdf.close()
    if box:
        l, t, r, b = box
        crop = (max(0, l * scale), max(0, (height - t) * scale), min(img.width, r * scale), min(img.height, (height - b) * scale))
        img = img.crop(tuple(round(x) for x in crop))
    if turn:
        img = img.rotate(turn, expand=True)
        label += " · turned upright"
    f = min(1.0, MAX_SIDE / max(img.size), (MAX_PIXELS / (img.width * img.height)) ** 0.5)
    if f < 1.0:
        img = img.resize((max(1, int(img.width * f)), max(1, int(img.height * f))))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    w, h = img.size
    where = view(name).page_tag(page)
    est = round(w * h / IMAGE_TOKEN_DIVISOR)
    caption = f"{label} · {where} · {w}×{h} px · about {est} image tokens"
    if text:
        text = "text layer in the box:\n" + text
    else:
        text = _parsed_text(name, page, box)
        text = "no text layer here; parsed text in the box:\n" + text if text else "no text in the box"
    return Region(caption, buf.getvalue(), w, h, _cap(text))


def page_space(pg: Any, box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """A box in Docling's frame (the page as displayed: crop-box origin, page rotation applied; l, t,
    r, b from the bottom left) as PDF page coordinates (left, bottom, right, top), which the text
    layer uses. The two differ when the crop box does not start at 0,0 or the page is rotated."""
    w, h = pg.get_size()
    k = 10  # device units per point, for precision
    xs, ys = [], []
    l, t, r, b = box
    for x, y in ((l, t), (r, t), (l, b), (r, b)):
        px, py = ctypes.c_double(), ctypes.c_double()
        pdfium_c.FPDF_DeviceToPage(pg.raw, 0, 0, round(w * k), round(h * k), 0, round(x * k), round((h - y) * k),
                                   ctypes.byref(px), ctypes.byref(py))
        xs.append(px.value)
        ys.append(py.value)
    return min(xs), min(ys), max(xs), max(ys)


SIDEWAYS_SHARE = 0.5  # of the glyphs in a box, for its image to be turned upright


def _upright_turn(pg: Any, box: Box | None) -> int:
    """Degrees (counter-clockwise) to turn a crop so its text reads upright: 90 or -90 when most
    text-layer glyphs in the box run up or down the page (a landscape table printed sideways), else
    0. Pages with their own /Rotate are shown as the PDF says."""
    if pg.get_rotation():
        return 0
    tp = pg.get_textpage()
    try:
        left, bottom, right, top = page_space(pg, box) if box else (float("-inf"), float("-inf"), float("inf"), float("inf"))
        up = down = n = 0
        count = tp.count_chars()
        for i in range(0, count, max(1, count // 600)):
            a = pdfium_c.FPDFText_GetCharAngle(tp.raw, i)
            if a < 0 or pdfium_c.FPDFText_GetUnicode(tp.raw, i) in (32, 10, 13):
                continue
            l, b, r, t = tp.get_charbox(i)
            if not (left <= (l + r) / 2 <= right and bottom <= (b + t) / 2 <= top):
                continue
            deg = math.degrees(a) % 360
            n += 1
            up += 45 < deg < 135
            down += 225 < deg < 315
    finally:
        tp.close()
    if n < 20 or max(up, down) <= SIDEWAYS_SHARE * n:
        return 0
    return 90 if up > down else -90  # pdfium measures glyph angles clockwise


def _layer_text(pg: Any, box: tuple[float, float, float, float] | None) -> str:
    """The PDF text layer inside the box (or the whole page), one line per text line."""
    tp = pg.get_textpage()
    try:
        if box:
            l, t, r, b = box
            left, bottom, right, top = page_space(pg, (l, t, r, b))
            raw = tp.get_text_bounded(left=left, bottom=bottom, right=right, top=top)
        else:
            raw = tp.get_text_range()
    finally:
        tp.close()
    lines = (" ".join(x.split()) for x in raw.replace("\r", "\n").split("\n"))
    return "\n".join(x for x in lines if x)


def _parsed_text(name: str, page: int, box: tuple[float, float, float, float] | None) -> str:
    """Docling's text of the blocks overlapping the box, for pages without a text layer."""
    l0, t0, r0, b0 = box or (float("-inf"), float("inf"), float("inf"), float("-inf"))
    out = []
    for b in view(name).by_page.get(page, []):
        if b.bbox is None or b.page != page or not b.text.strip():
            continue
        l, t, r, bot = b.bbox
        if not (r < l0 or l > r0 or t < b0 or bot > t0):
            out.append(" ".join(b.text.split()))
    return "\n".join(out)


def _cap(text: str) -> str:
    if count_tokens(text) <= REGION_TEXT_TOKENS:
        return text
    lines, used = [], 0
    for line in text.split("\n"):
        used += count_tokens(line) + 1
        if used > REGION_TEXT_TOKENS:
            break
        lines.append(line)
    return "\n".join(lines) + "\n[text cut; read the address for the rest]"
