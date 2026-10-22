"""One document's compact view (task F) as text files for a shell's own Read/Grep (G1).

The text is rendered once, as tglw.views.api.read renders it (page marks, section heads, tables with
their column legend, D1's missing words), without its caps. It is then cut into files of about
file_tokens at the strongest boundaries -- a top-level section start, then deeper section starts,
then pages, then blocks -- and neighbouring small files are joined again. Each line is folded to at
most fold.WIDTH characters. Tables stay in the text (replay: moving them to files of their own, or
copying them there, loses Grep context and doubles hits); optionally a table over table_file_tokens is
moved to tables/<addr>.md and its place in the text names that file.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tglw.common.text import count_tokens
from tglw.tables.loose import render as render_loose
from tglw.views.docview import DocView, Segment, short_ref
from tglw.views.render import render_loss, render_segment, render_table, squeeze
from tglw.views.switches import V1

from . import fold

FILE_TOKENS = 4000
_PAGE = 50   # boundary strength of a page change (section starts are 1, 2, ... by depth)
_BLOCK = 100


@dataclass
class Chunk:
    """One block as shown (a paragraph going on over a page break stays one chunk)."""
    segs: list[Segment]
    text: str
    sid: str | None  # section of the block; None outside sections (front matter)
    tokens: int = 0
    table_file: str | None = None  # tables/<addr>.md when the table is shown there instead

    @property
    def page(self) -> int:
        return self.segs[0].page


@dataclass
class TextFile:
    name: str
    chunks: list[Chunk]
    lines: list[str] = field(default_factory=list)
    first_line: list[int] = field(default_factory=list)  # 1-based line where each chunk starts

    @property
    def pages(self) -> tuple[int, int]:
        pages = [s.page for c in self.chunks for s in c.segs]
        return min(pages), max(pages)


def segments(v: DocView) -> list[Segment]:
    """Every segment of the document in reading position (page, header/body/footer, order); what is not
    shown is left out while rendering, as read does."""
    return sorted((s for b in v.doc.blocks for s in v.block_segments(b)), key=v.place)


def _sid(v: DocView, seg: Segment) -> str | None:
    b = seg.block
    sid = v.section_heading.get(b.ref) or v.section_of(b)
    return None if sid and v.sections[sid].get("kind") == "root" else sid


def render_chunks(v: DocView, segs: list[Segment], table_file_tokens: int | None = None) -> list[Chunk]:
    """The body loop of tglw.views.api.read, uncapped, one chunk per block (with read's switches: a table
    Docling did not recognise, rebuilt by task D2, is shown where its first figure is); a table over
    table_file_tokens (None: none) is replaced by a line naming its own file."""
    out: list[Chunk] = []
    page: int | None = None
    section: str | None = None
    last: Segment | None = None
    loose, rebuilt = v.loose_tables if V1["loose_tables"] else ({}, set())
    for seg in segs:
        b = seg.block
        loose_table = loose.get((seg.page, b.ref))
        if loose_table is None and ((seg.page, b.ref) in rebuilt or not v.visible(b, seg.page)):
            continue
        if last is not None and b.ref == last.block.ref and seg.page != last.page and b.kind == "text" and out:
            out[-1].text += f" [p{seg.page}] {squeeze(seg.text)}"
            out[-1].segs.append(seg)
            page, last = None, seg
            continue
        head = []
        if seg.page != page:
            head.append(f"== {v.page_tag(seg.page)} ==")
        opens = v.section_heading.get(b.ref)
        sid = _sid(v, seg)
        heading_as = None
        if sid and sid != section:
            if opens:
                heading_as = sid
            else:
                head.append(f"## {sid} · {v.section_path(sid)} (cont.)")
        elif opens:
            heading_as = sid
        table_file = None
        if b.kind == "table":
            body = render_table(v, b)
            if table_file_tokens is not None and count_tokens(body) > table_file_tokens:
                table_file = f"tables/{short_ref(b.ref)}.md"
                body = f"{body.split(chr(10))[0]} shown in {table_file} ({_tok(count_tokens(body))} tokens)"
        else:
            body = render_segment(v, seg, heading_as=heading_as)
        if loose_table is not None:
            shown = not loose_table["restored"] and v.visible(b, seg.page)
            body = render_loose(loose_table) + ("\n" + body if shown else "")
        if body.strip(" …") == "":
            continue
        lines = head + [body]
        if b.kind != "table":
            lines += [render_loss(x) for x in v.losses.get(b.ref, []) if seg.page == b.page]
        out.append(Chunk([seg], "\n".join(lines), sid, table_file=table_file))
        page, section, last = seg.page, sid or section, seg
    for c in out:
        c.tokens = count_tokens(c.text)
    return out


def _tok(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def chain(v: DocView, sid: str | None) -> list[str]:
    """Section ids from the top-level section down to sid (empty outside sections)."""
    out = []
    while sid and sid in v.sections and v.sections[sid].get("kind") != "root":
        out.append(sid)
        sid = v.sections[sid].get("parent")
    return out[::-1]


def boundaries(v: DocView, chunks: list[Chunk], by_section: bool) -> list[int]:
    """Strength of the boundary before each chunk (lower is stronger; the first is 0): the depth at
    which its section path first differs from the previous chunk's, else a page or block change."""
    out = [0]
    for prev, cur in zip(chunks, chunks[1:]):
        a, b = chain(v, prev.sid), chain(v, cur.sid)
        if by_section and a != b:
            out.append(next((i + 1 for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)) + 1))
        else:
            out.append(_PAGE if cur.page != prev.page else _BLOCK)
    return out


