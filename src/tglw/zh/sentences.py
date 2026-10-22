"""Chinese sentence ends for excerpting (。！？ and a closing quote or bracket right after them)."""

from __future__ import annotations

import re

_END = re.compile(r"(?<=[。！？])(?![”’」』）》])|(?<=[。！？][”’」』）》])")


def sentences(text: str) -> list[str]:
    """Split after Chinese sentence-ending punctuation; text without it comes back as one piece, so an
    English splitter can still run on each piece."""
    return [s for s in _END.split(text) if s.strip()]
