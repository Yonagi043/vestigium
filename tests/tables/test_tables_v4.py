"""D2 v4: rows Docling merged (text layer) and tables it did not recognise (loose figures). Synthetic data."""

from tglw.common.bundle import Block, Span
from tglw.tables import loose
from tglw.tables.grid import Cell, Table
from tglw.tables.layout import layout
from tglw.tables.rows import merged_rows, row_note, value_cell, value_line


def _cell(r, c, text, top, bottom, x0, x1):
    return Cell(r, c, 1, 1, text, (x0, top, x1, bottom), r == 0, False, False)


def _gpt4_like() -> Table:
    """Header, a row holding two printed rows (Bar Exam + LSAT), and a normal row."""
    cells = [_cell(0, 0, "Exam", 110, 120, 80, 200), _cell(0, 1, "GPT-4", 110, 120, 280, 330),
             _cell(0, 2, "GPT-3.5", 110, 120, 460, 520),
             _cell(1, 0, "Uniform Bar Exam LSAT", 130, 155, 80, 260),
             _cell(1, 1, "298 / 400 (~90th) 163 (~88th)", 130, 155, 280, 340),
             _cell(1, 2, "213 / 400 (~10th) 149 (~40th)", 130, 155, 460, 520),
             _cell(2, 0, "SAT Math", 160, 170, 80, 200), _cell(2, 1, "700 / 800", 160, 170, 280, 340),
             _cell(2, 2, "590 / 800", 160, 170, 460, 520)]
    return Table("#/tables/0", "table", (1,), 3, 3, cells, None, None, 0)


LINES = [(140, 135, "Uniform Bar Exam"), (300, 135, "298 / 400 (~90th)"), (490, 135, "213 / 400 (~10th)"),
         (140, 150, "LSAT"), (300, 150, "163 (~88th)"), (490, 150, "149 (~40th)"),
         (140, 165, "SAT Math"), (300, 165, "700 / 800"), (490, 165, "590 / 800")]


def test_row_holding_two_printed_rows_is_merged():
    t = _gpt4_like()
    assert merged_rows(t, layout(t), LINES) == [(1, "merged")]


def test_wrapped_label_with_one_value_line_is_not_merged():
    t = _gpt4_like()
    lines = [(140, 135, "Uniform Bar Exam"), (300, 135, "298 / 400 (~90th)"), (490, 135, "213 / 400 (~10th)"),
             (140, 150, "(multistate essay)"), (140, 165, "SAT Math"), (300, 165, "700 / 800"), (490, 165, "590 / 800")]
    assert merged_rows(t, layout(t), lines) == []


def test_value_cells_and_lines():
    assert value_cell("298 / 400 (~90th) 163 (~88th)") and value_cell("392 (below 5th)")
    assert not value_cell("September 24, 1958") and not value_cell("October 2006 The Act required the Secretary")
    assert value_line("188.49") and not value_line("(3)")


def test_row_note_names_rows_and_packed_cells():
    t = _gpt4_like()
    note = row_note(t, [(1, "merged")], ["58% 46%"])
    assert "'Uniform Bar Exam LSAT'" in note and "'58% 46%'" in note and note.endswith("check the page image")
    assert row_note(t, [], []) is None


def _block(i, text, l, t, r, b, page=1):
    box = (l, t, r, b)
    return Block(ref=f"#/texts/{i}", kind="text", label="text", text=text, page=page, pages=(page,), bbox=box,
                 order=i, parent=None, layer="body", spans=(Span(page, box, None, None),), in_picture=False, marker=None)


def _loose_doc():
    rows = [("Interest-bearing bank borrowings", ["-", "108,489", "112,084"]),
            ("Trade and bills payables", ["-", "130,983", "130,983"]),
            ("Lease liabilities", ["-", "1,384", "2,542"]),
            ("Total", ["99,123", "240,856", "344,732"])]
    blocks = [_block(0, "As at 13 August 2025", 71, 588, 150, 580),
              _block(1, "On demand", 270, 572, 309, 565), _block(2, "Less than 1 year", 340, 572, 381, 565),
              _block(3, "Total", 505, 572, 524, 565)]
    y = 536
    for label, vals in rows:
        blocks.append(_block(len(blocks), label, 95, y, 220, y - 7))
        for v, right in zip(vals, (309, 381, 524)):
            blocks.append(_block(len(blocks), v, right - 6 * len(v), y, right, y - 7))
        y -= 9
    return type("D", (), {"blocks": blocks})()


def test_loose_figures_are_rebuilt_into_a_table():
    found = loose.find(_loose_doc())
    assert len(found) == 1
    e = loose.entry(found[0])
    assert e["restored"] and e["title"] == "As at 13 August 2025"
    assert e["grid"][0] == ["", "On demand", "Less than 1 year", "Total"]
    assert e["grid"][-1] == ["Total", "99,123", "240,856", "344,732"]
    text = loose.render(e)
    assert text.splitlines()[1] == "cols: On demand | Less than 1 year | Total"


def test_figures_without_labels_are_not_a_table():
    doc = _loose_doc()
    doc.blocks = [b for b in doc.blocks if not b.text[0].isalpha() or b.text in ("On demand", "Total")]
    assert loose.find(doc) == []
