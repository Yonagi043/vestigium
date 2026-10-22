"""Precompute data/M/wip/doc_index.json for list_documents: pages, tokens, structure, TOC per document.

python -m tglw.mcp_server.index
"""

from __future__ import annotations

import json
import os

from tglw.common import registry
from tglw.common.paths import DATA
from tglw.views import view
from tglw.views.api import _block_tokens


def build() -> list[dict]:
    rows = []
    for d in registry.docs():
        v = view(d.name)
        rows.append({
            "doc": d.name,
            "cls": d.cls,
            "pages": d.pages,
            "tokens": sum(_block_tokens(d.name)),
            "structure": (v.side.structure or {}).get("verdict", {}).get("status", "none"),
            "toc": (v.side.toc or {}).get("verdict", {}).get("status") == "found",
            "printed_labels": (v.side.pagemap or {}).get("verdict", {}).get("status", "none"),
        })
    return rows


if __name__ == "__main__":
    out = DATA / "M" / "wip" / "doc_index.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(build(), indent=1, ensure_ascii=False))
    os.replace(tmp, out)
    print(out)