def cut(tokens: list[int], strength: list[int], limit: int) -> list[tuple[int, int]]:
    """[start, end) ranges of at most limit tokens, cut at the strongest boundaries first (a single
    chunk over the limit stays whole), then neighbouring ranges joined while within limit."""
    def split(lo: int, hi: int) -> list[tuple[int, int]]:
        if hi - lo == 1 or sum(tokens[lo:hi]) <= limit:
            return [(lo, hi)]
        best = min(strength[lo + 1:hi])
        cuts = [lo] + [i for i in range(lo + 1, hi) if strength[i] == best] + [hi]
        return [r for a, b in zip(cuts, cuts[1:]) for r in split(a, b)]

    out: list[tuple[int, int]] = []
    for lo, hi in split(0, len(tokens)):
        if out and sum(tokens[out[-1][0]:hi]) <= limit:
            out[-1] = (out[-1][0], hi)
        else:
            out.append((lo, hi))
    return out


def _file_name(i: int, width: int, chunks: list[Chunk], by_section: bool) -> str:
    """NNN-<address>: the first section it holds, else its pages."""
    sid = next((c.sid for c in chunks if c.sid), None) if by_section else None
    if sid:
        return f"{i:0{width}d}-{sid}.md"
    pages = [s.page for c in chunks for s in c.segs]
    first, last = min(pages), max(pages)
    return f"{i:0{width}d}-p{first}.md" if first == last else f"{i:0{width}d}-p{first}-{last}.md"


def layout(v: DocView, chunks: list[Chunk], by_section: bool, file_tokens: int = FILE_TOKENS,
           width: int = fold.WIDTH) -> list[TextFile]:
    """The chunks cut into files, each rendered to folded lines; a file starting inside a page or a
    section repeats the page mark and the section head ("(cont.)"), as lines of its first chunk."""
    ranges = cut([c.tokens for c in chunks], boundaries(v, chunks, by_section), file_tokens)
    digits = max(2, len(str(len(ranges))))
    files = []
    for i, (lo, hi) in enumerate(ranges, 1):
        f = TextFile(_file_name(i, digits, chunks[lo:hi], by_section), chunks[lo:hi])
        first = f.chunks[0]
        if not first.text.startswith("== "):
            f.lines.append(f"== {v.page_tag(first.page)} ==")
        if first.sid and not any(x.startswith(f"## {first.sid} ") for x in first.text.split("\n")):
            f.lines.append(f"## {first.sid} · {v.section_path(first.sid)} (cont.)")
        for c in f.chunks:
            f.first_line.append(len(f.lines) + 1 if f.first_line else 1)
            f.lines += fold.fold(c.text, width)
        files.append(f)
    return files
