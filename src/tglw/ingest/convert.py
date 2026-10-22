"""Convert one PDF with Docling into the bundle cache: <cache_root>/slottree-docling-bundle-v1/<sha256>/.

Ported from an earlier converter of this project (convert_pdf and the page artifacts; the cache's
_store_entry and its payloads). document.json, pages.json and manifest.json keep that format; the
manifest only gains keys (Docling versions, options, OCR engine and models, page plan, timings).

Changes from that converter: OCR is set per page from pageplan (full-page OCR for scans without a text layer
and for undecodable text layers, Docling's default elsewhere); the OCR engine is explicit instead of
Docling's auto choice; page images are not stored unless asked (no tglw reader uses them, and they
are ~95% of a bundle).

Needs Docling (pip install <package>[ingest]). Run in that environment:
python -m tglw.ingest.convert PDF --cache-root DIR [--ocr rapidocr] [--page-images] [--device auto]
It prints one JSON line describing the entry.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

from . import pageplan

CACHE_SCHEMA = "slottree-docling-bundle-v1"
CONVERTER = "tglw.ingest.convert v1"
PACKAGES = ("docling", "docling-core", "docling-parse", "docling-ibm-models", "docling-slim", "rapidocr",
            "onnxruntime", "easyocr", "torch", "torchvision", "transformers", "pypdfium2")
ENGINES = ("rapidocr", "easyocr", "none")
SCORES = ("parse_score", "layout_score", "table_score", "ocr_score", "mean_score", "low_score")
ARTIFACTS_ENV = "TGLW_INGEST_ARTIFACTS"  # Docling model directory (docling-tools models download -o DIR)
THREADS = int(os.environ.get("TGLW_INGEST_THREADS", "8"))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def versions() -> dict[str, str]:
    out = {}
    for p in PACKAGES:
        try:
            out[p] = importlib.metadata.version(p)
        except importlib.metadata.PackageNotFoundError:
            pass
    return out


def default_artifacts() -> Path:
    """$TGLW_INGEST_ARTIFACTS, else the directory `docling-tools models download` fills by default."""
    env = os.environ.get(ARTIFACTS_ENV)
    return Path(env).expanduser() if env else Path.home() / ".cache" / "docling" / "models"


def ensure_models(artifacts: Path, engine: str) -> None:
    """Fetch the layout, table and OCR models into `artifacts` once, as `docling-tools models download`
    does. Left to itself Docling would fetch layout and tables on first use but let RapidOCR pick its
    own default models, so a first conversion could OCR with other files than later ones."""
    import docling
    from docling.utils.model_downloader import download_models

    marker = artifacts / f".tglw-models-{importlib.metadata.version('docling')}-{engine}"
    if marker.exists():
        return
    download_models(artifacts, with_layout=True, with_tableformer=True, with_code_formula=False,
                    with_picture_classifier=False, with_rapidocr=engine == "rapidocr", with_easyocr=engine == "easyocr",
                    easyocr_languages=["en", "ch_sim", "ch_tra"] if engine == "easyocr" else None)
    marker.write_text(str(Path(docling.__file__).parent))


def _home(path: Path | str | None) -> str | None:
    if path is None:
        return None
    s, home = str(path), str(Path.home())
    return "~" + s[len(home):] if s.startswith(home) else s


def _ocr_options(engine: str, lang: list[str] | None) -> Any:
    from docling.datamodel.pipeline_options import EasyOcrOptions, RapidOcrOptions

    if engine == "easyocr":
        return EasyOcrOptions(lang=lang or ["en"])
    return RapidOcrOptions(lang=lang or ["english"], backend="onnxruntime")


class _PlannedOcr:
    """Wraps Docling's OCR model and sets its mode per page before handing the page over."""

    def __init__(self, inner: Any, modes: dict[int, str]) -> None:
        from docling.datamodel.pipeline_options import OcrMode

        self.inner = inner
        if hasattr(inner, "options"):
            inner.options = inner.options.model_copy()
        self.default = getattr(getattr(inner, "options", None), "mode", None)  # this Docling version's default
        self.modes = {p: OcrMode.FULL_PAGE if m == pageplan.FULL_PAGE else None if m == pageplan.OFF else self.default
                      for p, m in modes.items()}

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def __call__(self, conv_res: Any, page_batch: Iterable[Any]) -> Iterable[Any]:
        if not getattr(self.inner, "enabled", False):
            yield from page_batch
            return
        for page in page_batch:
            mode = self.modes.get(page.page_no, self.default)
            if mode is None:
                yield page
                continue
            self.inner.options.mode = mode
            yield from self.inner(conv_res, [page])


def _pipeline_cls(modes: dict[int, str]) -> type:
    from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline

    class PlannedPdfPipeline(StandardPdfPipeline):
        def _make_ocr_model(self, art_path: Path | None) -> Any:
            return _PlannedOcr(super()._make_ocr_model(art_path), modes)

    return PlannedPdfPipeline


