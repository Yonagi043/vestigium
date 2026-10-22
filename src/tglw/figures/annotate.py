"""One-call integration for read and outline text: E2 figure lines, E5 drawing hints and E4 scan marks
on a read; E6 figure counts on an outline. With every VIS switch off both return their input unchanged."""

from __future__ import annotations

import re
from collections.abc import Iterable

from . import counts, drawings, inline, ocr, switches

_FIG = re.compile(r"^\[fig(\d+)[:\]]")


def read(name: str, text: str, addrs: Iterable[str] = ()) -> str:
    """A views.read result with the VIS additions. addrs are the read's addresses: a figure asked for by
    its own address keeps all its labels."""
    from tglw.views import view
    from tglw.views.docview import long_ref
    from tglw.views.render import render_picture, render_segment

    fig_on = any(switches.on(k) for k in ("FIG_HINT", "FIG_LABELS", "OCR_NOTE"))
    hints = drawings.by_caption(name) if switches.on("DRAWINGS") else {}
    if fig_on or hints:
        v = view(name)
        direct = {long_ref(a.split(" from ")[0].strip()) for a in addrs}
        caps = {}
        for ref, d in hints.items():
            for seg in v.block_segments(v.doc.by_ref[ref])[:1]:
                caps[render_segment(v, seg)] = d
        lines = []
        for line in text.split("\n"):
            m = _FIG.match(line) if fig_on else None
            b = v.doc.by_ref.get(f"#/pictures/{m.group(1)}") if m else None
            if b is not None and line == render_picture(v, b):
                line = inline.picture_line(v, b, full=b.ref in direct)
            lines.append(line)
            if line in caps:
                lines.append(drawings.hint(name, caps[line].caption))
        text = "\n".join(lines)
    return ocr.mark_read(name, text)


def outline(name: str, text: str) -> str:
    return counts.mark_outline(name, text)
