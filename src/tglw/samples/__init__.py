"""Bundled sample documents: a public-domain PDF with its Docling bundle, so the tools and tests run
without Docling installed.

gao-26-107790: GAO-26-107790, "Disaster Response: Lessons Learned in Supporting Mothers and Young
Children" (10 pages; a work of the U.S. government, https://www.gao.gov/copyright). Its bundle is the
one this project's corpus was built from, minus the base64 page images in document.json (pages[*].image),
which tglw never reads.
"""

from __future__ import annotations

import gzip
import json
import shutil
from importlib.resources import files
from pathlib import Path

NAMES = ("gao-26-107790",)
CLASSES = {"gao-26-107790": "gao"}


def _root() -> Path:
    return Path(str(files(__name__)))


def pdf(name: str) -> Path:
    return _root() / f"{name}.pdf"


def sha256(name: str) -> str:
    return json.loads((_root() / f"{name}.bundle" / "manifest.json").read_text())["source_sha256"]


def install_bundle(name: str, cache_dir: Path) -> Path:
    """Write the sample's bundle to cache_dir/<pdf sha256>/ (document.json, pages.json, manifest.json)."""
    src = _root() / f"{name}.bundle"
    dest = cache_dir / sha256(name)
    tmp = dest.with_name(dest.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    shutil.copyfile(src / "manifest.json", tmp / "manifest.json")
    for part in ("document.json", "pages.json"):
        (tmp / part).write_bytes(gzip.decompress((src / f"{part}.gz").read_bytes()))
    shutil.rmtree(dest, ignore_errors=True)
    tmp.rename(dest)
    return dest
