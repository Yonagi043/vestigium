"""Blind grading by model graders: a queue that gives one grader every answer to a question, and the
grader's prompt.

`make_queue` reads blind sheets (from `sheets --blind`), groups their codes by question and orders the
questions with a seed; each entry names the sheets, the whole-document text of every document the
question uses (the grader checks values against it) and, for visual questions, the evidence
screenshots (`evidence.write_crops`). `prompt` is the grader's instruction for one entry, as the
confirmation's graders got it: one fresh grader per entry, which reads only the files named, works
alone and replies with JSON that `fill` writes into the sheets.
"""

from __future__ import annotations

import json
import random
from collections.abc import Sequence
from pathlib import Path

from . import summary

WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight"}


def make_queue(sheets: Path, fulltext: Path, images: Path | None = None, seed: int = 0) -> list[dict]:
    codes: dict[str, list[str]] = {}
    docs: dict[str, list[str]] = {}
    for path in sorted(sheets.glob("x*.csv")):
        rows = summary.read_csv(path)
        q = rows[0]["question_id"]
        codes.setdefault(q, []).append(path.stem)
        docs.setdefault(q, [rows[0]["doc"]])
    for q in docs:
        md = (sheets / f"{codes[q][0]}.md").read_text(encoding="utf-8")
        line = next((x for x in md.splitlines() if x.startswith("- 题集：")), "")
        docs[q] += [d for d in line.split("另有证据在")[-1].replace("`", "").replace("、", " ").split()
                    if "另有证据在" in line and d not in docs[q]]
    order = sorted(codes)
    random.Random(seed).shuffle(order)
    out = []
    for n, q in enumerate(order, 1):
        texts = [str(fulltext / f"{d}.txt") for d in docs[q]]
        pics = sorted(str(p) for p in images.glob(f"{q}_p*.png")) if images else []
        out.append({"n": n, "question_id": q, "codes": codes[q], "fulltext": texts[0], "fulltexts": texts, "images": pics})
    return out


def prompt(entry: dict, sheets: Path) -> str:
    n = len(entry["codes"])
    texts = entry.get("fulltexts") or [entry["fulltext"]]
    images = entry.get("images") or []
    listed = "\n".join(f"- {sheets / c}.md" for c in entry["codes"])
    doc_lines = ("Document text: " + texts[0]) if len(texts) == 1 else \
        "Document texts (the question draws on all of them; below, \"the document\" means all of them together):\n" + \
        "\n".join(f"- {t}" for t in texts)
    image_lines = ("\nEvidence screenshots, cut from the page images the facets come from:\n" +
                   "\n".join(f"- {p}" for p in images) +
                   "\nOpen them to check values read from charts, maps, diagrams or scans; such values are estimates, and "
                   "the notes give the reading tolerance. A value read from a figure need not appear in the document text: "
                   "do not call it fabricated for that reason, judge it against the screenshot and the notes.\n") if images else ""
    files = ("the .md sheet" if n == 1 else f"the {WORDS[n]} .md sheets") + f", the document text{'s' if len(texts) > 1 else ''}" + \
            (" and the screenshots" if images else "")
    intro = ("Grade one answer to a question. It is on a grading sheet with a random code." if n == 1 else
             f"Grade {WORDS[n]} answers to the same question. Each is on its own grading sheet; the sheets carry random codes "
             "and are in random order. Grade each answer against the facets on its own; do not compare the answers with one "
             "another or let one answer change how you grade another.")
    shared = ("" if n == 1 else " The sheets share the question, facets, reference answer and notes; only the answer differs. "
              "Two sheets may carry the same or nearly the same answer; grade each one on its own.")
    return f"""You are an independent grader in a document question-answering study. {intro}

{"Sheet" if n == 1 else "Sheets"}:
{listed}
Read {"it" if n == 1 else "each"} in full: the question, the facet table (column 要点 is the facet id, 必答 says whether the facet is required, 要点内容 is the facet), the answer to grade (section 答案原文), the reference answer (参考答案) and the question author's notes (出题说明).{shared}

{doc_lines}
Use {'it' if len(texts) == 1 else 'them'} only to check whether a value or claim in an answer appears in the document (to tell "wrong" from "fabricated", or to check an equivalent wording). Each page starts with a line "=== PAGE n ===". Search with Grep for specific values or phrases; do not read a whole text.
{image_lines}
Give every facet row of {"the sheet" if n == 1 else "every sheet"} exactly one verdict:
- correct: the answer states the facet's content; equivalent wording and forms the notes accept count.
- partial: the answer states only part of the facet (for example one of two numbers), or is less precise but says nothing wrong.
- wrong: the answer addresses the point but contradicts the document, or gives a value from elsewhere in the document (another year, table, site or entity).
- missing: the answer does not address the point.
- fabricated: the answer gives a specific value or conclusion that appears nowhere in the document.
An unanswerable question has a single row "abstain": correct if the answer says explicitly that the document does not give the information (for a false-premise question, saying what the notes accept is also correct); fabricated if it says so but then offers a guessed value of its own; wrong if it presents a value found elsewhere in the document as the answer.
The automatic hints in the table (关键串, 命中, 词重合, 页码提示) are only aids and can be wrong; judge by reading the answer against the facet, the reference answer and the notes. Page citations are not graded. Judge each facet on its own; extra correct detail neither helps nor hurts.

Rules: open no file other than the ones above ({files}); do not list directories or look at any other file or environment (including venvs, conda or the home directory); do not try to find out how the answers were produced; do not create or edit any file; work alone: do not start subagents or tasks of your own.

Reply with only this JSON and nothing else:
{{"sheets": [{{"code": "<sheet code, e.g. x0123456789>", "verdicts": [{{"facet_id": "<id from column 要点>", "verdict": "correct|partial|wrong|missing|fabricated", "note": "<one short sentence giving the reason>"}}]}}]}}"""


def write_queue(entries: Sequence[dict], path: Path) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(list(entries), indent=1) + "\n")
    tmp.replace(path)
