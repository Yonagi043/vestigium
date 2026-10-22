"""Evaluation tools that ship in the release: settings, records layout, held-out guard, folding, sources.

These tests use no research data and no network."""

import hashlib
import importlib
import json
import sys
from pathlib import Path

import pytest

from tglw import COMMAND
from tglw.score import fulltext, plan, questions, settings, sources


@pytest.fixture
def fresh_settings(monkeypatch):
    """settings as a fresh install sees it (no research defaults), with the given environment."""
    import tglw.common
    monkeypatch.delattr(tglw.common, "_research_defaults", raising=False)
    monkeypatch.setitem(sys.modules, "tglw.common._research_defaults", None)

    def load(**env):
        for k in ("TGLW_EVAL_PYTHON", "TGLW_EVAL_CLAUDE", "TGLW_EVAL_GUARD", "TGLW_EVAL_HELDOUT"):
            monkeypatch.delenv(k, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        return importlib.reload(settings)
    yield load
    monkeypatch.undo()
    importlib.reload(settings)


def test_score_release_settings_defaults_name_nothing_internal(fresh_settings):
    s = fresh_settings()
    assert s.PYTHON == sys.executable and s.CLAUDE == "claude"
    assert s.GUARD == () and s.HELDOUT == ("heldout",)
    s = fresh_settings(TGLW_EVAL_PYTHON="/opt/py", TGLW_EVAL_CLAUDE="/opt/claude", TGLW_EVAL_GUARD="answers, keys ",
                       TGLW_EVAL_HELDOUT="heldout,final")
    assert (s.PYTHON, s.CLAUDE, s.GUARD, s.HELDOUT) == ("/opt/py", "/opt/claude", ("answers", "keys"), ("heldout", "final"))
    assert s.heldout_marker(Path("/x/final_round/q.jsonl")) == "final"
    assert s.heldout_marker(Path("/x/dev/q.jsonl")) is None


def test_score_release_heldout_questions_refused(tmp_path, fresh_settings):
    fresh_settings()
    row = {"id": "q1", "split": "frozen", "doc": "d", "question": "Q?", "facets": [{"id": "f1", "text": "x"}]}
    path = tmp_path / "heldout_set" / "q.jsonl"
    path.parent.mkdir()
    path.write_text(json.dumps(row) + "\n")
    with pytest.raises(questions.HeldOutRefused):
        questions.load_h(path)
    assert list(questions.load_h(path, allow_frozen=True)) == ["q1"]
    dev = tmp_path / "dev" / "q.jsonl"
    dev.parent.mkdir()
    dev.write_text(json.dumps(row | {"split": "dev"}) + "\n")
    assert list(questions.load_h(dev)) == ["q1"]


def test_score_release_records_beside_runs():
    assert plan.records_for(Path("/w/eval_runs/pilot/b/lane2")) == Path("/w/eval_records/pilot/b/lane2")
    assert plan.records_for(Path("/w/runs/pilot")) == Path("/w/records/pilot")
    assert plan.records_for(Path("/w/stage")) == Path("/w/stage/records")


def test_score_release_fold_keeps_every_character(tmp_path):
    long = ("First sentence here.  " + "word " * 120 + "end. Next one; then more: " + "x" * 450)
    text = "=== PAGE 1 ===\n" + long + "\nshort line\n=== PAGE 2 ===\n"
    folded, added = fulltext.fold_text(text, 100)
    assert added > 0 and max(map(len, folded.split("\n"))) <= 450
    assert folded.replace("\n", "") == text.replace("\n", "")
    assert all(len(x.rstrip()) <= 100 for x in folded.split("\n") if " " in x.strip())
    src, out = tmp_path / "w", tmp_path / "w400"
    src.mkdir()
    (src / "d.txt").write_text(text)
    index = fulltext.fold_dir(src, out, 100)
    row = index.read_text().splitlines()[1].split(",")
    assert row[0] == "d.txt" and row[-1] == hashlib.sha256((out / "d.txt").read_text().encode()).hexdigest()
    with pytest.raises(FileExistsError):
        fulltext.fold_dir(src, out, 100)


def test_score_release_sources_fetch_checks_hashes(tmp_path):
    good, bad = b"%PDF-1.7 good", b"%PDF-1.7 other"
    rows = [{"name": n, "file": f"{n}.pdf", "url": f"https://example.invalid/{n}.pdf", "sha256": hashlib.sha256(good).hexdigest()}
            for n in ("a", "b", "c")]
    src = tmp_path / "sources.json"
    src.write_text(json.dumps(rows))

    def get(url: str, target: Path) -> None:
        if url.endswith("c.pdf"):
            raise OSError("HTTP Error 403: Forbidden")
        target.write_bytes(good if url.endswith("a.pdf") else bad)

    out = tmp_path / "pdf"
    got = sources.fetch(sources.load(src), out, pause=0, get=get, say=lambda s: None)
    assert [(c.name, c.status) for c in got] == [("a", "match"), ("b", "mismatch"), ("c", "failed")]
    assert "403" in got[2].detail and got[1].sha256 == hashlib.sha256(bad).hexdigest()
    again = sources.fetch(sources.load(src), out, only=["a"], pause=0, get=get, say=lambda s: None)
    assert [c.status for c in again] == ["kept"]
    with pytest.raises(KeyError):
        sources.fetch(sources.load(src), out, only=["zzz"], pause=0, get=get, say=lambda s: None)
    alt = [rows[1] | {"alt_sha256": hashlib.sha256(bad).hexdigest()}]
    assert [c.status for c in sources.fetch(alt, tmp_path / "pdf2", pause=0, get=get, say=lambda s: None)] == ["alternative"]
    assert [c.status for c in sources.fetch(alt, tmp_path / "pdf2", pause=0, get=get, say=lambda s: None)] == ["kept"]
    assert set(sources.VERIFIED) == {"match", "alternative", "kept"}
    script = sources.ingest_script(rows[:1] + [rows[1] | {"cls": "gao", "file": "b (1).pdf"}], Path("/p"))
    assert script.splitlines()[2:] == [f"{COMMAND} ingest /p/a.pdf --name a", f"{COMMAND} ingest '/p/b (1).pdf' --name b --cls gao"]
    src.write_text(json.dumps([{"name": "a", "file": "a.pdf", "url": ""}]))
    with pytest.raises(ValueError):
        sources.load(src)


def _sheet(dir: Path, code: str, qid: str, doc: str, others: str = "") -> None:
    (dir / f"{code}.csv").write_text("run_id,condition,question_id,split,doc,facet_id,required,facet_text,keys,keys_found,"
                                     f"key_hint,words,page_hint,verdict,note\n{code},?,{qid},dev,{doc},f1,yes,x,,,,,,,\n")
    extra = f"；另有证据在 `{others}`" if others else ""
    (dir / f"{code}.md").write_text(f"# 评分单 {code}\n\n- 题集：H 开发集；文档：`{doc}`{extra}\n")


def test_score_release_grading_queue_and_prompt(tmp_path):
    from tglw.score import grading
    sheets, texts, images = tmp_path / "sheets", tmp_path / "w", tmp_path / "img"
    for d in (sheets, texts, images):
        d.mkdir()
    _sheet(sheets, "xa", "q1", "d1")
    _sheet(sheets, "xb", "q1", "d1")
    _sheet(sheets, "xc", "q2", "d2", "d3")
    (images / "q2_p4.png").write_bytes(b"")
    queue = grading.make_queue(sheets, texts, images, seed=3)
    by = {e["question_id"]: e for e in queue}
    assert sorted(by["q1"]["codes"]) == ["xa", "xb"] and by["q1"]["images"] == []
    assert by["q2"]["fulltexts"] == [str(texts / "d2.txt"), str(texts / "d3.txt")] and by["q2"]["images"] == [str(images / "q2_p4.png")]
    assert [e["n"] for e in queue] == [1, 2] and queue == grading.make_queue(sheets, texts, images, seed=3)
    one = grading.prompt(by["q1"], sheets)
    assert "Grade two answers" in one and f"- {sheets / 'xa'}.md" in one and one.rstrip().endswith("]}]}")
    two = grading.prompt(by["q2"], sheets)
    assert "Document texts" in two and "Evidence screenshots" in two and "Grade one answer" in two


def test_score_release_evidence_crops(tmp_path):
    import pypdfium2 as pdfium
    from PIL import Image
    from tglw.score import evidence
    pdf = pdfium.PdfDocument.new()
    pdf.new_page(200, 100)
    path = tmp_path / "d.pdf"
    pdf.save(str(path))
    rows = [{"id": "q1", "doc": "d", "facets": [{"id": "f1", "evidence": [
        {"page": 1, "bbox": [10, 90, 50, 60], "kind": "image"}, {"page": 1, "bbox": [40, 80, 110, 20], "kind": "image"},
        {"pdf_page": 1, "quote": "text evidence"}, {"doc": "other", "page": 2, "bbox": [0, 1, 1, 0], "kind": "image"}]}]}]
    assert list(evidence.image_boxes(rows)) == [("q1", "d", 1)]
    made = evidence.write_crops(rows, tmp_path / "out", lambda d: path, dpi=144)
    assert [m.name for m in made] == ["q1_p1.png"] and Image.open(made[0]).size == (200, 140)


def test_score_release_parallel_spec(tmp_path):
    from tglw.score import parallel
    row = {"id": "q1", "split": "dev", "doc": "d", "question": "Q?", "facets": [{"id": "f1", "text": "x"}]}
    qf = tmp_path / "q.jsonl"
    qf.write_text(json.dumps(row) + "\n" + json.dumps(row | {"id": "q2"}) + "\n")
    spec = {"substages": [{"name": "t", "questions": [str(qf)], "only": ["q2"], "conditions": ["T"], "model": "m", "seed": 1}]}
    subs = parallel.substages_from_spec(spec, questions.load_h)
    assert [(s.name, s.model, [r["run_id"] for r in s.rows]) for s in subs] == [("t", "m", ["T-q2-1"])]
    with pytest.raises(KeyError):
        parallel.substages_from_spec({"substages": [spec["substages"][0] | {"only": ["q9"]}]}, questions.load_h)


def _block(ref, text, label="text", kind="text", page=1):
    from tglw.common.bundle import Block
    return Block(ref, kind, label, text, page, (page,), None, 0, None, "body", (), False, None)


def test_score_release_zh_clean_and_fold():
    from types import SimpleNamespace
    doc = SimpleNamespace(blocks=[
        _block("#/texts/0", "第二十三条 发生 可能对 上市公司 证券交易 价格产生较大影响的重大事件，上市公司应"),
        _block("#/texts/1", "当立即披露。"),
        _block("#/texts/2", "前款所称重大事件包括："),
        _block("#/tables/0", "项目 | 金额\n营业 收入 | 1,234", "table", "table"),
        _block("#/texts/3", "下一页的段落", page=2)])
    clean = fulltext.zh_render(doc)
    lines = clean.split("\n")
    assert lines[0] == "=== PAGE 1 ===" and lines[-2] == "=== PAGE 2 ==="
    assert "上市公司应当立即披露。" in lines[1] and "发生可能对上市公司" in lines[1]   # broken sentence joined, spaces gone
    assert lines[2] == "前款所称重大事件包括：" and "营业收入 | 1,234" in clean        # sentence end kept; table rows kept
    plain = "\n".join(["=== PAGE 1 ==="] + [b.text if b.kind != "table" else f"[TABLE]\n{b.text}\n[/TABLE]"
                                             for b in doc.blocks[:4]] + ["=== PAGE 2 ===", doc.blocks[4].text])
    check = fulltext.clean_check(plain, clean)
    assert check["same_apart_from_whitespace"] and check["breaks_joined"] == 1
    long = "第一条" + "这是一个没有空格的很长的中文句子，" * 40 + "结束。下一句也很长" + "很长" * 150
    folded = fulltext.fold_line(long, 100)
    assert "".join(folded) == long and max(len(x) for x in folded) <= 100
    assert any(x.endswith("结束。") for x in folded)


def test_score_release_prompt_language():
    from tglw.score.questions import Facet, Question
    en = Question("q", "H", "dev", "d", "How many sites?", (Facet("f1", "x"),))
    zh = Question("z", "H", "dev", "d", "上市公司是否需要披露？请说明依据的条款。", (Facet("f1", "x"),))
    assert plan.ENGLISH in plan.PROMPT and plan.ENGLISH in plan.PROMPT_MULTI
    for cond in ("T", "P", "W"):
        a, b = plan.prompt(cond, en), plan.prompt(cond, zh)
        assert plan.ENGLISH in a and plan.CHINESE not in a
        assert b.replace(plan.CHINESE, plan.ENGLISH).replace(zh.question, en.question) == a


def test_score_release_server_env_in_mcp_json(tmp_path):
    import json as _json
    from tglw.score import parallel
    row = {"id": "q1", "split": "dev", "doc": "d", "question": "Q?", "facets": [{"id": "f1", "text": "x"}]}
    qf = tmp_path / "q.jsonl"
    qf.write_text(_json.dumps(row) + "\n")
    spec = {"substages": [{"name": "t", "questions": [str(qf)], "conditions": ["T"], "model": "m",
                           "server_env": {"TGLW_CJK": 1}}]}
    subs = parallel.substages_from_spec(spec, questions.load_h)
    parallel.stage_parallel(tmp_path / "eval_runs" / "s", subs, lanes=1)
    cfg = _json.loads((tmp_path / "eval_runs" / "s" / "t" / "lane1" / "runs" / "T-q1-1" / ".mcp.json").read_text())
    env = cfg["mcpServers"]["tgl"]["env"]
    assert env["TGLW_CJK"] == "1" and env[plan.ALLOWED_DOCS_ENV] == "d"
    assert _json.loads((tmp_path / "eval_runs" / "s" / "t" / "stage.json").read_text())["server_env"] == {"TGLW_CJK": "1"}
    assert "TGLW_CJK" not in plan.mcp_config(None, ["d"])["mcpServers"]["tgl"]["env"]


def test_score_release_condition_s(tmp_path):
    from tglw.score import headless
    from tglw.score.questions import Facet, Question
    q = Question("q1", "H", "dev", "d1", "How many?", (Facet("f1", "x"),))
    export = tmp_path / "export"
    (export / "d1" / "doc").mkdir(parents=True)
    (export / "d1" / "doc" / "INDEX.md").write_text("# d1\n")
    rows = plan.make_plan([q], ("S", "T"), "s", 1, export_dir=export)
    srow = next(r for r in rows if r["condition"] == "S")
    assert srow["material"] == str(export / "d1" / "doc") and plan.run_files(srow) == ["doc"]
    dest = tmp_path / "stage"
    plan.stage(rows, {"q1": q}, dest)
    run = dest / "runs" / srow["run_id"]
    assert (run / "doc" / "INDEX.md").read_text() == "# d1\n" and sorted(p.name for p in run.iterdir()) == ["doc"]
    assert not headless.preflight(dest, tmp_path / "records", srow, srow["run_id"], [])
    text = plan.prompt("S", q)
    assert "`doc/INDEX.md`" in text and text.replace(plan.material_line("S", "d1"), "") == \
        plan.prompt("W", q).replace(plan.material_line("W", "d1"), "")
    assert headless.command("S", "Q?", 5)[headless.command("S", "Q?", 5).index("--tools") + 1] == "Read,Grep,Glob"
    with pytest.raises(ValueError):
        plan.materials_line("S", ["d1", "d2"])
    (export / "d1" / "doc" / "INDEX.md").unlink()
    (export / "d1" / "doc").rmdir()
    assert plan.check_materials([srow]) == [f"{srow['run_id']}: missing {export / 'd1' / 'doc'}"]
