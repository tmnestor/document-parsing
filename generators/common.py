"""Shared utilities for synthetic document generation.

Font loading, text drawing helpers, and ABN/GST validation.

Every page this corpus ships is pristine. The predecessor's image degradation
(`generators/degradation/`) does not cross into this repo — design §3 puts
degraded and scanned pages out of scope — so nothing here dirties a page.

Draw helpers take a `DrawSurface`, which at render time is the coverage
invariant's `TranscriptDraw` proxy rather than a bare `ImageDraw`: text
reaching the canvas from here is checked against an emitted event.
"""

import random
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from PIL import ImageFont

from generators.transcript import DrawSurface

Font = ImageFont.FreeTypeFont | ImageFont.ImageFont

_FONT_CACHE: dict[tuple[int, str, bool], Font] = {}

# Maps id(font) -> Path it was loaded from, so fit measurement can assert it is
# using a vendored face rather than something the caller built itself.
_FONT_SOURCE: dict[int, Path] = {}


class FontSourceError(RuntimeError):
    """Raised when a font used for measurement is not a vendored face."""


class FontFamilyError(RuntimeError):
    """Raised when a layout names a font family that is not vendored."""


# Bundled fonts directory (committed to repo for cross-platform consistency)
_BUNDLED_FONTS_DIR = Path(__file__).resolve().parent.parent / "fonts"

# The vendored font families, as {family: (regular, bold)} filenames.
#
# There is deliberately NO system-font fallback. A fallback cannot be a
# reproducibility-neutral convenience here: a missing face would silently swap
# glyph metrics, which re-runs every `fit_text()` decision and diverges the
# rendered pixels Mac<->PROD without erroring. Since these files are vendored
# and tracked in git, a missing one means a broken checkout, and the only safe
# response is to fail loudly. This is the single-path guarantee `fit_text`
# rests on, and therefore what makes a render reproducible across machines.
#
# All three families are metric-compatible substitutes for the proprietary
# fonts real Australian business documents actually use, so they are both
# redistributable (OFL) and closer to the target distribution than a generic
# face would be:
#   carlito         -> Calibri  (the modern invoice/statement register)
#   liberation_sans -> Arial    (the older, more conservative register)
#   liberation_mono -> Courier  (thermal/dot-matrix receipts)
FONT_FAMILIES: dict[str, tuple[str, str]] = {
    "carlito": ("Carlito-Regular.ttf", "Carlito-Bold.ttf"),
    "liberation_sans": ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf"),
    "liberation_mono": ("LiberationMono-Regular.ttf", "LiberationMono-Bold.ttf"),
}


def font_path(family: str, *, bold: bool = False) -> Path:
    """Resolve a family+weight to its single vendored file path.

    Args:
        family: A key of `FONT_FAMILIES`.
        bold: Select the bold face rather than the regular one.

    Returns:
        Path to the vendored TTF. The path is not checked for existence here;
        `load_font` reports a missing file with its own diagnostic.

    Raises:
        FontFamilyError: `family` is not a vendored family.
    """
    faces = FONT_FAMILIES.get(family)
    if faces is None:
        raise FontFamilyError(
            "Unknown font family.\n"
            f"  What:     '{family}' is not a vendored font family.\n"
            f"  Where:    the `family:` key on a block, or `defaults.family` in "
            f"config/layouts/*.yml\n"
            f"  Expected: one of {sorted(FONT_FAMILIES)}, e.g.\n"
            f"              defaults:\n"
            f"                family: carlito\n"
            f"  Recover:  set `family:` to a vendored family, or vendor the new "
            f"face in fonts/ and register it in FONT_FAMILIES in generators/common.py."
        )
    return _BUNDLED_FONTS_DIR / (faces[1] if bold else faces[0])


