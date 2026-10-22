"""E6: the outline says how many figures each section holds ("· 3 fig"), so a reader looking for a chart
knows where to go; icons and logos (no caption, no labels, under FIGURE_SHARE of the page) do not count.
Counts cover subsections, like the outline's token counts."""

from __future__ import annotations

import re
from functools import lru_cache

from . import switches
from .inline import is_figure

_LINE = re.compile(r"^(?P<indent>\s*)(?P<sid>s\d+) ")


@lru_cache(maxsize=64)
def section_figures(name: str) -> dict[str, int]:
    """Section id -> figures whose block lies in the section's block range (subsections included)."""
    from tglw.views import view

    v = view(name)
    orders = sorted(b.order for b in v.doc.blocks if b.kind == "picture" and not b.in_picture and v.visible(b) and is_figure(v, b))
    out = {}
    for sid, n in v.sections.items():
        lo, hi = n.get("start_order"), n.get("end_order")
        if lo is None or hi is None:
            continue
        out[sid] = sum(lo <= o < hi for o in orders)
    return out


def mark_outline(name: str, text: str) -> str:
    """Under OUTLINE_FIGS, each section line with figures gets "· N fig" after its token count (before
    "· +N sub"). Lines of the printed-TOC and headings fallbacks carry no section id and stay as they are."""
    if not switches.on("OUTLINE_FIGS"):
        return text
    counts = section_figures(name)
    out = []
    for line in text.split("\n"):
        m = _LINE.match(line)
        n = counts.get(m.group("sid"), 0) if m else 0
        if n:
            tag = f" · {n} fig"
            cut = line.rfind(" · +")
            line = line[:cut] + tag + line[cut:] if cut > 0 and line.endswith(" sub") else line + tag
        out.append(line)
    return "\n".join(out)
