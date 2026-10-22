import asyncio
import hashlib
import json
import sys
from pathlib import Path

import mcp_smoke
from tglhome import SAMPLE, clean_env, registry_names, run
from tglw import samples


def test_sample_pdf_matches_bundle():
    assert SAMPLE in samples.NAMES
    assert hashlib.sha256(samples.pdf(SAMPLE).read_bytes()).hexdigest() == samples.sha256(SAMPLE)


def test_install_bundle(tmp_path):
    dest = samples.install_bundle(SAMPLE, tmp_path / "cache")
    assert dest.name == samples.sha256(SAMPLE)
    assert sorted(p.name for p in dest.iterdir()) == ["document.json", "manifest.json", "pages.json"]
    doc = json.loads((dest / "document.json").read_text())
    assert len(doc["texts"]) > 100 and all("image" not in p for p in doc["pages"].values())
    assert samples.install_bundle(SAMPLE, tmp_path / "cache") == dest  # reinstalling replaces it


def test_sample_home_serves_the_sample(sample_env):
    assert registry_names(sample_env) == [SAMPLE]
    out = run("from tglw.views import api; print(api.outline('gao-26-107790'))", sample_env)
    assert "Why This Matters" in out and "Endnotes" in out


def test_mcp_tools_over_stdio(sample_home):
    calls = asyncio.run(mcp_smoke.calls(sample_home, SAMPLE, mcp_smoke.default_command()))
    assert calls[0]["names"] == sorted(mcp_smoke.TOOLS)
    by_tool = {c["tool"]: c for c in calls[1:]}
    assert sorted(by_tool) == sorted(mcp_smoke.TOOLS)
    assert not [c for c in calls[1:] if c["is_error"]]
    assert by_tool["list_documents"]["text"].startswith(SAMPLE)
    assert "FEMA" in by_tool["search"]["text"] and by_tool["region"]["images"] == 1
    assert " · engine G" in by_tool["search"]["text"].splitlines()[0]  # the evaluated search, not the local BM25


def test_empty_home_lists_nothing(tmp_path):
    env = clean_env(tmp_path)
    (tmp_path / "data" / "common").mkdir(parents=True)
    (tmp_path / "data" / "common" / "docs.json").write_text("[]")
    assert registry_names(env) == []
