"""VIS switches and helpers on synthetic inputs and the bundled sample PDF (no research data)."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from types import SimpleNamespace

import pypdfium2 as pdfium
import pytest

from tglw import samples
from tglw.figures import cost, counts, drawings, inline, ocr, rank, switches, zoom


@pytest.fixture(autouse=True)
def clean_switches(monkeypatch):
    for key in switches.DEFAULTS:
        monkeypatch.delenv(switches.env_name(key), raising=False)
    monkeypatch.setattr(switches, "OVERRIDE", {})


def set_all(monkeypatch, value: bool) -> None:
    for key in switches.DEFAULTS:
        monkeypatch.setenv(switches.env_name(key), "1" if value else "0")


def test_switches_read_the_environment_then_override_then_default(monkeypatch):
    assert switches.on("FIG_HINT") is True and switches.on("TOKEN_NOTE") is False and switches.on("FIG_LABELS") is False
    monkeypatch.setattr(switches, "OVERRIDE", {"FIG_HINT": False})
    assert switches.on("FIG_HINT") is False
    monkeypatch.setenv("TGLW_VIS_FIG_HINT", "1")
    assert switches.on("FIG_HINT") is True
    monkeypatch.setenv("TGLW_VIS_TOKEN_NOTE", "off")
    assert switches.on("TOKEN_NOTE") is False
    assert [name for name, _, _ in switches.table()] == [f"TGLW_VIS_{k}" for k in switches.DEFAULTS]


def test_adaptive_zoom_is_on_in_a_clean_environment():
    env = {k: v for k, v in os.environ.items() if not k.startswith(("TGLW_", "TGL_"))}
    code = "from tglw.figures import switches; print(switches.DEFAULTS['ADAPTIVE_ZOOM'], switches.on('ADAPTIVE_ZOOM'))"
    out = subprocess.run([sys.executable, "-c", code], env={**env, "PYTHONDONTWRITEBYTECODE": "1"},
                         capture_output=True, text=True, check=True).stdout.split()
    assert out == ["True", "True"]


def test_token_note_replaces_only_when_on(monkeypatch):
    text = "Read long PDFs. " + cost.OLD_INSTRUCTION
    assert cost.instructions(text) == text
    assert cost.region_description("x. " + cost.OLD_DESCRIPTION) == "x. " + cost.OLD_DESCRIPTION
    monkeypatch.setenv("TGLW_VIS_TOKEN_NOTE", "1")
    assert cost.instructions(text) == "Read long PDFs. " + cost.NEW_INSTRUCTION
    assert "300-1,500 tokens" in cost.region_description("x. " + cost.OLD_DESCRIPTION)
    assert "costly" not in cost.region_description("x. " + cost.OLD_DESCRIPTION)


def test_auto_scale_follows_small_text_and_small_crops():
    assert zoom.auto_scale(12.0, (0, 600, 600, 0)) == 1.5  # large text, large crop: unchanged
    assert zoom.auto_scale(8.0, (0, 600, 600, 0)) == 2.0  # 8 pt -> 16 px
    assert zoom.auto_scale(4.0, (0, 600, 600, 0)) == 3.0  # capped
    assert zoom.auto_scale(None, (0, 200, 200, 0)) == 3.0  # 200 pt crop: up to 800 px, capped at 3
    assert zoom.auto_scale(None, (0, 400, 400, 0)) == 2.0


def test_default_scale_and_cap_only_move_under_adaptive_zoom(monkeypatch):
    box = (0.0, 100.0, 100.0, 0.0)
    monkeypatch.setenv("TGLW_VIS_ADAPTIVE_ZOOM", "0")
    assert zoom.default_scale("any", 1, box) == 1.5 and zoom.max_side() == 1200
    monkeypatch.setenv("TGLW_VIS_ADAPTIVE_ZOOM", "1")
    monkeypatch.setattr(zoom, "em_of", lambda name, page, b: 6.0)
    assert zoom.default_scale("any", 1, box) == 3.0
    assert zoom.default_scale("any", 1, None) == 1.5  # whole pages stay at the base scale
    assert zoom.max_side() == 1568


def test_crop_note(monkeypatch):
    assert zoom.crop_note(15, (99.4, 714.0, 557.0, 116.2)).startswith(" · box p15:99,714,557,116 (pt")
    assert zoom.crop_note(15, None) == ""
    monkeypatch.setenv("TGLW_VIS_CROP_BOX", "0")
    assert zoom.crop_note(15, (1, 2, 3, 4)) == ""


def test_ocr_em_heights_use_the_short_side_of_word_boxes():
    blocks = [SimpleNamespace(text="LITHOGRAPHY", bbox=(10, 50, 17, 14)),  # sideways word, 7 pt wide
              SimpleNamespace(text="CHASE 121", bbox=(20, 40, 48, 34)),  # 6 pt tall
              SimpleNamespace(text="x", bbox=(30, 30, 31, 29)),  # too short to judge
              SimpleNamespace(text="LITHOGRAPHY", bbox=(500, 50, 507, 14))]  # outside the box
    assert zoom.ocr_em_heights(blocks, (0, 100, 100, 0)) == pytest.approx([7 / 0.75, 6 / 0.75])


def test_layer_em_heights_on_the_sample_pdf():
    pdf = pdfium.PdfDocument(str(samples.pdf("gao-26-107790")))
    try:
        pg = pdf[1]
        w, h = pg.get_size()
        ems = zoom.layer_em_heights(pg, (0, 0, w, h))
    finally:
        pdf.close()
    assert len(ems) > 500 and 6 < sorted(ems)[len(ems) // 2] < 16


def test_scanned_marks_go_on_scanned_page_headers_only(monkeypatch):
    monkeypatch.setattr(ocr, "scanned", lambda name, page: page in (15, 16))
    text = "== p14 (printed 8) ==\nA\n== p15 (printed 9) ==\nB\n== p16 ==\nC"
    out = ocr.mark_read("doc", text)
    assert out.split("\n") == ["== p14 (printed 8) ==", "A", "== p15 (printed 9)" + ocr.PAGE_MARK + " ==", ocr.note_line(15),
                               "B", "== p16" + ocr.PAGE_MARK + " ==", "C"]
    monkeypatch.setenv("TGLW_VIS_OCR_NOTE", "0")
    assert ocr.mark_read("doc", text) == text


def test_region_text_header(monkeypatch):
    monkeypatch.setattr(ocr, "scanned", lambda name, page: page == 3)
    assert ocr.region_text_header("d", 3, layer=True) == ocr.LAYER_OCR
    assert ocr.region_text_header("d", 4, layer=True) == ocr.LAYER
    assert ocr.region_text_header("d", 4, layer=False) == ocr.PARSED_OCR
    monkeypatch.setenv("TGLW_VIS_OCR_NOTE", "0")
    assert (ocr.region_text_header("d", 3, True), ocr.region_text_header("d", 4, False)) == (ocr.LAYER, ocr.PARSED)


@dataclass
class Blk:
    ref: str
    text: str = ""
    label: str = "text"
    kind: str = "text"
    page: int = 1
    bbox: tuple | None = None
    order: int = 0
    block: object | None = None


@dataclass
class FakeView:
    name: str = "doc"
    children: dict = field(default_factory=dict)
    captions: dict = field(default_factory=dict)

    def block_segments(self, b):
        return [SimpleNamespace(text=b.text, page=b.page)]

    def hidden(self, page, ref):
        return False

    @property
    def doc(self):
        caps = self.captions
        return SimpleNamespace(item=lambda ref: {"captions": [{"$ref": r} for r in caps.get(ref, [])]},
                               raw={"pages": {"1": {"size": {"width": 600, "height": 800}}}})


def chart_view(n_labels: int) -> tuple[FakeView, Blk]:
    pic = Blk("#/pictures/7", kind="picture", label="picture", bbox=(100, 500, 400, 300))
    cap = Blk("#/texts/1", "Figure 8: Revenue by year", label="caption")
    labels = [Blk(f"#/texts/{i + 2}", t) for i, t in enumerate(["e::", "~"] + [str(2000 + i) for i in range(n_labels)])]
    return FakeView(children={pic.ref: labels + [cap]}, captions={pic.ref: [cap.ref]}), pic


def test_picture_line_off_is_render_picture(monkeypatch):
    from tglw.views.render import render_picture

    set_all(monkeypatch, False)
    v, pic = chart_view(20)
    assert inline.picture_line(v, pic) == render_picture(v, pic)


def test_picture_line_keeps_caption_and_first_telling_labels(monkeypatch):
    monkeypatch.setattr(inline, "labels_ocr", lambda name, ref: False)
    monkeypatch.setenv("TGLW_VIS_FIG_LABELS", "1")
    v, pic = chart_view(20)
    line = inline.picture_line(v, pic)
    assert line == ("[fig7: Figure 8: Revenue by year · 2000 · 2001 · 2002 · 2003 · 2004 · 2005 · 2006 · 2007 · "
                    "+14 labels; read fig7 for all] (image: region fig7)")
    full = inline.picture_line(v, pic, full=True)
    assert "2019" in full and "+" not in full.split("]")[0] and full.endswith("(image: region fig7)")
    monkeypatch.setattr(inline, "labels_ocr", lambda name, ref: ref == pic.ref)
    assert inline.picture_line(v, pic).startswith("[fig7, labels by OCR: Figure 8")


def test_icons_get_no_image_hint(monkeypatch):
    monkeypatch.setattr(inline, "labels_ocr", lambda name, ref: False)
    icon = Blk("#/pictures/2", kind="picture", label="picture", bbox=(10, 30, 30, 10))
    assert inline.picture_line(FakeView(), icon) == "[fig2]"


def test_outline_counts(monkeypatch):
    monkeypatch.setattr(counts, "section_figures", lambda name: {"s4": 3, "s6": 2})
    text = ("doc · 15 pages\ns4 3 Model Architecture · p2-6 · 2.4k\n  s6 3.2 Attention · p3-5 · 1.1k · +3 sub\n"
            "s13 4 Why · p6-7 · 633")
    assert counts.mark_outline("doc", text).split("\n")[1:] == [
        "s4 3 Model Architecture · p2-6 · 2.4k · 3 fig", "  s6 3.2 Attention · p3-5 · 1.1k · 2 fig · +3 sub",
        "s13 4 Why · p6-7 · 633"]
    monkeypatch.setenv("TGLW_VIS_OUTLINE_FIGS", "0")
    assert counts.mark_outline("doc", text) == text


def hit(whole: int, page: int, order: int, picture: bool, label: str = "text"):
    seg = SimpleNamespace(page=page, block=SimpleNamespace(order=order, label=label))
    return (whole, 1, seg, SimpleNamespace(kind="picture" if picture else "text"))


def test_literal_figure_hits_rank_after_text_hits_of_the_same_quality(monkeypatch):
    hits = [hit(1, 1, 5, False), hit(1, 55, 9, True), hit(1, 3, 2, False), hit(0, 2, 1, False),
            hit(1, 60, 4, True, label="caption")]
    order = sorted(hits, key=rank.block_key)
    assert [(h[2].page, h[3].kind) for h in order] == [(1, "text"), (3, "text"), (60, "picture"), (55, "picture"), (2, "text")]
    pages = {55: [hit(1, 55, 9, True), hit(1, 55, 3, False)], 1: [hit(0, 1, 5, False)], 70: [hit(1, 70, 1, True)],
             46: [hit(2, 46, 1, False)]}
    assert [p for p, _ in sorted(pages.items(), key=rank.page_key)] == [46, 55, 70, 1]
    monkeypatch.setenv("TGLW_VIS_LITERAL_FIGS_LAST", "0")
    assert [h[2].page for h in sorted(hits, key=rank.block_key)] == [1, 3, 55, 60, 2]
    assert [p for p, _ in sorted(pages.items(), key=rank.page_key)] == [46, 55, 70, 1]


def test_drawing_caption_pattern_and_clusters():
    for text in ("Figure 8: Normalized subspace", "Fig. 3. Non-Federated", "FIGURE G-1: CONCEPTUAL VIEW",
                 "Figure 2.3.6-2.- Lunar observatory layout.", "Figure 26."):
        assert drawings.CAPTION.match(text), text
    for text in ("Figure 5 presents MNLI Dev accuracy", "Figures 3 and 4 show", "Table 2: Model sizes"):
        assert not (drawings.CAPTION.match(text) and drawings.re.match(r"^\s*\S+\s+\S+?\s*[:.|]", text)), text
    groups = drawings._clusters([(0, 10, 10, 0), (15, 10, 25, 0), (100, 10, 110, 0)])
    assert sorted((g[0], g[1]) for g in groups) == [([0, 10, 25, 0], 2), ([100, 10, 110, 0], 1)]
    frame = [100.0, 200.0, 300.0, 100.0]
    assert drawings._text_share(frame, [SimpleNamespace(text="a b c", bbox=(100, 200, 300, 120))]) == pytest.approx(0.8)


def bars_page(n: int):
    """A one-page PDF with n filled bars (a vector chart) between y=400 and y=600."""
    import pypdfium2.raw as raw

    pdf = pdfium.PdfDocument.new()
    page = pdf.new_page(612, 792)
    for i in range(n):
        obj = raw.FPDFPageObj_CreateNewRect(120 + i * 18, 400, 12, 40 + i * 8)
        raw.FPDFPath_SetDrawMode(obj, raw.FPDF_FILLMODE_ALTERNATE, False)
        raw.FPDFPage_InsertObject(page.raw, obj)
    raw.FPDFPage_GenerateContent(page.raw)
    return pdf, page


def test_drawing_found_next_to_a_caption_without_a_picture():
    pdf, page = bars_page(16)
    cap = Blk("#/texts/9", "Figure 3: Bars by year", label="caption", bbox=(110, 390, 420, 378))
    label = Blk("#/texts/10", "2019", bbox=(118, 398, 140, 388))
    v = SimpleNamespace(by_page={1: [cap, label]})
    d = drawings._find(v, page, 1, cap)
    assert d is not None and d.paths == 16 and d.caption == cap.ref
    l, t, r, b = d.box
    assert l <= 118 and r >= 120 + 15 * 18 + 12 and t >= 400 + 40 + 15 * 8 and b <= 388  # bars and their label
    assert d.target.startswith("p1:")
    few_pdf, few = bars_page(4)  # a frame or rule, not a chart
    assert drawings._find(v, few, 1, cap) is None
    pic = Blk("#/pictures/1", kind="picture", label="picture", bbox=(110, 600, 420, 400))
    assert drawings._find(SimpleNamespace(by_page={1: [cap, pic]}), page, 1, cap) is None  # Docling has it
    pdf.close()
    few_pdf.close()
