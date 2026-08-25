# Layout and Structure Ground Truth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Emit the element boxes, classes, reading order and table structure the renderer already computes and discards, in OmniDocBench's annotation shape, without changing a single pixel or transcript.

**Architecture:** Geometry is captured where text already is — inside `TranscriptDraw`, the proxy that intercepts every `draw.text()` call and attributes it to the authorising event. Each intercepted call becomes a **span** with its own box; a content event's **block** box is the union of its spans. Two new pure projections read the enriched event stream: one emits OmniDocBench `layout_dets` JSON, one emits table HTML. `serialise` is untouched.

**Tech Stack:** Python 3.12, Pillow (already present, for font metrics), PyYAML, typer, rich. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-26-layout-and-structure-ground-truth-design.md`

## Global Constraints

- **`tests/` is gitignored.** Tests are written and run but **never committed**. Every `git add` in this plan lists source and config files only. `git add tests/...` silently adds nothing — do not include it.
- **The corpus must not move.** All 165 images and all 165 transcripts must stay byte-identical. This is verified in Task 11 and is a hard gate, not a nicety: a moved pixel is a corpus revision that invalidates every prediction already scored.
- **Never bypass pre-commit hooks.** No `--no-verify`. **No Claude attribution in commit messages.**
- Commit format: gitmoji + conventional type, e.g. `✨ feat:`, `🐛 fix:`, `♻️ refactor:`.
- **Line length 108.** Gates before every commit: `conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 .`, then `ruff format .`, then `mypy generators --ignore-missing-imports`, then `python -m pytest tests/ -q`.
- **Python 3.12 typing:** `X | Y`, never `Union[X, Y]`. No `from __future__ import annotations`. **No `TYPE_CHECKING` guards for types used in runtime signatures.**
- `pathlib.Path` for paths. Google-style docstrings. B904: in `except` blocks always `raise ... from err` or `from None`.
- **YAML is the single source of truth.** No Python default may shadow a config value; every config key is required; a missing key fails fast.
- **Every fail-fast error carries four elements:** What / Where (absolute path + key) / Expected (a concrete example) / Recover (a one-line remediation). `tests/helpers.py::assert_diagnostic_error` asserts all four.
- **`scoring/` must never import `generators/`** — `tests/scoring/test_boundaries.py` enforces this. Nothing in this plan touches `scoring/`.
- Run everything through conda: `conda run -n docparse <cmd>`.

---

## File Structure

| File | Responsibility |
|---|---|
| `generators/layout_dsl/categories.py` (create) | primitive → OmniDocBench `category_type`; exhaustive over the primitive registry |
| `generators/transcript.py` (modify) | `Event` gains `poly`/`category_type`/`order`/`spans`; `TranscriptDraw` measures each drawn line; recorder holds the box invariant |
| `generators/layout_dsl/primitives_text.py` (modify) | emit sites pass `category_type` |
| `generators/layout_dsl/primitives_table.py` (modify) | table block emits `category_type`; cells stay box-free |
| `generators/layout_dsl/primitives_container.py` (modify) | containers stay annotation-free |
| `generators/tables.py` (create) | events → table HTML |
| `generators/layout.py` (create) | events → `layout_dets` JSON |
| `generators/pipeline.py` (modify) | `export` writes `layout/` and `tables/` |
| `generators/export.py` (modify) | manifest rows gain `layout` and `tables` paths |

Tests: `tests/test_categories.py`, `tests/test_transcript_geometry.py`, `tests/test_tables_html.py`, `tests/test_layout_json.py`, `tests/test_corpus_unchanged.py`.

---

## Task 1: The category map

**Files:**
- Create: `generators/layout_dsl/categories.py`
- Test: `tests/test_categories.py`

**Interfaces:**
- Produces:
  - `CATEGORY_BY_PRIMITIVE: dict[str, str | None]`
  - `BLOCK_CATEGORIES: tuple[str, ...]`, `SPAN_CATEGORY: str`
  - `category_for(primitive: str) -> str | None`
  - `CategoryError(RuntimeError)`

- [ ] **Step 1: Write the failing test**

Create `tests/test_categories.py`:

```python
"""Every primitive has an OmniDocBench category, or an explicit None."""

import pytest

from generators.layout_dsl.categories import (
    BLOCK_CATEGORIES,
    CATEGORY_BY_PRIMITIVE,
    CategoryError,
    category_for,
)
from generators.layout_dsl.engine import PRIMITIVE_DRAWERS
from tests.helpers import assert_diagnostic_error


def test_every_registered_primitive_is_mapped():
    """A new primitive must declare its category before it can draw."""
    unmapped = sorted(set(PRIMITIVE_DRAWERS) - set(CATEGORY_BY_PRIMITIVE))

    assert not unmapped, f"primitives with no category: {unmapped}"


def test_the_map_declares_no_primitive_that_does_not_exist():
    stale = sorted(set(CATEGORY_BY_PRIMITIVE) - set(PRIMITIVE_DRAWERS))

    assert not stale, f"category map names primitives that are not registered: {stale}"


def test_text_bearing_primitives_carry_a_block_category():
    assert category_for("text") == "text_block"
    assert category_for("pair") == "text_block"
    assert category_for("block") == "text_block"
    assert category_for("banner") == "title"
    assert category_for("table") == "table"


def test_containers_and_whitespace_carry_no_category():
    """A panel is structure, not content; a spacer is nothing at all."""
    for primitive in ("panel", "split", "spacer"):
        assert category_for(primitive) is None


def test_every_declared_category_is_one_omnidocbench_names():
    declared = {c for c in CATEGORY_BY_PRIMITIVE.values() if c is not None}

    assert declared <= set(BLOCK_CATEGORIES), f"not OmniDocBench categories: {declared - set(BLOCK_CATEGORIES)}"