def _models(converter: Any) -> dict[str, Any]:
    """Where the loaded models came from (paths shortened to ~), for the manifest."""
    out: dict[str, Any] = {}
    for pipeline in getattr(converter, "initialized_pipelines", {}).values():
        ocr = getattr(pipeline, "ocr_model", None)
        inner = getattr(ocr, "inner", ocr)
        reader = getattr(inner, "reader", None)
        cfg = getattr(reader, "cfg", None) or getattr(reader, "config", None)
        out["ocr_model"] = type(inner).__name__ if inner is not None else None
        if cfg is not None:
            try:
                out["ocr_files"] = {k: _home(cfg[k]["model_path"]) for k in ("Det", "Cls", "Rec") if k in cfg}
            except (KeyError, TypeError):
                pass
    return out


def convert(source: Path, plans: list[pageplan.PagePlan], engine: str = "rapidocr", lang: list[str] | None = None,
            page_images: bool = False, device: str = "auto", artifacts: Path | None = None,
            ) -> tuple[Any, dict[str, Any]]:
    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.datamodel.settings import settings
    from docling.document_converter import DocumentConverter, PdfFormatOption

    settings.debug.profile_pipeline_timings = True
    options = PdfPipelineOptions(
        generate_parsed_pages=True,
        generate_page_images=page_images,
        do_ocr=engine != "none",
        artifacts_path=artifacts,
        accelerator_options=AcceleratorOptions(device=device, num_threads=THREADS),
    )
    if engine != "none":
        options.ocr_options = _ocr_options(engine, lang)
    modes = {p.page: p.ocr for p in plans}
    converter = DocumentConverter(
        allowed_formats=[InputFormat.PDF],
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_cls=_pipeline_cls(modes), pipeline_options=options)},
    )
    t0 = time.time()
    result = converter.convert(source)
    seconds = time.time() - t0
    info = {
        "options": {"generate_parsed_pages": True, "page_images": page_images, "do_table_structure": True,
                    "table_mode": str(getattr(options.table_structure_options, "mode", "")).split(".")[-1].lower(),
                    "ocr_engine": engine, "ocr_lang": getattr(options.ocr_options, "lang", None) if engine != "none" else None,
                    "ocr_scale": getattr(options.ocr_options, "scale", None), "device": device, "threads": THREADS,
                    "artifacts": _home(artifacts)},
        "ocr": {**_models(converter), "full_page_pages": sorted(p for p, m in modes.items() if m == pageplan.FULL_PAGE),
                "off_pages": sorted(p for p, m in modes.items() if m == pageplan.OFF)},
        "timing": {"seconds": round(seconds, 1), "seconds_per_page": round(seconds / max(1, len(result.pages)), 2),
                   "stages": _timings(result)},
    }
    return result, info


def _timings(result: Any) -> dict[str, float]:
    out = {}
    for key, item in (getattr(result, "timings", None) or {}).items():
        times = getattr(item, "times", None) or []
        out[key] = round(float(sum(times)), 2)
    return out


def _score(value: Any, name: str) -> float | None:
    score = getattr(value, name, None)
    if score is None:
        return None
    score = float(score)
    return score if math.isfinite(score) else None


def _confidence(value: Any) -> dict[str, float | None] | None:
    return None if value is None else {name: _score(value, name) for name in SCORES}


def _bbox(rect: Any) -> list[list[float]] | None:
    if rect is None:
        return None
    values = [getattr(rect, f, None) for f in ("r_x0", "r_y0", "r_x1", "r_y1", "r_x2", "r_y2", "r_x3", "r_y3")]
    if any(v is None for v in values):
        return None
    return [[float(values[i]), float(values[i + 1])] for i in range(0, 8, 2)]


def _cell(page_no: int, level: str, ordinal: int, cell: Any) -> dict[str, Any]:
    rect = getattr(cell, "rect", None)
    origin = getattr(rect, "coord_origin", None)
    return {
        "cell_id": f"page:{page_no}:{level}:{ordinal:06d}",
        "level": level,
        "text": str(getattr(cell, "text", "")),
        "original_text": str(getattr(cell, "orig", "")),
        "bbox": _bbox(rect),
        "coordinate_origin": None if origin is None else str(getattr(origin, "value", origin)),
        "confidence": float(getattr(cell, "confidence", 1.0)),
        "from_ocr": bool(getattr(cell, "from_ocr", False)),
    }


