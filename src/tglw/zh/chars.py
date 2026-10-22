"""Character classes shared by the Chinese rules."""

from __future__ import annotations

import re

HAN = ("⺀-⻿⼀-⿟々〇㐀-䶿一-鿿豈-﫿"
       "\U00020000-\U0002ebef\U00030000-\U0003134f")
CJK_PUNCT = "　-〿＀-￯"
CJK = HAN + CJK_PUNCT
NEUTRAL = "‘’“”—…·‧"

HAN_RE = re.compile(f"[{HAN}]")
CJK_RE = re.compile(f"[{CJK}]")
LETTER_RE = re.compile(r"[^\W\d_]")
QUOTE_OPEN = "“‘「『《〈\"'＂"
QUOTE_CLOSE = "”’」』》〉\"'＂"


def has_han(s: str) -> bool:
    return HAN_RE.search(s) is not None


def is_cjk_char(c: str) -> bool:
    return CJK_RE.match(c) is not None