def load_font(size: int, *, family: str, bold: bool = False) -> Font:
    """Load a vendored font face, cached.

    Resolves to exactly one file per (family, weight) — see `FONT_FAMILIES` for
    why there is no system fallback.

    Args:
        size: Font size in points.
        family: A key of `FONT_FAMILIES`, e.g. "carlito".
        bold: Use bold weight.

    Returns:
        Loaded PIL font object.

    Raises:
        FontFamilyError: `family` is not a vendored family.
        FileNotFoundError: The vendored file for this family/weight is missing
            or unreadable.
    """
    key = (size, family, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]

    path = font_path(family, bold=bold)
    try:
        font: Font = ImageFont.truetype(str(path), size)
    except OSError as err:
        raise FileNotFoundError(
            "Vendored font file is missing or unreadable.\n"
            f"  What:     cannot load {path.name} for family '{family}' "
            f"(bold={bold}): {err}.\n"
            f"  Where:    {path}\n"
            f"  Expected: the vendored face present in {_BUNDLED_FONTS_DIR}, "
            f"tracked in git alongside its OFL license file.\n"
            "  Recover:  restore the fonts/ directory from the repo "
            "(`git checkout -- fonts/`); never substitute a system font, which "
            "would silently change fit decisions and rendered pixels."
        ) from None

    _FONT_SOURCE[id(font)] = path
    _FONT_CACHE[key] = font
    return font


def font_source_path(font: Font) -> Path | None:
    """Return the file a font was loaded from, or None if unknown."""
    return _FONT_SOURCE.get(id(font))


def assert_bundled_font(font: Font) -> None:
    """Fail loud if `font` did not come from the vendored fonts/ directory.

    `load_font` can no longer return a system font, so this now guards the
    remaining way an unvendored face could reach measurement: a caller that
    builds an `ImageFont` itself and passes it in. Measuring against such a
    font would diverge Mac<->PROD and silently corrupt fit decisions.

    Raises:
        FontSourceError: the font is not a vendored face.
    """
    src = font_source_path(font)
    if src is not None and _BUNDLED_FONTS_DIR in src.parents:
        return
    raise FontSourceError(
        "Font used for measurement is not a bundled font.\n"
        f"  What:     fit measurement requires a vendored face; got {src}.\n"
        f"  Where:    bundled fonts directory {_BUNDLED_FONTS_DIR}\n"
        f"  Expected: a face loaded via load_font(family=...) from one of "
        f"{sorted(FONT_FAMILIES)}, e.g. fonts/Carlito-Regular.ttf.\n"
        "  Recover:  load the font with load_font(size, family=..., bold=...) "
        "instead of constructing an ImageFont directly."
    )


FitStrategy = str  # one of: "shrink", "wrap", "shrink_then_wrap"
_FIT_STRATEGIES = ("shrink", "wrap", "shrink_then_wrap")


@dataclass(frozen=True)
class FitResult:
    """Lossless render plan for a field: the full string laid out to fit its box."""

    lines: list[str]
    size: int
    line_height: int


class FitError(RuntimeError):
    """Raised when a string cannot fit its box even at the font floor / max lines."""


def _text_width(text: str, size: int, *, family: str, bold: bool) -> int:
    """Pixel width of `text` at `size`, measured against the bundled font."""
    font = load_font(size, family=family, bold=bold)
    assert_bundled_font(font)
    bbox = font.getbbox(text)
    return int(bbox[2] - bbox[0])


def _fit_error_message(text: str, *, width: int, min_font: int, max_lines: int, fit: str) -> str:
    """Four-element diagnostic body (caller prepends entry/field context)."""
    return (
        "string cannot fit its box losslessly.\n"
        f"  What:     {text!r} exceeds width {width}px at min_font {min_font} "
        f"across max_lines {max_lines} (fit={fit}).\n"
        "  Where:    the field's `field_budgets` entry in its config/layouts/*.yml.\n"
        "  Expected: width >= measured, or larger max_lines, or lower min_font; "
        "fit one of shrink|wrap|shrink_then_wrap.\n"
        "  Recover:  raise `width` (or `max_lines`) for this field in the layout YAML; "
        "never truncate the string."
    )


