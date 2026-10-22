"""Figures (VIS items E1-E7): what the reader is told about figures and how region crops them.

Switches (environment TGLW_VIS_<NAME>, "1"/"0"; defaults in switches.DEFAULTS):
- E1 TOKEN_NOTE (off): cost.instructions / cost.region_description give image token sizes, not "costly";
- E2 FIG_HINT (on), FIG_LABELS (off): inline.picture_line -- "(image: region figN)"; caption and first labels;
- E3 ADAPTIVE_ZOOM (on): zoom.default_scale / zoom.max_side; CROP_BOX (on): zoom.crop_note;
- E4 OCR_NOTE (on): ocr.mark_read on read, ocr.region_text_header in region; OCR'd figure labels;
- E5 DRAWINGS (on): drawings.hint after a figure caption whose figure Docling did not cut out;
- E6 OUTLINE_FIGS (on): counts.mark_outline;
- E7 LITERAL_FIGS_LAST (on): rank.block_key / rank.page_key for literal search.
annotate.read and annotate.outline apply E2, E4, E5 and E6 to finished text.
"""

from . import annotate, cost, counts, drawings, inline, ocr, rank, switches, zoom

__all__ = ["annotate", "cost", "counts", "drawings", "inline", "ocr", "rank", "switches", "zoom"]
