"""Write one document's export folder: text files, INDEX.md (with sub-indexes when large), TABLES.md,
FIGURES.md, index.json (and tables/ when tables are moved out of the text); and the export root's README.md."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tglw
from tglw.common.text import count_tokens
from tglw.views import api, sidecars
from tglw.views.docview import DocView, short_ref
from tglw.views.render import render_table, squeeze, table_rows

from . import build, fold

FORMAT = "tg-export/1"
INDEX_TOKENS = 6000  # a longer index shows fewer levels and moves each top-level subtree to INDEX-<id>.md
CAPTION_CHARS = 200
TITLE_CHARS = 120  # a section's own title in an index entry; its ancestors' titles are shortened to PATH_PART_CHARS
PATH_PART_CHARS = 40
HOW_TO = [
    "# Read this index first, then Read only the lines you need: each entry ends with file:first-last "
    "(Read that file with offset=first and limit=last-first+1). Grep this folder for words; a hit shows file:line.",
    "# Text files: '== pN ==' starts PDF page N (the printed page follows when it differs); '## sN · title' starts "
    "section sN; a table is a '[tabN rows×cols]' line, a 'cols:' legend and rows of cells split by '|'.",
    "# TABLES.md lists every table and FIGURES.md every figure caption, each with its file:lines.",
]


@dataclass
class Entry:
    depth: int
    text: str
    key: str  # sub-index name part (section id or file stem)


@dataclass
class Place:
    file: str
    first: int
    last: int


def _places(files: list[build.TextFile]) -> list[Place]:
    """Each chunk's file and lines, in chunk order."""
    out = []
    for f in files:
        ends = f.first_line[1:] + [len(f.lines) + 1]
        out += [Place(f.name, a, b - 1) for a, b in zip(f.first_line, ends)]
    return out


def _span(places: list[Place], lengths: dict[str, int]) -> str:
    """'file:a-b', or for a span over several files its first and last part."""
    a, b = places[0], places[-1]
    if a.file == b.file:
        return f"{a.file}:{a.first}-{b.last}"
    names = list(lengths)
    n = names.index(b.file) - names.index(a.file) + 1
    return f"{a.file}:{a.first}-{lengths[a.file]} … {b.file}:1-{b.last} ({n} files)"


def _pages(chunks: list[build.Chunk]) -> tuple[int, int]:
    pages = [s.page for c in chunks for s in c.segs]
    return min(pages), max(pages)


def section_spans(v: DocView, chunks: list[build.Chunk]) -> list[tuple[int, str | None, list[int]]]:
    """(depth, section id, its subtree's chunks) for each section in reading order of its first chunk;
    each run of chunks outside sections as (1, None, chunks)."""
    members: dict[str, list[int]] = {}
    out: list[tuple[int, str | None, list[int]]] = []
    for i, c in enumerate(chunks):
        path = build.chain(v, c.sid)
        if not path:
            if out and out[-1][1] is None and out[-1][2][-1] == i - 1:
                out[-1][2].append(i)
            else:
                out.append((1, None, [i]))
            continue
        for depth, sid in enumerate(path, 1):
            if sid not in members:
                members[sid] = []
                out.append((depth, sid, members[sid]))
            members[sid].append(i)
    return out


def section_entries(v: DocView, spans: list[tuple[int, str | None, list[int]]], chunks: list[build.Chunk],
                    places: list[Place], lengths: dict[str, int]) -> list[Entry]:
    """The section tree in reading order, each section with its whole subtree's pages, tokens and lines;
    text outside sections as '(no section)' entries, with the headings of a run over several files. An entry names its whole section path, so a Grep for
    a parent title also lists the subsections (replay: +5 evidence quotes for the prototype's index)."""
    unsure = (v.side.structure or {}).get("verdict", {}).get("status") != "reliable"
    out = []
    for depth, sid, idx in spans:
        cs = [chunks[i] for i in idx]
        where = (f"{v.page_range_tag(*_pages(cs))} · {build._tok(sum(c.tokens for c in cs))} · "
                 f"{_span([places[i] for i in idx], lengths)}")
        if sid is None:
            key = f"p{cs[0].page}"
            out.append(Entry(depth, f"(no section) · {where}", key))
            if len({places[i].file for i in idx}) > 1:  # a long run outside sections: its headings
                out += [Entry(depth + 1, f"{v.display(squeeze(chunks[i].segs[0].block.text))[:120]} · {v.page_tag(chunks[i].page)} · "
                                         f"{places[i].file}:{places[i].first}", key)
                        for i in idx if chunks[i].segs[0].block.is_heading]
            continue
        flag = " [level?]" if unsure and v.sections[sid].get("confidence") != "high" else ""
        out.append(Entry(depth, f"{sid} {section_title(v, sid)}{flag} · {where}", sid))
    return out


