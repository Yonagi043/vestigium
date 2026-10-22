"""Child-process entry: point DATA at a scratch root, then run one pipeline step there.

python -m tglw.pipeline.worker <data_root> <step> [doc ...]

The patch must happen before any task module is imported: they bind DATA at import time.
"""

from __future__ import annotations

import sys
from pathlib import Path

import tglw.common.paths as paths


def main(argv: list[str]) -> None:
    root, step, names = Path(argv[0]), argv[1], argv[2:]
    paths.DATA = root
    if step == "mcp":
        import json

        from .check import mcp_smoke

        print(json.dumps(mcp_smoke(names), ensure_ascii=False))
        return
    from . import steps

    steps.STEPS[step](names)


if __name__ == "__main__":
    main(sys.argv[1:])
