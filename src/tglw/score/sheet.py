"""Per-answer grading sheets: one row per facet with automatic hints and empty verdict columns.

The CSV is the record a grader fills in (column `verdict`); the Markdown shows the same rows next
to the question, the answer, and task H's notes. Unanswerable questions get a single `abstain` row:
saying the document does not give the information is correct, a made-up value is fabricated.
"""

from __future__ import annotations

import csv
import io
import os
from dataclasses import dataclass, field
from pathlib import Path

from .hints import abstains, cited_pages, key_hint, page_hint, stated_values, word_overlap
from .questions import Facet, Question

VERDICTS = ("correct", "partial", "wrong", "missing", "fabricated")
VERDICT_ZH = {"correct": "对", "partial": "部分对", "wrong": "错", "missing": "没写", "fabricated": "编造"}
ABSTAIN = Facet("abstain", "States that the document does not give the information asked for, "
                "without supplying a value of its own.")
COLUMNS = ("run_id", "condition", "question_id", "split", "doc", "facet_id", "required", "facet_text",
           "keys", "keys_found", "key_hint", "words", "page_hint", "verdict", "note")
MD_COLUMNS = ("facet_id", "required", "facet_text", "keys", "keys_found", "key_hint", "words", "page_hint",
              "verdict", "note")


@dataclass
class Sheet:
    run_id: str
    condition: str
    question: Question
    answer: str
    cited: set[int] = field(default_factory=set)
    rows: list[dict[str, str]] = field(default_factory=list)


def _row(sheet: Sheet, facet: Facet) -> dict[str, str]:
    q = sheet.question
    base = {"run_id": sheet.run_id, "condition": sheet.condition, "question_id": q.id, "split": q.split,
            "doc": q.doc or "", "facet_id": facet.id, "required": "yes" if facet.required else "no",
            "facet_text": facet.text, "verdict": "", "note": ""}
    if facet is ABSTAIN:
        values = stated_values(sheet.answer)
        return base | {"keys": "", "keys_found": "; ".join(values[:12]),
                       "key_hint": "abstains" if abstains(sheet.answer) else "no-abstain", "words": "",
                       "page_hint": "values" if values else "no-values"}
    kh = key_hint(facet.text, sheet.answer, sheet.question.question)
    hit, total = word_overlap(facet.text, sheet.answer)
    keys = " · ".join(kh.keys) + (f" (echo: {' · '.join(kh.echo)})" if kh.echo else "")
    return base | {"keys": keys, "keys_found": " · ".join(kh.found),
                   "key_hint": kh.status, "words": f"{hit}/{total}",
                   "page_hint": page_hint(facet, sheet.answer, kh.found, sheet.question.docs)}


def build(run_id: str, condition: str, question: Question, answer: str) -> Sheet:
    sheet = Sheet(run_id, condition, question, answer, cited_pages(answer, question.docs))
    facets = (ABSTAIN,) if question.unanswerable else question.facets
    sheet.rows = [_row(sheet, f) for f in facets]
    return sheet


