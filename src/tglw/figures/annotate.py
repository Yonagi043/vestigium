"""One-call integration for read and outline text: E2 figure lines, E5 drawing hints and E4 scan marks
on a read; E6 figure counts on an outline. With every VIS switch off both return their input unchanged."""

from __future__ import annotations

import re
from collections.abc import Iterable

from tglw.trace import record

from . import counts, drawings, inline, ocr, switches, tracing

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
        lines, changed, hinted = [], [], []
        for line in text.split("\n"):
            m = _FIG.match(line) if fig_on else None
            b = v.doc.by_ref.get(f"#/pictures/{m.group(1)}") if m else None
            if b is not None and line == render_picture(v, b):
                new = inline.picture_line(v, b, full=b.ref in direct)
                if new != line:
                    changed.append((b.ref, new))
                line = new
            lines.append(line)
            if line in caps:
                lines.append(drawings.hint(name, caps[line].caption))
                hinted.append(caps[line])
        new_text = "\n".join(lines)
        if changed or hinted:
            tracing.guarded(_report_figs, changed, hinted, text, new_text)
        text = new_text
    return ocr.mark_read(name, text)


def _report_figs(changed: list[tuple[str, str]], hinted: list[drawings.Drawing], before: str, after: str) -> None:
    figs, acted = [], set()
    for ref, line in changed:
        figs.append(inline.short(ref))
        acted |= inline.applied(line, figs[-1])
    figs += [d.target for d in hinted]
    acted |= {"DRAWINGS"} if hinted else set()
    record.sig("fig_hint", figs=figs)
    record.dec("fig_hints", src="|".join(switches.env_name(k) for k in sorted(acted)), tokens=tracing.added(before, after))


def outline(name: str, text: str) -> str:
    return counts.mark_outline(name, text)
