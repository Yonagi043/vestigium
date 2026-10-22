# Tools

[中文](TOOLS_ZH.md) · [README](../README.md)

Vestigium is an MCP server named `vestigium` with six tools. Every tool returns plain text, except `region`, which also returns an image. Unless noted, the examples below are real outputs on the sample document, a 10-page GAO report, shortened where marked with `…`. The cross-document examples come from a library of 59 public documents.

## The tools

| Tool | Parameters (default) | Returns |
|---|---|---|
| `list_documents` | `filter` (""): part of a name, or a document class | One line per document: name, class, pages, tokens, how reliable its section structure is |
| `outline` | `doc`, `depth` (2), `section` (none) | Section tree: address, number and title, PDF pages (printed pages in brackets), size in tokens, number of figures. `section="s12"` expands one section |
| `search` | `doc`, `query`, `k` (5, at most 10; at least 3 for blocks), `unit` (`block` or `page`), `mode` (`bm25` or `literal`) | Ranked hits: address · page · section path \| matching sentence |
| `read` | `doc`, `addrs` (a list), `max_tokens` (8000, also the maximum) | The text at those addresses, page by page, with section headings |
| `context` | `doc`, `addr`, `max_tokens` (1500, at most 4000), `part` (none) | Where the address sits: section path, parent's opening, sibling and child sections, references out and in, defined terms used, footnotes |
| `region` | `doc`, `target`, `scale` (1.5, from 0.5 to 3.0), `preview` (false) | A PNG picture of a table, figure, block, page or box, plus its text layer. `preview=true` returns only the layout and coordinates |

`doc` can be the full registered name or any part of it that matches only one document, such as `107790` or `800-53r5`.

### `outline`

```
gao-26-107790 · 10 pages (printed 1–10) · 6.0k tokens · structure reliable · …
s1 Why This Matters · p1 · 4
s2 Key Takeaways · p1 · 491
…
s6 What lessons have FEMA and disaster service providers learned about supporting this population? · p5-7 · 1.2k · 1 fig
…
s11 Endnotes · p9-10 · 1.2k
```

### `search`

Normal search ranks passages by keyword relevance (BM25). Section titles also count, and each figure's caption and inner labels are searchable together as one passage:

```
search "lessons learned mothers infants formula" · blocks · top 5 · …
1. t12 · p1 · s2 Key Takeaways | FEMA officials and service providers told us about lessons learned, such as to evacuate children with their families …
…
4. t85 · p5 · s6 What lessons have FEMA and disaster ser… | …Service providers from one organization told us about lessons they learned about prioritizing ready-to-feed bottles over cans of powdered formula …
```

`mode="literal"` finds exact text and counts every match. Use it for numbers, identifiers and "does the document say X at all":

```
4 matches for "ready-to-feed" in 4 blocks · literal · blocks
1. tab0 · p2 · 1× · s4 How have selected disaster service prov… | …Families may also need ready-to-feed formula, which is already in a bottle and…
2. t48 · p4 · 1× · …
```

### `read`

Give several addresses in one call. Output is grouped by page (`== p5 ==`) and section:

```
== p5 ==
## s6 · What lessons have FEMA and disaster ser… (cont.)
FEMA officials and service providers from the 12 local, state, and nonprofit organizations …
· Service providers from one organization told us about lessons they learned about prioritizing ready-to-feed bottles …
== p6 ==
…
next: p6 from t91
```

- A read that hits its size limit ends with `next: …`. Pass that text back as the next address to go on.
- `continues: p151 (s17)` means the section goes on to a page that was not shown.
- Reading a block also shows the rest of its page in the same section, so nearby conditions are not missed. When a short read stops where its section goes on, the next page is added.
- A page right before or after what you read is added when it ranked high in your earlier searches. It is marked `[adjacent pages that match your searches]`. This depends on the session, which every setup has except HTTP with `--new-protocol`; see [Agent apps](CLIENTS.md).

