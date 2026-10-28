# Similar projects

[中文](RELATED_ZH.md) · [README](../README.md)

Several projects help language models read long documents through a tree or a hierarchy instead of flat chunks. This page describes a few of them in their own terms, and where Vestigium differs in design and in how it was tested.

**We have not run any of these projects on our questions, nor Vestigium on their benchmarks.** Their published numbers come from other benchmarks and setups. Nothing here says that Vestigium is more accurate or cheaper than they are.

Sources were checked on 22 October 2026.

## Overview

| Project | How the structure is built | Who walks it at question time | Unit of reference | How it was evaluated |
|---|---|---|---|---|
| [PageIndex](https://github.com/VectifyAI/PageIndex) | A tree from the document's layout; a language model summarizes and refines it | A language model reasons over the tree | Pages (local mode); blocks (hosted Cloud) | Vendor reports on FinanceBench and its own benchmark |
| [RAPTOR](https://arxiv.org/abs/2401.18059) | Text chunks are embedded, clustered and summarized by a language model, bottom up | Embedding similarity; no model navigation | Text chunks | NarrativeQA, QASPER, QuALITY |
| [NavTree](https://arxiv.org/abs/2610.06902) | A fixed balanced tree over chunks, with no model calls | A lexical and dense search walk; no model navigation | Text chunks | QuALITY, QASPER, NarrativeQA and three multi-hop sets |
| [DeepRead](https://arxiv.org/abs/2602.05014) | OCR to Markdown; headings become nodes; every paragraph gets a coordinate | A language-model agent with search and read-section tools | Section and paragraph coordinates | FinanceBench, its own ContextBench, multi-document versions of QASPER and SyllabusQA |
| [MemWalker](https://arxiv.org/abs/2310.05029) | A tree of summary nodes written by a language model | The model navigates the tree | Text segments | Long-text question answering sets |
| **Vestigium** | Docling layout and fixed rules: sections, printed page numbers, cross-references, definitions, tables. No model calls, no embeddings | The user's own agent, through six MCP tools | PDF pages, with printed page numbers | Inside Claude Code, against the whole text and the raw PDF, with a pre-registered method |

## Notes on each

- **PageIndex** (VectifyAI, 2025; MIT license) calls itself "vectorless, reasoning-based RAG". Per its README, "the tree structure itself is extracted from the document layout without an LLM; the index model only summarizes and refines it." The open-source package handles text-based PDFs and runs on your machine with your own model key. The hosted Cloud version adds OCR, image understanding and an MCP server. The README reports 98.7% accuracy on FinanceBench; that is the vendor's own figure.
- **RAPTOR** (Sarthi et al., ICLR 2024) builds a tree by "recursively embedding, clustering, and summarizing chunks of text". Retrieval picks nodes by embedding similarity, either walking the tree or searching all levels at once. It is a retrieval pipeline rather than an agent, and the tree follows topic clusters rather than the document's own sections.
- **NavTree** (Jayraj, Goyal and Goyal, arXiv preprint 2610.06902, 2026) asks whether the summaries are needed at all. It builds a deterministic tree with "zero language-model calls at indexing" and compares it with summary trees at matched cost.
- **DeepRead** (Li et al., arXiv preprint, 2026) is the closest in spirit: an agent that first locates and then reads ("locate-then-read"), with paragraph addresses it can cite. It uses an OCR model to recover the document's headings.
- **MemWalker** (Chen et al., 2023) is an early example of a model walking down a tree of summaries to find the part of a long text it needs.

## Where Vestigium differs

Compared with PageIndex and other "vectorless RAG", Vestigium differs in method in three fundamental ways:

- **No summaries, no retrieval pipeline: the agent reads the original text.** PageIndex has a model write summaries for a table-of-contents tree, retrieves over those summaries, and passes the selected text to the answering model. Vestigium builds its index with no model calls: it turns the document's own structure into addresses, and the agent answering the question reads the original text itself. Search is one step it can take or skip.
- **Citations down to the document's own units.** PageIndex's open-source version cites pages (its hosted version, blocks). Vestigium pins down paragraphs, tables, figures, footnotes and printed page numbers, goes straight to the document's own numbered items such as `Note 7` or `AC-2(3)`, and can crop any box on a page.
- **Reading you can see and steer.** In PageIndex, how a document gets read is model reasoning you cannot see. Vestigium formalizes reading as a language, the V language (the Vestigium reading language), built from 7 blocks and 5 kinds of logic. Every reading becomes a trace, and how to read becomes rules the tool carries out: edit, undo or reset them, and preview their effect on past readings first. See [The reading language](LANGUAGE.md).

Also:

- **Tested inside a real agent app, against fair baselines, with the method fixed in advance.** We measured Vestigium in Claude Code, with the app's own prompt, tools, limits and caching. The baselines were the app's own ways of reading: the whole text with its built-in search, and the PDF itself. Our first text baseline lost content because Claude Code hides long lines; we fixed that before the main test. The questions were new, the analysis was fixed in advance, and the numbers were recomputed independently. See [Methods](METHODS.md).
- **Runs locally.** Parsing, indexing and serving all happen on your machine; the only model calls are the ones your agent makes.

## Compare with us

We have not yet run these projects on the same questions. Our benchmark package is public (see [Methods](METHODS.md)); run the comparison and tell us what you find.