def page_entries(v: DocView, files: list[build.TextFile], places: list[Place]) -> list[Entry]:
    """Without a usable section tree: each file with its pages and tokens, and under it the printed
    table of contents entries (else the headings) that fall in it."""
    first_on: dict[int, Place] = {}
    chunk_place: dict[str, Place] = {}
    i = 0
    for f in files:
        for c in f.chunks:
            first_on.setdefault(c.page, places[i])
            chunk_place.setdefault(c.segs[0].block.ref, places[i])
            i += 1
    toc = [(v.display(e["number"] + " " + e["title"] if e.get("number") else e["title"]), e["pdf_page"])
           for e in (v.side.toc or {}).get("entries", []) if e["kind"] == "section" and e["pdf_page"]]
    marks = [(t, p, first_on.get(p)) for t, p in toc] if toc else \
        [(v.display(b.text), b.page, chunk_place.get(b.ref)) for b in v.doc.headings() if b.ref in chunk_place]
    out = []
    for f in files:
        first, last = f.pages
        tokens = sum(c.tokens for c in f.chunks)
        stem = f.name.removesuffix(".md")
        out.append(Entry(1, f"{f.name} · {v.page_range_tag(first, last)} · {build._tok(tokens)} · {f.name}:1-{len(f.lines)}", stem))
        for title, page, place in marks:
            if place is not None and place.file == f.name:
                out.append(Entry(2, f"{squeeze(title)[:120]} · {v.page_tag(page)} · {place.file}:{place.first}", stem))
    return out


def _render(entries: list[Entry], base: int) -> list[str]:
    return ["  " * (e.depth - base) + e.text for e in entries]


def index_files(entries: list[Entry], name: str, head: list[str], limit: int = INDEX_TOKENS) -> dict[str, list[str]]:
    """INDEX files: all entries if they fit; else as many levels as fit, each top-level entry with deeper
    entries pointing to INDEX-<key>.md, which holds its whole subtree (split again the same way)."""
    base = min((e.depth for e in entries), default=1)
    if count_tokens("\n".join(head + _render(entries, base))) <= limit:
        return {name: head + _render(entries, base)}
    depth = base
    while depth + 1 <= max(e.depth for e in entries) and \
            count_tokens("\n".join(head + _render([e for e in entries if e.depth <= depth + 1], base))) <= limit:
        depth += 1
    out: dict[str, list[str]] = {}
    shown: list[Entry] = []
    groups: list[list[Entry]] = []
    for e in entries:
        if e.depth == base or not groups:
            groups.append([e])
        else:
            groups[-1].append(e)
    for top, *rest in groups:
        hidden = [e for e in rest if e.depth > depth]
        if not hidden:
            shown += [top] + rest
            continue
        sub = f"INDEX-{top.key}.md"
        out.update(index_files(rest, sub, [head[0], f"# subtree of: {top.text}"] + head[1:]))
        shown += [Entry(top.depth, f"{top.text} · {len(rest)} entries in {sub}", top.key)]
        shown += [e for e in rest if e.depth <= depth]
    out[name] = head + _render(shown, base)
    return out


def _caption(v: DocView, ref: str) -> str:
    refs = [c.get("$ref") for c in v.doc.item(ref).get("captions") or []]
    return _cut(v.display(squeeze(" ".join(v.doc.by_ref[r].text for r in refs if r in v.doc.by_ref))), CAPTION_CHARS)


def _cut(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n - 1] + "…"


def section_title(v: DocView, sid: str) -> str:
    """Its ancestors' titles (shortened) and its own: "Volume B > 5 Example Implementation > 5.1 Use Cases"."""
    parent = v.sections[sid].get("parent")
    head = v.section_path(parent, width=PATH_PART_CHARS) if parent else ""
    own = _cut(v.section_label(sid), TITLE_CHARS)
    return f"{head} > {own}" if head else own


def _section_note(v: DocView, sid: str | None) -> str:
    return f"{sid} {v.section_label(sid)}" if sid else "no section"


