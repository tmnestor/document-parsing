"""Fail-fast overflow backstop.

Renders each ground-truth entry and collects any FitError raised by fit_text
(a string that cannot fit its box even after lossless wrap/shrink). Routine
length variation is handled at render time and never appears here — this gate
fires only for genuinely-impossible geometry, a real layout design error.

Rendering (rather than re-deriving each field's string) means the backstop
tests exactly the same code path that `generate` runs, with no duplication of
per-renderer string construction.
"""

from collections.abc import Callable

from generators.common import FitError


class OverflowError_(RuntimeError):
    """Aggregated render-overflow report across a ground-truth file."""


def check_overflow(
    gt_data: dict,
    layouts: dict,
    renderer: Callable[[dict, dict], object],
) -> list[str]:
    """Render each entry; collect a violation line for every FitError.

    Args:
        gt_data: Mapping of case id -> entry dict.
        layouts: Layout registry (layout id -> layout dict).
        renderer: The doc type's renderer, called as renderer(entry, layout).

    Returns:
        A list of human-readable violation lines (empty if everything fits).
        Never raises for a fitting document — collects all problems in one pass.
    """
    violations: list[str] = []
    for case_id, entry in gt_data.items():
        layout_ref = entry.get("layout", "")
        layout = layouts.get(layout_ref)
        if not layout:
            continue
        e = dict(entry)
        e["case_id"] = str(case_id)
        try:
            renderer(e, layout)
        except FitError as exc:
            first = str(exc).splitlines()[0]
            violations.append(f"{case_id} / {layout_ref}: {first}")
    return violations


def build_overflow_error(violations: list[str], *, layout_dir: str = "config/layouts/") -> OverflowError_:
    """Build a four-element fail-fast diagnostic from collected violations."""
    listing = "\n    ".join(violations)
    return OverflowError_(
        "Content overflow: fields cannot fit their boxes losslessly.\n"
        f"  What:     {len(violations)} field(s) overflow even after wrap/shrink:\n"
        f"    {listing}\n"
        f"  Where:    the field_budgets entries in {layout_dir}*.yml\n"
        "  Expected: each listed field's content <= its budget width, or a larger\n"
        "            max_lines / lower min_font so it fits losslessly.\n"
        "  Recover:  widen `width` (or raise `max_lines`) for the listed fields in\n"
        "            the layout YAML, or shorten the source strings; never truncate."
    )
