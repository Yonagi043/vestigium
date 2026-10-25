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


def recorded(tool: str, fn):
    """Run fn inside a recorded call; return its result and the call event."""
    from tglw.trace import record

    events: list[dict] = []
    record.set_sink(events.append)
    try:
        with record.call(tool, {"doc": "doc"}):
            out = fn()
    finally:
        record.set_sink(None)
    return out, events[-1]


def record_problems(ev: dict) -> list[str]:
    from tglw.trace import record

    return record.validate(ev)


def test_scan_marks_are_traced_without_changing_the_read(monkeypatch):
    monkeypatch.setattr(ocr, "scanned", lambda name, page: page in (15, 16))
    monkeypatch.delenv("TGLW_TRACE", raising=False)
    text = "== p14 (printed 8) ==\nA\n== p15 (printed 9) ==\nB\n== p16 ==\nC"
    plain = ocr.mark_read("doc", text)
    out, ev = recorded("read", lambda: ocr.mark_read("doc", text))
    assert out == plain
    assert ev["sig"] == [{"k": "scan", "pages": [15, 16]}]
    (d,) = ev["dec"]
    assert (d["k"], d["by"], d["src"], d["pages"]) == ("scan_notes", "tool", "TGLW_VIS_OCR_NOTE", [15, 16]) and d["tokens"] > 20
    assert record_problems(ev) == []
    monkeypatch.setenv("TGLW_TRACE", "0")
    from tglw.trace import record

    events: list[dict] = []
    record.set_sink(events.append)
    try:
        with record.call("read", {"doc": "doc"}):
            assert ocr.mark_read("doc", text) == plain
    finally:
        record.set_sink(None)
    assert events == []


def test_outline_counts_and_figure_lines_are_traced(monkeypatch):
    monkeypatch.setattr(counts, "section_figures", lambda name: {"s4": 3})
    text = "doc · 15 pages\ns4 3 Model Architecture · p2-6 · 2.4k"
    out, ev = recorded("outline", lambda: counts.mark_outline("doc", text))
    assert out.endswith("· 3 fig") and ev["dec"][0]["k"] == "fig_hints"
    assert ev["dec"][0]["src"] == "TGLW_VIS_OUTLINE_FIGS" and ev["dec"][0]["tokens"] > 0
    assert inline.applied("[fig7: Figure 8 · 2000 · +3 labels; read fig7 for all] (image: region fig7)", "fig7") == {
        "FIG_HINT", "FIG_LABELS"}
    assert inline.applied("[fig7, labels by OCR: x]", "fig7") == {"OCR_NOTE"}
    assert inline.applied("[fig7: x]", "fig7") == set()


def test_auto_zoom_is_traced(monkeypatch):
    monkeypatch.setattr(zoom, "em_of", lambda name, page, b: 6.0)
    scale, ev = recorded("region", lambda: zoom.default_scale("any", 1, (0.0, 100.0, 100.0, 0.0)))
    assert scale == 3.0
    assert ev["dec"] == [{"k": "auto_zoom", "by": "tool", "src": "TGLW_VIS_ADAPTIVE_ZOOM", "scale": 3.0, "em_pt": 6.0}]


def test_trace_errors_never_reach_the_tool(monkeypatch):
    """Counting tokens fails, then the recorder itself fails: results stay the same, the losses are counted."""
    from tglw.common import text as text_mod
    from tglw.trace import record

    monkeypatch.setattr(ocr, "scanned", lambda name, page: page == 15)
    monkeypatch.setattr(counts, "section_figures", lambda name: {"s4": 3})
    monkeypatch.setattr(zoom, "em_of", lambda name, page, b: 6.0)
    monkeypatch.delenv("TGLW_TRACE", raising=False)
    page = "== p15 (printed 9) ==\nB"
    outline = "doc · 15 pages\ns4 3 Model Architecture · p2-6 · 2.4k"
    plain = (ocr.mark_read("doc", page), counts.mark_outline("doc", outline), zoom.default_scale("d", 1, (0.0, 100.0, 100.0, 0.0)))

    def broken(*a, **k):
        raise RuntimeError("injected")

    def run() -> tuple:
        return (ocr.mark_read("doc", page), counts.mark_outline("doc", outline), zoom.default_scale("d", 1, (0.0, 100.0, 100.0, 0.0)))

    monkeypatch.setattr(text_mod, "count_tokens", broken)
    out, ev = recorded("read", run)
    assert out == plain
    assert [d["tokens"] for d in ev["dec"] if d["k"] != "auto_zoom"] == [None, None]
    monkeypatch.setattr(record, "dec", broken)
    out, ev = recorded("read", run)
    assert out == plain
    _, nxt = recorded("read", lambda: None)
    assert nxt["lost"]["error:fig_tokens"] == 2
    assert {"error:_report_scan", "error:_report_counts", "error:_report_zoom"} <= set(nxt["lost"])


