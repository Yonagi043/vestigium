"""tools.region with the VIS switches: E3's default scale, pixel cap and crop box, E4's OCR labels.
With ADAPTIVE_ZOOM, CROP_BOX and OCR_NOTE off it returns what tools.region returns, byte for byte
(tests/figures). INT can point the server's region at this function or move its few changed lines into
tools.region."""

from __future__ import annotations

import io

import pypdfium2 as pdfium

from tglw.common import registry
from tglw.mcp_server import tools
from tglw.views import view

from . import ocr, zoom


def region(doc: str, target: str, scale: float | None = None) -> tools.Region:
    """scale None: tools.REGION_SCALE, or under ADAPTIVE_ZOOM the scale for the crop's smallest text."""
    name = tools.resolve_doc(doc)
    page, box, label = tools._target_box(name, target)
    if scale is None:
        scale = zoom.default_scale(name, page, box, tools.REGION_SCALE)
    pdf = pdfium.PdfDocument(str(registry.get(name).pdf_path))
    try:
        pg = pdf[page - 1]
        height = pg.get_height()
        img = pg.render(scale=scale).to_pil()
        text = tools._layer_text(pg, box)
        turn = tools._upright_turn(pg, box)
    finally:
        pdf.close()
    if box:
        l, t, r, b = box
        crop = (max(0, l * scale), max(0, (height - t) * scale), min(img.width, r * scale), min(img.height, (height - b) * scale))
        img = img.crop(tuple(round(x) for x in crop))
    if turn:
        img = img.rotate(turn, expand=True)
        label += " · turned upright"
    f = min(1.0, zoom.max_side(tools.MAX_SIDE) / max(img.size), (tools.MAX_PIXELS / (img.width * img.height)) ** 0.5)
    if f < 1.0:
        img = img.resize((max(1, int(img.width * f)), max(1, int(img.height * f))))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    w, h = img.size
    where = view(name).page_tag(page)
    est = round(w * h / tools.IMAGE_TOKEN_DIVISOR)
    caption = f"{label} · {where} · {w}×{h} px · about {est} image tokens" + zoom.crop_note(page, box)
    if text:
        text = ocr.region_text_header(name, page, layer=True) + "\n" + text
    else:
        text = tools._parsed_text(name, page, box)
        text = ocr.region_text_header(name, page, layer=False) + "\n" + text if text else "no text in the box"
    return tools.Region(caption, buf.getvalue(), w, h, tools._cap(text))
