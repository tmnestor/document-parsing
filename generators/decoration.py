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
  the whole transcript's length on every one of 55 receipts; docling 124%.
  Pure insertions, penalising a system for reading what the page plainly shows.
- **Dot leaders, included by the corpus.** `gemma-4-12B-it-qat-w4a16-ct` ran
  away to 128,768 characters on two pages and could not complete them at all;
  `InternVL3.5-8B` omitted them entirely.

So the rule is now one rule: a run of repeated glyphs is decoration and is not
recorded. The glyphs are still **drawn** — the page is unchanged, and every
prediction made against it stays valid — only the transcript changes.

**Where the rule lives.** Which glyphs form a run, and how long a run must be,
are declared in `config/serialisation.yml` as `decoration_glyphs` and
`decoration_min_run`, and reach this module as arguments. There are
deliberately no defaults here: a Python literal supplying either would put a
decision that changes ground truth somewhere the policy file cannot describe,
and reading `serialisation.yml` alone must answer what a transcript does with a
dot leader.

**Applied at serialise time, not at capture.** Capture records the ink; whether
a leader survives into the transcript is a convention. Keeping it here means a
change to the rule re-emits every transcript in seconds without re-rendering an
image (design §6) — the same property every other key in that file has.
"""

import re
from functools import cache


@cache
def _run_pattern(glyphs: str, min_run: int) -> re.Pattern[str]:
    """Compile (and cache) the trailing-run pattern for one policy.

    Args:
        glyphs: The characters that count as decoration, as a set of literals.
        min_run: How many consecutive glyphs make a run rather than punctuation.

    Returns:
        A pattern matching a trailing run and any whitespace after it.
    """
    return re.compile(rf"[{re.escape(glyphs)}]{{{min_run},}}\s*$")


def strip_decoration_run(text: str, *, glyphs: str, min_run: int) -> str:
    """Remove a trailing run of repeated decorative glyphs.

    The glyph class is deliberately narrow — punctuation used for rules and
    leaders — so that real content built from the same characters is untouched:
    the hyphen in "5-7 business days" and in a statement's date range are single
    glyphs, not runs. The threshold does the rest of that work; at the shipped
    value of four, an ellipsis ("continued...") and a decimal point survive
    while a 40-dot leader does not.

    Args:
        text: The captured string, as drawn.
        glyphs: `decoration_glyphs` from the serialisation policy.
        min_run: `decoration_min_run` from the serialisation policy.

    Returns:
        The string with any trailing decoration run removed, and trailing
        whitespace tidied. Text with no run is returned unchanged.
    """
    return _run_pattern(glyphs, min_run).sub("", text).rstrip()