def fake_region(sizes: dict[str, tuple[int, int]]):
    from tglw.mcp_server import tools

    def render(name, target, scale=None):
        w, h = sizes[target] if scale is None else (round(sizes[target][0] * scale / 1.5), round(sizes[target][1] * scale / 1.5))
        return tools.Region(f"{target} · p1 · {w}×{h} px · about {round(w * h / 750)} image tokens", b"png", w, h, "")
    return render


def test_add_image_candidates_follow_the_read(monkeypatch):
    from tglw.figures import actions

    monkeypatch.setattr(actions, "_is_figure", lambda name, ref: ref != "#/pictures/2")  # fig2 is a logo
    monkeypatch.setattr(ocr, "scanned", lambda name, page: page in (15, 16))
    text = ("== p14 ==\n[fig2]\n[fig5: Figure 1 · 10 · 20] (image: region fig5)\n[tab4 3×2]\na | b\n"
            "== p15 · scanned, OCR text ==\n[fig7, labels by OCR: x]\n[tab4 3×2 cont. of tab3]\n== p16 ==\n[fig5]")
    assert actions.candidates("d", "figure", text) == ["fig5", "fig7"]
    assert actions.candidates("d", "table", text) == ["tab4"]
    assert actions.candidates("d", "scan", text) == ["p15", "p16"]
    with pytest.raises(ValueError):
        actions.candidates("d", "chart", text)
    assert [actions.target_key(t) for t in ("#/pictures/3", " fig3 ", "#/tables/4", "p15", "p15:1,2,3,4")] == [
        "fig3", "fig3", "tab4", "p15", "p15:1,2,3,4"]


def test_add_image_skips_seen_and_too_big(monkeypatch):
    from tglw.figures import actions

    monkeypatch.setattr(actions, "_is_figure", lambda name, ref: True)
    monkeypatch.setattr(actions.region, "region", fake_region({"fig5": (900, 900), "fig7": (600, 450), "fig9": (300, 300)}))
    text = "[fig5: a]\n[fig7: b]\n[fig9: c]"
    got = actions.add_image("d", "figure", text, seen={"#/pictures/7"}, limit=800, prefix="[rule r-1: add image] ")
    assert got.target == "fig9" and got.img_tokens == 120 and got.skipped == [{"what": "fig5", "why": "budget"}]
    assert got.line.startswith("[rule r-1: add image] fig9 · p1 · 300×300 px") and got.tokens > got.img_tokens
    assert actions.add_image("d", "figure", text, seen={"fig9", "fig7"}, limit=800, why="call") == [{"what": "fig5", "why": "call"}]
    assert actions.add_image("d", "figure", "no figures here", seen=set(), limit=800) == []


def test_zoom_takes_the_larger_scale_and_skips_small_gains(monkeypatch):
    from tglw.figures import actions
    from tglw.mcp_server import tools

    render = fake_region({"fig3": (600, 400), "p2": (900, 1160)})
    monkeypatch.setattr(actions.region, "region", render)
    boxes = {"fig3": (0.0, 300.0, 400.0, 0.0), "p2": None}
    monkeypatch.setattr(actions.tools, "_target_box", lambda name, t: (1, boxes[t], t))
    monkeypatch.setattr(actions.zoom, "chosen_scale", lambda name, page, box, base=1.5: 1.8 if box else base)
    shown = render("d", "fig3", 1.8)
    z = actions.zoom_view("d", "fig3", 3, shown)
    assert (z.scale_from, z.scale_to, z.region.width) == (1.8, 3.0, 1200) and z.tokens > 0
    assert actions.zoom_view("d", "fig3", 1.5, shown) == []  # E3 already chose more
    assert actions.zoom_view("d", "p2", 3, render("d", "p2")) == []  # whole pages are not enlarged
    assert actions.zoom_view("d", "fig3", 3, shown, limit=10) == [{"what": "fig3", "why": "budget"}]
    monkeypatch.setattr(actions.region, "region", lambda name, t, scale=None: tools.Region("c", b"", 760, 520, ""))
    assert actions.zoom_view("d", "fig3", 3, shown) == []  # the pixel caps leave it under 1.1x
