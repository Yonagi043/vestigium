"""The ingest command end to end on the bundled sample (its Docling bundle ships with the package: no Docling)."""

import json
import os
import subprocess
import sys

import pytest

from tglw import HOME_ENV
from tglw.common import paths

SAMPLE = "gao-26-107790"


def _run(home, *args):
    env = {k: v for k, v in os.environ.items() if k not in ("TGL_HOME", "TGL_DATA")}
    env.update({HOME_ENV: str(home), "PYTHONDONTWRITEBYTECODE": "1"})
    return subprocess.run([sys.executable, "-m", "tglw.ingest", *args], capture_output=True, text=True, env=env)


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    home = tmp_path_factory.mktemp("lib")
    proc = _run(home, "--sample")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return home


def test_sample_is_registered_with_sidecars_index_and_report(library):
    data = library / "data"
    (row,) = json.loads((data / "common" / "docs.json").read_text())
    assert row["name"] == SAMPLE and row["pdf"] == f"{SAMPLE}.pdf" and row["cached"]
    assert (library / "docs" / f"{SAMPLE}.pdf").exists()
    for task, suffix in (("A", "structure"), ("B", "pagemap"), ("B", "toc"), ("C1", "terms"), ("C2", "xrefs"),
                         ("D1", "textloss"), ("D1", "patch"), ("D2", "tables")):
        assert (data / task / f"{SAMPLE}.{suffix}.json").exists(), (task, suffix)
        assert json.loads((data / task / "MANIFEST.json").read_text())["version"], task
    index = json.loads((data / "M" / "doc_index.json").read_text())
    assert [r["doc"] for r in index] == [SAMPLE]
    report = json.loads((data / "ING" / f"{SAMPLE}.qc.json").read_text())
    assert report["verdict"]["status"] == "ok" and report["pages"] == 10
    assert report["text"]["coverage_patched"] == 1.0
    assert not (data / "ING" / "wip").exists() or not any((data / "ING" / "wip").iterdir())


def test_again_is_a_no_op_and_qc_list_print(library):
    again = _run(library, "--sample")
    assert again.returncode == 0 and again.stdout.startswith("exists")
    qc = _run(library, "qc", SAMPLE)
    assert qc.returncode == 0 and qc.stdout.startswith("quality ok")
    listed = _run(library, "list")
    assert listed.stdout.startswith(f"{SAMPLE} · ") and "quality ok" in listed.stdout


def test_pipeline_check_is_clean(library):
    env = {**os.environ, HOME_ENV: str(library), "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run([sys.executable, "-m", "tglw.pipeline", "check", SAMPLE], capture_output=True, text=True,
                          env=env)
    assert proc.returncode == 0, proc.stderr[-800:]
    assert "error 0, warning 0" in proc.stdout


def test_usage_errors(tmp_path):
    assert _run(tmp_path, "add").returncode == 2
    bad = _run(tmp_path, str(tmp_path / "missing.pdf"))
    assert bad.returncode == 1 and "not a PDF file" in bad.stdout


def test_remove_cleans_up(tmp_path):
    assert _run(tmp_path, "--sample").returncode == 0
    proc = _run(tmp_path, "remove", SAMPLE)
    assert proc.returncode == 0, proc.stderr
    data = tmp_path / "data"
    assert json.loads((data / "common" / "docs.json").read_text()) == []
    assert not list(data.glob(f"*/{SAMPLE}.*")) and not (tmp_path / "docs" / f"{SAMPLE}.pdf").exists()
    assert not any((tmp_path / "cache" / paths.BUNDLE_SCHEMA).iterdir())
    assert json.loads((data / "M" / "doc_index.json").read_text()) == []


@pytest.mark.skipif(not paths.RESEARCH, reason="only a research checkout has delivered data to protect")
def test_research_checkout_refuses_to_ingest(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("TGL_HOME", HOME_ENV, "TGL_DATA")}
    proc = subprocess.run([sys.executable, "-m", "tglw.ingest", "--sample"], capture_output=True, text=True,
                          env={**env, "PYTHONDONTWRITEBYTECODE": "1"})
    assert proc.returncode == 2 and "research checkout" in proc.stderr
