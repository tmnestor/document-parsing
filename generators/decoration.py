"""Repeated glyphs are decoration, wherever they are drawn.

Decided 2026-08-19, after the §8.6 calibration pass measured the cost of having
it both ways. A `rule` with a `fill_char` paints a row of repeated glyphs and
emits nothing, through the sanctioned `decoration()` escape hatch. A dot leader
appended to a table cell — the same device, drawn by a different primitive —
was captured verbatim. Which treatment a glyph run received depended on an
implementation detail no reader of the page can see.

Both halves of that split cost real accuracy:

- **Separator rules, excluded by the corpus.** Three of four systems transcribe
  them anyway. `gemma-4-12B-it-qat-w4a16-ct` inserts characters equal to 63% of
  the whole transcript's length on every one of 55 receipts; docling 124%. Pure
  insertions, penalising a system for reading what the page plainly shows.
- **Dot leaders, included by the corpus.** `gemma-4-12B-it-qat-w4a16-ct` ran
  away to 128,768 characters on two pages and could not complete them at all;
  `InternVL3.5-8B` omitted them entirely.

So the rule is now one rule: a run of repeated glyphs is decoration and is not
recorded. The glyphs are still **drawn** — the page is unchanged, and every
prediction made against it stays valid — only the capture changes.
"""

import re

# Three is punctuation, four is a leader. An ellipsis ("continued...") and a
# decimal point must survive; a 40-dot leader must not. The glyph class is
# deliberately narrow — punctuation used for rules and leaders — so that real
# content built from the same characters is untouched: the hyphen in "5-7
# business days" and in a statement's date range are single glyphs, not runs.
_DECORATION_RUN = re.compile(r"[.\-_=*~]{4,}\s*$")


def strip_decoration_run(text: str) -> str:
    """Remove a trailing run of repeated decorative glyphs.

    Applied at capture time, so the transcript records the content and not the
    typography that leads the eye to it.

    Args:
        text: The string about to be drawn.

    Returns:
        The string with any trailing decoration run removed, and trailing
        whitespace tidied. Text with no run is returned unchanged.
    """
    return _DECORATION_RUN.sub("", text).rstrip()
