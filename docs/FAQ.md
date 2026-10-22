# Limits and FAQ

[中文](FAQ_ZH.md) · [README](../README.md)

## Known limits

- **Tested narrowly.** The main test used only long English public reports (company filings, government reports, standards, research papers, NASA technical reports), only in Claude Code, and only with Claude models. Chinese had one small trial.
- **Weak on figures with small models.** On questions that need a chart or diagram, Haiku with Vestigium scored 33%. The `region` tool gives the model a picture, but a small model often misreads it. Opus was not measured on these questions.
- **Chinese is experimental.** On 15 Chinese development questions, Haiku with Vestigium scored 96.0% against 96.5% for the whole Chinese text, at about half the cost. All setups scored above 96%, so these questions were too easy to show differences on hard ones. See [Results](RESULTS.md#chinese-experimental).
- **Parsing sets the ceiling.** Vestigium can only serve what Docling extracted. Scans, rotated pages, complex tables and unusual layouts can lose or misplace text. Check the quality report of each new PDF with `vestigium ingest qc NAME`. Known weak spots: text rotated by 90° on scanned pages cannot be read (the report marks such pages `no_text`); OCR can turn some traditional Chinese characters into simplified ones on scanned pages; Docling occasionally treats a whole page as one table; and table structure errors occur in every Docling version we tried.
- **Accuracy is "not worse", not "better".** Against giving the model the whole text, Vestigium was not worse by more than 5 points and cost about half. It was not shown to be more accurate.
- **Graded by AI.** The test answers were graded by Claude Opus agents. A human spot check is still to come.

## Questions

### Which model should I use?

If accuracy comes first, Claude Opus with Vestigium: it was near the ceiling on our 27 hard text questions. Opus with the whole text scored 99.6% there too, so this test cannot tell the two apart. If cost comes first, Claude Haiku with Vestigium: about 92% on the same questions, about 6.5 US cents per question at list price. Other models are experimental.

### Will it save me money?

It depends on how you pay.

- **Per token** (for example an API key): yes, in our test. Haiku with Vestigium cost about half as much as Haiku reading the whole text, and about a quarter as much as Haiku reading the PDF.
- **Per request or per message** (some subscription plans): maybe not. Vestigium saves tokens, and a plan that does not charge by tokens will not pass that saving on. We have not measured this.

Either way, with Haiku Vestigium answered in about half the time of the whole-text setup (with Opus, 0.87 of the time).

### Does it send my documents anywhere?

Vestigium itself runs on your machine. Parsing, indexing and serving are all local, and the server talks to your agent app on the same machine. But the passages your agent reads are sent to whatever model the agent uses, the same as if you pasted them in. If a document must not leave your machine, use a local model. The optional HTTP server has no login: keep it on 127.0.0.1, its default (see [Agent apps](CLIENTS.md)).

### Does it use a language model to build the index?

No. Docling uses its own layout, table and OCR models, which run locally. Everything Vestigium adds on top (sections, page numbers, cross-references, definitions, tables) comes from fixed rules. No summaries are generated, so nothing in the index is reworded.

### Do I need a GPU?

No. Docling runs on a CPU; a GPU makes it faster. On our server (a shared GPU and 24 CPU cores, under load, so treat these as upper bounds):

| Pages | With a GPU | CPU only |
|---|---|---|
| Born-digital text | 0.3–0.7 s per page | about 1.6 s per page |
| Scanned, with OCR filling gaps | 2–6 s per page | 4–7 s per page |

OCR runs on the CPU either way. Building the Vestigium map adds 3–18 s per document. The first conversion also downloads Docling's models (about 0.6 GB). The Python environment with Docling took about 6 GB on our server, most of it PyTorch. On Linux, the `[ingest]` extra installs PyTorch with NVIDIA's CUDA libraries, which have their own licenses; installing a CPU-only build of PyTorch first should avoid them, but we have not tested that setup.

### Does it work offline?

Yes, after one download. The token counter (tiktoken) fetches its vocabulary file, `o200k_base` (about 3.6 MB from `openaipublic.blob.core.windows.net`), the first time it runs, and caches it. On a machine without internet access, copy that file into the cache first and point `TIKTOKEN_CACHE_DIR` at it; otherwise `vestigium mcp` fails the first time it counts tokens. Docling also downloads its models (about 0.6 GB) the first time you process a PDF; run `docling-tools models download layout tableformer rapidocr` while online to fetch them in advance.

### Does it work with scanned PDFs?

Yes, through Docling's OCR, but OCR text can contain errors. Our document set includes scanned NASA reports. Text that came from OCR is marked as such, with a warning that it may contain errors. When it looks wrong, the agent can look at the page itself with `region`.

### Why do answers sometimes miss items?

In our tests, most lost points were items missing from the answer, not wrong facts. In our development runs with Haiku, 30–47% of the lost points were items whose passage had already reached the model. For questions that ask for a list, check the count yourself. Asking for every item explicitly may help, but we have not measured it.

### Should I ask several questions in one session?

For unrelated questions, a new session (or subagent) per question is usually cheaper. In an offline simulation, putting a series of questions into one long session cost about 1.1 to 1.2 times as much at list price (1.2 to 1.5 times the input tokens), because the agent app re-reads the whole conversation on every turn.

### Can I use it in VS Code, Cursor or another app?

Apps that support MCP servers should be able to connect; see [Agent apps](CLIENTS.md). We have tested only Claude Code. Other apps have their own output limits and may handle images differently. If your app has no MCP, or your organization has switched MCP off, export the document as files instead.

### Can I use it with GPT, Gemini or open models?

The server does not depend on Claude, so other models your agent app supports should be able to call it. In a small check, a GPT model used the tools on 5 development questions without errors. That shows the tools work with it, not how accurate it is; other models are experimental.

### Will my results match yours?

Not exactly. Results depend on:

- **The Docling version and OCR engine.** Vestigium pins Docling 2.117.0. Our test documents were parsed with that version on a Mac, using Apple's OCR; `vestigium ingest` uses RapidOCR, so scanned pages come out differently (in our comparison, with far fewer misplaced duplicate lines). Newer Docling versions recognize some tables better, but they replace the text layer of scanned pages with fresh OCR, which garbled typewritten scans in our comparison; that is why we have not moved to them yet.
- **The agent app and its version.** We used Claude Code 2.1.283.
- **The questions.** Ours were hard questions about long reports.
- **Chance.** Two runs of the same question differed in cost by a median of 13–23% in our tests.

[Methods](METHODS.md#reproducing) explains how to rerun our comparison on the development questions provided with the release.

### What license is it under?

The code is under MIT. The questions and answer keys in `benchmark/` are under CC BY 4.0; see [benchmark/LICENSE](../benchmark/LICENSE). The Python dependencies have their own licenses, listed in [THIRD_PARTY_LICENSES.md](../THIRD_PARTY_LICENSES.md). Docling downloads its models at run time; Vestigium does not distribute them, and their licenses are listed under [Models downloaded at run time](../THIRD_PARTY_LICENSES.md#models-downloaded-at-run-time). Among those downloads is a font file (FZYTK.TTF, copyright Founder Corporation) for which we found no license.