def _wrap_to_width(text: str, *, width: int, size: int, family: str, bold: bool) -> list[str] | None:
    """Greedy word-wrap at `size`.

    Returns lines each within `width`, or None if a single word cannot fit
    (caller treats None as unfittable — never splits a word / truncates).
    """
    words = text.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        if _text_width(word, size, family=family, bold=bold) > width:
            return None  # unbreakable word wider than the box
        candidate = word if not current else f"{current} {word}"
        if _text_width(candidate, size, family=family, bold=bold) <= width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def fit_text(
    text: str,
    *,
    width: int,
    fit: FitStrategy,
    min_font: int,
    max_lines: int,
    nominal_size: int,
    family: str,
    bold: bool = False,
) -> FitResult:
    """Compute a lossless layout of `text` fitting within `width` px.

    Never truncates. Applies the field's `fit` strategy and raises FitError if
    the full string cannot fit even at the font floor across max_lines.

    Args:
        text: The full string to lay out (rendered verbatim).
        width: Horizontal box in pixels the string must fit within.
        fit: Strategy — "shrink", "wrap", or "shrink_then_wrap".
        min_font: Smallest font size shrinking may reach.
        max_lines: Lines the field may occupy.
        nominal_size: The field's default font size.
        family: Vendored font family to measure against, a key of
            `FONT_FAMILIES`. Required — there is no Python-side default,
            because the family is a layout decision that must be visible in
            the layout YAML.
        bold: Measure with the bold weight.

    Returns:
        FitResult with the laid-out lines, chosen size, and line height.

    Raises:
        FitError: the string cannot fit losslessly.
        ValueError: unknown fit strategy.
    """
    if fit not in _FIT_STRATEGIES:
        raise ValueError(f"unknown fit strategy {fit!r}; allowed: {_FIT_STRATEGIES}")

    def line_height(size: int) -> int:
        fnt = load_font(size, family=family, bold=bold)
        return int(fnt.size) if isinstance(fnt, ImageFont.FreeTypeFont) else size

    # Fits as-is at nominal size on one line -> unchanged (day-one path).
    if _text_width(text, nominal_size, family=family, bold=bold) <= width:
        return FitResult(lines=[text], size=nominal_size, line_height=line_height(nominal_size))

    if fit == "shrink":
        for size in range(nominal_size - 1, min_font - 1, -1):
            if _text_width(text, size, family=family, bold=bold) <= width:
                return FitResult(lines=[text], size=size, line_height=line_height(size))
        raise FitError(
            _fit_error_message(text, width=width, min_font=min_font, max_lines=max_lines, fit=fit)
        )

    if fit == "wrap":
        lines = _wrap_to_width(text, width=width, size=nominal_size, family=family, bold=bold)
        if lines is None or len(lines) > max_lines:
            raise FitError(
                _fit_error_message(text, width=width, min_font=min_font, max_lines=max_lines, fit=fit)
            )
        return FitResult(lines=lines, size=nominal_size, line_height=line_height(nominal_size))

    if fit == "shrink_then_wrap":
        for size in range(nominal_size, min_font - 1, -1):
            if _text_width(text, size, family=family, bold=bold) <= width:
                return FitResult(lines=[text], size=size, line_height=line_height(size))
            wrapped = _wrap_to_width(text, width=width, size=size, family=family, bold=bold)
            if wrapped is not None and len(wrapped) <= max_lines:
                return FitResult(lines=wrapped, size=size, line_height=line_height(size))
        raise FitError(
            _fit_error_message(text, width=width, min_font=min_font, max_lines=max_lines, fit=fit)
        )

    raise ValueError(f"unhandled fit strategy {fit!r}")


def _fit_from_budget(text: str, budget: dict, nominal_size: int, *, family: str, bold: bool) -> FitResult:
    """Run fit_text using a field's budget dict (width/fit/min_font/max_lines)."""
    return fit_text(
        text,
        width=budget["width"],
        fit=budget["fit"],
        min_font=budget["min_font"],
        max_lines=budget["max_lines"],
        nominal_size=nominal_size,
        family=family,
        bold=bold,
    )


