"""E7: literal search ranks hits in figure text (tick labels, legends) after hits in the text of the same
match quality (whole word or not), so a generic number on a chart does not push the prose that states
it down. Captions count as text. Ranking figure hits after every text hit was tried and dropped: it
pushed values printed only in a chart out of the top 3.

Once figure text became searchable, a generic number on a chart page could outrank the page whose text
states it.
"""

from __future__ import annotations

from typing import Any

from . import switches

Hit = tuple[int, int, Any, Any]  # whole-word hits, all hits, segment, owner block (views.api._literal_hits)


def in_figure(hit: Hit) -> bool:
    _, _, seg, owner = hit
    return owner.kind == "picture" and seg.block.label != "caption"


def block_key(hit: Hit) -> tuple:
    """Sort key for literal block hits; views.api.literal_search's own key when LITERAL_FIGS_LAST is off."""
    whole, _, seg, _ = hit
    if not switches.on("LITERAL_FIGS_LAST"):
        return (-whole, seg.page, seg.block.order)
    return (-whole, in_figure(hit), seg.page, seg.block.order)


def page_key(item: tuple[int, list[Hit]]) -> tuple:
    """Sort key for (page, hits) in literal page results: whole-word hits in the text first, a page's
    figure hits only when it has none in the text."""
    page, hits = item
    if not switches.on("LITERAL_FIGS_LAST"):
        return (-sum(h[0] for h in hits), page)
    text = sum(h[0] for h in hits if not in_figure(h))
    figs = sum(h[0] for h in hits if in_figure(h))
    return (-text, 0 if text else -figs, page)
