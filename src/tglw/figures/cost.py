"""E1: the server instructions and region's description say what an image costs in tokens, and how to
enlarge small print, instead of "use sparingly" / "Images are costly".

E0: images were about 5% of haiku's weighted input in the vision pilot (median about 600 image tokens
a run); haiku looked once at 1.5x and answered, opus enlarged and zoomed into parts. The sizes below
cover region crops at 1.5x and under ADAPTIVE_ZOOM (figure median 291 / 631 tokens, p90 548 / 1,250,
cap 1,533) and a whole page at 1.5x (about 1,400).
"""

from __future__ import annotations

from . import switches

OLD_INSTRUCTION = "region() returns images: use sparingly."
NEW_INSTRUCTION = "region() image: about 300-1,500 tokens; small print: scale up to 3; part of a figure: pN:l,t,r,b."
OLD_DESCRIPTION = "Images are costly."
NEW_DESCRIPTION = "An image is about 300-1,500 tokens; scale up to 3 enlarges small print."


def instructions(text: str) -> str:
    """The server instructions with the region sentence replaced under TOKEN_NOTE."""
    return text.replace(OLD_INSTRUCTION, NEW_INSTRUCTION) if switches.on("TOKEN_NOTE") else text


def region_description(text: str) -> str:
    """region's tool description with the cost sentence replaced under TOKEN_NOTE."""
    return text.replace(OLD_DESCRIPTION, NEW_DESCRIPTION) if switches.on("TOKEN_NOTE") else text