def draw_fitted_left(
    draw: DrawSurface,
    text: str,
    x: int,
    y: int,
    *,
    budget: dict,
    nominal_size: int,
    family: str,
    bold: bool = False,
    fill: str = "black",
    line_spacing: int | None = None,
) -> int:
    """Left-align `text` at x, fitting it to its budget. Returns the advanced y.

    `line_spacing` overrides the per-line vertical advance (e.g. the layout's
    line_height); when None the font's own height is used. Advancing by a
    caller-supplied line_spacing keeps the single-line case pixel-identical to
    the pre-fit renderer while multi-line wrap pushes following content down.
    """
    r = _fit_from_budget(text, budget, nominal_size, family=family, bold=bold)
    font = load_font(r.size, family=family, bold=bold)
    spacing = line_spacing if line_spacing is not None else r.line_height
    for line in r.lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += spacing
    return y


def draw_fitted_center(
    draw: DrawSurface,
    text: str,
    y: int,
    canvas_width: int,
    *,
    budget: dict,
    nominal_size: int,
    family: str,
    bold: bool = False,
    fill: str = "black",
    line_spacing: int | None = None,
) -> int:
    """Center `text` within canvas_width, fitting it to its budget. Returns advanced y.

    See draw_fitted_left for `line_spacing` semantics.
    """
    r = _fit_from_budget(text, budget, nominal_size, family=family, bold=bold)
    font = load_font(r.size, family=family, bold=bold)
    spacing = line_spacing if line_spacing is not None else r.line_height
    for line in r.lines:
        bbox = font.getbbox(line)
        w = int(bbox[2] - bbox[0])
        x = (canvas_width - w) // 2
        draw.text((x, y), line, font=font, fill=fill)
        y += spacing
    return y


def draw_fitted_right(
    draw: DrawSurface,
    text: str,
    x_right: int,
    y: int,
    *,
    budget: dict,
    nominal_size: int,
    family: str,
    bold: bool = False,
    fill: str = "black",
    line_spacing: int | None = None,
) -> int:
    """Right-align `text` to x_right, fitting it to its budget. Returns advanced y.

    See draw_fitted_left for `line_spacing` semantics.
    """
    r = _fit_from_budget(text, budget, nominal_size, family=family, bold=bold)
    font = load_font(r.size, family=family, bold=bold)
    spacing = line_spacing if line_spacing is not None else r.line_height
    for line in r.lines:
        bbox = font.getbbox(line)
        w = int(bbox[2] - bbox[0])
        draw.text((x_right - w, y), line, font=font, fill=fill)
        y += spacing
    return y


def draw_text_left(
    draw: DrawSurface,
    text: str,
    x: int,
    y: int,
    font: Font,
    fill: str = "black",
) -> None:
    """Draw text left-aligned at (x, y)."""
    draw.text((x, y), text, font=font, fill=fill)


def draw_text_right(
    draw: DrawSurface,
    text: str,
    x_right: int,
    y: int,
    font: Font,
    fill: str = "black",
) -> None:
    """Draw text right-aligned to x_right."""
    bbox = font.getbbox(text)
    text_width = int(bbox[2] - bbox[0])
    left = x_right - text_width
    draw.text((left, y), text, font=font, fill=fill)


def draw_text_center(
    draw: DrawSurface,
    text: str,
    y: int,
    width: int,
    font: Font,
    fill: str = "black",
) -> None:
    """Draw text centered within given width."""
    bbox = font.getbbox(text)
    text_width = int(bbox[2] - bbox[0])
    x = (width - text_width) // 2
    draw.text((x, y), text, font=font, fill=fill)


