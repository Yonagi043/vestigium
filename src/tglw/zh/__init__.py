"""Chinese support: document detection, display clean-up, traditional/simplified folding, and the
Chinese heading / cross-reference / page-number / definition rules (task ZH)."""

from .detect import is_cjk_doc, profile
from .display import normalize_display
from .fold import FOLD_AVAILABLE, fold
from .sentences import sentences
from .switches import DISPLAY_ENV, display_on

__all__ = ["DISPLAY_ENV", "FOLD_AVAILABLE", "display_on", "fold", "is_cjk_doc", "normalize_display", "profile",
           "sentences"]
