"""Screenshots of image evidence for grading visual questions.

An image evidence item ({"page", "bbox": [l, t, r, b], "kind": "image", ...}; PDF points of the page as
displayed, origin bottom left) belongs to its question's own document. For each question and page,
the union of its image boxes is cut from the page rendered at `dpi` (pixel edges truncated) into
<question>_p<page>.png, as the development sets' screenshots were made (150 dpi, no margin).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from pathlib import Path

DPI = 150


def image_boxes(rows: Iterable[dict]) -> dict[tuple[str, str, int], list[list[float]]]:
    """(question, document, page) -> the image evidence boxes on that page of the question's own document."""
    out: dict[tuple[str, str, int], list[list[float]]] = {}
    for row in rows:
        for f in row.get("facets") or []:
            for e in f.get("evidence") or []:
                if e.get("kind") == "image" and e.get("bbox") and (e.get("doc") or row["doc"]) == row["doc"]:
                    out.setdefault((row["id"], row["doc"], int(e["page"])), []).append(e["bbox"])
    return out


def read_rows(paths: Iterable[Path]) -> list[dict]:
    return [json.loads(x) for p in paths for x in Path(p).read_text(encoding="utf-8").splitlines() if x.strip()]


def write_crops(rows: Iterable[dict], out: Path, pdf_path: Callable[[str], Path], dpi: int = DPI) -> list[Path]:
    import pypdfium2 as pdfium

    out.mkdir(parents=True, exist_ok=True)
    made = []
    for (qid, doc, page), boxes in sorted(image_boxes(rows).items()):
        left, top = min(b[0] for b in boxes), max(b[1] for b in boxes)
        right, bottom = max(b[2] for b in boxes), min(b[3] for b in boxes)
        scale = dpi / 72
        pdf = pdfium.PdfDocument(str(pdf_path(doc)))
        try:
            pg = pdf[page - 1]
            h = pg.get_size()[1]
            img = pg.render(scale=scale).to_pil()
        finally:
            pdf.close()
        img = img.crop((int(left * scale), int((h - top) * scale), int(right * scale), int((h - bottom) * scale)))
        target = out / f"{qid}_p{page}.png"
        tmp = target.with_suffix(".tmp.png")
        img.save(tmp)
        tmp.replace(target)
        made.append(target)
    return made
