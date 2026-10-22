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

Much of Vestigium's reading approach is shared with the projects above: PageIndex also points to pages and runs locally, NavTree also builds its index without model calls, and DeepRead also gives an agent tools that read by address. What we can point to is mainly how Vestigium was tested. None of this is a claim of better results.

- **Tested inside a real agent app, against fair baselines, with the method fixed in advance.** We measured Vestigium in Claude Code, with the app's own prompt, tools, limits and caching. The baselines were the app's own ways of reading: the whole text with its built-in search, and the PDF itself. Our first text baseline lost content because Claude Code hides long lines; we fixed that before the main test. The questions were new, the analysis was fixed in advance, and the numbers were recomputed independently. See [Methods](METHODS.md).
- **Runs locally.** Parsing, indexing and serving all happen on your machine; the only model calls are the ones your agent makes.
- **Reads by address.** The agent works through six tools that search and read sections, pages, tables and figures by short addresses, and every passage carries its PDF page.

## What we have not done

- Run any of these projects on our questions, or Vestigium on their benchmarks.
- Compared index-building cost or time with them.
- Tested Vestigium with non-Claude models.

If you run such a comparison, we would like to hear about it.