def table_file(v: DocView, c: build.Chunk, place: Place) -> list[str]:
    b = c.segs[0].block
    body = render_table(v, b)
    cap = _caption(v, b.ref)
    head = [f"# {short_ref(b.ref)} · {v.page_range_tag(min(b.pages), max(b.pages))} · {_section_note(v, c.sid)} · "
            f"{build._tok(count_tokens(body))} tokens · its place in the text: {place.file}:{place.first}"]
    return head + ([f"# caption: {cap}"] if cap else []) + [body]


def table_list(v: DocView, chunks: list[build.Chunk], places: list[Place]) -> list[str]:
    out = [f"# {v.name} · tables in reading order: address · PDF page · size · where it is shown · caption"]
    for c, p in zip(chunks, places):
        b = c.segs[0].block
        if b.kind != "table":
            continue
        grid = table_rows(v, b)
        rows = f"{len(grid)}×{max(map(len, grid), default=0)}"
        where = f"{c.table_file} (from {p.file}:{p.first})" if c.table_file else f"{p.file}:{p.first}-{p.last}"
        cap = _caption(v, b.ref)
        out.append(f"{short_ref(b.ref)} · {v.page_tag(c.page)} · {rows} · {where}" + (f" · {cap}" if cap else ""))
    return out


def figure_list(v: DocView, chunks: list[build.Chunk], places: list[Place]) -> list[str]:
    out = [f"# {v.name} · figures in reading order (images are not exported; captions and inner text are in the "
           "text files): address · PDF page · section · where it is shown · caption"]
    for c, p in zip(chunks, places):
        b = c.segs[0].block
        if b.kind == "picture":
            shown = next((x for x in c.text.split("\n") if x.startswith(f"[{short_ref(b.ref)}")), "")
            inner = shown.strip("[]").partition(": ")[2]
            cap = _caption(v, b.ref) or (f"(no caption; inner text: {_cut(inner, 100)})" if inner else "(no caption)")
            out.append(f"{short_ref(b.ref)} · {v.page_tag(c.page)} · {_section_note(v, c.sid)} · {p.file}:{p.first}-{p.last} · {cap}")
    return out


def _write(path: Path, lines: list[str], width: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(fold.fold("\n".join(lines), width)) + "\n")


def export_doc(name: str, out: Path, file_tokens: int = build.FILE_TOKENS, table_file_tokens: int | None = None,
               width: int = fold.WIDTH, folder: str | None = None) -> dict[str, Any]:
    """Write <out>/<folder or name>/ (replacing an older export there only once the new one is complete)
    and return its index.json content. A short folder name keeps every Grep hit line short."""
    v = api.view(name)
    status = (v.side.structure or {}).get("verdict", {}).get("status")
    by_section = bool(v.sections) and status in ("reliable", "partial")
    chunks = build.render_chunks(v, build.segments(v), table_file_tokens)
    files = build.layout(v, chunks, by_section, file_tokens, width)
    places = _places(files)
    lengths = {f.name: len(f.lines) for f in files}
    spans = section_spans(v, chunks)
    total = sum(c.tokens for c in chunks)
    head = [f"# {name} · {v.doc.page_count} PDF pages · {build._tok(total)} tokens · {len(files)} text files · "
            f"exported by {tglw.NAME} {tglw.VERSION}"] + HOW_TO
    if by_section:
        entries = section_entries(v, spans, chunks, places, lengths)
        head.append("# section · PDF pages (printed) · tokens · file:lines")
    else:
        entries = page_entries(v, files, places)
        head.append("# no reliable section tree: text files by page, with the printed contents (or headings) in each")
    out.mkdir(parents=True, exist_ok=True)
    folder = folder or name
    if (out / folder).exists() and not is_export(out / folder):
        raise FileExistsError(f"{out / folder} exists and is not an export; choose another --out or --as")
    tmp = out / f".{folder}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    for f in files:
        (tmp / f.name).write_text("\n".join(f.lines) + "\n")
    for fname, lines in index_files(entries, "INDEX.md", head).items():
        _write(tmp / fname, lines, width)
    for c, p in zip(chunks, places):
        if c.table_file:
            _write(tmp / c.table_file, table_file(v, c, p), width)
    _write(tmp / "TABLES.md", table_list(v, chunks, places), width)
    _write(tmp / "FIGURES.md", figure_list(v, chunks, places), width)
    meta = manifest(v, chunks, files, places, spans, total, status, width)
    (tmp / "index.json").write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":")) + "\n")
    _replace(tmp, out / folder)
    return meta


