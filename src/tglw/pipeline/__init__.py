"""One command for all per-document sidecars: `<COMMAND> pipeline ...` or `python -m tglw.pipeline ...`.

build <doc ...|--all> [--out DIR]   # B -> A -> C1 -> C2 -> D1 -> D2 -> M
check <doc ...|--all> [--data DIR]  # completeness, versions, MCP readability
compare --run DIR                   # a build against the delivered sidecars

Each step runs in a child process whose `tglw.common.paths.DATA` points at a scratch data root, so
every task's own delivered generator writes there and reads its upstream sidecars from there.
"""
