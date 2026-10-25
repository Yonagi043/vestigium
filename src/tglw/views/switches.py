"""On/off switches of the views, set in the environment before import (tests and replays change V1 itself)."""

from __future__ import annotations

import os


def switch(env: str, default: bool) -> bool:
    """An on/off switch set in the environment ("0", "false", "off", "no" turn it off), else the default."""
    raw = os.environ.get(env, "").strip().lower()
    return default if not raw else raw not in ("0", "false", "off", "no")


# footnote_addresses (C3): "fn: 69" reads footnote 69; heading_next (C4): a search hit that is one short heading also
# shows the block after it; running_headers (task SRC, F1): page headers Docling took for headings (A v4's list) are
# not shown, like the headers Docling put in the furniture; zh_addresses (task ZH): "第二十七条", "第三章", "附錄二" read
# the section A numbered so; table_notes (task SRC, D2 v4): a table's head says when rows may be merged or values
# moved; loose_tables (D2 v4): a table Docling did not recognise is shown rebuilt where its first figure is; task SRC's
# F4 (D1 v3 boxfix): box_fix -- region and preview take a misplaced block's true box; dup_blocks -- a misplaced copy of
# a line another shown block reads is hidden; rotated_pages -- garbled text-layer blocks of rotated pages are hidden
# heading_next, table_notes, loose_tables, box_fix, dup_blocks and rotated_pages are on since the phase-B integration
# regression (65 paired development questions); running_headers stays off. Off by default (task SRC's ⑥):
# empty_cells -- a table's empty cells are written "·" (tables.empty), so a row keeps its columns;
# table_rows -- rows of a scanned table Docling took for headings (task SRC's TR sidecar) are text, not "###" lines
V1 = {"footnote_addresses": switch("TGLW_VIEWS_FOOTNOTES", True), "heading_next": switch("TGLW_VIEWS_HEADING_NEXT", True),
      "running_headers": switch("TGLW_SRC_RUNNING_HEADERS", False), "zh_addresses": switch("TGLW_ZH_ADDRESSES", True),
      "table_notes": switch("TGLW_SRC_TABLE_NOTES", True), "loose_tables": switch("TGLW_SRC_LOOSE_TABLES", True),
      "box_fix": switch("TGLW_SRC_BOX_FIX", True), "dup_blocks": switch("TGLW_SRC_DUP_BLOCKS", True),
      "rotated_pages": switch("TGLW_SRC_ROTATED_PAGES", True), "empty_cells": switch("TGLW_SRC_EMPTY_CELLS", False),
      "table_rows": switch("TGLW_SRC_TABLE_ROWS", False)}