def _page(page: Any, result: Any) -> dict[str, Any]:
    page_no = int(page.page_no)
    if page.size is None:
        raise ValueError(f"Docling page {page_no} has no size")
    parsed = getattr(page, "parsed_page", None)
    words = (getattr(parsed, "word_cells", ()) or ()) if parsed is not None else ()
    return {
        "page_no": page_no,
        "width": float(page.size.width),
        "height": float(page.size.height),
        "has_image": getattr(page, "image", None) is not None,
        "textline_cells": [_cell(page_no, "textline", i, c) for i, c in enumerate(getattr(page, "cells", ()) or ())],
        "word_cells": [_cell(page_no, "word", i, c) for i, c in enumerate(words)],
        "confidence": _confidence(getattr(result.confidence, "pages", {}).get(page_no)),
    }


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def plan_summary(plans: list[pageplan.PagePlan]) -> dict[str, Any]:
    """Page kinds: a count for "text", page lists for the rest; CJK pages listed."""
    out: dict[str, Any] = {}
    for p in plans:
        if p.kind == "text":
            out["text"] = out.get("text", 0) + 1
        else:
            out.setdefault(p.kind, []).append(p.page)
    cjk = [p.page for p in plans if p.script == "cjk"]
    if cjk:
        out["cjk"] = cjk
    return out


def store(result: Any, source: Path, digest: str, entry: Path, info: dict[str, Any]) -> None:
    """Write the three files into a temporary directory, then rename it to the entry."""
    from docling_core.types.doc import ImageRefMode

    entry.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{digest}.", dir=str(entry.parent)))
    try:
        result.document.save_as_json(tmp / "document.json", image_mode=ImageRefMode.EMBEDDED)
        pages = [_page(page, result) for page in result.pages]
        _write_json(tmp / "pages.json", {"document_confidence": _confidence(result.confidence), "pages": pages})
        _write_json(tmp / "manifest.json", {
            "schema": CACHE_SCHEMA,
            "source_sha256": digest,
            "source_size": source.stat().st_size,
            "page_count": len(pages),
            "converter": CONVERTER,
            "converted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "versions": versions(),
            "host": {"python": platform.python_version(), "platform": platform.platform(terse=True)},
            **info,
        })
        if entry.exists():
            raise FileExistsError(entry)
        tmp.chmod(0o755)  # mkdtemp makes it private
        tmp.rename(entry)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)


def run(pdf: Path, cache_root: Path, engine: str = "rapidocr", lang: list[str] | None = None,
        page_images: bool = False, device: str = "auto", artifacts: Path | None = None,
        force: bool = False, scan_text_ocr: str = "fill") -> dict[str, Any]:
    source = pdf.expanduser().resolve()
    digest = sha256(source)
    entry = cache_root.expanduser().resolve() / CACHE_SCHEMA / digest
    if entry.is_dir() and not force:
        return {"entry": str(entry), "sha256": digest, "converted": False}
    plans = pageplan.with_scan_text_ocr(pageplan.plan(str(source)), scan_text_ocr)
    artifacts = artifacts or default_artifacts()
    ensure_models(artifacts, engine)
    result, info = convert(source, plans, engine, lang, page_images, device, artifacts)
    info["options"]["scan_text_ocr"] = scan_text_ocr
    status = str(getattr(result, "status", ""))
    if "success" not in status.lower():
        errors = [str(getattr(e, "error_message", e)) for e in getattr(result, "errors", []) or []]
        if "partial" not in status.lower():
            raise RuntimeError(f"Docling conversion {status}: {errors[:3]}")
        info["status"] = {"docling": status, "errors": errors[:20]}
    info["page_plan"] = plan_summary(plans)
    if force and entry.is_dir():
        shutil.rmtree(entry)
    store(result, source, digest, entry, info)
    return {"entry": str(entry), "sha256": digest, "converted": True, "pages": len(result.pages),
            "seconds": info["timing"]["seconds"], "full_page_ocr": info["ocr"]["full_page_pages"]}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m tglw.ingest.convert")
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--cache-root", type=Path, required=True, help=f"directory holding {CACHE_SCHEMA}/")
    ap.add_argument("--ocr", choices=ENGINES, default="rapidocr")
    ap.add_argument("--lang", action="append", default=None, help="OCR language (repeatable)")
    ap.add_argument("--page-images", action="store_true", help="keep rendered page images in document.json")
    ap.add_argument("--device", default="auto", help="auto, cpu, cuda or cuda:N")
    ap.add_argument("--artifacts", type=Path, default=None, help=f"Docling model directory (or ${ARTIFACTS_ENV})")
    ap.add_argument("--force", action="store_true", help="convert again even if the entry exists")
    ap.add_argument("--scan-text-ocr", choices=("fill", "off"), default="fill",
                    help="scanned pages with their own text layer: let OCR fill gaps (fill) or not (off)")
    args = ap.parse_args(argv)
    out = run(args.pdf, args.cache_root, args.ocr, args.lang, args.page_images, args.device, args.artifacts, args.force,
              args.scan_text_ocr)
    print(json.dumps(out, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    sys.exit(main())