### `context`

Use it before answering, to check the scope and conditions that apply to a passage:

```
context t85 · p5 · 1 blocks
path: s6 What lessons have FEMA and disaster service providers learned about supporting this population?
siblings (11):
  …
refs out (1):
  figure "fig. 4" → t92 p6 s6 (from t91)
```

When the output is cut, it ends with `next: part=…`; call again with that `part`.

### `region`

Returns a picture plus the text layer under it. A picture costs up to about 1,500 tokens, depending on its size. Use `region` when the text layer is not enough: charts, diagrams, scanned pages, or a table whose columns are garbled. `preview=true` lists the blocks on the page with their boxes, without a picture:

```
p5 whole page · p5 · page 612×792 pt · box 0,792,612,0 (l,t,r,b; origin bottom-left)
t79 text 180,754,570,680 programs that can include counseling, case management, and
…
```

## Searching across documents (experimental)

Three additions for working with a whole library. They are off by default until a trial shows they help; turn them on when you start the server.

- **Search every document** (`TGLW_SEARCH_LIBRARY=1`): `search(doc="*", query=…)`. The reply first lists the documents that match best, then the hits, each written `document:address`:

  ```
  search "account management automated audit actions" · library: 59 documents · top 3 · blocks · …
  documents by best match: nist__NIST.SP.800-53r5 · nist__NIST.SP.800-171r3 · … (+41 more with matches)
  1. nist__NIST.SP.800-53r5:t603 · p48 (printed 21) · s30 AC-2 ACCOUNT MANAGEMENT > (4) … | …
  ```

- **Read from several documents at once** (`TGLW_READ_DOC_ADDR=1`): `read(doc="*", addrs=["nist__NIST.SP.800-53r5:AC-2(3)", "800-53B:p31"])`. The document part can be any fragment of its name that matches only one document.
- **List every match** (`TGLW_SEARCH_LIST=1`): `search(doc, query, mode="list")` gives every exact match as addresses only, grouped by section, with counts, for questions such as "which sections mention X". A long list ends with `next: offset=N`; pass `offset=N` to go on.

  ```
  1208 matches for "privacy" in 637 blocks, 297 sections · literal · every match, addresses only
  s4 Abstract: t45 p4 (4×)
  s5 Keywords: t47 p4 (4×)
  …
  ```

## Addresses

| Form | Means | Example |
|---|---|---|
| `s12` | Section, from `outline` | `s6` |
| `p45`, `p45-47` | PDF page or page range, counted from 1 | `p5-6` |
| `t123` | One text block | `t85` |
| `tab4` | A whole table | `tab0` |
| `fig5` | A figure, with its caption | `fig5` |
| `P36` | Printed page number, as printed on the page | `P36`, `Pxii` |
| `P3@Exhibit 10.21` | Printed page within a part, when several parts reuse the same numbers | |
| Numbered names | Notes, items, sections and controls, as the document numbers them | `Note 7`, `Item 7A`, `§2.2`, `AC-2(3)` |
| `p45:l,t,r,b` | A box on a page, in PDF points, origin at the bottom left (for `region`) | `p5:180,131,577,57` |
| `fn: 69`, `fn69`, `fn. 69`, `footnote 69` | The footnote whose text starts with 69. If several footnotes share the number, their addresses are listed to choose from | `fn: 12` in NIST SP 800-53 |
| `document:address` | An address in another document, with `doc="*"` (experimental, `TGLW_READ_DOC_ADDR=1`) | `800-53B:p31` |

Always cite **PDF pages** (`p45`) in answers. Printed page numbers are shown next to PDF pages wherever they differ, but they are less reliable: only a few PDFs label their pages, so Vestigium infers printed numbers from the page text.

## Limiting which documents are served

Set `TGL_ALLOWED_DOCS` to a comma-separated list of registered names. The server then lists and opens only those documents; any other name is refused. This is useful when one library serves several projects, and it is how our tests kept each run to its own documents.