def test_an_unknown_primitive_fails_fast():
    with pytest.raises(CategoryError) as err:
        category_for("carousel")

    assert_diagnostic_error(str(err.value), mentions=("carousel", "categories.py"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n docparse python -m pytest tests/test_categories.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'generators.layout_dsl.categories'`

- [ ] **Step 3: Write the implementation**

Create `generators/layout_dsl/categories.py`:

```python
"""Which OmniDocBench category each primitive's ink belongs to.

Derived from the primitive that drew it, never authored per block, so a layout
cannot mislabel its own content and the mapping is checkable at startup.

The category names are OmniDocBench's, verbatim (design §3.1). This corpus
populates three of its eighteen block categories: it is three Australian
business document types and contains no figures, formulas, captions, headers,
footers, page numbers, code or references. The unused names are honest
absences, not gaps to fill.

`rule` is mapped to `abandon` because a rule with a `fill_char` paints a row of
repeated glyphs — real ink that no metric should score. Annotating it with
`ignore: true` states that, where emitting nothing would leave the annotation
silently disagreeing with the page.
"""

# OmniDocBench's block-level category names, verbatim. Listed in full rather
# than trimmed to what we use, so a reader can see what this corpus does not
# contain, and so a future document type can be mapped without re-deriving them.
BLOCK_CATEGORIES: tuple[str, ...] = (
    "title",
    "text_block",
    "figure",
    "figure_caption",
    "figure_footnote",
    "table",
    "table_caption",
    "table_footnote",
    "equation_isolated",
    "equation_caption",
    "header",
    "footer",
    "page_number",
    "page_footnote",
    "abandon",
    "code_txt",
    "code_txt_caption",
    "reference",
)

# The only span-level category this corpus produces. OmniDocBench also defines
# `equation_ignore`, `equation_inline` and `footnote_mark`, none of which can
# occur here.
SPAN_CATEGORY = "text_span"

# None means "draws no annotatable ink": a container shapes the walk, a spacer
# is whitespace. `banner` is a title because the bank mastheads are the page's
# own title line.
CATEGORY_BY_PRIMITIVE: dict[str, str | None] = {
    "text": "text_block",
    "pair": "text_block",
    "block": "text_block",
    "banner": "title",
    "table": "table",
    "rule": "abandon",
    "spacer": None,
    "panel": None,
    "split": None,
}


class CategoryError(RuntimeError):
    """Raised when a primitive has no declared category."""


def category_for(primitive: str) -> str | None:
    """Return the OmniDocBench category for a primitive's ink.

    Args:
        primitive: A key of `engine.PRIMITIVE_DRAWERS`, e.g. `text`.

    Returns:
        The category name, or None when the primitive draws no annotatable ink.

    Raises:
        CategoryError: The primitive is not declared in the map.
    """
    if primitive not in CATEGORY_BY_PRIMITIVE:
        raise CategoryError(
            "Primitive has no OmniDocBench category.\n"
            f"  What:     '{primitive}' is not declared in the category map.\n"
            "  Where:    generators/layout_dsl/categories.py -> CATEGORY_BY_PRIMITIVE\n"
            f"  Expected: an entry mapping it to one of {list(BLOCK_CATEGORIES)}, or to None "
            'when it draws no annotatable ink, e.g.\n              "'
            f'{primitive}": "text_block",\n'
            f"  Recover:  add '{primitive}' to CATEGORY_BY_PRIMITIVE."
        )
    return CATEGORY_BY_PRIMITIVE[primitive]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n docparse python -m pytest tests/test_categories.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Gates and commit**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
conda run -n docparse python -m pytest tests/ -q
git add generators/layout_dsl/categories.py
git commit -m "✨ feat: map each primitive to an OmniDocBench category"
```

---

## Task 2: Spans measured by the draw proxy

**Files:**
- Modify: `generators/transcript.py`
- Test: `tests/test_transcript_geometry.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `Span` — frozen dataclass: `text: str`, `poly: tuple[int, ...]` (8 ints)
  - `TranscriptRecorder.note_span(poly, text)` — attach a span to the current event
  - `Event` gains `spans: tuple[Span, ...]`, default empty
  - `TranscriptDraw.text()` measures the drawn line and calls `note_span`

**Design note for the implementer:** `TranscriptDraw.text()` receives PIL's
`draw.text((x, y), line, font=font, fill=...)`. Extract `xy` and the string from
`args`/`kwargs` and the font from `kwargs`. **If the call shape is not what you
expect, fail fast** with a four-element diagnostic rather than guessing a box —
a wrong box is worse than a loud stop.

- [ ] **Step 1: Write the failing test**

Create `tests/test_transcript_geometry.py`:

```python
"""Every drawn line becomes a span, measured where the ink lands."""

import pytest
from PIL import Image, ImageDraw

from generators.common import load_font
from generators.transcript import CoverageError, TranscriptDraw, TranscriptRecorder


def _surface():
    image = Image.new("RGB", (800, 400), "white")
    recorder = TranscriptRecorder()
    return TranscriptDraw(ImageDraw.Draw(image), recorder), recorder


def test_a_drawn_line_becomes_a_span_on_its_event():
    draw, recorder = _surface()
    font = load_font(24, family="carlito")

    recorder.emit("line", "Tax Invoice")
    draw.text((100, 50), "Tax Invoice", font=font, fill="black")

    (event,) = recorder.events
    assert len(event.spans) == 1
    assert event.spans[0].text == "Tax Invoice"


def test_a_span_poly_is_eight_coordinates_at_the_draw_position():
    draw, recorder = _surface()
    font = load_font(24, family="carlito")

    recorder.emit("line", "Total")
    draw.text((100, 50), "Total", font=font, fill="black")

    poly = recorder.events[0].spans[0].poly
    assert len(poly) == 8
    assert poly[0] == 100 and poly[1] == 50, "top-left is the draw position"
    assert poly[2] > poly[0], "the box has positive width"
    assert poly[5] > poly[1], "the box has positive height"


def test_two_wrapped_lines_become_two_spans_on_one_event():
    """A wrapped address is one block of text and two lines of ink."""
    draw, recorder = _surface()
    font = load_font(20, family="carlito")

    recorder.emit("line", "55 Smith Tce, Richmond VIC 3121")
    draw.text((100, 50), "55 Smith Tce,", font=font, fill="black")
    draw.text((100, 80), "Richmond VIC 3121", font=font, fill="black")

    (event,) = recorder.events
    assert [s.text for s in event.spans] == ["55 Smith Tce,", "Richmond VIC 3121"]
    assert event.spans[1].poly[1] > event.spans[0].poly[1], "the second line is lower"


def test_spans_attach_to_the_authorising_event_not_the_previous_one():
    draw, recorder = _surface()
    font = load_font(20, family="carlito")

    recorder.emit("line", "first")
    draw.text((10, 10), "first", font=font, fill="black")
    recorder.emit("line", "second")
    draw.text((10, 40), "second", font=font, fill="black")

    assert [len(e.spans) for e in recorder.events] == [1, 1]
    assert recorder.events[1].spans[0].text == "second"


def test_decoration_draws_produce_no_spans():
    """A rule's fill glyphs are ink, but not content ink."""
    draw, recorder = _surface()
    font = load_font(20, family="carlito")

    recorder.emit("line", "Subtotal")
    draw.text((10, 10), "Subtotal", font=font, fill="black")
    with recorder.decoration():
        draw.text((100, 10), "-" * 20, font=font, fill="black")

    assert len(recorder.events[0].spans) == 1


def test_an_unauthorised_draw_still_raises():
    """The pre-existing coverage invariant is unchanged."""
    draw, recorder = _surface()
    font = load_font(20, family="carlito")

    with pytest.raises(CoverageError):
        draw.text((10, 10), "unauthorised", font=font, fill="black")


def test_as_dict_carries_the_spans():
    draw, recorder = _surface()
    font = load_font(20, family="carlito")

    recorder.emit("line", "Total")
    draw.text((10, 10), "Total", font=font, fill="black")

    payload = recorder.events[0].as_dict()
    assert len(payload["spans"]) == 1
    assert payload["spans"][0]["text"] == "Total"
    assert len(payload["spans"][0]["poly"]) == 8
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n docparse python -m pytest tests/test_transcript_geometry.py -v`
Expected: FAIL — `ImportError: cannot import name 'Span'` (or `AttributeError: 'Event' object has no attribute 'spans'`)

- [ ] **Step 3: Add `Span` and extend `Event`**

In `generators/transcript.py`, add above `Event`:

```python
@dataclass(frozen=True)
class Span:
    """One drawn line of ink, measured where it landed.

    A block's text is captured pre-wrap (design §4.2) because wrapping is an
    artifact of font size and fit budget. A span is the other half of that
    truth: the post-wrap line, and the box it actually occupies.

    Attributes:
        text: The line as drawn.
        poly: Eight coordinates — top-left, top-right, bottom-right,
            bottom-left as (x, y) pairs, matching OmniDocBench's `poly`.
    """

    text: str
    poly: tuple[int, ...]

    def as_dict(self) -> dict:
        """Return the flat serialisable form."""
        return {"text": self.text, "poly": list(self.poly)}
```

Then extend `Event` — add the field and include it in `as_dict`:

```python
    spans: tuple[Span, ...] = ()

    def as_dict(self) -> dict:
        """Return the flat serialisable form (design §4.2)."""
        return {
            "seq": self.seq,
            "kind": self.kind,
            "text": self.text,
            "meta": self.meta,
            "spans": [s.as_dict() for s in self.spans],
        }
```

- [ ] **Step 4: Add `note_span` to the recorder**

`Event` is frozen, so a span is attached by replacing the event in the list.
Add to `TranscriptRecorder`:

```python
    def note_span(self, poly: tuple[int, ...], text: str) -> None:
        """Attach a drawn line to the event that authorised it.

        Called by `TranscriptDraw` for every text draw that is content rather
        than decoration. Does nothing when no event is current — that case is
        already a `CoverageError` raised by `note_text_drawn`.

        Args:
            poly: The drawn line's eight box coordinates.
            text: The line as drawn.
        """
        if self._decorating or self._current_seq is None:
            return
        existing = self._events[self._current_seq]
        self._events[self._current_seq] = replace(
            existing, spans=(*existing.spans, Span(text=text, poly=poly))
        )
```

Add `replace` to the dataclasses import: `from dataclasses import dataclass, field, replace`.

- [ ] **Step 5: Measure the draw in `TranscriptDraw`**

Replace `TranscriptDraw.text` with:

```python
    def text(self, *args, **kwargs) -> None:
        """Draw text, first checking that an event authorises it.

        Also measures the drawn line and attaches it to that event as a span
        (design §4). Measuring here rather than in the primitives is what makes
        the boxes ink-shaped: this is the one place that sees the string, the
        position and the font together, after every fit and wrap decision.

        Raises:
            CoverageError: No event authorises this draw.
            GeometryError: The call shape is not one this proxy can measure.
        """
        self._recorder.note_text_drawn()
        self._recorder.note_span(self._measure(args, kwargs), _drawn_string(args, kwargs))
        self._draw.text(*args, **kwargs)

    def _measure(self, args: tuple, kwargs: dict) -> tuple[int, ...]:
        """Return the eight box coordinates of a pending text draw.

        Args:
            args: Positional arguments as passed to `ImageDraw.text`.
            kwargs: Keyword arguments as passed to `ImageDraw.text`.

        Returns:
            Top-left, top-right, bottom-right, bottom-left as (x, y) pairs.

        Raises:
            GeometryError: The position, string or font cannot be recovered.
        """
        xy = kwargs.get("xy", args[0] if args else None)
        string = _drawn_string(args, kwargs)
        font = kwargs.get("font")
        if not (isinstance(xy, tuple) and len(xy) == 2) or font is None:
            raise GeometryError(
                "Cannot measure a text draw.\n"
                f"  What:     draw.text() was called with args={args!r} kwargs={sorted(kwargs)}; "
                "the position or the font could not be recovered.\n"
                "  Where:    generators/transcript.py -> TranscriptDraw._measure\n"
                "  Expected: draw.text((x, y), string, font=font, ...) — the shape every helper "
                "in generators/common.py uses.\n"
                "  Recover:  call draw.text with an (x, y) tuple and an explicit font=, or extend "
                "_measure to handle the new call shape."
            )
        x, y = int(xy[0]), int(xy[1])
        width = int(self._draw.textlength(string, font=font))
        ascent, descent = font.getmetrics()
        height = ascent + descent
        return (x, y, x + width, y, x + width, y + height, x, y + height)
```

Add at module level, beside `CoverageError`:

```python
class GeometryError(RuntimeError):
    """Raised when a text draw cannot be measured into a box."""


def _drawn_string(args: tuple, kwargs: dict) -> str:
    """Recover the string from an `ImageDraw.text` call's arguments."""
    if "text" in kwargs:
        return str(kwargs["text"])
    return str(args[1]) if len(args) > 1 else ""
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `conda run -n docparse python -m pytest tests/test_transcript_geometry.py -v`
Expected: PASS (7 tests)

- [ ] **Step 7: Confirm the corpus has not moved**

```bash
conda run -n docparse python -m pytest tests/ -q
```
Expected: PASS, including `tests/test_pipeline.py::test_the_same_input_renders_byte_identical_images`.

- [ ] **Step 8: Gates and commit**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
git add generators/transcript.py
git commit -m "✨ feat: measure each drawn line as a span in the draw proxy"
```

---

## Task 3: Block boxes, categories and reading order on events

**Files:**
- Modify: `generators/transcript.py`
- Test: `tests/test_transcript_geometry.py` (extend)

**Interfaces:**
- Consumes: `Span` (Task 2), `category_for` (Task 1).
- Produces:
  - `Event` gains `category_type: str | None` and `order: int | None`
  - `Event.poly` — a property returning the union of its spans' boxes, or None
  - `TranscriptRecorder.emit(..., category_type=None)` — a real parameter, not `**meta`
  - `TranscriptRecorder.annotations` — content events in `order`, each with a box

- [ ] **Step 1: Write the failing test**

Append to `tests/test_transcript_geometry.py`:

```python
def test_a_block_box_is_the_union_of_its_spans():
    draw, recorder = _surface()
    font = load_font(20, family="carlito")

    recorder.emit("line", "two lines", category_type="text_block")
    draw.text((100, 50), "two", font=font, fill="black")
    draw.text((100, 80), "lines that are much longer", font=font, fill="black")

    poly = recorder.events[0].poly
    assert poly[0] == 100, "left edge is the leftmost span"
    assert poly[1] == 50, "top edge is the topmost span"
    assert poly[5] > 80, "bottom edge clears the lower span"


def test_an_event_with_no_spans_has_no_box():
    _, recorder = _surface()

    recorder.emit("panel_open")

    assert recorder.events[0].poly is None


def test_order_counts_only_annotatable_events():
    """Structural markers shape the walk and must not consume an ordinal."""
    draw, recorder = _surface()
    font = load_font(20, family="carlito")

    recorder.emit("panel_open")
    recorder.emit("line", "first", category_type="text_block")
    draw.text((10, 10), "first", font=font, fill="black")
    recorder.emit("panel_close")
    recorder.emit("line", "second", category_type="text_block")
    draw.text((10, 40), "second", font=font, fill="black")

    assert [e.order for e in recorder.events] == [None, 0, None, 1]


def test_annotations_are_content_events_in_reading_order():
    draw, recorder = _surface()
    font = load_font(20, family="carlito")

    recorder.emit("split_open")
    recorder.emit("line", "left", category_type="text_block")
    draw.text((10, 10), "left", font=font, fill="black")
    recorder.emit("line", "right", category_type="text_block")
    draw.text((400, 10), "right", font=font, fill="black")
    recorder.emit("split_close")

    annotations = recorder.annotations
    assert [a.text for a in annotations] == ["left", "right"]
    assert [a.order for a in annotations] == [0, 1]


def test_a_categorised_event_that_drew_nothing_is_not_an_annotation():
    """A suppressed block emits nothing; a block that emits but draws nothing is a bug."""
    _, recorder = _surface()

    recorder.emit("line", "never drawn", category_type="text_block")

    assert recorder.annotations == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n docparse python -m pytest tests/test_transcript_geometry.py -k "block_box or order or annotations" -v`
Expected: FAIL — `TypeError: emit() got an unexpected keyword argument 'category_type'`

- [ ] **Step 3: Extend `Event`**

Add the two fields and the `poly` property:

```python
    category_type: str | None = None
    order: int | None = None

    @property
    def poly(self) -> tuple[int, ...] | None:
        """The union of this event's span boxes, or None when it drew nothing.

        Ink-shaped rather than region-shaped (design §4): the box cannot claim
        a region the glyphs do not occupy, because it is built from them.
        """
        if not self.spans:
            return None
        xs = [c for s in self.spans for c in s.poly[0::2]]
        ys = [c for s in self.spans for c in s.poly[1::2]]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        return (x0, y0, x1, y0, x1, y1, x0, y1)
```

Include both in `as_dict`, alongside `poly`:

```python
            "category_type": self.category_type,
            "order": self.order,
            "poly": list(self.poly) if self.poly is not None else None,
```

- [ ] **Step 4: Give `emit` a real `category_type` parameter and an order counter**

`category_type` must be an explicit parameter, not swept into `**meta` — it is
part of the annotation, not kind-specific detail.

```python
    def emit(self, kind: str, text: str | None = None, *, category_type: str | None = None, **meta) -> int:
        """Append an event and authorise the draw that follows it.

        Args:
            kind: Event kind.
            text: The resolved string as drawn, or None for a structural marker.
            category_type: The OmniDocBench category this element's ink belongs
                to, or None for a structural marker that draws nothing.
            **meta: Kind-specific detail.

        Returns:
            The new event's seq.
        """
        seq = len(self._events)
        order = None
        if category_type is not None:
            order = self._next_order
            self._next_order += 1
        self._events.append(
            Event(seq=seq, kind=kind, text=text, meta=dict(meta), category_type=category_type, order=order)
        )
        self._current_seq = seq
        return seq
```

Initialise `self._next_order = 0` in `__init__`, and add:

```python
    @property
    def annotations(self) -> list[Event]:
        """Content events that drew ink, in reading order.

        An event with a category but no spans is excluded: it declared itself
        annotatable and then drew nothing, which the §7.2 invariant reports as
        an error rather than silently annotating an empty region.
        """
        return [e for e in self._events if e.category_type is not None and e.spans]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `conda run -n docparse python -m pytest tests/test_transcript_geometry.py -v`
Expected: PASS (12 tests)

- [ ] **Step 6: Gates and commit**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
conda run -n docparse python -m pytest tests/ -q
git add generators/transcript.py
git commit -m "✨ feat: give events an ink-shaped box, a category and a reading order"
```

---

## Task 4: Pass categories from the text primitives

**Files:**
- Modify: `generators/layout_dsl/primitives_text.py`
- Test: `tests/layout_dsl/test_text_events.py` (extend)

**Interfaces:**
- Consumes: `category_for` (Task 1), `emit(..., category_type=)` (Task 3).
- Produces: no new symbols — every `emit` in this module passes a category.

The five emit sites are at roughly `primitives_text.py:410` (`text`/`block`),
`:562` (`pair`), `:650` and `:657` (`block` heading and lines), and `:838`
(`banner`). Each gains `category_type=category_for("<primitive>")`.

- [ ] **Step 1: Write the failing test**

Append to `tests/layout_dsl/test_text_events.py`:

```python
def test_every_text_event_carries_a_category_and_a_box():
    """Driven through the real shipped layouts, not a fixture."""
    from generators.layout_dsl.categories import BLOCK_CATEGORIES

    events = _render("invoices").events
    text_events = [e for e in events if e.kind in ("line", "title", "pair")]

    assert text_events, "the invoice layout draws text"
    for event in text_events:
        assert event.category_type in BLOCK_CATEGORIES, f"{event.kind} has {event.category_type!r}"
        assert event.poly is not None, f"{event.kind} {event.text!r} drew no ink"


def test_the_page_title_is_categorised_as_a_title():
    events = _render("invoices").events
    titles = [e for e in events if e.kind == "title"]

    assert len(titles) == 1, "exactly one title per page"
    assert titles[0].category_type == "title"


def test_a_pair_is_a_text_block():
    pairs = [e for e in _render("invoices").events if e.kind == "pair"]

    assert pairs, "invoices draw label/value pairs"
    assert all(e.category_type == "text_block" for e in pairs)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n docparse python -m pytest tests/layout_dsl/test_text_events.py -k category -v`
Expected: FAIL — `assert None in (...)`, because no emit passes a category yet.

- [ ] **Step 3: Pass the category at each emit site**

Add the import at the top of `primitives_text.py`:

```python
from generators.layout_dsl.categories import category_for
```

Then at each site, add the keyword. For example the `text`/`block` site:

```python
        ctx.transcript.emit(
            "title" if is_title else "line",
            text,
            category_type=category_for("banner") if is_title else category_for("text"),
        )
```

the `pair` site:

```python
        ctx.transcript.emit("pair", None, category_type=category_for("pair"), label=label_text, value=value)
```

the two `block` sites:

```python
            ctx.transcript.emit("line", heading_text, category_type=category_for("block"))
...
            ctx.transcript.emit("line", line_text, category_type=category_for("block"))
```

and the `banner` site:

```python
        ctx.transcript.emit("title", text, category_type=category_for("banner"))
```

**Note on the title category:** a `text` block with `title: true` is the page's
title, so it takes `banner`'s category (`title`), not `text`'s. That is why the
first site branches.

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n docparse python -m pytest tests/layout_dsl/ -q`
Expected: PASS.

- [ ] **Step 5: Confirm the corpus has not moved**

```bash
conda run -n docparse python -m pytest tests/ -q
```

- [ ] **Step 6: Gates and commit**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
git add generators/layout_dsl/primitives_text.py
git commit -m "✨ feat: categorise the text primitives' events"
```

---

## Task 5: Categorise the table and leave cells box-free

**Files:**
- Modify: `generators/layout_dsl/primitives_table.py`
- Test: `tests/layout_dsl/test_table_events.py` (extend)

**Interfaces:**
- Consumes: `category_for` (Task 1).
- Produces: `table_open` carries `category_type="table"`; `cell`, `row_open`,
  `cell_sub_line` and `row_close` carry none.

**Why cells get no category:** OmniDocBench has no `table_cell` category and
scores tables by TEDS over the HTML, not by cell geometry (spec §3.3). A cell
box would be data no metric consumes. Cell *structure* is preserved where it is
scored — in the HTML that Task 7 emits.

**A wrinkle the implementer must handle:** the table block's box is the union of
its spans, but the table's ink is drawn during its `cell` events, not during
`table_open`. So `table_open` has no spans of its own and would have no box.
Fix it by computing the table's box at `table_close` from the boxes of the cells
between, and attaching it to the `table_open` event.

- [ ] **Step 1: Write the failing test**

Append to `tests/layout_dsl/test_table_events.py`:

```python
def test_the_table_block_is_categorised_and_boxed():
    events = _render("bank_statements", case_index=0).events
    opens = [e for e in events if e.kind == "table_open"]

    assert opens, "bank statements draw a table"
    for event in opens:
        assert event.category_type == "table"
        assert event.poly is not None, "the table block has a box"


def test_the_table_box_encloses_its_cells():
    events = _render("bank_statements", case_index=0).events
    table = next(e for e in events if e.kind == "table_open")
    cells = [e for e in events if e.kind == "cell" and e.spans]

    assert cells
    tx0, ty0 = table.poly[0], table.poly[1]
    tx1, ty1 = table.poly[4], table.poly[5]
    for cell in cells:
        assert cell.poly[0] >= tx0 and cell.poly[1] >= ty0, "cell starts inside the table box"
        assert cell.poly[4] <= tx1 and cell.poly[5] <= ty1, "cell ends inside the table box"


def test_cells_carry_no_category():
    """OmniDocBench has no table_cell category; structure lives in the HTML."""
    events = _render("bank_statements", case_index=0).events
    cells = [e for e in events if e.kind in ("cell", "cell_sub_line")]

    assert cells
    assert all(e.category_type is None for e in cells)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n docparse python -m pytest tests/layout_dsl/test_table_events.py -k "categorised or encloses" -v`
Expected: FAIL — `assert None == 'table'`

- [ ] **Step 3: Categorise `table_open` and close its box**

Add the import:

```python
from generators.layout_dsl.categories import category_for
```

At the `table_open` emit, pass the category:

```python
        table_seq = ctx.transcript.emit(
            "table_open", None, category_type=category_for("table"), columns=column_keys
        )
```

At `table_close`, attach the enclosing box. Add this method to
`TranscriptRecorder` in `generators/transcript.py` first:

```python
    def enclose(self, seq: int, since: int) -> None:
        """Give event `seq` a box enclosing every span drawn after `since`.

        A container primitive draws no ink itself — a table's glyphs belong to
        its cells — so its box cannot come from its own spans. This composes
        one from the events it opened.

        Args:
            seq: The event to give a box to.
            since: Only events after this seq contribute.
        """
        spans = tuple(s for e in self._events[since + 1 :] for s in e.spans)
        if not spans:
            return
        existing = self._events[seq]
        self._events[seq] = replace(existing, spans=existing.spans + spans)
```

Then at the `table_close` site in `primitives_table.py`:

```python
        ctx.transcript.enclose(table_seq, table_seq)
        ctx.transcript.emit("table_close")
```

**Note:** `enclose` copies the cells' spans onto the table event, so the table's
`poly` property computes the union for free. The cells keep their own spans; the
duplication is deliberate and local, and `layout.py` (Task 8) emits a box only
for events that carry a `category_type`, so no cell becomes an annotation.

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n docparse python -m pytest tests/layout_dsl/ -q`
Expected: PASS.

- [ ] **Step 5: Confirm the corpus has not moved, then gates and commit**

```bash
conda run -n docparse python -m pytest tests/ -q
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
git add generators/layout_dsl/primitives_table.py generators/transcript.py
git commit -m "✨ feat: categorise the table block and enclose its cells"
```

---

## Task 6: The box coverage invariant

**Files:**
- Modify: `generators/transcript.py`
- Modify: `generators/invoice.py`, `generators/receipt.py`, `generators/bank_statement.py`
- Test: `tests/test_transcript_geometry.py` (extend)

**Interfaces:**
- Produces: `TranscriptRecorder.assert_boxes_complete()`, raising `BoxCoverageError`.

**Why this exists.** `TranscriptDraw` already refuses a text draw no event
authorises, and that invariant runs on every `generate` rather than only under
test — "a test catches the case someone thought of, a runtime invariant catches
the primitive nobody has written yet." Geometry needs the same guarantee, and it
is the *converse* check: text coverage asks *was this draw authorised*, box
coverage asks *did every annotatable event get a box*.

Without it, a primitive added later emits a categorised event with no spans, the
annotation silently omits that element, and layout recall drops for a reason no
one can see.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_transcript_geometry.py`:

```python
def test_a_categorised_event_with_no_ink_fails_the_run():
    """The converse of the text-coverage invariant."""
    from generators.transcript import BoxCoverageError

    _, recorder = _surface()
    recorder.emit("line", "declared but never drawn", category_type="text_block")

    with pytest.raises(BoxCoverageError) as err:
        recorder.assert_boxes_complete()

    message = str(err.value)
    assert "text_block" in message
    assert "declared but never drawn" in message
    for element in ("What:", "Where:", "Expected:", "Recover:"):
        assert element in message


def test_a_page_whose_categorised_events_all_drew_ink_passes():
    draw, recorder = _surface()
    font = load_font(20, family="carlito")

    recorder.emit("line", "drawn", category_type="text_block")
    draw.text((10, 10), "drawn", font=font, fill="black")

    recorder.assert_boxes_complete()


def test_structural_markers_do_not_trip_the_invariant():
    _, recorder = _surface()

    recorder.emit("panel_open")
    recorder.emit("split_close")

    recorder.assert_boxes_complete()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n docparse python -m pytest tests/test_transcript_geometry.py -k boxes -v`
Expected: FAIL — `ImportError: cannot import name 'BoxCoverageError'`

- [ ] **Step 3: Implement the invariant**

Add beside `CoverageError` in `generators/transcript.py`:

```python
class BoxCoverageError(RuntimeError):
    """Raised when an annotatable event drew no ink to measure."""
```

and to `TranscriptRecorder`:

```python
    def assert_boxes_complete(self) -> None:
        """Check every annotatable event drew ink, at page end.

        The converse of `note_text_drawn`: that refuses ink no event
        authorised, this refuses an event that authorised no ink. A categorised
        event with no spans would annotate an empty region, so layout recall
        would fall with nothing on the page to explain it.

        Raises:
            BoxCoverageError: An event carries a category but drew nothing.
        """
        empty = [e for e in self._events if e.category_type is not None and not e.spans]
        if not empty:
            return
        first = empty[0]
        raise BoxCoverageError(
            "An annotatable element drew no ink.\n"
            f"  What:     event seq={first.seq} kind={first.kind!r} category={first.category_type!r} "
            f"text={first.text!r} declared itself annotatable and drew nothing "
            f"({len(empty)} such event(s) on this page).\n"
            "  Where:    generators/layout_dsl/primitives_*.py -> the primitive that emitted it\n"
            "  Expected: every emit() carrying a category_type is followed by at least one "
            "draw.text(), e.g.\n"
            "              ctx.transcript.emit('line', text, category_type=category_for('text'))\n"
            "              draw_text_left(ctx.draw, text, x, y, font)\n"
            "  Recover:  draw the element, or drop its category_type if it is a structural "
            "marker rather than content."
        )
```

- [ ] **Step 4: Call it at page end in all three renderers**

In each of `generators/invoice.py`, `generators/receipt.py` and
`generators/bank_statement.py`, after `render_body(...)` returns and before the
image is returned, add:

```python
    recorder.assert_boxes_complete()
```

In `receipt.py` this must come before the crop; the crop changes pixels, never
events. Add `BoxCoverageError` to each renderer's docstring `Raises:` section
alongside the existing `CoverageError`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `conda run -n docparse python -m pytest tests/ -q`
Expected: PASS. If a real layout trips the invariant, that is a genuine finding —
report it rather than weakening the check.

- [ ] **Step 6: Gates and commit**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
git add generators/transcript.py generators/invoice.py generators/receipt.py generators/bank_statement.py
git commit -m "✨ feat: fail a run when an annotatable element draws no ink"
```

---

## Task 7: Table HTML projection

**Files:**
- Create: `generators/tables.py`
- Test: `tests/test_tables_html.py`

**Interfaces:**
- Consumes: event dicts as stored in `events.jsonl`.
- Produces:
  - `table_html(events: list[dict]) -> list[str]` — one HTML string per table
  - `TableHtmlError(RuntimeError)`

**A pure function of the event stream**, like `serialise`: it imports no PIL and
renders nothing, so the convention can change and every table re-emit without
re-rendering a page.

- [ ] **Step 1: Write the failing test**

Create `tests/test_tables_html.py`:

```python
"""Table structure as HTML, for TEDS."""

import pytest

from generators.tables import TableHtmlError, table_html
from tests.helpers import assert_diagnostic_error


def _ev(seq, kind, text=None, **meta):
    return {"seq": seq, "kind": kind, "text": text, "meta": meta}


def _table(columns, header, *rows):
    events = [_ev(0, "table_open", None, columns=columns)]
    seq = 1
    for cells, is_header in [(header, True), *[(r, False) for r in rows]]:
        events.append(_ev(seq, "row_open"))
        seq += 1
        for col, value in enumerate(cells):
            events.append(
                _ev(seq, "cell", value, row=None if is_header else 0, col=col,
                    column_key=columns[col], header=is_header)
            )
            seq += 1
        events.append(_ev(seq, "row_close"))
        seq += 1
    events.append(_ev(seq, "table_close"))
    return events


def test_a_header_row_becomes_thead_th():
    (html,) = table_html(_table(["date", "amount"], ["Date", "Amount"], ["01/09/2023", "$12.00"]))

    assert "<thead><tr><th>Date</th><th>Amount</th></tr></thead>" in html


def test_body_rows_become_tbody_td():
    (html,) = table_html(_table(["date", "amount"], ["Date", "Amount"], ["01/09/2023", "$12.00"]))

    assert "<tbody><tr><td>01/09/2023</td><td>$12.00</td></tr></tbody>" in html


def test_an_empty_cell_is_an_empty_element_not_a_dropped_column():
    """A blank cell must keep its column, or the structure shifts left."""
    (html,) = table_html(_table(["a", "b", "c"], ["A", "B", "C"], ["x", "", "z"]))

    assert "<td>x</td><td></td><td>z</td>" in html


def test_markup_in_a_cell_is_escaped():
    (html,) = table_html(_table(["a"], ["A"], ["Smith & Co <Ltd>"]))

    assert "Smith &amp; Co &lt;Ltd&gt;" in html
    assert "<Ltd>" not in html


def test_a_sub_line_is_folded_into_its_cell():
    events = _table(["description", "amount"], ["Description", "Amount"], ["Consulting", "100.00"])
    events.insert(-1, _ev(90, "cell_sub_line", "Ref: 8842", column_key="description"))

    (html,) = table_html(events)

    assert "Consulting Ref: 8842" in html


def test_two_tables_yield_two_strings():
    events = _table(["a"], ["A"], ["1"]) + _table(["b"], ["B"], ["2"])

    assert len(table_html(events)) == 2


def test_a_page_with_no_table_yields_nothing():
    assert table_html([_ev(0, "line", "just text")]) == []


def test_an_unbalanced_table_fails_fast():
    events = _table(["a"], ["A"], ["1"])[:-1]  # drop table_close

    with pytest.raises(TableHtmlError) as err:
        table_html(events)

    assert_diagnostic_error(str(err.value), mentions=("table_close",))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n docparse python -m pytest tests/test_tables_html.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'generators.tables'`

- [ ] **Step 3: Write the implementation**

Create `generators/tables.py`:

```python
"""Captured table events to HTML, for TEDS.

A pure function of the event stream, like `serialise` — it imports no PIL and
renders nothing, so the convention can change and every table re-emit in seconds
without re-rendering an image (design §6 of the original spec, applied to a
third projection).

TEDS is defined over an HTML tree (Zhong et al., arXiv:1911.10683), so HTML is
the form that gets emitted. OmniDocBench also carries a `latex` field and marks
both optional; it is left absent here because nothing scores it and two
statements of one structure drift (design §5).

No `colspan` or `rowspan`: the table primitive has no merged-cell concept, so
every table is a uniform grid and the attributes would be constant. They arrive
with subsystem B.
"""

import html as html_escape

_CELL_JOIN = " "


class TableHtmlError(RuntimeError):
    """Raised when a table's event stream cannot be rendered as HTML."""


def _err(what: str, *, seq: int | None) -> TableHtmlError:
    """Build a four-element fail-fast diagnostic."""
    where = f"events.jsonl -> seq {seq}" if seq is not None else "events.jsonl -> end of stream"
    return TableHtmlError(
        "Cannot render a table as HTML.\n"
        f"  What:     {what}\n"
        f"  Where:    {where}\n"
        "  Expected: a balanced table stream, e.g.\n"
        "              table_open, row_open, cell..., row_close, ..., table_close\n"
        "  Recover:  regenerate the corpus; a truncated stream means `generate` did not "
        "finish, and a re-run repairs it."
    )


def table_html(events: list[dict]) -> list[str]:
    """Render every table in an event stream as an HTML table.

    Args:
        events: One page's events, as stored in `events.jsonl`.

    Returns:
        One HTML string per table, in walk order. Empty when the page has none.

    Raises:
        TableHtmlError: A table is not closed, or a row is not closed.
    """
    tables: list[str] = []
    rows: list[tuple[list[str], bool]] = []
    row: list[str] = []
    keys: list[str] = []
    is_header = False
    open_seq: int | None = None

    for event in events:
        kind = event["kind"]
        meta = event.get("meta") or {}
        if kind == "table_open":
            rows, open_seq = [], int(event["seq"])
        elif kind == "row_open":
            row, keys, is_header = [], [], False
        elif kind == "cell":
            row.append(str(event["text"] or ""))
            keys.append(str(meta.get("column_key", "")))
            is_header = is_header or bool(meta.get("header"))
        elif kind == "cell_sub_line":
            key = str(meta.get("column_key", ""))
            if key in keys:
                position = keys.index(key)
                row[position] = f"{row[position]}{_CELL_JOIN}{event['text'] or ''}".strip()
        elif kind == "row_close":
            rows.append((row, is_header))
            row, keys, is_header = [], [], False
        elif kind == "table_close":
            tables.append(_render(rows))
            rows, open_seq = [], None

    if open_seq is not None:
        raise _err("a table_open has no matching table_close.", seq=open_seq)
    return tables


def _render(rows: list[tuple[list[str], bool]]) -> str:
    """Render collected rows as one HTML table.

    Args:
        rows: Each row's cell texts, and whether it is a header row.

    Returns:
        The table's HTML, header rows in `<thead>` and the rest in `<tbody>`.
    """
    head = "".join(_row(cells, "th") for cells, header in rows if header)
    body = "".join(_row(cells, "td") for cells, header in rows if not header)
    parts = ["<table>"]
    if head:
        parts.append(f"<thead>{head}</thead>")
    if body:
        parts.append(f"<tbody>{body}</tbody>")
    parts.append("</table>")
    return "".join(parts)


def _row(cells: list[str], tag: str) -> str:
    """Render one row, escaping every cell.

    Args:
        cells: The row's cell texts.
        tag: `th` for a header row, `td` otherwise.

    Returns:
        The row's HTML. An empty cell keeps its element, so the column count
        survives — dropping it would shift every later column left.
    """
    return "<tr>" + "".join(f"<{tag}>{html_escape.escape(c)}</{tag}>" for c in cells) + "</tr>"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n docparse python -m pytest tests/test_tables_html.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Gates and commit**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
conda run -n docparse python -m pytest tests/ -q
git add generators/tables.py
git commit -m "✨ feat: project captured tables to HTML for TEDS"
```

---

## Task 8: The `layout_dets` projection

**Files:**
- Create: `generators/layout.py`
- Test: `tests/test_layout_json.py`

**Interfaces:**
- Consumes: event dicts; `table_html` (Task 7); `SPAN_CATEGORY` (Task 1).
- Produces: `layout_dets(events: list[dict], *, attribute: dict) -> dict`

Output shape, matching OmniDocBench (spec §3.1):

```json
{"layout_dets": [
  {"category_type": "text_block", "poly": [...8...], "anno_id": 3, "order": 1,
   "text": "…", "ignore": false, "attribute": {...},
   "line_with_spans": [{"category_type": "text_span", "poly": [...8...], "text": "…"}]}
]}
```

- [ ] **Step 1: Write the failing test**

Create `tests/test_layout_json.py`:

```python
"""Annotations in OmniDocBench's layout_dets shape."""

from generators.layout import layout_dets

ATTR = {"doc_type": "invoices", "layout_id": "tax_invoice_standard", "tier": "clean"}


def _ev(seq, kind, text=None, category_type=None, order=None, poly=None, spans=None, **meta):
    return {
        "seq": seq, "kind": kind, "text": text, "meta": meta,
        "category_type": category_type, "order": order, "poly": poly,
        "spans": spans or [],
    }


def _block(seq, text, order, poly, spans=None):
    return _ev(seq, "line", text, category_type="text_block", order=order, poly=poly,
               spans=spans or [{"text": text, "poly": poly}])


def test_a_block_becomes_one_annotation():
    out = layout_dets([_block(0, "Tax Invoice", 0, [0, 0, 10, 0, 10, 5, 0, 5])], attribute=ATTR)

    (det,) = out["layout_dets"]
    assert det["category_type"] == "text_block"
    assert det["poly"] == [0, 0, 10, 0, 10, 5, 0, 5]
    assert det["anno_id"] == 0
    assert det["order"] == 0
    assert det["text"] == "Tax Invoice"
    assert det["ignore"] is False


def test_wrapped_lines_become_line_with_spans():
    spans = [{"text": "55 Smith Tce,", "poly": [0, 0, 9, 0, 9, 5, 0, 5]},
             {"text": "Richmond VIC", "poly": [0, 6, 9, 6, 9, 11, 0, 11]}]
    out = layout_dets([_block(0, "55 Smith Tce, Richmond VIC", 0, [0, 0, 9, 0, 9, 11, 0, 11], spans)],
                      attribute=ATTR)

    lines = out["layout_dets"][0]["line_with_spans"]
    assert [s["text"] for s in lines] == ["55 Smith Tce,", "Richmond VIC"]
    assert all(s["category_type"] == "text_span" for s in lines)


def test_the_block_text_is_pre_wrap_not_the_joined_spans():
    """The block keeps the transcript's string; spans keep the ink."""
    spans = [{"text": "55 Smith Tce,", "poly": [0, 0, 9, 0, 9, 5, 0, 5]},
             {"text": "Richmond VIC", "poly": [0, 6, 9, 6, 9, 11, 0, 11]}]
    out = layout_dets([_block(0, "55 Smith Tce, Richmond VIC", 0, [0, 0, 9, 0, 9, 11, 0, 11], spans)],
                      attribute=ATTR)

    assert out["layout_dets"][0]["text"] == "55 Smith Tce, Richmond VIC"


def test_structural_markers_produce_no_annotation():
    out = layout_dets([_ev(0, "panel_open"), _block(1, "text", 0, [0, 0, 1, 0, 1, 1, 0, 1])],
                      attribute=ATTR)

    assert len(out["layout_dets"]) == 1


def test_decoration_is_annotated_but_ignored():
    events = [_ev(0, "rule", "-" * 20, category_type="abandon", order=0,
                  poly=[0, 0, 20, 0, 20, 2, 0, 2], spans=[{"text": "-" * 20, "poly": [0, 0, 20, 0, 20, 2, 0, 2]}])]

    (det,) = layout_dets(events, attribute=ATTR)["layout_dets"]
    assert det["category_type"] == "abandon"
    assert det["ignore"] is True


def test_a_table_annotation_carries_its_html():
    events = [
        _ev(0, "table_open", None, category_type="table", order=0,
            poly=[0, 0, 10, 0, 10, 5, 0, 5], spans=[{"text": "A", "poly": [0, 0, 10, 0, 10, 5, 0, 5]}],
            columns=["a"]),
        _ev(1, "row_open"),
        _ev(2, "cell", "A", row=None, col=0, column_key="a", header=True),
        _ev(3, "row_close"),
        _ev(4, "table_close"),
    ]

    (det,) = layout_dets(events, attribute=ATTR)["layout_dets"]
    assert det["category_type"] == "table"
    assert "<th>A</th>" in det["html"]


def test_annotations_are_ordered_by_reading_order():
    out = layout_dets(
        [_block(5, "second", 1, [0, 9, 1, 9, 1, 10, 0, 10]),
         _block(2, "first", 0, [0, 0, 1, 0, 1, 1, 0, 1])],
        attribute=ATTR,
    )

    assert [d["text"] for d in out["layout_dets"]] == ["first", "second"]


def test_the_attribute_block_is_carried_onto_every_annotation():
    out = layout_dets([_block(0, "x", 0, [0, 0, 1, 0, 1, 1, 0, 1])], attribute=ATTR)

    assert out["layout_dets"][0]["attribute"] == ATTR


# --- Validation (spec §9.3, §9.4, §9.5) ---


def test_a_degenerate_box_is_rejected():
    """Spec §9.3: a box with non-positive width or height annotates nothing."""
    from generators.layout import LayoutError

    with pytest.raises(LayoutError) as err:
        layout_dets([_block(0, "x", 0, [5, 5, 5, 5, 5, 5, 5, 5])], attribute=ATTR)

    assert_diagnostic_error(str(err.value), mentions=("seq 0",))


def test_a_box_outside_the_page_is_rejected():
    """Spec §9.3: a negative coordinate cannot be ink on a page."""
    from generators.layout import LayoutError

    with pytest.raises(LayoutError) as err:
        layout_dets([_block(0, "x", 0, [-4, 0, 10, 0, 10, 5, -4, 5])], attribute=ATTR)

    assert_diagnostic_error(str(err.value), mentions=("seq 0",))


def test_a_duplicate_order_is_rejected():
    """Spec §9.4: two elements cannot occupy one position in the reading."""
    from generators.layout import LayoutError

    events = [_block(0, "a", 0, [0, 0, 1, 0, 1, 1, 0, 1]), _block(1, "b", 0, [0, 2, 1, 2, 1, 3, 0, 3])]

    with pytest.raises(LayoutError) as err:
        layout_dets(events, attribute=ATTR)

    assert_diagnostic_error(str(err.value), mentions=("order",))


def test_a_gap_in_the_reading_order_is_rejected():
    """Spec §9.4: a gap means an element was annotated and then lost."""
    from generators.layout import LayoutError

    events = [_block(0, "a", 0, [0, 0, 1, 0, 1, 1, 0, 1]), _block(1, "b", 2, [0, 2, 1, 2, 1, 3, 0, 3])]

    with pytest.raises(LayoutError) as err:
        layout_dets(events, attribute=ATTR)

    assert_diagnostic_error(str(err.value), mentions=("order",))


def test_a_table_annotation_with_no_html_is_rejected():
    """Spec §9.5: a table whose structure is missing is not a table."""
    from generators.layout import LayoutError

    events = [
        _ev(0, "table_open", None, category_type="table", order=0,
            poly=[0, 0, 10, 0, 10, 5, 0, 5], spans=[{"text": "A", "poly": [0, 0, 10, 0, 10, 5, 0, 5]}],
            columns=["a"]),
    ]  # no row/cell/table_close events, so table_html yields nothing

    with pytest.raises(LayoutError) as err:
        layout_dets(events, attribute=ATTR)

    assert_diagnostic_error(str(err.value), mentions=("html",))
```

Add these imports at the top of the test file:

```python
import pytest

from tests.helpers import assert_diagnostic_error
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n docparse python -m pytest tests/test_layout_json.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'generators.layout'`

- [ ] **Step 3: Write the implementation**

Create `generators/layout.py`:

```python
"""Captured events to OmniDocBench `layout_dets` annotations.

The third projection over one capture, alongside `serialise`'s Markdown and
`tables`' HTML. Pure: it imports no PIL and renders nothing.

Field names are OmniDocBench's, verbatim (design §3.1), so a reader who works in
that vocabulary needs no translation table. What this corpus cannot align is
recorded in §3.2 — three of eighteen block categories, and no formula track,
therefore no composite score.

`text` is the block's PRE-wrap string, exactly as the transcript records it;
`line_with_spans` carries the post-wrap lines and their boxes. Both are true and
neither is lossy (design §4).
"""

from generators.layout_dsl.categories import SPAN_CATEGORY
from generators.tables import table_html

# Categories whose ink is real but which no metric should score.
_IGNORED_CATEGORIES = frozenset({"abandon"})


class LayoutError(RuntimeError):
    """Raised when an annotation cannot be trusted."""


def _err(what: str, *, key: str, expected: str, recover: str) -> LayoutError:
    """Build a four-element fail-fast diagnostic."""
    return LayoutError(
        "Invalid layout annotation.\n"
        f"  What:     {what}\n"
        f"  Where:    events.jsonl -> {key}\n"
        f"  Expected: {expected}\n"
        f"  Recover:  {recover}"
    )


def _check_box(det: dict) -> None:
    """Reject a box that cannot describe ink on a page (spec §9.3).

    Args:
        det: One annotation, already built.

    Raises:
        LayoutError: The box has non-positive area or a negative coordinate.
    """
    poly = det["poly"]
    x0, y0, x1, y1 = poly[0], poly[1], poly[4], poly[5]
    if x1 <= x0 or y1 <= y0 or min(poly) < 0:
        raise _err(
            f"seq {det['anno_id']} ({det['category_type']}, text={det['text']!r}) has box {poly}, "
            "which has non-positive area or a negative coordinate.",
            key=f"seq {det['anno_id']}",
            expected="a box with positive width and height inside the page, e.g.\n"
            "              [100, 340, 1800, 340, 1800, 388, 100, 388]",
            recover="a degenerate box means the span measurement is wrong; check "
            "TranscriptDraw._measure against the primitive that drew this element.",
        )


def _check_order(dets: list[dict]) -> None:
    """Reject a reading order with a duplicate or a gap (spec §9.4).

    A duplicate means two elements claim one position; a gap means an element
    was ordered and then lost. Either makes the sequence unscoreable.

    Args:
        dets: Every annotation on the page.

    Raises:
        LayoutError: The orders are not 0..n-1 exactly once each.
    """
    orders = sorted(d["order"] for d in dets if d["order"] is not None)
    if orders != list(range(len(orders))):
        duplicates = sorted({o for o in orders if orders.count(o) > 1})
        raise _err(
            f"reading order is {orders}, not a complete 0..{len(orders) - 1} sequence"
            + (f"; duplicated: {duplicates}" if duplicates else "; there is a gap"),
            key="order",
            expected=f"each annotatable event ordered exactly once, e.g. {list(range(len(orders)))}",
            recover="order is assigned by TranscriptRecorder.emit; a duplicate or gap means an "
            "event was constructed outside it, or the stream was edited.",
        )


def layout_dets(events: list[dict], *, attribute: dict) -> dict:
    """Build one page's annotations.

    Args:
        events: One page's events, as stored in `events.jsonl`.
        attribute: Key-value classification carried onto every annotation —
            doc type, layout id and degradation tier.

    Returns:
        `{"layout_dets": [...]}`, annotations in reading order.
    """
    tables = iter(table_html(events))
    dets = []
    for event in events:
        category = event.get("category_type")
        if category is None or not event.get("poly"):
            continue
        det = {
            "category_type": category,
            "poly": list(event["poly"]),
            "anno_id": int(event["seq"]),
            "order": event.get("order"),
            "text": event.get("text") or "",
            "ignore": category in _IGNORED_CATEGORIES,
            "attribute": dict(attribute),
            "line_with_spans": [
                {"category_type": SPAN_CATEGORY, "poly": list(s["poly"]), "text": s["text"]}
                for s in event.get("spans", [])
            ],
        }
        if category == "table":
            det["html"] = next(tables, "")
            if not det["html"]:
                raise _err(
                    f"seq {det['anno_id']} is a table annotation with no html.",
                    key=f"seq {det['anno_id']} -> html",
                    expected="an HTML table from generators.tables.table_html, e.g.\n"
                    "              <table><thead><tr><th>Date</th></tr></thead>…</table>",
                    recover="the table's row and cell events are missing or unbalanced; "
                    "regenerate the corpus.",
                )
        _check_box(det)
        dets.append(det)

    _check_order(dets)
    dets.sort(key=lambda d: (d["order"] is None, d["order"]))
    return {"layout_dets": dets}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n docparse python -m pytest tests/test_layout_json.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Gates and commit**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
conda run -n docparse python -m pytest tests/ -q
git add generators/layout.py
git commit -m "✨ feat: project events to OmniDocBench layout_dets"
```

---

## Task 9: Export the new artifacts

**Files:**
- Modify: `generators/export.py`
- Modify: `generators/pipeline.py`
- Test: `tests/test_export.py` (extend)

**Interfaces:**
- Consumes: `layout_dets` (Task 8), `table_html` (Task 7).
- Produces: `manifest_record` gains `layout` and, when the page has a table,
  `tables`; `export_corpus` writes `layout/` and `tables/`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_export.py`:

```python
def test_the_export_writes_a_layout_json_per_page(tmp_path):
    """Every page gets annotations, whether or not it has a table."""
    import json

    from generators.export import export_corpus

    root = _minimal_export(tmp_path)  # existing helper in this module
    layouts = sorted((root / "layout").glob("*.json"))

    assert layouts, "layout/ holds one file per page"
    payload = json.loads(layouts[0].read_text(encoding="utf-8"))
    assert "layout_dets" in payload


def test_the_manifest_names_the_layout_file(tmp_path):
    import json

    root = _minimal_export(tmp_path)
    row = json.loads((root / "manifest.jsonl").read_text(encoding="utf-8").splitlines()[0])

    assert row["layout"].startswith("layout/")
    assert (root / row["layout"]).exists()


def test_a_page_with_no_table_gets_no_tables_file(tmp_path):
    """Absence is expressed by omission, not by an empty file."""
    import json

    root = _minimal_export(tmp_path)
    rows = [json.loads(x) for x in (root / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]

    for row in rows:
        if "tables" in row:
            assert (root / row["tables"]).exists()
```

**Note for the implementer:** `tests/test_export.py` already builds an export in
its existing tests. Extract that setup into a `_minimal_export(tmp_path)` helper
these three tests share rather than duplicating it — and keep the existing tests
working through the same helper.

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n docparse python -m pytest tests/test_export.py -k "layout or tables" -v`
Expected: FAIL — `AssertionError: layout/ holds one file per page` (the directory does not exist)

- [ ] **Step 3: Record the layout id on each event record**

`generate` currently writes `{"case_id", "doc_type", "image_file", "events"}` per
document — **no layout id** — but the annotation `attribute` block needs one
(spec §13). In `generators/pipeline.py`'s `generate`, where the record is built,
add it:

```python
            records.append(
                {
                    "case_id": str(case_id),
                    "doc_type": dtype,
                    "layout_id": layout_ref,
                    "image_file": image_file,
                    "events": [event.as_dict() for event in recorder.events],
                }
            )
```

`layout_ref` is already in scope — it is what looked the layout up. Note this
changes `events.jsonl`'s record shape, which is a derived artifact, not a shipped
one; `_merge_event_records` keys on `(case_id, doc_type)` and is unaffected.

Add a test to `tests/test_generate_events.py`:

```python
def test_each_record_names_the_layout_that_drew_it():
    """The annotation attribute block needs it, and a filename cannot carry it."""
    records = _generate_records()  # existing helper in this module

    assert records, "generate produced records"
    for record in records:
        assert record["layout_id"], f"{record['case_id']} has no layout_id"
```

- [ ] **Step 4: Write the layout and table artifacts during export**

In `generators/export.py`, import the projections:

```python
from generators.layout import layout_dets
from generators.tables import table_html
```

In `export_corpus`, beside the existing `images/` and `transcripts/` copies, add:

```python
    (root / "layout").mkdir(parents=True, exist_ok=True)
```

and per record, after the transcript is copied:

```python
        stem = Path(record["image_file"]).stem
        attribute = {
            "doc_type": record["doc_type"],
            "layout_id": str(record.get("layout_id", "")),
            "tier": "clean",
        }
        annotations = layout_dets(record["events"], attribute=attribute)
        (root / "layout" / f"{stem}.json").write_text(
            json.dumps(annotations, indent=2) + "\n", encoding="utf-8"
        )
        row["layout"] = f"layout/{stem}.json"

        tables = table_html(record["events"])
        if tables:
            (root / "tables").mkdir(parents=True, exist_ok=True)
            (root / "tables" / f"{stem}.html").write_text("\n".join(tables) + "\n", encoding="utf-8")
            row["tables"] = f"tables/{stem}.html"
```

**`tier` is hardcoded `"clean"` here** — the degrade CLI copies these artifacts
verbatim into each tier corpus, exactly as it copies transcripts, so a tier's
annotations describe the same page. Rewriting `tier` per tier is a follow-up, not
this task; note it in your report.

- [ ] **Step 5: Carry the new artifacts through `degrade`**

In `generators/degradation/cli.py`, add `"layout"` and `"tables"` to the
directories copied into each tier corpus, beside `transcripts`. The images
change; the annotations do not — a degraded page says the same thing and its
elements sit in the same places.

- [ ] **Step 6: Run tests to verify they pass**

Run: `conda run -n docparse python -m pytest tests/test_export.py -v`
Expected: PASS.

- [ ] **Step 7: Gates and commit**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
conda run -n docparse python -m pytest tests/ -q
git add generators/export.py generators/pipeline.py generators/degradation/cli.py
git commit -m "✨ feat: ship layout annotations and table HTML with the corpus"
```

---

## Task 10: Boxes verify against pixels

**Files:**
- Test: `tests/test_layout_pixels.py` (create — no source change)

**Interfaces:** none. This task adds the check that makes the geometry
trustworthy and changes no shipped code.

**Why this is its own task.** Every other test in this plan checks the annotation
against itself — the box matches the spans, the spans match the draws. All of
that passes if the boxes are consistently wrong. This is the only test that
compares a claimed box against the page, and spec §11 criterion 5 names it as the
one criterion that can fail on its merits.

- [ ] **Step 1: Write the test**

Create `tests/test_layout_pixels.py`:

```python
"""Claimed boxes contain ink, and ink falls inside a claimed box.

Everything else verifies the annotation against itself. This is the only check
that asks the page.
"""

from pathlib import Path

import pytest
import yaml
from PIL import Image, ImageDraw

from generators.invoice import render_invoice
from generators.loader import load_layout_registry
from generators.transcript import TranscriptDraw, TranscriptRecorder


def _render_invoice(case_index=0):
    gt = yaml.safe_load(Path("ground_truth/invoices.yml").read_text(encoding="utf-8"))
    case_id, entry = list(gt.items())[case_index]
    entry["case_id"] = str(case_id)
    layouts = load_layout_registry(Path("config/layouts/invoices.yml"))
    return render_invoice(entry, layouts[entry["layout"]])


def _has_ink(image, poly, *, inset=0):
    """True when the region has any non-white pixel."""
    x0, y0, x1, y1 = poly[0] + inset, poly[1] + inset, poly[4] - inset, poly[5] - inset
    if x1 <= x0 or y1 <= y0:
        return False
    crop = image.crop((x0, y0, x1, y1)).convert("L")
    return crop.getextrema()[0] < 250


def test_every_claimed_box_contains_ink():
    image, recorder = _render_invoice()

    empty = [a for a in recorder.annotations if not _has_ink(image, a.poly)]

    assert not empty, f"boxes claiming a blank region: {[(a.seq, a.text) for a in empty][:5]}"


def test_every_span_contains_ink():
    image, recorder = _render_invoice()

    empty = [
        (e.seq, s.text) for e in recorder.annotations for s in e.spans if not _has_ink(image, s.poly)
    ]

    assert not empty, f"spans claiming a blank region: {empty[:5]}"


def test_no_content_ink_falls_outside_every_box():
    """The converse: mask every claimed box and assert the page is blank."""
    image, recorder = _render_invoice()
    masked = image.copy()
    painter = ImageDraw.Draw(masked)
    for annotation in recorder.annotations:
        p = annotation.poly
        painter.rectangle([p[0] - 2, p[1] - 2, p[4] + 2, p[5] + 2], fill="white")

    assert not _has_ink(masked, (0, 0, masked.width, 0, masked.width, masked.height, 0, masked.height)), (
        "ink remains on the page after masking every annotated box"
    )


@pytest.mark.parametrize("case_index", [0, 1, 2])
def test_boxes_hold_across_several_cases(case_index):
    image, recorder = _render_invoice(case_index)

    assert recorder.annotations, "the page produced annotations"
    assert all(_has_ink(image, a.poly) for a in recorder.annotations)
```

- [ ] **Step 2: Run it**

Run: `conda run -n docparse python -m pytest tests/test_layout_pixels.py -v`
Expected: PASS.

If `test_no_content_ink_falls_outside_every_box` fails, **do not widen the mask
to make it pass.** Report what ink is unaccounted for: it means a primitive draws
content that no annotation covers, which is the exact gap the §7.2 invariant
exists to prevent and this test exists to detect.

- [ ] **Step 3: Commit**

No source file changed, and `tests/` is gitignored, so **this task produces no
commit.** Record the results in your report instead.

---

## Task 11: The corpus has not moved

**Files:**
- Test: `tests/test_corpus_unchanged.py` (create — no source change)

**Interfaces:** none.

**The hard gate.** Everything in this plan adds fields to events and files to the
export. None of it may change a pixel or a transcript. A moved pixel is a corpus
revision that invalidates every prediction already scored against it.

- [ ] **Step 1: Write the test**

Create `tests/test_corpus_unchanged.py`:

```python
"""Adding annotations must not move the corpus."""

import hashlib
from pathlib import Path

import pytest

from generators.loader import load_generation_config

CONFIG = Path("config/generation_config.yml")
SHIPPED = Path(load_generation_config(CONFIG)["output_dir"])
TRANSCRIPTS = Path(load_generation_config(CONFIG)["derived_dir"]) / "transcripts"


def _hashes(paths):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


@pytest.mark.skipif(not SHIPPED.exists(), reason="no generated corpus on this machine")
def test_regenerating_reproduces_the_shipped_images(tmp_path):
    """The byte-identity guarantee, applied across this whole subsystem."""
    from typer.testing import CliRunner

    from generators.pipeline import app

    result = CliRunner().invoke(
        app, ["generate", "--output", str(tmp_path / "out"), "--derived", str(tmp_path / "derived")]
    )
    assert result.exit_code == 0, result.output

    fresh = _hashes((tmp_path / "out").glob("*.png"))
    shipped = _hashes(SHIPPED.rglob("*.png"))
    assert fresh == shipped, "annotation work moved a pixel"


@pytest.mark.skipif(not TRANSCRIPTS.exists(), reason="no generated corpus on this machine")
def test_reserialising_reproduces_the_shipped_transcripts(tmp_path):
    from typer.testing import CliRunner

    from generators.pipeline import app

    runner = CliRunner()
    runner.invoke(app, ["generate", "--output", str(tmp_path / "out"), "--derived", str(tmp_path / "derived")])
    result = runner.invoke(app, ["serialise", "--derived", str(tmp_path / "derived")])
    assert result.exit_code == 0, result.output

    fresh = _hashes((tmp_path / "derived" / "transcripts").glob("*.md"))
    shipped = _hashes(TRANSCRIPTS.glob("*.md"))
    assert fresh == shipped, "annotation work changed a transcript"
```

- [ ] **Step 2: Run it**

Run: `conda run -n docparse python -m pytest tests/test_corpus_unchanged.py -v`
Expected: PASS. **If either fails, stop and report** — do not adjust the expected
hashes. A difference here means this subsystem changed the corpus, which it must
not.

- [ ] **Step 3: Regenerate and re-export for real**

```bash
conda run -n docparse python -m generators.pipeline generate
conda run -n docparse python -m generators.pipeline serialise
conda run -n docparse python -m generators.pipeline export --date 20260826 \
    --target /private/tmp/claude-501/-Users-tod-Desktop-document-parsing/505bf1ed-9f9d-41d1-a048-4c1cf3f7283d/scratchpad/t11-export
```

Confirm the scratch export contains `layout/` with 165 JSON files and `tables/`
with one HTML per page that has a table. **Export to the scratch target, not over
the shipped `parsing_20260825`** — that vintage has been scored against.

- [ ] **Step 4: Commit**

No source change and `tests/` is gitignored, so **no commit**. Record the hash
comparison and the export inventory in your report.

---

## Final verification

- [ ] **Full suite and gates**

```bash
conda run -n docparse python -m pytest tests/ -q --cov=generators --cov-report=term
conda run -n docparse ruff check --ignore ARG001,ARG002,F841 .
conda run -n docparse ruff format --check .
conda run -n docparse mypy generators --ignore-missing-imports
conda run -n docparse python -m generators.pipeline validate
```

`generators` was at 79% before this work; new code should not lower it.

- [ ] **Spec §11 success criteria, each checked explicitly**

1. Every content event carries `poly`, `category_type` and `order`, and the
   invariant fails a run that omits one (Tasks 3, 6).
2. All 165 images and 165 transcripts byte-identical (Task 11).
3. `export` writes `layout/` per page and `tables/` where a table exists (Task 9).
4. A wrapped field yields one block plus N spans, block text matching the
   transcript (Tasks 2, 8).
5. Boxes verify against pixels (Task 10) — **the one that can fail on its merits.**
6. An OmniDocBench-fluent reader needs no translation table (Task 8's field names).

- [ ] **Report what is now possible and what still is not.** Layout, reading-order
  and table labels exist; the *metrics* that consume them (mAP, an order metric,
  TEDS) do not. That is the next increment, and it belongs to `scoring/`.