def is_export(path: Path) -> bool:
    """Whether path is a folder written by export_doc (which may be replaced)."""
    try:
        return json.loads((path / "index.json").read_text()).get("format", "").startswith("tg-export/")
    except (OSError, ValueError, AttributeError):
        return False


def _replace(tmp: Path, dest: Path) -> None:
    old = dest.with_name(f".{dest.name}.old")
    shutil.rmtree(old, ignore_errors=True)
    if dest.exists():
        dest.rename(old)
    tmp.rename(dest)
    shutil.rmtree(old, ignore_errors=True)


def manifest(v: DocView, chunks: list[build.Chunk], files: list[build.TextFile], places: list[Place],
             spans: list[tuple[int, str | None, list[int]]], total: int, status: str | None, width: int) -> dict[str, Any]:
    """index.json: the files, sections (with their subtree's span), tables and figures, for programs."""
    def block(i: int, c: build.Chunk) -> dict[str, Any]:
        b = c.segs[0].block
        return {"addr": short_ref(b.ref), "ref": b.ref, "page": c.page, "file": places[i].file,
                "line": places[i].first, "last_line": places[i].last}

    sections = []
    for depth, sid, idx in spans:
        if sid is not None:
            a, b = places[idx[0]], places[idx[-1]]
            sections.append({"id": sid, "parent": v.sections[sid].get("parent"), "depth": depth,
                             "label": v.section_label(sid), "pages": list(_pages([chunks[i] for i in idx])),
                             "tokens": sum(chunks[i].tokens for i in idx), "file": a.file, "line": a.first,
                             "last_file": b.file, "last_line": b.last})
    return {
        "format": FORMAT, "doc": v.name, "pdf_pages": v.doc.page_count, "tokens": total, "structure": status,
        "producer": {"name": tglw.NAME, "version": tglw.VERSION, "sidecars": sidecars.versions()},
        "width": width,
        "files": [{"file": f.name, "pages": list(f.pages), "lines": len(f.lines),
                   "tokens": sum(c.tokens for c in f.chunks)} for f in files],
        "sections": sections,
        "tables": [{**block(i, c), "table_file": c.table_file} for i, c in enumerate(chunks)
                   if c.segs[0].block.kind == "table"],
        "figures": [{**block(i, c), "caption": _caption(v, c.segs[0].block.ref)} for i, c in enumerate(chunks)
                    if c.segs[0].block.kind == "picture"],
    }


README_TITLE = "# Documents exported by "


def write_readme(out: Path) -> Path | None:
    """<out>/README.md: how to read an export, and the documents in it (from their index.json). A README.md
    that is not ours is left alone (None)."""
    path = out / "README.md"
    if path.exists() and not path.read_text().startswith(README_TITLE):
        return None
    docs = []
    for p in sorted(out.glob("*/index.json")):
        m = json.loads(p.read_text())
        named = "" if m["doc"] == p.parent.name else f" ({m['doc']})"
        docs.append(f"- `{p.parent.name}/`{named} · {m['pdf_pages']} PDF pages · {build._tok(m['tokens'])} tokens · "
                    f"{len(m['files'])} text files, lines of at most {m['width']} characters")
    lines = [
        f"{README_TITLE}{tglw.NAME} {tglw.VERSION}",
        "",
        "Each folder is one PDF as plain text in reading order, cut into files of a few thousand tokens. "
        "To answer a question about a document:",
        "",
        "1. Read `<folder>/INDEX.md`: its sections with PDF pages, size in tokens, and `file:first-last` lines "
        "(a large tree continues in `INDEX-<id>.md`).",
        "2. Read only those lines (offset = first, limit = last - first + 1), or Grep the folder for exact words "
        "or numbers and Read around the hit.",
        "3. Cite PDF pages from the `== pN ==` lines above the text you use.",
        "",
        "`TABLES.md` lists every table and where it is, `FIGURES.md` every figure with its caption (images are "
        "not exported). `index.json` is for programs.",
        "",
        "## Documents",
        "",
        *docs,
    ]
    tmp = out / ".README.md.tmp"
    tmp.write_text("\n".join(lines) + "\n")
    os.replace(tmp, path)
    return path
