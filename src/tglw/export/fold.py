"""Lines of at most WIDTH characters for a shell's Grep (Claude Code hides lines over 500).

The W′ rule (data/K/fold_exact.py): a longer line is cut after sentence ends, packing sentences while
the line stays within WIDTH; a sentence still too long is cut between words. Here a table row is cut
after a cell separator before between words, Chinese text after its sentence punctuation, and a word
longer than WIDTH (a URL, Chinese without punctuation) every WIDTH characters. Every character is
kept: "".join(fold_line(x)) == x; the whitespace at a cut ends the line before it.
"""

from __future__ import annotations

import re
from collections.abc import Callable

WIDTH = 400
_SENTENCE = re.compile(r"(?<=[.;:!?])\s+|(?<=[。！？；：])\s*")
_CELL = re.compile(r"(?<=\|)\s+")
_WORD = re.compile(r"\s+")


def _len(text: str) -> int:
    return len(text.rstrip())


def _after(sep: re.Pattern[str]) -> Callable[[str], list[str]]:
    """A splitter cutting after each match of sep (the match kept at the end of its piece)."""
    def split(text: str) -> list[str]:
        out, start = [], 0
        for m in sep.finditer(text):
            if m.end() > start:
                out.append(text[start:m.end()])
                start = m.end()
        return out + [text[start:]] if start < len(text) else out
    return split


_RULES = [_after(_SENTENCE), _after(_CELL), _after(_WORD)]


def _pack(text: str, width: int, rules: list[Callable[[str], list[str]]]) -> list[str]:
    """Pieces of text by the first rule, joined while within width; a piece too long alone is cut
    by the next rules, and without rules every width characters."""
    if not rules:
        return [text[i:i + width] for i in range(0, len(text), width)]
    parts, cur = [], ""
    for unit in rules[0](text):
        if _len(cur + unit) <= width:
            cur += unit
            continue
        if cur:
            parts.append(cur)
        pieces = [unit] if _len(unit) <= width else _pack(unit, width, rules[1:])
        parts += pieces[:-1]
        cur = pieces[-1]
    if cur:
        parts.append(cur)
    return parts


def fold_line(line: str, width: int = WIDTH) -> list[str]:
    """One line as several of at most width characters (trailing whitespace not counted)."""
    return [line] if _len(line) <= width else _pack(line, width, _RULES)


def fold(text: str, width: int = WIDTH) -> list[str]:
    """Every line of text folded, trailing whitespace stripped."""
    return [p.rstrip() for line in text.split("\n") for p in fold_line(line, width)]
