# Vestigium public development questions

[中文](README_ZH.md) · [Reproducing the comparisons](REPRODUCE.md)

Questions, answer keys and evidence that we used while developing Vestigium, a structured reader for long PDFs that AI agents use through MCP tools. They let you rerun our development comparisons with your own Claude Code and model access, or test another reader on the same material.

The questions are about public documents: U.S. GAO reports, NIST special publications, NASA technical reports, arXiv papers, Apple filings and reports, and Hong Kong Exchange filings. **The PDFs are not included.** `documents.json` gives each document's public source and the SHA-256 of the file the questions were written against.

The questions of our formal confirmation are not in this package. They stay private, so that published results are not judged on questions anyone could have tuned for.

## What is in it

| File | What |
|---|---|
| `questions/*.jsonl` | One question per line: question, answer points (facets) with their evidence, reference answer, notes |
| `documents.json` | Every document the questions use: name, file, pages, SHA-256, public URL, our download check. `vestigium eval fetch-docs` reads it |
| `documents.csv` | The same as a table |
| `REPRODUCE.md` | How to rerun the development comparisons, and how the formal confirmation was run |
| `selfcheck.json` | Scan of this package for private paths, keys and confidential question ids: 0 hits |
| `MANIFEST.json` | Counts and the SHA-256 of every file |

## The question sets

| Set | Questions | Answerable | Required answer points | Need 2 documents | Documents |
|---|---|---|---|---|---|
| `old57` | 57 | 52 | 309 | 0 | 13 |
| `dev` | 15 | 13 | 61 | 0 | 7 |
| `dev2` | 25 | 22 | 125 | 0 | 10 |
| `dev3` | 10 | 9 | 60 | 2 | 9 |
| `vis_dev` | 15 | 14 | 43 | 0 | 12 |

- `old57`: 57 test questions we used in a comparison before the one on the project's Results page; they were then released for development, so they are no longer unseen. The numbers of that earlier comparison are not on the Results page.
- `dev`, `dev2`, `dev3` are text questions. `dev3` has the most answer points per question (about 7) and two questions that need two documents.
- `vis_dev` questions need a figure, chart, map or scanned table: the answer is in the page image, not in the PDF's text.
- Unanswerable questions (the document does not give the answer, or the question has a false premise) test whether a reader admits it.
- All sets together use 30 documents.

These questions were seen and used during development, and they have fewer answer points per question than our confirmation questions (about 6 against about 9). Use them to check a setup or to compare readers, not to make claims about unseen questions.

## Question format

```json
{"id": "...", "set": "dev3", "split": "dev", "doc": "<document name>", "docs": ["<document name>", "..."],
 "question": "...", "types": ["table_cell", "compare"], "hops": 2, "unanswerable": false,
 "facets": [{"id": "f1", "text": "<what a correct answer states>", "required": true,
             "evidence": [{"pdf_page": 25, "printed_page": "22", "ref": "#/tables/4", "quote": "..."}]}],
 "reference_answer": "...", "notes": "<accepted wordings, tolerances, near misses>", "checks": {...}}
```

- `doc` is the question's own document; `docs` also lists other documents its evidence cites. Names are those in `documents.json`.
- A piece of evidence from another document carries its own `doc` (in the questions that need two documents); evidence without `doc` comes from the question's `doc`.
- `pdf_page` counts from the first page of the PDF file (not the printed page number). `printed_page` is the number printed on the page, when there is one.
- `ref` is the Docling item the evidence came from, in our Docling version. Another Docling version may number items differently; the page numbers and quotes do not depend on it.
- Image evidence is `{"page", "bbox": [left, top, right, bottom], "kind": "image", "what", "probes"}`: the box is in PDF points of the page as displayed, origin at the bottom left. `probes` are the values that can only be read from the image.
- `checks` records how each question was checked: the author's own reading, and blind answers by separate agents compared with the key. In `vis_dev`, DI, DT and WT name blind checks given only the page image, only the page text, or the whole document's text. The underlying records are internal and not published.

## Getting the documents

```bash
vestigium eval fetch-docs documents.json --out pdfs --ingest-script ingest.sh
```

This downloads each PDF from its public URL and checks its SHA-256 against `documents.json`. A file that is already in `pdfs/` with the right SHA-256 is kept, so you can download a file in a browser, put it in `pdfs/` under the listed file name and run the command again.

The links to Hong Kong Exchange and Apple files may stop working. A copy from anywhere else will do if its SHA-256 matches `documents.json`: put it in `pdfs/` under the listed file name.

Our check on 2026-10-22 (from our server):

- 25 of the 30 files matched the SHA-256 exactly.
- `nist__NIST.SP.800-63-4`: NIST now serves a file with different bytes. It has the same 96 pages, and the text layer of every page is identical to ours, so the questions still hold. Its SHA-256 is listed as `alt_sha256`, and `fetch-docs` accepts that file too (reported as `alternative`).
- The 4 GAO reports could not be downloaded from our server (HTTP 403 from gao.gov) and are unchecked. The URLs open in an ordinary browser.

The `check` and `check_note` columns of `documents.json` give the result for each file.

## Grading

Each required answer point gets one verdict:

| Verdict | Meaning |
|---|---|
| correct | The answer states the point; equivalent wording and the forms the notes accept count |
| partial | Only part of the point (for example one of two numbers), or less precise but nothing wrong |
| wrong | Addresses the point but contradicts the document, or gives a value from elsewhere in it |
| missing | Does not address the point |
| fabricated | A specific value or conclusion that appears nowhere in the document |

An unanswerable question has a single point: correct if the answer says the document does not give it, fabricated if it then guesses a value anyway. Strict accuracy counts only "correct"; lenient accuracy also counts "partial". Page citations are not graded.

## License

The questions, answer keys and notes we wrote are licensed under CC BY 4.0 (Creative Commons Attribution 4.0 International); see `LICENSE` in this directory. The code of Vestigium, `vestigium eval` included, is under the MIT License in the repository's root `LICENSE`. The excerpts quoted from the documents (the `quote` fields and quotations in the notes), and the documents themselves, remain under their own original terms.
