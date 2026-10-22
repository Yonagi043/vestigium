import gzip
import json
from types import SimpleNamespace

import pytest

from tglw import samples
from tglw.ingest import convert, pageplan


def _rect(x0, y0, x1, y1):
    return SimpleNamespace(r_x0=x0, r_y0=y0, r_x1=x1, r_y1=y0, r_x2=x1, r_y2=y1, r_x3=x0, r_y3=y1,
                           coord_origin=SimpleNamespace(value="TOPLEFT"))


def _sample_pages():
    root = samples.pdf("gao-26-107790").with_suffix(".bundle")
    return json.loads(gzip.decompress((root / "pages.json.gz").read_bytes()))


def test_page_payload_has_the_cache_format():
    """Same keys as the bundles the 59-document corpus was built from (the earlier converter)."""
    old = _sample_pages()
    cell = SimpleNamespace(text="Hello", orig="Hello", rect=_rect(1, 2, 30, 12), confidence=0.9, from_ocr=True)
    page = SimpleNamespace(page_no=1, size=SimpleNamespace(width=612, height=792), image=None, cells=[cell],
                           parsed_page=SimpleNamespace(word_cells=[cell]))
    scores = SimpleNamespace(parse_score=1.0, layout_score=0.9, table_score=float("nan"), ocr_score=None,
                             mean_score=0.95, low_score=0.9)
    result = SimpleNamespace(confidence=SimpleNamespace(pages={1: scores}))
    new = convert._page(page, result)
    assert set(new) == set(old["pages"][0])
    assert set(new["textline_cells"][0]) == set(old["pages"][0]["textline_cells"][0])
    assert set(new["confidence"]) == set(old["pages"][0]["confidence"])
    assert new["confidence"]["table_score"] is None  # NaN is not JSON
    assert new["textline_cells"][0]["cell_id"] == "page:1:textline:000000"
    assert new["word_cells"][0]["bbox"] == [[1.0, 2.0], [30.0, 2.0], [30.0, 12.0], [1.0, 12.0]]
    assert new["has_image"] is False


def test_scan_text_ocr_off_only_touches_scan_text_pages():
    mk = lambda page, kind: pageplan.PagePlan(page, kind, "latin", pageplan.OCR_MODE[kind], 0, 0, None, 0, 0.0, False)
    plans = [mk(1, "text"), mk(2, "scan_text"), mk(3, "scan")]
    assert [p.ocr for p in pageplan.with_scan_text_ocr(plans, "fill")] == [p.ocr for p in plans]
    assert [p.ocr for p in pageplan.with_scan_text_ocr(plans, "off")] == [pageplan.DEFAULT, pageplan.OFF,
                                                                         pageplan.FULL_PAGE]


def test_plan_summary_lists_non_text_pages():
    mk = lambda page, kind, script="latin": pageplan.PagePlan(page, kind, script, pageplan.OCR_MODE[kind], 0, 0, None,
                                                              0, 0.0, False)
    out = convert.plan_summary([mk(1, "text"), mk(2, "scan"), mk(3, "text", "cjk"), mk(4, "scan_text")])
    assert out == {"text": 2, "scan": [2], "scan_text": [4], "cjk": [3]}


def test_home_paths_are_shortened(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert convert._home(tmp_path / ".cache" / "docling") == "~/.cache/docling"
    assert convert._home("/opt/models") == "/opt/models"


def test_planned_ocr_sets_the_mode_per_page():
    pytest.importorskip("docling")
    from docling.datamodel.pipeline_options import OcrMode, RapidOcrOptions

    seen = []

    class Inner:
        enabled = True
        options = RapidOcrOptions()

        def __call__(self, conv_res, pages):
            for page in pages:
                seen.append((page.page_no, self.options.mode))
                yield page

    default = RapidOcrOptions().mode
    inner = Inner()
    ocr = convert._PlannedOcr(inner, {1: pageplan.DEFAULT, 2: pageplan.FULL_PAGE, 4: pageplan.OFF})
    pages = [SimpleNamespace(page_no=n) for n in (1, 2, 3, 4)]
    assert [p.page_no for p in ocr(None, pages)] == [1, 2, 3, 4]
    assert seen == [(1, default), (2, OcrMode.FULL_PAGE), (3, default)]  # page 4 skips OCR
    assert Inner.options.mode == default  # the shared options object is not touched
