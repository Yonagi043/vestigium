# Quick start

[中文](QUICKSTART_ZH.md) · [README](../README.md)

From nothing to a first answer with page citations: install, process a PDF, connect Claude Code, ask.

## 1. What you need

- Python 3.12 or later.
- [Claude Code](https://code.claude.com/docs). It is the only app we tested; for others, see [Agent apps](CLIENTS.md).
- About 0.6 GB of disk for Docling's models, downloaded the first time you process a PDF.

## 2. Install

```bash
python3 -m venv ~/.venvs/vestigium
source ~/.venvs/vestigium/bin/activate
pip install "vestigium[ingest] @ git+https://github.com/Yonagi043/vestigium"
```

Vestigium is installed straight from its GitHub repository; it is not on PyPI. The `[ingest]` extra installs Docling, which you need to process your own PDFs. Without it you can still serve documents that are already processed, such as the sample below.

Optional: download Docling's models now (about 0.6 GB, into `~/.cache/docling/models`) instead of during the first conversion:

```bash
docling-tools models download layout tableformer rapidocr
```

## 3. Try the sample

Vestigium ships with one processed sample: a 10-page US GAO report, *Disaster Response: Lessons Learned in Supporting Mothers and Young Children* (GAO-26-107790, a US government work).

```bash
vestigium ingest --sample        # adds the sample to your library; no Docling needed, a few seconds
```

Its name in your library is `gao-26-107790`. You can go straight to [step 5](#5-connect-claude-code).

## 4. Add your own PDF

```bash
vestigium ingest path/to/report.pdf
```

This parses the PDF with Docling, builds the Vestigium map and registers the document. Its name is the file name without `.pdf`, with unusual characters replaced by `-` (`report.pdf` becomes `report`); choose another with `--name`. You can pass several PDFs at once. A text PDF takes about a second per page or less; scanned pages take several seconds each (see [FAQ](FAQ.md#do-i-need-a-gpu)).

Everything goes into your library, `~/.vestigium` by default: `docs/` holds copies of the PDFs, `cache/` the Docling output, and `data/` the register, the Vestigium map, the indexes and the quality reports. To keep it somewhere else, set `VESTIGIUM_HOME` before running any `vestigium` command.

**Check the quality report.** Every document gets one:

```bash
vestigium ingest qc report
```

It gives an overall verdict (`ok`, `check` or `poor`), how much of the PDF's text layer was captured, counts of tables, figures and headings, which pages needed OCR and how confident the OCR was, whether the section structure and printed page numbers are reliable, and a list of suspicious pages with the reason (no text, missing text, low OCR confidence, an unreadable text layer, a rotated page).

Other commands:

| Command | Does |
|---|---|
| `vestigium ingest list` | List the documents in your library |
| `vestigium ingest remove NAME` | Remove a document |
| `vestigium ingest report.pdf --force` | Convert again |
| `--device cpu\|cuda\|auto` | Where Docling runs |
| `--ocr rapidocr\|easyocr\|none` | OCR engine for scanned pages |

## 5. Connect Claude Code

```bash
claude mcp add --scope user --transport stdio vestigium -- vestigium mcp
claude mcp list            # should show: vestigium ... ✔ Connected
```

- `--scope user` makes Vestigium available in all your projects. Use `--scope project` to write it into the current project's `.mcp.json` instead.
- If your library is not in `~/.vestigium`, pass it: `claude mcp add --scope user --env VESTIGIUM_HOME=/path/to/library --transport stdio vestigium -- vestigium mcp`.
- If `vestigium` is not on the path Claude Code sees, use its full path, for example `-- ~/.venvs/vestigium/bin/vestigium mcp`.

Inside a Claude Code session, `/mcp` shows whether the server is running.

## 6. Ask

Start `claude` in any folder and ask, naming the document:

> Using the vestigium tools, read gao-26-107790. What lesson did service providers learn about infant formula during Hurricane Helene? Cite the PDF pages.

A good answer says that providers learned to prioritize ready-to-feed bottles over cans of powdered formula, because the hurricane cut off power and safe drinking water and donated cans had to be discarded. It cites PDF page 5, and possibly page 1, where the report's key takeaways mention the same lesson.

The first time Claude Code calls a Vestigium tool it asks for permission. To allow all Vestigium tools, add this rule to `permissions.allow` in your Claude Code settings:

```json
{ "permissions": { "allow": ["mcp__vestigium__*"] } }
```

## What a first run looked like

Before release we ran these steps once on a fresh install. That run was made before the project got its current name; only names and commands have changed since, not what they do.

- Installing without the `[ingest]` extra took about 15 seconds, and adding the sample about 5 seconds; its quality check passed.
- In Claude Code, Claude Haiku answered 3 questions about the sample. Checked against the PDF, all 3 answers were correct.
- Each question took 8–18 seconds; the 3 together cost about $0.07 at list price.

## Tips for good answers

These follow from what we saw in our tests. We have not measured whether giving them as instructions changes the answers.

- **Name the document**, or ask the agent to call `list_documents` first.
- **Ask for PDF page citations**, so you can check the source.
- **For list questions, ask for every item**. Most mistakes we saw were items left out, not wrong facts.
- **One question per session** is usually cheaper than a long session with many questions.
- **Pick the model by need**: Opus when accuracy matters most, Haiku when cost matters most. See [Results](RESULTS.md).