def to_csv(sheet: Sheet) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, COLUMNS, lineterminator="\n")
    w.writeheader()
    w.writerows(sheet.rows)
    return buf.getvalue()


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def to_markdown(sheet: Sheet) -> str:
    q = sheet.question
    split = {"6x10": "6×10", "dev": "H 开发集", "frozen": "H 冻结集（只在最终验收时打开）"}[q.split]
    legend = "、".join(f"{v}（{VERDICT_ZH[v]}）" for v in VERDICTS)
    others = "；另有证据在 " + "、".join(f"`{d}`" for d in q.other_docs) if q.other_docs else ""
    lines = [
        f"# 评分单 {sheet.run_id}：条件 {sheet.condition}，题 {q.id}",
        "",
        f"- 题集：{split}；文档：`{q.doc or '无缓存'}`{others}",
        f"- 题目：{q.question}",
        f"- 答案引用的 PDF 页：{', '.join(map(str, sorted(sheet.cited))) or '无'}",
        f"- 判定填在同名 CSV 的 verdict 列（或本表“判定”列）：{legend}",
        "- 提示只是辅助：关键串 all/some/none/unchecked（要点里没有数字或缩写时为 unchecked；题面里已有的串记为 echo，"
        "不参与判断）；词重合是要点里的实词有几个出现在答案里，分不出“是”与“否”；页码看关键串所在句子引的页"
        "（带 ~ 表示退而看全文引的页）：pdf=引到证据页、printed?=只和印刷页码同号、no=引了页但不在证据页、none=没引页码"
        + ("；本题跨文档，页码提示只比页号，分不出答案引的是哪份文档的页" if q.other_docs else ""),
        "- 判定口径：对=说出了要点的内容（等价说法和 notes 认可的写法都算）；部分对=只说出一部分，或不够精确但没有说错；"
        "错=回答了这一点，但内容和文档不符，或拿了文档里别处的值；没写=没有回答这一点；"
        "编造=给出文档里任何地方都没有的具体值或结论",
        "",
        "| 要点 | 必答 | 要点内容 | 关键串 | 命中 | 关键串提示 | 词重合 | 页码提示 | 判定 | 备注 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sheet.rows:
        lines.append("| " + " | ".join(_cell(r[c]) for c in MD_COLUMNS) + " |")
    if q.unanswerable:
        lines += ["", "不可回答题：关键串提示一栏写 abstains（答案说了文档没有）或 no-abstain；"
                  "页码提示一栏写答案里有没有给出数值（values），命中一栏列出这些数值，供判断是否编造。"]
    lines += _where(q)
    lines += ["", "## 答案原文", "", *(f"> {x}" if x else ">" for x in sheet.answer.strip().splitlines())]
    if q.reference:
        lines += ["", "## 参考答案", "", q.reference]
    if q.notes:
        lines += ["", "## 出题说明（notes）", "", q.notes]
    return "\n".join(lines) + "\n"


def _where(q: Question) -> list[str]:
    """Where each facet's evidence is, for cross-document questions and questions with image evidence;
    sheets of single-document text questions stay as they were."""
    if not (q.other_docs or q.visual):
        return []
    out = ["", "## 证据位置", ""]
    if q.visual:
        out += ["本题有图像证据：要点里的数值是从图上读的，容差见出题说明。", ""]
    for f in q.facets:
        what = dict(f.images)
        parts = [f"`{q.doc}` p{p}" + (f"（图像：{what[p]}）" if what.get(p) else "") for p in f.pages]
        parts += [f"`{d}` p{p}" for d, p in f.elsewhere]
        out.append(f"- {f.id}：" + ("；".join(parts) or "无"))
    return out


def _write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def write(sheet: Sheet, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    md, cv = out_dir / f"{sheet.run_id}.md", out_dir / f"{sheet.run_id}.csv"
    _write(md, to_markdown(sheet))
    _write(cv, to_csv(sheet))
    return md, cv


def fill(csv_path: Path, verdicts: list[dict[str, str]]) -> None:
    """Write a grader's verdicts (facet_id, verdict, note) into a sheet's CSV. Every facet must get
    exactly one of VERDICTS; nothing is written otherwise."""
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    given: dict[str, dict[str, str]] = {}
    for v in verdicts:
        fid, verdict = str(v.get("facet_id", "")), str(v.get("verdict", "")).strip().lower()
        if fid in given:
            raise ValueError(f"{csv_path}: facet {fid!r} given twice")
        if verdict not in VERDICTS:
            raise ValueError(f"{csv_path}: facet {fid!r}: verdict {verdict!r} is not one of {VERDICTS}")
        given[fid] = {"verdict": verdict, "note": " ".join(str(v.get("note", "")).split())}
    ids = [r["facet_id"] for r in rows]
    if sorted(given) != sorted(ids):
        raise ValueError(f"{csv_path}: verdicts for {sorted(given)}, sheet has {sorted(ids)}")
    for r in rows:
        r.update(given[r["facet_id"]])
    buf = io.StringIO()
    w = csv.DictWriter(buf, COLUMNS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    _write(csv_path, buf.getvalue())
