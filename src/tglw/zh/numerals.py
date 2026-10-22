"""Chinese numerals: 三 -> 3, 二十三 -> 23, 一百零五 -> 105, 貳拾 -> 20, 〇 -> 0."""

from __future__ import annotations

DIGITS = {
    "零": 0, "〇": 0, "○": 0, "一": 1, "二": 2, "两": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
    "八": 8, "九": 9, "壹": 1, "贰": 2, "貳": 2, "叁": 3, "參": 3, "叄": 3, "肆": 4, "伍": 5, "陆": 6, "陸": 6,
    "柒": 7, "捌": 8, "玖": 9,
}
UNITS = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
TENS = {"廿": 20, "卅": 30}
CHARS = "".join(DIGITS) + "".join(UNITS) + "".join(TENS)


def cn_int(s: str) -> int | None:
    """Value of a Chinese (or Arabic, or full-width) numeral up to 9999; None if `s` is not one."""
    s = s.strip()
    if not s:
        return None
    if s.isdecimal():
        return int(s)
    if any(c not in CHARS for c in s):
        return None
    if all(c in DIGITS for c in s) and len(s) > 1:
        return int("".join(str(DIGITS[c]) for c in s))
    total, digit, last_unit = 0, None, 10_000
    for c in s:
        if c in DIGITS:
            if digit is not None and DIGITS[c] != 0:
                return None
            digit = DIGITS[c] if DIGITS[c] else None
        elif c in TENS:
            if digit is not None or total:
                return None
            total = TENS[c]
            last_unit = 10
        else:
            unit = UNITS[c]
            if unit >= last_unit:
                return None
            total += (1 if digit is None else digit) * unit
            digit, last_unit = None, unit
    return total + (digit or 0)
