"""VIS switches (E1-E7). Each is read from its environment variable at call time ("1"/"0"), else
from OVERRIDE (replays and tests), else from DEFAULTS. Items that only add information default on;
items that change what the model is told about cost or how images are rendered default off until a
pilot shows they help (ADAPTIVE_ZOOM did)."""

from __future__ import annotations

import os

DEFAULTS: dict[str, bool] = {
    "TOKEN_NOTE": False,  # E1: instructions and region description give image token sizes, not "costly"
    "FIG_HINT": True,  # E2: a figure line in read ends with "region figN" for the image
    "FIG_LABELS": False,  # E2: a figure line keeps its caption and first labels, the rest as "+N labels"
    # (off: chart labels can be the evidence a question asks for)
    "ADAPTIVE_ZOOM": True,  # E3: region's default scale follows the smallest text in the box (1.5-3.0; vision pilot)
    "CROP_BOX": True,  # E3: region's caption gives the crop's page box, for zooming into part of it
    "OCR_NOTE": True,  # E4: scanned pages and OCR'd figure text are marked as OCR
    "DRAWINGS": True,  # E5: a figure caption without a picture gets the box of its drawing
    "OUTLINE_FIGS": True,  # E6: outline lines give the section's figure count
    "LITERAL_FIGS_LAST": True,  # E7: literal hits inside figures rank after hits in the text
}
OVERRIDE: dict[str, bool] = {}


def env_name(key: str) -> str:
    return f"TGLW_VIS_{key}"


def on(key: str) -> bool:
    raw = os.environ.get(env_name(key))
    if raw is not None:
        return raw.strip().lower() not in ("0", "false", "off", "no", "")
    return OVERRIDE.get(key, DEFAULTS[key])


def table() -> list[tuple[str, bool, bool]]:
    """(environment variable, default, current) for every switch."""
    return [(env_name(k), d, on(k)) for k, d in DEFAULTS.items()]
