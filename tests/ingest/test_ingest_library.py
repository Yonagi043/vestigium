import json

import pytest

from tglw import samples
from tglw.common import registry
from tglw.ingest import library


def test_slug_keeps_safe_characters():
    assert library.slug("GAO-26-107790") == "GAO-26-107790"
    assert library.slug("10-K 2025 (As Filed)") == "10-K-2025-(As-Filed)"
    assert library.slug("報告:2025") == "2025"
    assert ":" not in library.slug("a:b")
    with pytest.raises(library.LibraryError):
        library.slug("：：")
    with pytest.raises(library.LibraryError):
        library.valid_name("doc:p3")


def test_pdf_meta_row_matches_the_research_script_fields():
    row = library.pdf_meta_row("gao-26-107790", samples.pdf("gao-26-107790"))
    assert set(row) == {"name", "pages", "outline", "outline_depth", "page_labels"}
    assert row["pages"] == 10


def test_registry_put_remove_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "COMMON_DATA", tmp_path / "common")
    registry.reload()
    try:
        assert registry.docs() == []
        research_row = {"name": "x__a", "cls": "x", "pdf": "x/a.pdf", "sha256": "0" * 64, "pages": 3, "cached": True,
                        "cases": ["a1_01"]}
        (tmp_path / "common").mkdir()
        (tmp_path / "common" / "docs.json").write_text(json.dumps([research_row]))
        registry.reload()
        added = library.info("b", "doc", "b.pdf", "1" * 64, 5, True, "b.pdf")
        registry.put(added)
        rows = json.loads((tmp_path / "common" / "docs.json").read_text())
        assert rows[0] == research_row  # untouched rows keep exactly their keys
        assert rows[1]["source"] == "b.pdf" and rows[1]["cases"] == []
        assert [d.name for d in registry.docs()] == ["x__a", "b"]
        assert library.by_sha("1" * 64).name == "b"
        assert library.free_name("b") == "b-2"
        registry.remove("b")
        assert [d.name for d in registry.docs()] == ["x__a"]
    finally:
        registry.reload()


def test_stamp_follows_the_marker_written_last(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "COMMON_DATA", tmp_path / "common")
    registry.reload()
    try:
        assert registry.stamp() is None
        registry.put(library.info("b", "doc", "b.pdf", "1" * 64, 5, True, "b.pdf"))
        first = registry.stamp()  # docs.json, while no marker exists
        registry.touch()
        marked = registry.stamp()
        assert first is not None and marked is not None and marked != first
        registry.put(library.info("c", "doc", "c.pdf", "2" * 64, 5, True, "c.pdf"))
        assert registry.stamp() == marked  # registered, not finished: the marker has not moved
    finally:
        registry.reload()
