"""MANIFEST for data/B: which task-B sidecars are delivered, with producer and version.

python -m tglw.pagemap.manifest           # print only
python -m tglw.pagemap.manifest --write   # rewrite data/B/MANIFEST.json (deliver does this itself)
"""

from __future__ import annotations

import json
import sys
from datetime import date

from tglw.common import paths, registry

from .build import DELIVERED_DIR, write_json

PARTS = {"pagemap": ".pagemap.json", "toc": ".toc.json"}


def current_version() -> int:
    path = DELIVERED_DIR / "MANIFEST.json"
    return json.loads(path.read_text()).get("version", 1) if path.exists() else 1


def manifest(version: int) -> dict:
    parts = {}
    for part, suffix in PARTS.items():
        files = sorted(p.name for p in DELIVERED_DIR.glob(f"*{suffix}"))
        producers = sorted({json.loads((DELIVERED_DIR / f).read_text())["producer"] for f in files})
        parts[part] = {
            "status": "delivered" if files else "pending",
            "pattern": f"data/B/<doc>{suffix}",
            "count": len(files),
            "producers": producers,
            "missing": [d.name for d in registry.docs() if files and f"{d.name}{suffix}" not in files],
        }
    out = {
        "task": "B",
        "version": version,
        "updated": date.today().isoformat(),
        "parts": parts,
        "archives": sorted(p.name for p in DELIVERED_DIR.glob("v[0-9]*") if p.is_dir()),
        "label_key": "tglw.pagemap.normalize_label",
        "lookup": "tglw.pagemap.build.pdf_pages(sidecar, text, context=None)",
    }
    if paths.RESEARCH:
        out |= dict(getattr(paths._research, "PAGEMAP_NOTES", {}))
    return out


def write(version: int) -> dict:
    m = manifest(version)
    write_json(DELIVERED_DIR / "MANIFEST.json", m)
    return m


def main(argv: list[str]) -> dict:
    m = write(current_version()) if "--write" in argv else manifest(current_version())
    json.dump({"version": m["version"], **m["parts"]}, sys.stdout, indent=1)
    print()
    return m


if __name__ == "__main__":
    main(sys.argv[1:])