```bash
TGL_ALLOWED_DOCS=gao-26-107790,nist__NIST.SP.800-53r5 vestigium mcp
```

## Output size

- `read`: at most 8,000 tokens and 40,000 characters per call.
- `context`: at most 4,000 tokens.
- `search`: at most 10 hits; a block search always lists at least 3.
- `outline`: at most 60 lines; expand one section with `section` to see more.
- `region`: the text layer under a picture is cut at 2,000 tokens.

These limits keep every reply below the point where Claude Code moves a tool result into a file. Some apps move smaller results into files. Vestigium recognizes them by the name they report when they connect: for VS Code it splits each reply into pieces of at most 8,000 characters (same content), and for GitHub Copilot CLI it keeps each reply under 18,000 bytes and continues with `next:`. See `TGLW_MCP_LIMITS` below and [Agent apps](CLIENTS.md).

## Switches

Switches are environment variables, read when the server starts. Unless noted, `1` turns a switch on and `0` turns it off. The defaults are set in the code; `vestigium mcp` needs no settings to run.

### Settings

The `TGLW_` prefix of most switches, like the import package name `tglw` in `python -m tglw.corpus build` below, comes from an early name of the project.

| Variable | Default | Effect |
|---|---|---|
| `VESTIGIUM_HOME` | `~/.vestigium` | Where your library lives: PDFs, parsed files, indexes, quality reports |
| `TGL_ALLOWED_DOCS` | not set (all documents) | Serve only these documents (comma-separated names) |
| `TGLW_CACHE_MB` | 2048 | Memory budget, in MB, for parsed documents kept in memory. The HTTP server preloads documents up to this budget |
| `TGLW_CORPUS_DB` | `$VESTIGIUM_HOME/data/corpus/corpus.sqlite` | Where the library-wide search index is kept |
| `TGLW_MCP_LIMITS` | `auto` | Output size by app: `auto` chooses from the name the app reports; `off` uses Claude Code's limits for every app; `vscode` or `copilot` force that app's limits. See [Output size](#output-size) |

### On by default

With all of these off and `TGLW_MCP_LIMITS=off`, the tool output on the documents we tested is byte-for-byte that of the version we tested (checked by replaying 1,826 recorded tool calls).

| Variable | Effect |
|---|---|
| `TGLW_VIEWS_FOOTNOTES` | Footnote addresses such as `fn: 69` |
| `TGLW_ZH_ADDRESSES` | Chinese article, chapter and appendix numbers such as 第二十七条, 第三章, 附錄二 work as addresses |
| `TGLW_VIS_FIG_HINT` | `read` marks each figure with `(image: region figN)` |
| `TGLW_VIS_CROP_BOX` | `region` states the page box of the picture it returns |
| `TGLW_VIS_OCR_NOTE` | Text that came from OCR (scanned pages) is marked as possibly containing errors |
| `TGLW_VIS_DRAWINGS` | Drawings that Docling did not cut out as figures are given page coordinates |
| `TGLW_VIS_OUTLINE_FIGS` | `outline` shows how many figures each section has |
| `TGLW_VIS_LITERAL_FIGS_LAST` | Literal search lists matches inside figures after matches in the text |
| `TGLW_ING_QC_NOTES` | The quality verdict from `vestigium ingest` is shown in `list_documents`, `outline` and `read` |
| `TGLW_VIEWS_HEADING_NEXT` † | A search hit that is only a short heading also shows the passage after it |
| `TGLW_SRC_TABLE_NOTES` † | Warns in a table's header when rows may have been merged or a cell holds several numbers, and points to the page image |
| `TGLW_SRC_LOOSE_TABLES` † | Shows tables that Docling did not recognize, rebuilt, where they appear |
| `TGLW_SRC_BOX_FIX` † | `region` and `preview` use the true position of blocks whose box the parser placed wrongly (seen on scanned pages) |
| `TGLW_SRC_DUP_BLOCKS` † | Hides misplaced duplicate lines when the original is visible on the same page |
| `TGLW_SRC_ROTATED_PAGES` † | Hides garbled text-layer blocks on rotated pages |
| `TGLW_ZH_DISPLAY` † | Default `auto`: in Chinese documents, removes stray spaces between Chinese characters and cuts excerpts at Chinese sentence ends. `1` does this for any text with Chinese characters, `0` turns it off |
| `TGLW_SEARCH_CJK_AUTO` † | Chinese documents use a search index built for Chinese text, so Chinese queries find their matches |
| `TGLW_SEARCH_FOLD` † | Chinese and literal search treat traditional and simplified characters as the same; text is still shown as printed |
| `TGLW_VIS_ADAPTIVE_ZOOM` † | `region` picks the picture scale from the smallest text in it (1.5 to 3 times). It also lets every picture be up to 1,568 pixels on the long side instead of 1,200, with the same area limit, so pictures can be larger even when the model sets the scale itself. In a replay of recorded calls, picture tokens rose by about 12% |

The switches without † only add information, or turn an address that used to fail into its content. The switches marked † change what the model is shown, and were switched on only after a check. The six text switches were checked on 65 development questions: with Haiku, this release scored 97.2% against 97.1% for the version we tested, at 0.92 of the cost, and passed all five no-regression rules fixed in advance. The picture scale was checked on 15 development questions that need a figure, where it did no worse than the fixed scale. The three Chinese switches, which act only on Chinese documents, were checked on 15 Chinese development questions; see [Results](RESULTS.md#chinese-experimental). Each question was run once and Haiku's reading of figures varies a lot, so we read both checks as no loss, not as an improvement; see [Results](RESULTS.md#this-release-and-the-tested-version).

The `TGLW_SRC_BOX_FIX`, `TGLW_SRC_DUP_BLOCKS` and `TGLW_SRC_ROTATED_PAGES` switches repair parses made with an older Docling setup. They work only in a library that has the matching repair files, which `vestigium ingest` does not create, so in your own library they have no effect. Documents added with `vestigium ingest` rarely need them anyway: when 6 documents were converted again with the current setup, wrongly placed boxes fell from 574 to 3 and duplicate blocks from 424 to 0.

### Off by default (experimental)

These change what the model is told or shown. They stay off until a trial shows they help.

| Variable | Effect |
|---|---|
| `TGLW_SEARCH_LIBRARY` | `search(doc="*")` searches every document; see [Searching across documents](#searching-across-documents-experimental) |
| `TGLW_READ_DOC_ADDR` | `read`, `context` and `region` accept `document:address` |
| `TGLW_SEARCH_LIST` | `search(mode="list", offset=N)` lists every match as addresses |
| `TGLW_MCP_ANSWER_HINT` | The server's instructions ask the model to answer item by item, with a page for each. In a trial on 21 development questions it did not improve accuracy |
| `TGLW_SRC_RUNNING_HEADERS` | Hides running page headers that the parser mistook for headings, and stops them from splitting sections. After turning it on, rebuild the library index with the same setting: `python -m tglw.corpus build` |
| `TGLW_VIS_TOKEN_NOTE` | Tool descriptions give the approximate token cost of a picture instead of saying pictures are costly. In a trial on 15 figure questions it did not improve accuracy |
| `TGLW_VIS_FIG_LABELS` | Shortens long lists of labels inside figures (can drop information) |
| `TGLW_CJK` | Every document, not just Chinese ones, uses Chinese search (the older switch) |

Chinese support is experimental; see [Results](RESULTS.md#chinese-experimental).

### Command-line options of `vestigium mcp --http`

`--host` (default 127.0.0.1), `--port` (8765), `--preload` (`all`), `--new-protocol` (off). See [Agent apps](CLIENTS.md).