def draw_separator(
    draw: DrawSurface,
    y: int,
    width: int,
    margin: int,
    font: Font,
    fill: str = "black",
    char: str = "-",
) -> None:
    """Draw a separator line made of a repeated glyph (a dash, by default)."""
    dash_bbox = font.getbbox(char)
    dash_width = int(dash_bbox[2] - dash_bbox[0])
    count = (width - 2 * margin) // dash_width
    dash = char * count
    draw.text((margin, y), dash, font=font, fill=fill)


def draw_separator_line(
    draw: DrawSurface,
    x1: int,
    x2: int,
    y: int,
    color: str = "black",
    width: int = 1,
) -> None:
    """Draw a thin horizontal rule from x1 to x2 at vertical position y.

    Args:
        draw: PIL ImageDraw object.
        x1: Left x coordinate.
        x2: Right x coordinate.
        y: Vertical position.
        color: Line color (hex or name).
        width: Line width in pixels.
    """
    draw.line([(x1, y), (x2, y)], fill=color, width=width)


def draw_line_item(
    draw: DrawSurface,
    desc: str,
    amount: str,
    y: int,
    font: Font,
    margin: int,
    width: int,
    fill: str = "black",
) -> None:
    """Draw a receipt line item: left-aligned description, right-aligned amount."""
    draw.text((margin, y), desc, font=font, fill=fill)
    draw_text_right(draw, amount, x_right=width - margin, y=y, font=font, fill=fill)


def fmt_amount(amount: Decimal | float | int) -> str:
    """Format a numeric amount as $X,XXX.XX."""
    d = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"${d:,.2f}"


# --- ABN Validation (Australian Business Number checksum) ---

_ABN_WEIGHTS = [10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19]


def validate_abn(abn: str) -> bool:
    """Validate an Australian Business Number using the official checksum algorithm.

    Args:
        abn: ABN string, with or without spaces (e.g. "53 004 085 616" or "53004085616").

    Returns:
        True if checksum is valid.
    """
    digits_str = abn.replace(" ", "")
    if len(digits_str) != 11 or not digits_str.isdigit():
        return False
    digits = [int(d) for d in digits_str]
    digits[0] -= 1  # Subtract 1 from first digit, per the published algorithm
    total = sum(d * w for d, w in zip(digits, _ABN_WEIGHTS, strict=True))
    return total % 89 == 0


def generate_abn() -> str:
    """Generate a valid 11-digit ABN with correct checksum.

    Returns:
        ABN formatted as "XX XXX XXX XXX".
    """
    base = [random.randint(0, 9) for _ in range(9)]
    for d0 in range(1, 10):
        for d1 in range(0, 10):
            digits = [d0, d1, *base]
            test = digits.copy()
            test[0] -= 1
            total = sum(d * w for d, w in zip(test, _ABN_WEIGHTS, strict=True))
            if total % 89 == 0:
                s = "".join(str(d) for d in digits)
                return f"{s[:2]} {s[2:5]} {s[5:8]} {s[8:11]}"
    msg = "Failed to generate valid ABN"
    raise RuntimeError(msg)


# --- GST Calculation ---


def calculate_gst_inclusive(total: Decimal) -> tuple[Decimal, Decimal]:
    """Calculate GST from a GST-inclusive total.

    GST = total / 11 (rounded to 2dp).
    Ex-GST = total - GST.

    Args:
        total: GST-inclusive total amount.

    Returns:
        (gst_amount, ex_gst_amount) both rounded to 2 decimal places.
    """
    gst = (total / Decimal("11")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    ex_gst = total - gst
    return gst, ex_gst


def calculate_gst_exclusive(subtotal: Decimal) -> tuple[Decimal, Decimal]:
    """Calculate GST from an ex-GST subtotal.

    GST = subtotal * 0.10.
    Total = subtotal + GST.

    Args:
        subtotal: Ex-GST subtotal.

    Returns:
        (gst_amount, gst_inclusive_total) both rounded to 2 decimal places.
    """
    gst = (subtotal * Decimal("0.10")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    total = subtotal + gst
    return gst, total
