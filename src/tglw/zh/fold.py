"""Traditional/simplified folding for search: map every character to one simplified form.

`fold` is applied to both the indexed text and the query, so it only has to be consistent, not a
faithful conversion; it is character-by-character and keeps the length (offsets stay valid).
Displayed text is never folded. Tables come from OpenCC (Apache-2.0): t2s characters, the
Hong Kong variant characters, and 著 -> 着; Kangxi radicals and compatibility ideographs go to
their unified ideograph (NFKC). Non-Chinese characters are untouched.
"""

from __future__ import annotations

import importlib.util
import unicodedata
from functools import lru_cache

_RANGES = ((0x2E80, 0x2FDF), (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF), (0x20000, 0x2EBEF))
_EXTRA = {"著": "着"}
FOLD_AVAILABLE = importlib.util.find_spec("opencc") is not None


def _opencc(config: str, chars: list[str]) -> list[str]:
    import opencc

    out = opencc.OpenCC(config).convert("\n".join(chars)).split("\n")
    if len(out) != len(chars):
        raise RuntimeError(f"OpenCC {config}: character table misaligned")
    return out


@lru_cache(maxsize=1)
def table() -> dict[int, str]:
    """str.translate table: code point -> one simplified character."""
    chars = [chr(c) for a, b in _RANGES for c in range(a, b + 1)]
    nfkc = [unicodedata.normalize("NFKC", c) for c in chars]
    base = [n if len(n) == 1 else c for c, n in zip(chars, nfkc)]
    t2s, hk2s = (_opencc("t2s", base), _opencc("hk2s", base)) if FOLD_AVAILABLE else (base, base)
    m: dict[str, str] = {}
    for c, b, s, h in zip(chars, base, t2s, hk2s):
        to = h if len(h) == 1 and h != s else s
        to = _EXTRA.get(to, to)
        if len(to) == 1 and to != c:
            m[c] = to
    for c in list(m):
        for _ in range(4):
            if m[c] not in m or m[m[c]] == c:
                break
            m[c] = m[m[c]]
    return {ord(k): v for k, v in m.items()}


def fold(text: str) -> str:
    """Simplified-Chinese folding for matching; same length as `text`, deterministic. Without OpenCC
    installed (FOLD_AVAILABLE is False) only radicals and compatibility ideographs are mapped."""
    return text.translate(table())
