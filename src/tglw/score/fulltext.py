"""Condition W material: one whole-document text file per cached document.

The text is `tglw.common.text.render` (reading order, "=== PAGE n ===" markers, furniture kept), the
unrepaired Docling text: unlike condition T it does not apply task D1's patches. Pages that render
leaves without a marker (blank pages, or pages whose text sits in a block starting on an earlier
page) get an empty marker, so every page 1..N has one. Lines longer than `width` characters are
broken at spaces, without changing any character otherwise, because file-reading tools in agent
shells may truncate very long lines.

`zh_clean` is the Chinese counterpart of condition V′ (V′-zh): the same text with the display clean-up
the reader applies to Chinese (tglw.zh.normalize_display: spaces between Chinese characters dropped,
Kangxi radicals mapped to ideographs) and line breaks inside a text block joined where both sides are
Chinese and the break does not follow a sentence end; tables keep their rows. Apart from whitespace and
the radical mapping, not a character changes (`clean_check`).

`fold_dir` makes the folded texts of the confirmation's W′ condition: Claude Code's Grep hides lines over
500 characters, so a line over `width` (400) is cut after sentence ends ([.;:!?] then whitespace, or
a Chinese 。！？； with nothing after it needed), packing sentences while the line stays within the width
(measured without the whitespace at the cut), and a sentence longer than that between words; a run of
Chinese longer than the width, which has no spaces, every `width` characters. The line break goes after the whitespace run, so
deleting the added breaks gives the original back character for character; every line is checked.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import unicodedata
from collections.abc import Iterable
from pathlib import Path

from tglw.common import bundle, registry
from tglw.common.text import count_tokens, render, render_block
from tglw.zh import normalize_display
from tglw.zh.chars import CJK, is_cjk_char

WIDTH = 1000
FOLD_WIDTH = 400
HEADER = ("doc", "file", "pages", "tokens", "chars", "lines", "lines_broken", "markers_added", "sha256")
_MARK = re.compile(r"=== PAGE (\d+) ===")


def fill_markers(text: str, pages: int) -> tuple[str, int]:
    """Insert an empty "=== PAGE n ===" for every page render skipped; markers stay in order."""
    out: list[str] = []
    last = added = 0
    for line in text.split("\n"):
        m = _MARK.fullmatch(line)
        if m:
            n = int(m.group(1))
            for p in range(last + 1, n):
                out.append(f"=== PAGE {p} ===")
                added += 1
            last = max(last, n)
        out.append(line)
    for p in range(last + 1, pages + 1):
        out.append(f"=== PAGE {p} ===")
        added += 1
    return "\n".join(out), added


def wrap_line(line: str, width: int = WIDTH) -> list[str]:
    out: list[str] = []
    while len(line) > width:
        cut = line.rfind(" ", width // 2, width + 1)
        cut = cut if cut > 0 else width
        out.append(line[:cut].rstrip(" "))
        line = line[cut:].lstrip(" ")
    out.append(line)
    return out


def wrap(text: str, width: int = WIDTH) -> tuple[str, int]:
    lines, broken = [], 0
    for line in text.split("\n"):
        parts = wrap_line(line, width)
        broken += len(parts) > 1
        lines += parts
    return "\n".join(lines) + "\n", broken


_BREAK = re.compile(f"(?<=[{CJK}])\n(?=[{CJK}])")
SENTENCE_END = "。！？；：…"
_RADICALS = re.compile("[⺀-⻿⼀-⿟]")  # Kangxi radicals, which the display clean-up maps to ideographs


def _radical(c: str) -> str:
    n = unicodedata.normalize("NFKC", c)
    return n if len(n) == 1 else c
CLEAN_HEADER = ("breaks_joined", "radicals_mapped", "same_apart_from_whitespace")


def join_breaks(text: str) -> str:
    """Drop a line break between two Chinese characters unless the first ends a sentence."""
    return _BREAK.sub(lambda m: "\n" if text[m.start() - 1] in SENTENCE_END else "", text)


def _continues(prev: bundle.Block | None, b: bundle.Block, marker: bool) -> bool:
    """b goes on in the middle of a Chinese sentence that the paragraph before it on the same page broke
    off (Docling splits such paragraphs into two text blocks)."""
    return (prev is not None and not marker and prev.label == b.label == "text" and bool(prev.text) and bool(b.text)
            and is_cjk_char(prev.text[-1]) and is_cjk_char(b.text[0]) and prev.text[-1] not in SENTENCE_END)


def zh_render(doc: bundle.Doc) -> str:
    """render(doc) cleaned for Chinese: line breaks joined inside text blocks (tables keep their rows) and
    between two paragraphs of the same page that break off a sentence, then the display clean-up."""
    out: list[str] = []
    page, prev = None, None
    for b in doc.blocks:
        marker = b.page is not None and b.page != page
        if marker:
            out.append(f"=== PAGE {b.page} ===")
            page = b.page
        text = render_block(b)
        text = normalize_display(text if b.kind == "table" else join_breaks(text))
        if _continues(prev, b, marker):
            out[-1] += text
        else:
            out.append(text)
        prev = b
    return "\n".join(out)


def clean_check(plain: str, clean: str) -> dict[str, int | bool]:
    """Line breaks the clean text dropped, radicals it mapped, and whether it equals the plain text once
    whitespace is removed and the radicals are mapped."""
    squeeze = "".join(plain.split())
    return {"breaks_joined": plain.count("\n") - clean.count("\n"), "radicals_mapped": len(_RADICALS.findall(squeeze)),
            "same_apart_from_whitespace": _RADICALS.sub(lambda m: _radical(m.group(0)), squeeze) == "".join(clean.split())}


def fulltext(name: str, width: int = WIDTH, zh_clean: bool = False) -> tuple[str, int, int]:
    doc = bundle.load(name)
    filled, added = fill_markers(zh_render(doc) if zh_clean else render(doc), registry.get(name).pages)
    text, broken = wrap(filled, width)
    return text, broken, added


def write_all(out_dir: Path, names: Iterable[str] | None = None, width: int = WIDTH, zh_clean: bool = False) -> Path:
    """Write one text per document and index.csv; with zh_clean the V′-zh texts, each checked against the
    plain text (CLEAN_HEADER columns); a text that differs in anything but whitespace is refused."""
    out_dir.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(HEADER + (CLEAN_HEADER if zh_clean else ()))
    for name in sorted(names or (d.name for d in registry.docs())):
        text, broken, added = fulltext(name, width, zh_clean)
        extra: tuple = ()
        if zh_clean:
            check = clean_check(fulltext(name, width)[0], text)
            if not check["same_apart_from_whitespace"]:
                raise AssertionError(f"{name}: the clean text differs from the plain one beyond whitespace")
            extra = tuple(check[k] for k in CLEAN_HEADER)
        path = out_dir / f"{name}.txt"
        tmp = path.with_suffix(".txt.tmp")
        tmp.write_text(text)
        os.replace(tmp, path)
        w.writerow((name, path.name, registry.get(name).pages, count_tokens(text), len(text),
                    text.count("\n"), broken, added, hashlib.sha256(text.encode()).hexdigest()) + extra)
    index = out_dir / "index.csv"
    index.write_text(buf.getvalue())
    return index


_SENTENCE = re.compile(r"(?<=[.;:!?])(\s+)")
_TOKEN = re.compile(r"\S+\s*|\s+")
_CJK_END = re.compile("(?<=[。！？；])")  # Chinese sentence ends: a cut needs no whitespace after them
_HAS_CJK = re.compile(f"[{CJK}]")


def _units(line: str) -> list[str]:
    bits = _SENTENCE.split(line)
    units = [bits[i] + (bits[i + 1] if i + 1 < len(bits) else "") for i in range(0, len(bits), 2)]
    return [u for unit in units for u in (_CJK_END.split(unit) if _HAS_CJK.search(unit) else [unit]) if u]


def _tokens(unit: str, width: int) -> list[str]:
    """Whitespace-separated tokens; a token with Chinese characters that is longer than `width` (Chinese
    has no spaces to cut at) is split into pieces of `width` characters."""
    out = []
    for tok in _TOKEN.findall(unit):
        if len(tok.rstrip()) > width and _HAS_CJK.search(tok):
            out += [tok[i:i + width] for i in range(0, len(tok), width)]
        else:
            out.append(tok)
    return out


def _len(text: str) -> int:
    return len(text.rstrip())


def fold_line(line: str, width: int = FOLD_WIDTH) -> list[str]:
    if len(line) <= width:
        return [line]
    parts, cur = [], ""
    for unit in _units(line):
        if _len(cur + unit) <= width:
            cur += unit
            continue
        if cur:
            parts.append(cur)
            cur = ""
        for tok in _tokens(unit, width):
            if _len(cur + tok) <= width or not cur:
                cur += tok
            else:
                parts.append(cur)
                cur = tok
    if cur:
        parts.append(cur)
    return parts


def fold_text(text: str, width: int = FOLD_WIDTH) -> tuple[str, int]:
    """(folded text, line breaks added); refuses a line that would not come back unchanged."""
    out, added = [], 0
    for line in text.split("\n"):
        pieces = fold_line(line, width)
        if "".join(pieces) != line:
            raise AssertionError(f"line does not come back: {line[:80]!r}")
        out += pieces
        added += len(pieces) - 1
    return "\n".join(out), added


FOLD_HEADER = ("file", "lines_in", "lines_out", "breaks_added", "longest", "over_width", "over_500", "src_sha256", "sha256")


def fold_dir(src: Path, out: Path, width: int = FOLD_WIDTH) -> Path:
    """Fold every .txt of src into the new directory out (refused if it exists), with an index.csv."""
    if out.exists():
        raise FileExistsError(out)
    tmp_dir = out.with_name(out.name + ".tmp")
    tmp_dir.mkdir(parents=True)
    rows = []
    for f in sorted(src.glob("*.txt")):
        text_in = f.read_text()
        text, added = fold_text(text_in, width)
        marks = [x for x in text.split("\n") if x.startswith("=== PAGE ")]
        if text.replace("\n", "") != text_in.replace("\n", "") or marks != [x for x in text_in.split("\n") if x.startswith("=== PAGE ")]:
            raise AssertionError(f"{f.name}: folding changed the text")
        (tmp_dir / f.name).write_text(text)
        lines = text.split("\n")
        rows.append((f.name, text_in.count("\n") + 1, len(lines), added, max(map(len, lines)),
                     sum(len(x) > width for x in lines), sum(len(x) > 500 for x in lines),
                     hashlib.sha256(text_in.encode()).hexdigest(), hashlib.sha256(text.encode()).hexdigest()))
    with open(tmp_dir / "index.csv", "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(FOLD_HEADER)
        w.writerows(rows)
    tmp_dir.rename(out)
    return out / "index.csv"
