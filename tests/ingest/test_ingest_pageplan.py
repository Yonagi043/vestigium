from PIL import Image, ImageDraw

from tglw import samples
from tglw.ingest import pageplan


def test_kind_of_rules():
    assert pageplan.kind_of(chars=900, bad=0, vowels=0.38, cover=0.05) == "text"
    assert pageplan.kind_of(chars=900, bad=0, vowels=0.10, cover=0.0) == "garbled"  # shifted font codes
    assert pageplan.kind_of(chars=900, bad=40, vowels=0.38, cover=0.0) == "garbled"  # control characters
    assert pageplan.kind_of(chars=900, bad=0, vowels=0.10, cover=0.95) == "scan_text"  # OCR layers keep their text
    assert pageplan.kind_of(chars=3, bad=0, vowels=None, cover=0.95) == "scan"
    assert pageplan.kind_of(chars=3, bad=0, vowels=None, cover=0.1) == "sparse"
    assert pageplan.OCR_MODE["scan"] == pageplan.OCR_MODE["garbled"] == pageplan.FULL_PAGE
    assert pageplan.OCR_MODE["text"] == pageplan.OCR_MODE["scan_text"] == pageplan.DEFAULT


def test_private_use_bullets_are_not_bad():
    assert not pageplan._is_bad("")
    assert pageplan._is_bad("\x14") and pageplan._is_bad("�") and not pageplan._is_bad("\n")
    assert pageplan._vowel_share(list("$FW7KLQN" * 40)) < pageplan.VOWEL_SHARE
    assert pageplan._vowel_share(list("Thinkaboutthequestion" * 20)) > pageplan.VOWEL_SHARE


def test_born_digital_sample_is_text():
    plans = pageplan.plan(str(samples.pdf("gao-26-107790")))
    assert len(plans) == 10
    assert {p.kind for p in plans} == {"text"} and all(p.ocr == pageplan.DEFAULT for p in plans)


def test_image_only_page_is_a_scan(tmp_path):
    img = Image.new("L", (850, 1100), 255)
    ImageDraw.Draw(img).rectangle((100, 100, 700, 140), fill=0)
    pdf = tmp_path / "scan.pdf"
    img.save(pdf, resolution=100.0)
    (p,) = pageplan.plan(str(pdf))
    assert p.kind == "scan" and p.ocr == pageplan.FULL_PAGE and p.image_cover > 0.95 and p.chars == 0


def test_cjk_script():
    text = "本集團截至二零二五年十二月三十一日止年度的收益" * 3
    letters = list(text)
    assert sum(pageplan._is_cjk(c) for c in letters) == len(letters)
