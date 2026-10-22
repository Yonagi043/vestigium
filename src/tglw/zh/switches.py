"""Switches for showing Chinese text to the reader (read at call time, so tests can set them)."""

from __future__ import annotations

import os

from tglw.common.bundle import Doc

DISPLAY_ENV = "TGLW_ZH_DISPLAY"


DEFAULT_MODE = "auto"
_MODES = {"0": "off", "off": "off", "false": "off", "no": "off", "1": "on", "on": "on", "true": "on", "yes": "on",
          "auto": "auto"}


def display_mode() -> str:
    """TGLW_ZH_DISPLAY: unset -> "auto" (default: only documents that is_cjk_doc accepts), 0/off -> "off",
    1/on -> "on" (every text with Chinese characters). An unknown value falls back to the default."""
    return _MODES.get(os.environ.get(DISPLAY_ENV, "").strip().lower(), DEFAULT_MODE)


def display_on(doc: Doc | str) -> bool:
    """Whether `normalize_display` should be applied to this document's text."""
    mode = display_mode()
    if mode == "auto":
        from .detect import is_cjk_doc

        return is_cjk_doc(doc)
    return mode == "on"
