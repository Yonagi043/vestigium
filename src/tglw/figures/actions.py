"""R6 rule actions (LANG_SPEC 11.4): the content of `add image` (on read) and `zoom xN` (on view).

The executor (INT) decides that a rule fires, keeps the conversation's seen images (target_key), checks the
budgets it owns and writes the provenance line and the trace's `dec rule`; these functions choose the image and
render it exactly as region() would, and say what it costs: image tokens are width x height / 750, as in
region's caption. Their own renders report nothing to the trace (the rule's decision says what was added).
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass, field

from tglw.common.text import count_tokens
from tglw.mcp_server import tools
from tglw.trace import record

from . import ocr, region, tracing, zoom

KINDS = ("figure", "table", "scan")
ZOOM_MIN_GAIN = 1.1  # a zoom whose long side grows less than this (the pixel caps) is not done
_FIG = re.compile(r"^\[(fig\d+)[,:\]]", re.M)
_TAB = re.compile(r"^\[(tab\d+) \d+×\d+", re.M)
_PAGE = re.compile(r"^== p(\d+)\b", re.M)


def target_key(target: str) -> str:
    """A region target as the seen-images set keeps it: block references short (#/pictures/3 -> fig3), others as given."""
    from tglw.views.docview import long_ref, short_ref

    t = target.strip()
    ref = long_ref(t)
    return short_ref(ref) if ref else t


def image_tokens(width: int, height: int) -> int:
    return round(width * height / tools.IMAGE_TOKEN_DIVISOR)


def _is_figure(name: str, ref: str) -> bool:
    from tglw.views import view

    from .inline import is_figure

    v = view(name)
    b = v.doc.by_ref.get(ref)
    return b is not None and is_figure(v, b)


def candidates(name: str, kind: str, text: str) -> list[str]:
    """Region targets of `kind` in a read's text, in the order shown: figures that get v1's image hint (not icons or
    logos), tables, or scanned pages (whole page)."""
    if kind == "figure":
        found = [m.group(1) for m in _FIG.finditer(text)]
        return [t for t in dict.fromkeys(found) if _is_figure(name, f"#/pictures/{t[3:]}")]
    if kind == "table":
        return list(dict.fromkeys(m.group(1) for m in _TAB.finditer(text)))
    if kind == "scan":
        return [f"p{p}" for p in dict.fromkeys(int(m.group(1)) for m in _PAGE.finditer(text)) if ocr.scanned(name, p)]
    raise ValueError(f"add image takes one of {KINDS}, not {kind!r}")


@dataclass(frozen=True)
class AddedImage:
    target: str
    line: str  # prefix + region's caption: the text line the read ends with (before any "next:")
    png: bytes
    px: tuple[int, int]
    img_tokens: int
    tokens: int  # img_tokens + o200k tokens of line: what the action adds
    skipped: list[dict] = field(default_factory=list)  # [{what, why}] candidates over the limit, tried first


def add_image(name: str, kind: str, text: str, seen: Collection[str], limit: int, prefix: str = "",
              why: str = "budget") -> AddedImage | list[dict]:
    """The first image of `kind` shown in a read's text that the conversation has not seen and fits `limit`
    tokens, rendered as region(name, target) with no scale (E3; whole pages 1.5). With none, the candidates
    held back by the limit as [{what, why}] (empty: nothing to add)."""
    skipped: list[dict] = []
    seen_keys = {target_key(s) for s in seen}
    for target in candidates(name, kind, text):
        if target in seen_keys:
            continue
        with tools.PDFIUM, tracing.discarded():
            r = region.region(name, target)
        line = prefix + r.caption
        img = image_tokens(r.width, r.height)
        cost = img + count_tokens(line)
        if cost > limit:
            skipped.append({"what": target, "why": why})
            continue
        return AddedImage(target, line, r.png, (r.width, r.height), img, cost, skipped)
    return skipped


@dataclass(frozen=True)
class Zoomed:
    region: tools.Region  # replaces the one region() returned: its caption, text and image
    scale_from: float
    scale_to: float
    img_tokens: int
    tokens: int  # what the zoom adds over the image and caption it replaces


def zoom_view(name: str, target: str, n: float, shown: tools.Region, limit: int | None = None,
              why: str = "budget") -> Zoomed | list[dict]:
    """`zoom xN` for a region call that gave no scale: the larger of N and the scale E3 chose, at most 3; blocks,
    figures, tables and boxes only (whole pages are not enlarged). Nothing to do ([]) when that is no larger or
    the pixel caps keep the long side under ZOOM_MIN_GAIN times the shown one; [{what, why}] when it would add
    more than `limit`."""
    page, box, _ = tools._target_box(name, target)
    if box is None:
        return []
    with tools.PDFIUM:
        base = zoom.chosen_scale(name, page, box, tools.REGION_SCALE)
        to = min(zoom.MAX_SCALE, max(float(n), base))
        if to <= base:
            return []
        with tracing.discarded():
            r = region.region(name, target, scale=to)
    if max(r.width, r.height) < ZOOM_MIN_GAIN * max(shown.width, shown.height):
        return []
    img = image_tokens(r.width, r.height)
    cost = img - image_tokens(shown.width, shown.height) + count_tokens(r.caption) - count_tokens(shown.caption)
    if limit is not None and cost > limit:
        return [{"what": target_key(target), "why": why}]
    capped = max(r.width, r.height) + 1 < max(box[2] - box[0], box[1] - box[3]) * to
    tracing.guarded(_report_img, page, box, (r.width, r.height), to, img, capped)
    return Zoomed(r, base, to, img, cost)


def _report_img(page: int, box: tuple[float, ...], px: tuple[int, int], scale: float, img: int, capped: bool) -> None:
    """The view's image is now the zoomed one (out.img replaced)."""
    record.out(img={"page": page, "box": [round(x, 1) for x in box], "px": list(px), "scale": scale, "img_tokens": img,
                    "capped": capped})
