"""Key normalisation. Numbered keys go through task A's tglw.structure.number_key so both sides match."""

from __future__ import annotations

import re

from tglw.structure import number_key

NUMBER_KEY_SOURCE = "tglw.structure.number_key"


def section_key(raw: str) -> str | None:
    """'4.2' -> '4.2', '03.01.05' -> '3.1.5', 'A.5' -> 'A.5', 'II.3' -> 'II.3'."""
    for kind in ("dotted", "letter", "roman"):
        if k := number_key(kind, raw):
            return k
    return None


def note_key(raw: str) -> tuple[str | None, str | None]:
    """('2.2' -> '2.2', parent '2'), ('2(J)(ii)' -> '2', no parent), ('7' -> '7')."""
    m = re.match(r"\s*(\d{1,2})(?:\.(\d{1,2}))?", raw)
    if not m:
        return None, None
    base = number_key("note", m.group(1))
    return (f"{base}.{int(m.group(2))}", base) if m.group(2) else (base, None)


def caption_key(raw: str) -> str:
    """Table/figure numbers compared upper-cased without spaces ('c.1' -> 'C.1'). A roman suffix that OCR
    read as digits or l's is folded on both sides: '3.2.2-11' -> '3.2.2-II' (so '3-1' -> '3-I' too)."""
    key = re.sub(r"\s+", "", raw).rstrip(".")
    key = re.sub(r"-([1Il|]{1,3})$", lambda m: "-" + "I" * len(m.group(1)), key)
    return key.upper()


def control_key(raw: str) -> str | None:
    return number_key("control", raw)
