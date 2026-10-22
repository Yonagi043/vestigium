"""The export subcommand on the bundled sample, in a fresh document home (released test: no private data)."""

import json
import subprocess
import sys

import pytest

SAMPLE = "gao-26-107790"


def export(env, *args, cwd):
    return subprocess.run([sys.executable, "-m", "tglw.export.cli", *args], env=env, capture_output=True, text=True,
                          cwd=str(cwd))


@pytest.fixture(scope="module")
def exported(sample_env, tmp_path_factory):
    out = tmp_path_factory.mktemp("export")
    proc = export(sample_env, SAMPLE, "--out", str(out), cwd=out)
    assert proc.returncode == 0, proc.stderr[-3000:]
    return out


def test_sample_export_layout(exported):
    d = exported / SAMPLE
    meta = json.loads((d / "index.json").read_text())
    assert meta["doc"] == SAMPLE and meta["pdf_pages"] == 10 and meta["files"]
    assert (d / "INDEX.md").exists() and (d / "TABLES.md").exists() and (d / "FIGURES.md").exists()
    assert f"`{SAMPLE}/`" in (exported / "README.md").read_text()
    texts = [d / f["file"] for f in meta["files"]]
    assert all(p.exists() for p in texts)
    lines = [x for p in d.rglob("*.md") for x in p.read_text().split("\n")]
    assert max(map(len, lines)) <= meta["width"] == 400
    pages = {int(x.split()[1][1:]) for p in texts for x in p.read_text().split("\n") if x.startswith("== p")}
    assert pages <= set(range(1, 11)) and len(pages) >= 8


def test_sample_index_places(exported):
    d = exported / SAMPLE
    meta = json.loads((d / "index.json").read_text())
    for s in meta["sections"]:
        lines = (d / s["file"]).read_text().split("\n")
        assert any(x.startswith(f"## {s['id']} ") for x in lines[s["line"] - 1:s["line"] + 2])
    index = (d / "INDEX.md").read_text()
    for f in meta["files"]:
        assert f["file"] in index or not meta["sections"]


def test_sample_export_as_and_replace(sample_env, exported):
    proc = export(sample_env, SAMPLE, "--out", str(exported), "--as", "gao", cwd=exported)
    assert proc.returncode == 0, proc.stderr[-3000:]
    first = (exported / "gao" / "INDEX.md").read_text()
    proc = export(sample_env, SAMPLE, "--out", str(exported), "--as", "gao", cwd=exported)
    assert proc.returncode == 0 and (exported / "gao" / "INDEX.md").read_text() == first
    assert not [p for p in exported.iterdir() if p.name.startswith(".")]
    assert "`gao/` (gao-26-107790)" in (exported / "README.md").read_text()


def test_cli_errors(sample_env, tmp_path):
    assert export(sample_env, "no-such-doc", "--out", str(tmp_path), cwd=tmp_path).returncode == 2
    assert export(sample_env, SAMPLE, "--out", str(tmp_path), "--as", "../x", cwd=tmp_path).returncode == 2
    assert export(sample_env, "--out", str(tmp_path), cwd=tmp_path).returncode == 2
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "keep.txt").write_text("user file")
    assert export(sample_env, SAMPLE, "--out", str(tmp_path), "--as", "src", cwd=tmp_path).returncode == 2
    assert (tmp_path / "src" / "keep.txt").read_text() == "user file"


def test_foreign_readme_kept(sample_env, tmp_path):
    (tmp_path / "README.md").write_text("# My project\n")
    proc = export(sample_env, SAMPLE, "--out", str(tmp_path), "--as", "gao", cwd=tmp_path)
    assert proc.returncode == 0 and "left unchanged" in proc.stdout
    assert (tmp_path / "README.md").read_text() == "# My project\n" and (tmp_path / "gao" / "INDEX.md").exists()


def test_through_the_console_command(sample_env, tmp_path):
    """`<command> export` runs this subcommand (the parser names itself so)."""
    from tglw import COMMAND
    help_text = subprocess.run([sys.executable, "-m", "tglw.cli", "export", "--help"], env=sample_env, capture_output=True,
                               text=True, cwd=str(tmp_path)).stdout
    assert help_text.startswith(f"usage: {COMMAND} export ")
    proc = subprocess.run([sys.executable, "-m", "tglw.cli", "export", SAMPLE, "--out", str(tmp_path), "--as", "gao"],
                          env=sample_env, capture_output=True, text=True, cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stderr[-3000:]
    from tglw import NAME
    assert f"exported by {NAME} " in (tmp_path / "gao" / "INDEX.md").read_text().split("\n")[0]
