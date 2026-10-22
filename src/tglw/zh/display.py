"""Display clean-up for Chinese text: drop the spaces PDF extraction puts between Chinese characters.

"中 醫 藥" -> "中醫藥"; "（ 2023 年 版 ）" -> "（2023年版）"; "以下简称 “ 腾 讯 ”" -> "以下简称“腾讯”".
Text without Chinese characters is returned unchanged (same object). Newlines are never removed.
"""

from __future__ import annotations

import re
import unicodedata

from .chars import CJK, HAN_RE, NEUTRAL

_GAP = r"[ \t　 ]+"
_JOIN = f"[{CJK}{NEUTRAL}]"
_BETWEEN = re.compile(f"(?<={_JOIN}){_GAP}(?={_JOIN})")
_SANDWICH = re.compile(
    f"(?<=[{CJK}])({_GAP})([0-9][0-9.,:/%\\-]*|[A-Za-z]*[A-Z][A-Za-z0-9]*)({_GAP})(?=[{CJK}])")
_RADICALS = re.compile("[⺀-⻿⼀-⿟]")
_NUM_CHARS = "0-9一二三四五六七八九十百千零〇两兩"
_LEADING_NUMBER = re.compile(
    rf"^\s*(?:第(?:\s?[{_NUM_CHARS}])+\s?(?:部分|编|編|章|节|節|条|條|篇)|附\s?[录錄件]\s?(?:[{_NUM_CHARS}]+|[A-Z]))([ \t　 ]+)(?=\S)")


def _radical(c: str) -> str:
    n = unicodedata.normalize("NFKC", c)
    return n if len(n) == 1 else c


def gaps(text: str) -> list[tuple[int, int]]:
    """Whitespace spans to drop: between two Chinese characters / full-width punctuation (not between
    two neutral marks such as ” “), and around a number or capitalised Latin token sandwiched by them.
    The space after a leading article/chapter/appendix number ("第九条 信息…", "附錄五 一般資料") is kept."""
    out = []
    for m in _BETWEEN.finditer(text):
        if not (text[m.start() - 1] in NEUTRAL and text[m.end()] in NEUTRAL):
            out.append(m.span())
    for m in _SANDWICH.finditer(text):
        out += [m.span(1), m.span(3)]
    if lead := _LEADING_NUMBER.match(text):
        out = [g for g in out if g != lead.span(1)]
    return sorted(out)


def normalize_map(text: str, nfkc: bool = False) -> tuple[str, list[int]]:
    """Display-normalised text and, for each of its characters, the offset in `text` it came from.
    With nfkc=True every kept character is also NFKC-normalised (（ -> (, ： -> :, ① -> 1)."""
    drop = gaps(text) if HAN_RE.search(text) else []
    out: list[str] = []
    idx: list[int] = []
    k = 0
    for i, c in enumerate(text):
        while k < len(drop) and drop[k][1] <= i:
            k += 1
        if k < len(drop) and drop[k][0] <= i:
            continue
        c = unicodedata.normalize("NFKC", c) if nfkc else _radical(c) if _RADICALS.match(c) else c
        out.append(c)
        idx += [i] * len(c)
    return "".join(out), idx


def normalize_display(text: str, radicals: bool = True) -> str:
    """Remove horizontal whitespace between Chinese characters / full-width punctuation (and around a
    number or a capitalised Latin token sandwiched between them); map Kangxi radicals to ideographs."""
    if not HAN_RE.search(text):
        return text
    drop = gaps(text)
    parts, pos = [], 0
    for a, b in drop:
        parts.append(text[pos:a])
        pos = b
    parts.append(text[pos:])
    s = "".join(parts)
    return _RADICALS.sub(lambda m: _radical(m.group(0)), s) if radicals else s
