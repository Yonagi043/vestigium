"""Section tree from heading numbers alone, with sequence checks and conservative OCR repair.

A numbered heading is accepted only when it fits: its numbering parent is open (2.2 before 2.2.4,
AC-2 before its enhancements) and it follows its previous sibling (+1, or a gap of at most 3,
flagged). Heading-like body blocks (short text or list items) are accepted only on an exact fit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace


from .numbering import Num, parse, roman

RANK = {"part": 0, "app": 1, "dec": 1, "item": 2, "ctl": 3, "enh": 4, "note": 5}
# Chinese numbering (task ZH's rules): a number hangs under the nearest open number of a higher rank.
# 第X编/章/节/条 count through the whole document (a law's 第六条 opens its second chapter); 一、 and
# （一） restart under each parent.
ZH_RANK = {"zh_part": 0, "zh_appendix": 0, "zh_chapter": 1, "zh_section": 2, "zh_article": 3, "dec": 4,
           "zh_list": 5, "zh_paren": 6, "zh_arabic": 7, "zh_arabic_paren": 8, "zh_circled": 9}
ZH_GLOBAL = frozenset({"zh_part", "zh_chapter", "zh_section", "zh_article"})


def zh_rank(family: str) -> int:
    return ZH_RANK.get(family, 0 if family in ("part", "app") else 10)


@dataclass
class Cand:
    ref: str
    order: int
    page: int | None
    text: str
    num: Num
    heading: bool
    extra: tuple[str, ...] = ()


@dataclass
class NumNode:
    num: Num
    ref: str
    order: int
    page: int | None
    parent: int | None
    flags: list[str] = field(default_factory=list)
    extra: tuple[str, ...] = ()


def ordinal(comp: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d+)([A-Z]?)", comp)
    if m:
        return int(m.group(1)), (ord(m.group(2)) - 64 if m.group(2) else 0)
    if len(comp) == 1 and comp.isalpha():
        return ord(comp.upper()) - 64, 0
    r = roman(comp)
    return (r, 0) if r else (0, 0)


def app_ordinal(comp: str, letters: bool) -> tuple[int, int]:
    if comp.isdigit():
        return int(comp), 0
    if letters and len(comp) == 1:
        return ord(comp) - 64, 0
    r = roman(re.sub(r"[A-Z]$", "", comp) if len(comp) > 1 and roman(comp) is None else comp)
    suffix = ord(comp[-1]) - 64 if len(comp) > 1 and roman(comp) is None else 0
    return (r, suffix) if r else (ord(comp[0]) - 64, 0)


def step_enh(prev: tuple[int, int] | None, new: tuple[int, int]) -> str:
    """Control enhancements: any increase is accepted, a jump is flagged (withdrawn or lost titles)."""
    if prev is None:
        return "ok" if new == (1, 0) else "gap"
    if new == prev:
        return "dup"
    if new[0] == prev[0] + 1:
        return "ok"
    return "gap" if new[0] > prev[0] else "no"


def step(prev: tuple[int, int] | None, new: tuple[int, int]) -> str:
    """ok | gap | dup | no for a sibling sequence."""
    if prev is None:
        if new in ((1, 0), (0, 0)):
            return "ok"
        return "gap" if new[1] == 0 and 1 < new[0] <= 3 else "no"
    if new == prev:
        return "dup"
    if new == (prev[0], prev[1] + 1) or new == (prev[0] + 1, 0):
        return "ok"
    if prev[0] < new[0] <= prev[0] + 3:
        return "gap"
    return "no"


def _ocr_variants(key: tuple[str, ...]) -> list[tuple[str, ...]]:
    """'13.1' -> '1.3.1': a dropped dot inside a multi-part number. Single numbers ('27.') are lists."""
    out = []
    if len(key) < 2:
        return out
    for i, c in enumerate(key):
        if c.isdigit() and len(c) == 2 and c[1] != "0":
            out.append(key[:i] + (c[0], c[1]) + key[i + 1:])
    return out


class Builder:
    def __init__(self, letters_app: bool, loose: dict[tuple[str, ...], list[Cand]] | None = None):
        self.nodes: list[NumNode] = []
        self.stack: list[int] = []
        self.last: dict[tuple, tuple[int, int]] = {}
        self.seen_top: set[tuple] = set()
        self.family_last: dict[str, tuple[int, int]] = {}
        self.rejected: dict[str, str] = {}
        self.letters_app = letters_app
        self.loose = loose or {}

    def _open(self, pred) -> int | None:
        for i in reversed(self.stack):
            if pred(self.nodes[i]):
                return i
        return None

    def _parent(self, n: Num) -> tuple[bool, int | None]:
        """(found, parent index or None for root)."""
        f = n.family
        if f.startswith("zh_"):
            return True, self._open(lambda x: zh_rank(x.num.family) < ZH_RANK[f])
        if f == "part":
            return True, None
        if f in ("app", "item"):
            return True, self._open(lambda x: x.num.family == "part")
        if f == "dec" and n.depth == 1:
            main = any(k[0] is None and k[1] == "dec" for k in self.seen_top)
            return True, self._open(lambda x: x.num.family == "part" or (main and x.num.family == "app"))
        if f == "dec":
            want = n.key[:-1]
            p = self._open(lambda x: x.num.family == "dec" and x.num.key == want
                           or (len(want) == 1 and x.num.family in ("app", "zh_chapter", "zh_section", "zh_part")
                               and x.num.key == want))
            return p is not None, p
        if f == "ctl" and n.depth == 2:
            return True, self._open(lambda x: RANK.get(x.num.family, 1) < 3)
        if f == "ctl":
            p = self._open(lambda x: x.num.family == "ctl" and x.num.key == n.key[:2])
            return p is not None, p
        if f == "enh":
            p = self._open(lambda x: x.num.family == "ctl" and x.num.depth == 2)
            return p is not None, p
        return True, self._open(lambda x: RANK.get(x.num.family, 1) < 5)

    def _seq(self, n: Num, parent: int | None) -> tuple[tuple, tuple[int, int]]:
        f, last = n.family, n.key[-1]
        if f in ZH_GLOBAL:
            return (None, f), ordinal(last)
        if f == "app":
            return (parent, f), app_ordinal(last, self.letters_app)
        if f == "ctl" and n.depth == 2:
            return (parent, f, n.key[0]), ordinal(n.key[1])
        if f == "dec" and n.depth == 1:
            return (parent, f, last.isalpha()), ordinal(last)
        return (parent, f), ordinal(last)

    def _fit(self, n: Num) -> tuple[str, int | None, tuple, tuple[int, int]]:
        found, parent = self._parent(n)
        if not found:
            return "parent_missing", None, (), (0, 0)
        seq, o = self._seq(n, parent)
        status = (step_enh if n.family == "enh" else step)(self.last.get(seq), o)
        if status == "no" and n.family == "dec" and n.depth == 1 and parent is not None:
            glob = (None, "dec", n.key[0].isalpha())
            if step(self.last.get(glob), o) == "ok":
                status = "ok"
        if status == "no" and n.family in ("item", "note") and step(self.family_last.get(n.family), o) == "ok":
            status = "ok"
        return status, parent, seq, o

    def offer(self, c: Cand) -> bool:
        n = c.num
        status, parent, seq, o = self._fit(n)
        note = ""
        if status not in ("ok", "gap") and n.family == "dec":
            for key in _ocr_variants(n.key):
                alt = replace(n, key=key, note=f"ocr: {n.raw} -> {'.'.join(key)}")
                s2, p2, seq2, o2 = self._fit(alt)
                if s2 == "ok":
                    n, status, parent, seq, o, note = alt, s2, p2, seq2, o2, alt.note
                    break
        if status == "parent_missing" and c.heading and n.family == "dec" and n.depth >= 2:
            if self._virtual_parents(c, n):
                status, parent, seq, o = self._fit(n)
        if status == "dup" and c.heading and self._promote(c, n):
            return True
        if status == "gap" and (not c.heading or n.weak):
            status = "no"
        if status not in ("ok", "gap"):
            self.rejected[c.ref] = status
            return False
        while self.stack and self.stack[-1] != parent:
            self.stack.pop()
        flags = [x for x in (status if status == "gap" else "", note or n.note,
                             "" if c.heading else "relabeled", "weak" if n.weak else "") if x]
        self.nodes.append(NumNode(n, c.ref, c.order, c.page, parent, flags, c.extra))
        self.stack.append(len(self.nodes) - 1)
        self.last[seq] = o
        self.family_last[n.family] = o
        if n.family == "dec" and n.depth == 1:
            self.seen_top.add(seq)
        for k in [k for k in self.last if k and k[0] is not None and k[0] not in self.stack]:
            del self.last[k]
        return True


    def _promote(self, c: Cand, n: Num) -> bool:
        """A real heading repeating a number just taken from body text on the same page wins the anchor."""
        for i in reversed(self.stack):
            x = self.nodes[i]
            if x.num.family == n.family and x.num.key == n.key:
                if "relabeled" in x.flags and x.page == c.page and self.stack[-1] == i:
                    x.ref, x.order, x.num = c.ref, c.order, n
                    x.flags = [f for f in x.flags if f != "relabeled"]
                    return True
                return False
        return False

    def _virtual_parents(self, c: Cand, n: Num) -> bool:
        """Open implied parents (II, II.3 for II.3.1) when the chain above them is consistent."""
        missing = []
        for j in range(n.depth - 1, 0, -1):
            prefix = n.key[:j]
            probe = replace(n, key=prefix, raw=".".join(prefix), title="", weak=False, note="")
            found, _ = self._parent(replace(probe, key=prefix + ("0",)))
            if found:
                break
            missing.append(probe)
        if not missing or len(missing) > 2:
            return False
        floor = self.nodes[self.stack[-1]].order if self.stack else -1
        for probe in reversed(missing):
            status, parent, seq, o = self._fit(probe)
            if status not in ("ok", "gap"):
                return False
            while self.stack and self.stack[-1] != parent:
                self.stack.pop()
            found = [x for x in self.loose.get(probe.key, []) if floor < x.order < c.order]
            if found:
                x = found[-1]
                self.nodes.append(NumNode(replace(x.num), x.ref, x.order, x.page, parent, ["relabeled"]))
            else:
                self.nodes.append(NumNode(probe, "", c.order, c.page, parent, ["virtual"]))
            self.stack.append(len(self.nodes) - 1)
            self.last[seq] = o
            if probe.depth == 1:
                self.seen_top.add(seq)
        return True


def _weak_unconfirmed(nodes: list[NumNode]) -> set[str]:
    out = set()
    for i, nd in enumerate(nodes):
        if "weak" not in nd.flags:
            continue
        has_child = any(x.parent == i for x in nodes)
        siblings = [x for x in nodes if x is not nd and x.parent == nd.parent and x.num.family == "dec"
                    and x.num.depth == 1 and x.num.key[0].isalpha()]
        if not has_child and not siblings:
            out.add(nd.ref)
    return out


def build(cands: list[Cand], loose: list[Cand] | None = None) -> tuple[list[NumNode], dict[str, str]]:
    """Accept consistent numbers; `loose` are body blocks whose number may only fill a missing parent."""
    letters = any(c.num.family == "app" and len(c.num.key[0]) == 1 and roman(c.num.key[0]) is None
                  for c in cands)
    by_key: dict[tuple[str, ...], list[Cand]] = {}
    for c in loose or []:
        by_key.setdefault(c.num.key, []).append(c)
    parts = [c for c in cands if c.num.family == "part"]
    first_other = min((c.order for c in cands if c.num.family != "part" and not c.num.weak), default=-1)
    lone_front = len({c.num.key for c in parts}) == 1 and all(c.order < first_other for c in parts)
    skip: set[str] = {c.ref for c in parts} if lone_front else set()
    for _ in range(3):
        b = Builder(letters, by_key)
        for c in cands:
            if c.ref not in skip:
                b.offer(c)
        drop = _weak_unconfirmed(b.nodes)
        if not drop:
            break
        skip |= drop
    rejected = dict(b.rejected)
    rejected.update({r: "weak_unconfirmed" for r in skip})
    return b.nodes, rejected
