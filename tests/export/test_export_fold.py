import pytest

from tglw.export.fold import fold, fold_line

PROSE = ("The first sentence is short. " * 8 + "A second part follows; it has a semicolon: and a colon. ") * 3


@pytest.mark.parametrize("width", [50, 120, 400])
def test_fold_keeps_every_character_and_width(width):
    for line in (PROSE, "x" * 1000, "| a | " * 200, "中文句子。" * 150, "no-space-" * 80 + " tail"):
        parts = fold_line(line, width)
        assert "".join(parts) == line
        assert all(len(p.rstrip()) <= width for p in parts)


def test_fold_cuts_after_sentences_then_words():
    parts = [p.rstrip() for p in fold_line(PROSE, 100)]
    assert all(p.endswith((".", ";", ":")) for p in parts[:-1])
    words = "word " * 50
    assert [p.rstrip() for p in fold_line(words, 22)][0] == "word word word word"


def test_fold_short_line_unchanged_and_lines_stripped():
    assert fold_line("short line  ", 400) == ["short line  "]
    assert fold("a\nb  \n", 400) == ["a", "b", ""]


def test_fold_table_row_at_cells():
    row = " | ".join(f"cell number {i} with text" for i in range(30))
    parts = [p.rstrip() for p in fold_line(row, 120)]
    assert all(p.endswith("|") for p in parts[:-1])


def test_fold_chinese_after_punctuation():
    text = "这是第一句话，内容比较长。" * 40
    parts = fold_line(text, 100)
    assert "".join(parts) == text and all(p.endswith("。") for p in parts[:-1])
