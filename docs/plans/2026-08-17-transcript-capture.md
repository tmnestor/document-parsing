# Transcript Capture and Serialisation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture a structured event per drawn element at the moment the DSL
draws it, then serialise those events into the restricted Markdown transcript
that is this corpus's ground truth.

**Architecture:** A `TranscriptRecorder` hangs off `RenderContext` as
`ctx.transcript`. Each primitive appends an event where it has resolved its
content and is about to draw. A `TranscriptDraw` proxy wraps the PIL drawing
surface and refuses any `draw.text` call not authorised by an event, which is
the §8.2 coverage invariant — enforced on every `generate`, not only under
pytest. Serialisation is a separate pure function of events plus a YAML policy,
so the convention can change without re-rendering an image.

**Tech Stack:** Python 3.12, Pillow, PyYAML, typer, rich. pytest, ruff, mypy.
Conda env `docparse`.

**Spec:** `docs/superpowers/specs/2026-08-17-document-parsing-corpus-design.md`
(§4, §5, §6, §8.2)

**Predecessor plan:** `docs/superpowers/plans/2026-08-17-dsl-port.md` — completed;
the renderer this plan instruments came from it.

## Scope

Delivers capture through to a readable transcript: `generate` writes
`derived/events.jsonl`, `serialise` turns it into `derived/transcripts/*.md`,
and `preview` shows one page beside its transcript.

Deliberately **not** in this plan:

- The `export` command and the dated deliverable directory (spec §6.1) —
  manifest, sha256 hashes, `prompt.md`, the shipped `serialisation.yml` copy.
  Packaging is a separate concern from producing the artifact.
- The scoring tool and its normalisation (spec §5). It lives with the consumer,
  not the generator, and the spec is explicit that the generator never normalises.
- The §8.6 calibration pass (running real parsers to check the convention is
  fair). That is empirical work, budgeted separately, and needs a shipped corpus.

## Global Constraints

Unchanged from the port plan; repeated because every task's requirements include them.

- Python 3.12, `X | Y` unions, no `from __future__ import annotations`, no
  `TYPE_CHECKING` guards for runtime-signature types.
- Line length 108. `pathlib.Path` for all paths. Google-style docstrings.
- `ruff check --fix --ignore ARG001,ARG002,F841` and `ruff format .` clean.
- `mypy . --ignore-missing-imports` clean.
- Exceptions inside `except` blocks use `from err` or `from None` (B904).
- Dependencies stay exactly: `Pillow`, `PyYAML`, `typer`, `rich`, `Faker`.
- YAML is the single source of truth. Every serialisation key is **required**;
  a missing key fails fast and is never defaulted in Python.
- Every config validation error carries all four elements (what / where /
  expected / recover), asserted via `tests/helpers.py::assert_diagnostic_error`.
- `tests/` and `CLAUDE.md` are gitignored. Write and run tests; stage source only.
- **Never normalise in the generator.** One canonical form, emitted as drawn.
  Normalisation belongs to the scoring tool (§5).

## Two spec gaps this plan resolves

Both were found by reading the ported renderer. Neither is covered by §4.2's
event table, and both would otherwise trip the coverage invariant on day one.

### Gap 1 — decorative text

`rule` with a `fill_char` does not draw a line. It paints a row of repeated
glyphs through `common.draw_separator`, i.e. **text on the canvas that must emit
no event** (§4.3: `rule` emits nothing). A naive "all text must be authorised"
invariant fires on every receipt.

**Resolution:** the recorder exposes an explicit `decoration()` scope. Text drawn
inside it is exempt. `draw_rule` is its only caller in this plan. The scope is
deliberately narrow and explicit rather than a silent whitelist, so a future
primitive cannot drift into it by accident.

### Gap 2 — table sub-lines

A table column may carry a `sub_line`: a second, smaller line drawn beneath the
cell (a receipt line item's reference). It is real page content, it is drawn
text, and §4.2's table event list has no kind for it.

**Resolution:** it emits its own `cell_sub_line` event carrying the same `row`
and `column_key` meta as its cell. The serialiser folds it into that cell's
pipe-table text using a required `cell_sub_line_join` key in
`serialisation.yml`. A pipe table cannot express two lines in one cell, so the
alternative — dropping it — would mean the transcript omits ink that is on the
page, which is the one thing a transcription benchmark cannot do.

**Flag for the author:** both resolutions are decisions this plan makes on the
spec's behalf. If either is wrong, it is cheaper to change now than after golden
transcripts exist.

## File structure

| File | Responsibility |
|---|---|
| `generators/transcript.py` (new, ~220) | `Event`, `TranscriptRecorder`, `TranscriptDraw`, `CoverageError` |
| `generators/serialise.py` (new, ~180) | Events + policy → Markdown. Pure; imports no PIL |
| `config/serialisation.yml` (new) | The convention, every key required |
| `generators/layout_dsl/context.py` | `+ transcript: TranscriptRecorder \| None = None`, propagated by `within()` |
| `generators/layout_dsl/engine.py` | Per-block scope reset; `render_body` accepts `transcript=` |
| `generators/layout_dsl/primitives_text.py` | `title`, `line`, `pair` events; `decoration()` on rules |
| `generators/layout_dsl/primitives_table.py` | `table_open`/`row_open`/`cell`/`cell_sub_line`/`row_close`/`table_close` |
| `generators/layout_dsl/primitives_container.py` | `panel_open`/`close`, `split_open`/`column_open`/`close`/`split_close` |
| `generators/{invoice,receipt,bank_statement}.py` | Build the recorder, wrap `draw`, return events |
| `generators/pipeline.py` | `generate` writes `derived/events.jsonl`; new `serialise`, `preview` |

---

### Task 1: The event model and recorder

**Files:**
- Create: `generators/transcript.py`
- Test: `tests/test_transcript.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `Event(seq: int, kind: str, text: str | None, meta: dict)` with `as_dict() -> dict`
  - `TranscriptRecorder()` with:
    - `emit(kind: str, text: str | None = None, **meta) -> int`
    - `block()` — context manager, clears the authorising seq on entry
    - `decoration()` — context manager, exempts text drawn inside
    - `current_seq: int | None` (property)
    - `events: list[Event]` (property, read-only view)
    - `note_text_drawn()` — called by the proxy; raises when unauthorised
    - `as_jsonl() -> str`
  - `CoverageError(RuntimeError)`

- [ ] **Step 1: Write the failing test**

```python
"""The transcript event model and its coverage bookkeeping."""

import pytest

from generators.transcript import CoverageError, TranscriptRecorder


def test_events_are_numbered_from_zero_in_emission_order():
    rec = TranscriptRecorder()
    rec.emit("title", "TAX INVOICE")
    rec.emit("line", "Coastal Plumbing")
    assert [(e.seq, e.kind, e.text) for e in rec.events] == [
        (0, "title", "TAX INVOICE"),
        (1, "line", "Coastal Plumbing"),
    ]


def test_meta_is_carried_verbatim():
    rec = TranscriptRecorder()
    rec.emit("pair", None, label="Date", value="04/03/2025")
    assert rec.events[0].meta == {"label": "Date", "value": "04/03/2025"}


def test_emitting_authorises_a_draw():
    rec = TranscriptRecorder()
    rec.emit("line", "hello")
    rec.note_text_drawn()  # does not raise


def test_text_drawn_with_no_event_is_a_coverage_error():
    rec = TranscriptRecorder()
    with pytest.raises(CoverageError) as excinfo:
        rec.note_text_drawn()
    message = str(excinfo.value)
    for label in ("What:", "Where:", "Expected:", "Recover:"):
        assert label in message


def test_a_block_scope_revokes_the_previous_block_authorisation():
    """The whole point: a new primitive that draws without emitting must fail."""
    rec = TranscriptRecorder()
    with rec.block():
        rec.emit("line", "drawn by a primitive that does emit")
        rec.note_text_drawn()
    with rec.block():
        with pytest.raises(CoverageError):
            rec.note_text_drawn()


def test_decoration_scope_permits_unauthorised_text():
    rec = TranscriptRecorder()
    with rec.block(), rec.decoration():
        rec.note_text_drawn()


def test_decoration_scope_does_not_leak_past_its_block():
    rec = TranscriptRecorder()
    with rec.block(), rec.decoration():
        rec.note_text_drawn()
    with rec.block(), pytest.raises(CoverageError):
        rec.note_text_drawn()


def test_jsonl_round_trips_one_object_per_line():
    import json

    rec = TranscriptRecorder()
    rec.emit("title", "TAX INVOICE")
    rec.emit("cell", "143.08", row=0, col=3, column_key="total", header=False)
    lines = rec.as_jsonl().splitlines()
    assert len(lines) == 2
    second = json.loads(lines[1])
    assert second == {
        "seq": 1,
        "kind": "cell",
        "text": "143.08",
        "meta": {"row": 0, "col": 3, "column_key": "total", "header": False},
    }
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `conda run -n docparse pytest tests/test_transcript.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'generators.transcript'`.

- [ ] **Step 3: Write the module**

```python
"""Draw-time transcript capture.

The recorder is appended to by the DSL primitives at the moment each resolves
its content and is about to draw it (design §4.1). Capturing here rather than
re-walking the block tree at export time means the code that suppresses a block
is the code that would have emitted its event, so a transcript cannot disagree
with its image about what was drawn.

What it does *not* guarantee is coverage: a primitive added later could put text
on the canvas and emit nothing. `TranscriptDraw` closes that hole by refusing
any text draw the recorder has not authorised — see `note_text_drawn`.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field


class CoverageError(RuntimeError):
    """Raised when text reaches the canvas with no event authorising it."""


@dataclass(frozen=True)
class Event:
    """One drawn element, captured in walk order.

    Attributes:
        seq: Position in the append-only stream, from zero.
        kind: Event kind, e.g. `title`, `line`, `pair`, `cell`.
        text: The resolved string as drawn, before wrapping; None for markers.
        meta: Kind-specific detail, e.g. a cell's row and column.
    """

    seq: int
    kind: str
    text: str | None
    meta: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        """Return the flat serialisable form (design §4.2)."""
        return {"seq": self.seq, "kind": self.kind, "text": self.text, "meta": self.meta}


class TranscriptRecorder:
    """Append-only event stream with draw-authorisation bookkeeping."""

    def __init__(self) -> None:
        self._events: list[Event] = []
        self._current_seq: int | None = None
        self._decorating = False

    @property
    def events(self) -> list[Event]:
        """The captured events, in walk order."""
        return list(self._events)

    @property
    def current_seq(self) -> int | None:
        """The seq of the event authorising the next draw, if any."""
        return self._current_seq

    def emit(self, kind: str, text: str | None = None, **meta) -> int:
        """Append an event and authorise the draw that follows it.

        Args:
            kind: Event kind.
            text: The resolved string as drawn, or None for a structural marker.
            **meta: Kind-specific detail.

        Returns:
            The new event's seq.
        """
        seq = len(self._events)
        self._events.append(Event(seq=seq, kind=kind, text=text, meta=dict(meta)))
        self._current_seq = seq
        return seq

    @contextmanager
    def block(self) -> Iterator[None]:
        """Scope one primitive's drawing, revoking the previous one's authorisation.

        Without this, a primitive that draws but emits nothing would inherit the
        preceding primitive's seq and pass the coverage check silently. The
        walker opens one of these per block.
        """
        previous_seq, previous_decorating = self._current_seq, self._decorating
        self._current_seq, self._decorating = None, False
        try:
            yield
        finally:
            self._current_seq, self._decorating = previous_seq, previous_decorating

    @contextmanager
    def decoration(self) -> Iterator[None]:
        """Scope text that is deliberately not content.

        `rule` with a `fill_char` paints a row of glyphs rather than drawing a
        line, so it puts text on the canvas that design §4.3 says emits nothing.
        This is the only sanctioned way to do that.
        """
        previous = self._decorating
        self._decorating = True
        try:
            yield
        finally:
            self._decorating = previous

    def note_text_drawn(self) -> None:
        """Record that text reached the canvas, and check it was authorised.

        Raises:
            CoverageError: No event authorises this draw and it is not
                inside a `decoration()` scope.
        """
        if self._decorating or self._current_seq is not None:
            return
        raise CoverageError(
            "Text was drawn with no transcript event.\n"
            "  What:     a primitive drew text without first calling "
            "ctx.transcript.emit(), so the page shows ink the transcript omits.\n"
            "  Where:    generators/layout_dsl/primitives_*.py -> the primitive "
            "drawing at this point in the walk.\n"
            "  Expected: every draw of page content is preceded by an emit(), e.g.\n"
            "              ctx.transcript.emit('line', text)\n"
            "  Recover:  emit an event before drawing, or wrap genuinely "
            "decorative text in `with ctx.transcript.decoration():`."
        )

    def as_jsonl(self) -> str:
        """Return the event stream as newline-delimited JSON."""
        return "".join(json.dumps(event.as_dict()) + "\n" for event in self._events)
```

- [ ] **Step 4: Run the tests**

Run: `conda run -n docparse pytest tests/test_transcript.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 generators/
conda run -n docparse ruff format .
conda run -n docparse mypy generators/ --ignore-missing-imports
git add generators/transcript.py
git commit -m "✨ feat: add the draw-time transcript event model"
```

---

### Task 2: The drawing proxy and the coverage invariant

**Files:**
- Modify: `generators/transcript.py` (add `TranscriptDraw`)
- Test: `tests/test_transcript_draw.py`

**Interfaces:**
- Consumes: `TranscriptRecorder`, `CoverageError`
- Produces: `TranscriptDraw(draw: ImageDraw.ImageDraw, recorder: TranscriptRecorder)`

**Why a proxy rather than edits at each call site.** `ctx.draw` is handed to
`common.py`'s eight `draw.text` call sites, which know only a string and a box.
Wrapping the surface once means every current and future text draw is checked,
including from a helper nobody has written yet — which is precisely the risk
§8.2 names. `line`, `rectangle` and `textlength` forward untouched: they are
decoration or measurement and emit nothing.

- [ ] **Step 1: Write the failing test**

```python
"""The drawing proxy that enforces the coverage invariant."""

import pytest
from PIL import Image, ImageDraw

from generators.common import load_font
from generators.transcript import CoverageError, TranscriptDraw, TranscriptRecorder


def _surface():
    image = Image.new("RGB", (200, 100), "white")
    recorder = TranscriptRecorder()
    return TranscriptDraw(ImageDraw.Draw(image), recorder), recorder


def test_authorised_text_draws_normally():
    draw, rec = _surface()
    with rec.block():
        rec.emit("line", "hello")
        draw.text((10, 10), "hello", font=load_font(12, family="carlito"))


def test_unauthorised_text_raises_coverage_error():
    draw, rec = _surface()
    with rec.block(), pytest.raises(CoverageError):
        draw.text((10, 10), "unannounced", font=load_font(12, family="carlito"))


def test_decoration_lines_and_rectangles_need_no_event():
    draw, rec = _surface()
    with rec.block():
        draw.line([(0, 0), (100, 0)], fill="black")
        draw.rectangle([(0, 0), (10, 10)], fill="black")


def test_measurement_is_forwarded_and_needs_no_event():
    draw, rec = _surface()
    with rec.block():
        assert draw.textlength("hello", font=load_font(12, family="carlito")) > 0
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `conda run -n docparse pytest tests/test_transcript_draw.py -v`
Expected: FAIL — `ImportError: cannot import name 'TranscriptDraw'`.

- [ ] **Step 3: Add the proxy to `generators/transcript.py`**

```python
class TranscriptDraw:
    """A PIL drawing surface that refuses unauthorised text.

    Forwards every attribute to the wrapped `ImageDraw.ImageDraw` unchanged
    except `text`, which first asks the recorder whether an event authorises
    it. This is the design §8.2 coverage invariant, and it runs on every
    `generate` rather than only under pytest: a test catches the case someone
    thought of, a runtime invariant catches the primitive nobody has written yet.
    """

    def __init__(self, draw, recorder: TranscriptRecorder) -> None:
        """Wrap a drawing surface.

        Args:
            draw: The PIL `ImageDraw.ImageDraw` to forward to.
            recorder: The recorder whose events authorise text draws.
        """
        self._draw = draw
        self._recorder = recorder

    def text(self, *args, **kwargs) -> None:
        """Draw text, first checking that an event authorises it.

        Raises:
            CoverageError: No event authorises this draw.
        """
        self._recorder.note_text_drawn()
        self._draw.text(*args, **kwargs)

    def __getattr__(self, name: str):
        """Forward everything else (line, rectangle, textlength) untouched."""
        return getattr(self._draw, name)
```

Add `TranscriptDraw` to the module docstring's summary if it names its exports.

- [ ] **Step 4: Run the tests**

Run: `conda run -n docparse pytest tests/test_transcript_draw.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add generators/transcript.py
git commit -m "✨ feat: refuse unauthorised text draws at the drawing surface"
```

---

### Task 3: Thread the recorder through context and engine

**Files:**
- Modify: `generators/layout_dsl/context.py`, `generators/layout_dsl/engine.py`
- Test: `tests/layout_dsl/test_engine_transcript.py`

**Interfaces:**
- Consumes: `TranscriptRecorder`
- Produces:
  - `RenderContext.transcript: TranscriptRecorder | None = None`, propagated by `within()`
  - `render_body(..., transcript: TranscriptRecorder | None = None) -> int`
  - `render_blocks` opens `recorder.block()` per block

- [ ] **Step 1: Write the failing test**

```python
"""The walker scopes each block's draw authorisation."""

import pytest
from PIL import Image, ImageDraw

from generators.layout_dsl.context import Region, RenderContext
from generators.layout_dsl.engine import render_blocks
from generators.transcript import CoverageError, TranscriptDraw, TranscriptRecorder


def _ctx(recorder):
    image = Image.new("RGB", (800, 400), "white")
    return RenderContext(
        draw=TranscriptDraw(ImageDraw.Draw(image), recorder),
        entry={"fields": {}},
        layout={
            "font_sizes": {"body": 18},
            "field_budgets": {},
            "line_height": 24,
            "defaults": {"panel_padding": 10},
        },
        layout_id="test_layout",
        layout_path="config/layouts/test.yml",
        region=Region(x=40, width=720),
        transcript=recorder,
        render_children=render_blocks,
    )


def test_the_context_carries_the_recorder_into_child_regions():
    rec = TranscriptRecorder()
    ctx = _ctx(rec)
    assert ctx.within(Region(x=0, width=10)).transcript is rec


def test_a_spacer_emits_nothing_and_draws_nothing():
    rec = TranscriptRecorder()
    render_blocks([{"type": "spacer", "height": 20}], _ctx(rec), y=0)
    assert rec.events == []


def test_authorisation_does_not_leak_between_sibling_blocks():
    """A primitive drawing under its neighbour's event is exactly the §8.2 hole."""
    rec = TranscriptRecorder()
    ctx = _ctx(rec)
    render_blocks([{"type": "spacer", "height": 5}], ctx, y=0)
    assert rec.current_seq is None
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `conda run -n docparse pytest tests/layout_dsl/test_engine_transcript.py -v`
Expected: FAIL — `RenderContext.__init__() got an unexpected keyword argument 'transcript'`.

- [ ] **Step 3: Add the field to `context.py`**

In the `RenderContext` dataclass, directly after `region` and before
`render_children` (the slot `recorder` used to occupy):

```python
    transcript: "TranscriptRecorder | None" = None
```

Add to the `Attributes:` docstring:

```
        transcript: Optional draw-time transcript capture (design §4.1).
```

Add `from generators.transcript import TranscriptRecorder` at the top, and
`transcript=self.transcript,` inside `within()`'s constructor call.

- [ ] **Step 4: Scope each block in `engine.py`**

In `render_blocks`, wrap the dispatch so every block gets its own scope:

```python
        try:
            if ctx.transcript is not None:
                with ctx.transcript.block():
                    y = drawer(block, ctx, y)
            else:
                y = drawer(block, ctx, y)
        except _DSL_ERRORS as err:
            _tag_path(err, f"[{position}]({kind})")
            raise
```

Add `transcript: TranscriptRecorder | None = None` as the last keyword parameter
of `render_body`, document it in `Args:`, and pass `transcript=transcript` into
the `RenderContext(...)` construction.

- [ ] **Step 5: Run the tests**

Run: `conda run -n docparse pytest tests/layout_dsl/ -v`
Expected: all pass, including the ported tests from the previous plan. If
`test_context.py::test_context_carries_no_extraction_recorder` fails, read it —
it asserts no attribute named `recorder`, and `transcript` is a different name,
so it should still pass. Do not weaken it; it guards the extraction seam.

- [ ] **Step 6: Commit**

```bash
git add generators/layout_dsl/context.py generators/layout_dsl/engine.py
git commit -m "✨ feat: thread the transcript recorder through the walk"
```

---

### Task 4: Text primitives emit events

**Files:**
- Modify: `generators/layout_dsl/primitives_text.py`
- Test: `tests/layout_dsl/test_text_events.py`

**Interfaces:**
- Consumes: `ctx.transcript`
- Produces events: `title` (banner), `line` (text, block), `pair` (meta `label`, `value`)

**Where each emit goes.** Immediately after the string is fully resolved —
after interpolation, after `format_currency`, after `from_layout` lookup — and
immediately before the draw call. Per §4.2 the captured form is **pre-wrap**:
pass the logical string, never `FitResult.lines`.

**`draw_rule` is the decoration case.** Its `fill_char` branch calls
`common.draw_separator`, which paints glyphs as text. Wrap only that branch in
`with ctx.transcript.decoration():`. The `rule_thickness` branch draws a line and
needs nothing.

- [ ] **Step 1: Write the failing test**

```python
"""Text primitives capture what they draw, before wrapping."""

from PIL import Image, ImageDraw

from generators.layout_dsl.context import Region, RenderContext
from generators.layout_dsl.engine import render_blocks
from generators.layout_dsl.primitives_text import draw_pair, draw_text_block
from generators.transcript import TranscriptDraw, TranscriptRecorder

LAYOUT = {
    "font_sizes": {"body": 18, "header": 30},
    "field_budgets": {},
    "line_height": 24,
    "page_dimensions": {"width": 800, "height": 400},
    "defaults": {
        "pair_separator": ": ",
        "pair_value_align": "left",
        "pair_currency": "plain",
        "pair_min_gap": 0,
        "text_align": "left",
        "text_role": "body",
        "text_bold": False,
        "text_color": "black",
        "family": "carlito",
        "role": "body",
        "line_advance": 24,
    },
}


def _ctx(recorder, entry=None):
    image = Image.new("RGB", (800, 400), "white")
    return RenderContext(
        draw=TranscriptDraw(ImageDraw.Draw(image), recorder),
        entry=entry or {"fields": {"SUPPLIER_NAME": "Coastal Plumbing"}},
        layout=LAYOUT,
        layout_id="test_layout",
        layout_path="config/layouts/test.yml",
        region=Region(x=40, width=720),
        transcript=recorder,
        render_children=render_blocks,
    )


def test_a_text_block_emits_one_line_event_with_the_resolved_string():
    rec = TranscriptRecorder()
    with rec.block():
        draw_text_block({"type": "text", "content": "{SUPPLIER_NAME}"}, _ctx(rec), y=0)
    assert [(e.kind, e.text) for e in rec.events] == [("line", "Coastal Plumbing")]


def test_a_pair_emits_label_and_value_in_meta():
    rec = TranscriptRecorder()
    block = {"type": "pair", "label": "Date", "value": "04/03/2025"}
    with rec.block():
        draw_pair(block, _ctx(rec), y=0)
    event = rec.events[0]
    assert event.kind == "pair"
    assert event.meta["label"] == "Date"
    assert event.meta["value"] == "04/03/2025"


def test_a_pair_captures_the_formatted_value_not_the_raw_one():
    """§4.2: capture after currency formatting, as drawn."""
    rec = TranscriptRecorder()
    entry = {"fields": {"TOTAL_AMOUNT": "137.73"}}
    block = {"type": "pair", "label": "Total", "value": "{TOTAL_AMOUNT}", "currency": "symbol"}
    with rec.block():
        draw_pair(block, _ctx(rec, entry), y=0)
    assert rec.events[0].meta["value"] == "$137.73"


def test_a_suppressed_block_leaves_no_event():
    """§4.2: suppression is free — the code that suppresses is the code that emits."""
    rec = TranscriptRecorder()
    ctx = _ctx(rec, {"fields": {}})
    render_blocks([{"type": "text", "content": "x", "when": "MISSING_FIELD"}], ctx, y=0)
    assert rec.events == []


def test_a_glyph_rule_draws_decorative_text_and_emits_nothing():
    rec = TranscriptRecorder()
    ctx = _ctx(rec)
    render_blocks([{"type": "rule", "fill_char": "-"}], ctx, y=0)
    assert rec.events == []
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `conda run -n docparse pytest tests/layout_dsl/test_text_events.py -v`
Expected: FAIL. The first assertions fail on an empty event list; the rule test
fails with `CoverageError`, which is the invariant doing its job before the
decoration scope exists.

If a test fails because `LAYOUT`'s `defaults` is missing a key the primitive
resolves, read the real `PARAMETER_DEFAULTS` in `generators/layout_dsl/defaults.py`
and add the missing key to the fixture. Do not add a Python-side default.

- [ ] **Step 3: Emit from each text primitive**

In `draw_text_block`, after `text` is resolved and before the branch that
dispatches to `_draw_fitted_text` or `_draw_line`:

```python
    if ctx.transcript is not None:
        ctx.transcript.emit("line", text)
```

In `draw_banner`, after the rectangle is drawn and the title string is resolved,
before drawing it:

```python
    if ctx.transcript is not None:
        ctx.transcript.emit("title", text)
```

In `draw_pair`, after `label_text` and the formatted `value` are both resolved,
before any of the three draw branches. Emit the **drawn** label, trailing
separator included; §4.3's `pair_strip_trailing_colon` is the serialiser's job,
not the recorder's, so that the raw drawn form survives in the event stream:

```python
    if ctx.transcript is not None:
        ctx.transcript.emit("pair", None, label=label_text, value=value)
```

In `draw_block`, emit one `line` per drawn line, at the point each is resolved.

In `draw_rule`, wrap only the `fill_char` branch:

```python
        with _decoration(ctx):
            draw_separator(ctx.draw, y, width, margin, font, char=fill_char)
```

and add a small module-level helper so the `None` case stays readable:

```python
@contextmanager
def _decoration(ctx: RenderContext) -> Iterator[None]:
    """Scope decorative text, tolerating a context with no recorder."""
    if ctx.transcript is None:
        yield
        return
    with ctx.transcript.decoration():
        yield
```

`draw_spacer` emits nothing and draws nothing — leave it alone.

- [ ] **Step 4: Run the tests**

Run: `conda run -n docparse pytest tests/layout_dsl/ -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add generators/layout_dsl/primitives_text.py
git commit -m "✨ feat: capture title, line and pair events at draw time"
```

---

### Task 5: The table primitive emits events

**Files:**
- Modify: `generators/layout_dsl/primitives_table.py`
- Test: `tests/layout_dsl/test_table_events.py`

**Interfaces:**
- Produces events: `table_open` (meta `columns`), `row_open`, `cell`
  (meta `row`, `col`, `column_key`, `header`), `cell_sub_line`
  (meta `row`, `column_key`), `row_close`, `table_close`

**This is where `index` and `is_last` finally earn their keep.** The port left
them inert with a docstring saying the transcript would need them; `row` meta is
that need. Update the docstring to stop calling them unused.

**The `_date_is_redundant` case is the important test.** §4.2 calls the blanked
repeat date the clearest evidence that parsing truth differs from extraction
truth. The cell event must carry the **blank**, because that is what the page
shows.

- [ ] **Step 1: Write the failing test**

```python
"""The table primitive captures its structure and its cells."""

from PIL import Image, ImageDraw

from generators.layout_dsl.context import Region, RenderContext
from generators.layout_dsl.engine import render_blocks
from generators.transcript import TranscriptDraw, TranscriptRecorder


def _ctx(recorder, entry):
    image = Image.new("RGB", (1200, 800), "white")
    return RenderContext(
        draw=TranscriptDraw(ImageDraw.Draw(image), recorder),
        entry=entry,
        layout={
            "font_sizes": {"body": 18},
            "field_budgets": {},
            "line_height": 24,
            "row_height": 30,
            "defaults": {
                "table_header": True,
                "table_header_bold": True,
                "table_header_rule_top": False,
                "table_header_rule_gap": 4,
                "table_row_inset_y": 0,
                "table_cell_line_spacing": "row_height",
                "table_group_gap": 0,
                "table_fill_inset": 0,
                "table_dividers": [],
                "table_offset_y": 0,
                "table_sub_line_height": 0,
                "family": "carlito",
                "role": "body",
                "line_advance": 24,
            },
        },
        layout_id="test_layout",
        layout_path="config/layouts/test.yml",
        region=Region(x=40, width=1120),
        transcript=recorder,
        render_children=render_blocks,
    )


TABLE = {
    "type": "table",
    "rows": "pipe_fields",
    "frame": "plain",
    "grouping": "none",
    "params": {"fields": {"description": "LINE_ITEM_DESCRIPTIONS", "total": "LINE_ITEM_TOTAL_PRICES"}},
    "columns": [
        {"key": "description", "label": "Description", "align": "left", "x": 0},
        {"key": "total", "label": "Amount", "align": "right", "x_right": 0},
    ],
}

ENTRY = {
    "fields": {
        "LINE_ITEM_DESCRIPTIONS": "Consulting|Travel",
        "LINE_ITEM_TOTAL_PRICES": "100.00|50.00",
    }
}


def _kinds(rec):
    return [e.kind for e in rec.events]


def test_the_event_stream_is_balanced():
    rec = TranscriptRecorder()
    render_blocks([TABLE], _ctx(rec, ENTRY), y=0)
    kinds = _kinds(rec)
    assert kinds[0] == "table_open"
    assert kinds[-1] == "table_close"
    assert kinds.count("row_open") == kinds.count("row_close")


def test_table_open_declares_its_columns_in_order():
    rec = TranscriptRecorder()
    render_blocks([TABLE], _ctx(rec, ENTRY), y=0)
    assert rec.events[0].meta["columns"] == ["description", "total"]


def test_header_cells_are_marked_and_carry_the_label():
    rec = TranscriptRecorder()
    render_blocks([TABLE], _ctx(rec, ENTRY), y=0)
    headers = [e for e in rec.events if e.kind == "cell" and e.meta["header"]]
    assert [e.text for e in headers] == ["Description", "Amount"]


def test_body_cells_carry_row_col_and_column_key():
    rec = TranscriptRecorder()
    render_blocks([TABLE], _ctx(rec, ENTRY), y=0)
    body = [e for e in rec.events if e.kind == "cell" and not e.meta["header"]]
    assert [(e.meta["row"], e.meta["col"], e.text) for e in body] == [
        (0, 0, "Consulting"),
        (0, 1, "100.00"),
        (1, 0, "Travel"),
        (1, 1, "50.00"),
    ]
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `conda run -n docparse pytest tests/layout_dsl/test_table_events.py -v`
Expected: FAIL with `CoverageError` — the table draws header and cell text and
emits nothing yet. That failure *is* the invariant working.

If the fixture's `defaults` is missing a key, read `PARAMETER_DEFAULTS` and add
it to the fixture rather than defaulting in Python. If `pipe_fields` needs
different `params`, read `generators/layout_dsl/providers.py` for its real
parameter names.

- [ ] **Step 3: Emit from the table**

In `draw_table`, once `columns` and the resolved rows are known and before any
drawing:

```python
    if ctx.transcript is not None:
        ctx.transcript.emit("table_open", None, columns=[c["key"] for c in columns])
```

and after the last row, before returning:

```python
    if ctx.transcript is not None:
        ctx.transcript.emit("table_close")
```

In `_draw_header`, emit `row_open`, then one `cell` per column label with
`header=True`, `row=None`, `col=<index>`, `column_key=<key>`, then `row_close`.

In `_draw_row`, emit `row_open` before the column loop and `row_close` after
`_draw_sub_lines`. Inside the loop, after `text` is resolved (which is after
`_date_is_redundant` has had its say, so a blanked date is captured blank):

```python
        if ctx.transcript is not None:
            ctx.transcript.emit(
                "cell",
                text,
                row=index,
                col=position,
                column_key=column["key"],
                header=False,
            )
```

In `_draw_sub_lines`, emit `cell_sub_line` with the sub-line's resolved text and
the same `row` and `column_key` as its cell, before drawing it.

The emits must happen **inside** the same `recorder.block()` scope the walker
opened, which they will: `draw_table` is called from `render_blocks`.

- [ ] **Step 4: Run the tests**

Run: `conda run -n docparse pytest tests/layout_dsl/ -v`
Expected: all pass.

- [ ] **Step 5: Add the redundant-date regression test**

```python
def test_a_blanked_repeat_date_is_captured_as_blank():
    """§4.2: extraction states the date; transcription states what the page shows."""
    rec = TranscriptRecorder()
    grouped = dict(TABLE, grouping="inline")
    grouped["columns"] = [{"key": "date", "label": "Date", "align": "left", "x": 0}, *TABLE["columns"]]
    grouped["params"] = {
        "fields": {
            "date": "TRANSACTION_DATES",
            "description": "TRANSACTION_DESCRIPTIONS",
            "total": "TRANSACTION_AMOUNTS_PAID",
        }
    }
    entry = {
        "fields": {
            "TRANSACTION_DATES": "01/03/2025|01/03/2025",
            "TRANSACTION_DESCRIPTIONS": "Coffee|Lunch",
            "TRANSACTION_AMOUNTS_PAID": "4.50|18.00",
        }
    }
    render_blocks([grouped], _ctx(rec, entry), y=0)
    dates = [e.text for e in rec.events if e.kind == "cell" and e.meta.get("column_key") == "date"]
    assert dates[1] == "" or dates[2] == ""
```

Run it. If the row provider needs different field names, read
`generators/layout_dsl/providers.py` and use the real ones — the assertion that
matters is that a repeated date is captured blank, not the exact fixture shape.

- [ ] **Step 6: Commit**

```bash
git add generators/layout_dsl/primitives_table.py
git commit -m "✨ feat: capture table structure, cells and sub-lines"
```

---

### Task 6: Containers emit structure

**Files:**
- Modify: `generators/layout_dsl/primitives_container.py`
- Test: `tests/layout_dsl/test_container_events.py`

**Interfaces:**
- Produces events: `panel_open`, `panel_close`, `split_open`, `column_open`,
  `column_close`, `split_close` — all with `text=None`

**Split column order is the risky convention (§4.3).** Columns are emitted in
DSL order, left to right, never interleaved by vertical position. The events
carry the order; the serialiser relies on it; the shipped prompt must state it.

- [ ] **Step 1: Write the failing test**

```python
"""Containers capture structure, never text."""

from PIL import Image, ImageDraw

from generators.layout_dsl.context import Region, RenderContext
from generators.layout_dsl.engine import render_blocks
from generators.transcript import TranscriptDraw, TranscriptRecorder


def _ctx(recorder):
    image = Image.new("RGB", (800, 400), "white")
    return RenderContext(
        draw=TranscriptDraw(ImageDraw.Draw(image), recorder),
        entry={"fields": {}},
        layout={
            "font_sizes": {"body": 18},
            "field_budgets": {},
            "line_height": 24,
            "defaults": {
                "panel_padding": 10,
                "panel_border_color": "#000000",
                "split_gap": 20,
                "split_divider_color": "#cccccc",
                "text_align": "left",
                "text_role": "body",
                "text_bold": False,
                "text_color": "black",
                "family": "carlito",
                "role": "body",
                "line_advance": 24,
            },
        },
        layout_id="test_layout",
        layout_path="config/layouts/test.yml",
        region=Region(x=40, width=720),
        transcript=recorder,
        render_children=render_blocks,
    )


def test_a_panel_brackets_its_children():
    rec = TranscriptRecorder()
    block = {"type": "panel", "children": [{"type": "text", "content": "inside"}]}
    render_blocks([block], _ctx(rec), y=0)
    assert [e.kind for e in rec.events] == ["panel_open", "line", "panel_close"]


def test_a_split_emits_columns_in_dsl_order_never_by_vertical_position():
    rec = TranscriptRecorder()
    block = {
        "type": "split",
        "columns": [
            {"children": [{"type": "text", "content": "left"}]},
            {"children": [{"type": "text", "content": "right"}]},
        ],
    }
    render_blocks([block], _ctx(rec), y=0)
    assert [e.kind for e in rec.events] == [
        "split_open",
        "column_open",
        "line",
        "column_close",
        "column_open",
        "line",
        "column_close",
        "split_close",
    ]
    assert [e.text for e in rec.events if e.kind == "line"] == ["left", "right"]


def test_containers_never_carry_text():
    rec = TranscriptRecorder()
    block = {"type": "panel", "children": []}
    render_blocks([block], _ctx(rec), y=0)
    assert all(e.text is None for e in rec.events)
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `conda run -n docparse pytest tests/layout_dsl/test_container_events.py -v`
Expected: FAIL — the event list is `["line"]` with no brackets. If the real
`split` block key is not `columns`, read `draw_split` and use the real one.

- [ ] **Step 3: Emit from the containers**

In `draw_panel`, emit `panel_open` before rendering children and `panel_close`
after. In `draw_split`, emit `split_open`, then per column `column_open` /
`column_close` around that column's `render_children` call, then `split_close`.

The children render through `ctx.render_children`, which is `render_blocks`, so
each child opens its own `recorder.block()` scope. The container's own emits sit
outside those scopes, in the container's scope — which is correct: a container
emits markers, not authorised text, and draws only lines and rectangles.

- [ ] **Step 4: Run the tests**

Run: `conda run -n docparse pytest tests/layout_dsl/ -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add generators/layout_dsl/primitives_container.py
git commit -m "✨ feat: capture panel and split structure in DSL column order"
```

---

### Task 7: Renderers build the recorder; `generate` writes events

**Files:**
- Modify: `generators/invoice.py`, `generators/receipt.py`, `generators/bank_statement.py`, `generators/pipeline.py`
- Test: `tests/test_generate_events.py`

**Interfaces:**
- Produces:
  - `render_invoice(entry, layout) -> tuple[Image.Image, TranscriptRecorder]`
  - `render_receipt(entry, layout) -> tuple[Image.Image, TranscriptRecorder]`
  - `render_bank_statement(entry, layout) -> tuple[Image.Image, TranscriptRecorder]`
  - `generate` writes `derived/events.jsonl`, one JSON object per line, each
    carrying `{"case_id", "doc_type", "image_file", "events": [...]}`

**Signature change, and why not an out-parameter.** The port deliberately
removed the predecessor's `geometry_out` out-parameter. Returning a tuple keeps
the recorder a first-class result rather than reintroducing that pattern.
`check_overflow` calls `renderer(entry, layout)` and ignores the result, so it
keeps working; confirm by running `validate` after this task.

- [ ] **Step 1: Write the failing test**

```python
"""Rendering returns its transcript, and generate persists it."""

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from generators.invoice import render_invoice
from generators.loader import load_layout_registry
from generators.pipeline import app

runner = CliRunner()


def _first_invoice():
    gt = yaml.safe_load(Path("ground_truth/invoices.yml").read_text(encoding="utf-8"))
    case_id, entry = next(iter(gt.items()))
    entry["case_id"] = str(case_id)
    layouts = load_layout_registry(Path("config/layouts/invoices.yml"))
    return entry, layouts[entry["layout"]]


def test_rendering_returns_an_image_and_a_populated_transcript():
    entry, layout = _first_invoice()
    image, recorder = render_invoice(entry, layout)
    assert image.width > 0
    assert recorder.events, "an invoice drew no transcript events"
    assert any(e.kind == "cell" for e in recorder.events)


def test_generate_writes_one_events_record_per_document(tmp_path):
    result = runner.invoke(
        app,
        ["generate", "--type", "invoices", "--limit", "3", "--output", str(tmp_path),
         "--derived", str(tmp_path / "derived")],
    )
    assert result.exit_code == 0, result.output
    events_path = tmp_path / "derived" / "events.jsonl"
    assert events_path.exists()
    records = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 3
    assert {"case_id", "doc_type", "image_file", "events"} <= set(records[0])
    assert records[0]["image_file"].endswith(".png")


def test_every_rendered_page_has_a_transcript_record(tmp_path):
    result = runner.invoke(
        app,
        ["generate", "--type", "receipts", "--limit", "5", "--output", str(tmp_path),
         "--derived", str(tmp_path / "derived")],
    )
    assert result.exit_code == 0, result.output
    images = {p.name for p in tmp_path.glob("*.png")}
    records = [
        json.loads(line)
        for line in (tmp_path / "derived" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert {r["image_file"] for r in records} == images
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `conda run -n docparse pytest tests/test_generate_events.py -v`
Expected: FAIL — `render_invoice` returns an `Image`, not a tuple.

- [ ] **Step 3: Update the three renderers**

Each follows the same shape. For `invoice.py`:

```python
def render_invoice(entry: dict, layout: dict) -> tuple[Image.Image, TranscriptRecorder]:
    """Render a compliant Australian tax invoice from ground truth and layout config.

    Args:
        entry: Ground truth YAML entry with 'fields' dict and 'layout' id.
        layout: Layout registry entry carrying a `body:` tree,
            'page_dimensions', 'margin' and 'content_width'.

    Returns:
        The rendered page, and the transcript captured while drawing it.
    """
    dims = layout["page_dimensions"]
    width, height = int(dims["width"]), int(dims["height"])
    image = Image.new("RGB", (width, height), "white")
    recorder = TranscriptRecorder()

    render_body(
        layout,
        entry,
        layout_id=str(entry.get("layout", "")),
        layout_path=_LAYOUT_PATH,
        draw=TranscriptDraw(ImageDraw.Draw(image), recorder),
        region=Region(x=int(layout["margin"]), width=int(layout["content_width"])),
        y=int(layout["margin"]),
        transcript=recorder,
    )
    return image, recorder
```

`receipt.py` is identical except it keeps its crop and returns
`image.crop(...), recorder`. `bank_statement.py` threads the recorder through
`render_via_dsl`, which returns the tuple too.

- [ ] **Step 4: Update `pipeline.py`**

Add a `--derived` option to `generate`:

```python
    derived: Annotated[
        Path | None, typer.Option("--derived", help="Override the configured derived directory.")
    ] = None,
```

Unpack the tuple, accumulate one record per page, and write the file after the
loop:

```python
            image, recorder = renderer(entry, layout)
            image_file = f"{case_id}_{dtype}.png"
            image.save(target / image_file)
            records.append(
                {
                    "case_id": str(case_id),
                    "doc_type": dtype,
                    "image_file": image_file,
                    "events": [event.as_dict() for event in recorder.events],
                }
            )
```

```python
    derived_dir = derived if derived is not None else Path(cfg["derived_dir"])
    derived_dir.mkdir(parents=True, exist_ok=True)
    events_path = derived_dir / "events.jsonl"
    with events_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    rprint(f"[green]Events written: {events_path} ({len(records)} documents)[/green]")
```

Fix `check_overflow`'s caller if needed: it calls `renderer(entry, layout)` and
discards the result, so a tuple return is harmless — but run `validate` and
confirm rather than assuming.

- [ ] **Step 5: Run the tests, then the real thing**

```bash
conda run -n docparse pytest tests/ -v
conda run -n docparse python -m generators.pipeline validate
conda run -n docparse python -m generators.pipeline generate
```

Expected: validate passes, all 165 pages render, `derived/events.jsonl` has 165
lines. **The coverage invariant is now live on the whole corpus** — if any
primitive draws text it does not emit, this is where it surfaces, naming the
primitive. Treat any `CoverageError` here as a real finding and fix the
primitive; never widen `decoration()` to silence it.

- [ ] **Step 6: Commit**

```bash
git add generators/invoice.py generators/receipt.py generators/bank_statement.py generators/pipeline.py
git commit -m "✨ feat: return transcripts from renderers and persist events.jsonl"
```

---

### Task 8: The serialisation policy file

**Files:**
- Create: `config/serialisation.yml`
- Create: `generators/serialise.py` (policy loading only in this task)
- Test: `tests/test_serialisation_policy.py`

**Interfaces:**
- Produces:
  - `load_serialisation_policy(path: Path) -> dict`
  - `SerialisationError(RuntimeError)`
  - `REQUIRED_POLICY_KEYS: tuple[str, ...]`

**Every key is required, including the no-op ones.** §4.4 is explicit: `emphasis:
none` and `empty_cell_token: ""` are written out rather than omitted so that
reading this file alone answers what a transcript looks like.

- [ ] **Step 1: Write `config/serialisation.yml`**

```yaml
# How an event stream becomes a Markdown transcript. This file is the whole
# convention: reading it should answer what a transcript looks like without
# consulting Python. Every key is required — the loader fails fast on any
# omission, including the keys whose value is a no-op.

# `title` events become an ATX H1 heading.
title_style: atx_h1

# A `pair` event joins its label and value with this separator...
pair_separator: ": "
# ...after stripping any colon the layout already drew on the label, so a
# layout drawing "Total:" cannot produce "Total:: $137.73".
pair_strip_trailing_colon: true

# Tables become pipe tables with a header separator row.
table_style: pipe_with_header_rule
# What an empty cell renders as — including a date blanked as redundant by its
# grouping, which the page genuinely shows as blank.
empty_cell_token: ""
# A table sub-line is folded into its own cell's text with this joiner. A pipe
# table cannot express two lines in one cell, and dropping the sub-line would
# mean omitting ink that is on the page.
cell_sub_line_join: " "

# Split columns serialise column by column in DSL order, left to right, never
# interleaved by vertical position. This is the one convention competent models
# genuinely disagree on, so the shipped prompt must state it.
split_order: column_major

# Between top-level blocks.
block_separator: "\n\n"

# No bold, no italic. The renderer knows exactly what is bold, so emitting `**`
# is available — and deliberately not done: bold detection is a typographic
# judgement models make inconsistently, and scoring it would measure style
# recognition rather than reading.
emphasis: none
```

- [ ] **Step 2: Write the failing test**

```python
"""The serialisation policy is configuration, not code."""

from pathlib import Path

import pytest
import yaml

from generators.serialise import REQUIRED_POLICY_KEYS, SerialisationError, load_serialisation_policy
from tests.helpers import assert_diagnostic_error

POLICY_PATH = Path("config/serialisation.yml")


def test_the_shipped_policy_loads():
    policy = load_serialisation_policy(POLICY_PATH)
    assert policy["emphasis"] == "none"
    assert policy["split_order"] == "column_major"


def test_the_shipped_policy_declares_every_required_key():
    declared = set(yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8")))
    assert set(REQUIRED_POLICY_KEYS) == declared


def test_no_op_keys_are_written_out_rather_than_omitted():
    """§4.4: reading this file alone must answer what a transcript looks like."""
    text = POLICY_PATH.read_text(encoding="utf-8")
    assert "emphasis: none" in text
    assert "empty_cell_token:" in text


@pytest.mark.parametrize("missing", REQUIRED_POLICY_KEYS)
def test_omitting_any_key_fails_fast_rather_than_defaulting(tmp_path, missing):
    policy = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    del policy[missing]
    path = tmp_path / "serialisation.yml"
    path.write_text(yaml.safe_dump(policy), encoding="utf-8")
    with pytest.raises(SerialisationError) as excinfo:
        load_serialisation_policy(path)
    assert_diagnostic_error(str(excinfo.value), mentions=(missing, str(path)))


def test_an_unknown_enum_value_names_the_allowed_ones(tmp_path):
    policy = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    policy["split_order"] = "reading_order"
    path = tmp_path / "serialisation.yml"
    path.write_text(yaml.safe_dump(policy), encoding="utf-8")
    with pytest.raises(SerialisationError) as excinfo:
        load_serialisation_policy(path)
    assert_diagnostic_error(str(excinfo.value), mentions=("reading_order", "column_major"))
```

- [ ] **Step 3: Run it to make sure it fails**

Run: `conda run -n docparse pytest tests/test_serialisation_policy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'generators.serialise'`.

- [ ] **Step 4: Write the policy loader**

```python
"""Events plus policy to Markdown.

A pure function of the event stream and `config/serialisation.yml` — it imports
no PIL and renders nothing. That split is deliberate (design §6): the convention
is the risky, iterate-on-it part of this design, so it can change and every
transcript re-emit in seconds without re-rendering an image.
"""

from pathlib import Path

import yaml

REQUIRED_POLICY_KEYS: tuple[str, ...] = (
    "title_style",
    "pair_separator",
    "pair_strip_trailing_colon",
    "table_style",
    "empty_cell_token",
    "cell_sub_line_join",
    "split_order",
    "block_separator",
    "emphasis",
)

_ALLOWED: dict[str, tuple[str, ...]] = {
    "title_style": ("atx_h1",),
    "table_style": ("pipe_with_header_rule",),
    "split_order": ("column_major",),
    "emphasis": ("none",),
}


class SerialisationError(RuntimeError):
    """Raised when the serialisation policy is missing, malformed, or unknown."""
```

Then `load_serialisation_policy(path)`: read the YAML, and for each of
`REQUIRED_POLICY_KEYS` not present raise a `SerialisationError` carrying all
four diagnostic elements — what is missing, the absolute path plus the key, a
concrete YAML example, and a one-line remediation. Then check each key in
`_ALLOWED` against its tuple, naming the allowed values in the diagnostic.

- [ ] **Step 5: Run the tests**

Run: `conda run -n docparse pytest tests/test_serialisation_policy.py -v`
Expected: 13 passed (9 parametrised missing-key cases plus 4 others).

- [ ] **Step 6: Commit**

```bash
git add config/serialisation.yml generators/serialise.py
git commit -m "✨ feat: declare the serialisation convention in YAML"
```

---

### Task 9: The serialiser

**Files:**
- Modify: `generators/serialise.py`
- Test: `tests/test_serialise.py`

**Interfaces:**
- Produces: `serialise(events: list[dict], policy: dict) -> str`

**Takes dicts, not `Event` objects,** so it can run against `events.jsonl`
without importing the recorder. That is what makes it re-runnable over a stored
stream.

- [ ] **Step 1: Write the failing test**

```python
"""Event streams become the restricted Markdown subset, exactly."""

from pathlib import Path

from generators.serialise import load_serialisation_policy, serialise

POLICY = load_serialisation_policy(Path("config/serialisation.yml"))


def _ev(seq, kind, text=None, **meta):
    return {"seq": seq, "kind": kind, "text": text, "meta": meta}


def test_a_title_becomes_an_h1():
    assert serialise([_ev(0, "title", "TAX INVOICE")], POLICY) == "# TAX INVOICE"


def test_lines_become_paragraphs_separated_by_the_block_separator():
    events = [_ev(0, "line", "Coastal Plumbing"), _ev(1, "line", "133 Brown Dr")]
    assert serialise(events, POLICY) == "Coastal Plumbing\n\n133 Brown Dr"


def test_a_pair_joins_label_and_value():
    events = [_ev(0, "pair", None, label="Date", value="04/03/2025")]
    assert serialise(events, POLICY) == "Date: 04/03/2025"


def test_a_drawn_trailing_colon_is_stripped_before_joining():
    """Otherwise a layout drawing 'Total:' yields 'Total:: $137.73'."""
    events = [_ev(0, "pair", None, label="Total: ", value="$137.73")]
    assert serialise(events, POLICY) == "Total: $137.73"


def test_a_table_becomes_a_pipe_table_with_a_header_rule():
    events = [
        _ev(0, "table_open", None, columns=["description", "total"]),
        _ev(1, "row_open"),
        _ev(2, "cell", "Description", row=None, col=0, column_key="description", header=True),
        _ev(3, "cell", "Amount", row=None, col=1, column_key="total", header=True),
        _ev(4, "row_close"),
        _ev(5, "row_open"),
        _ev(6, "cell", "Consulting", row=0, col=0, column_key="description", header=False),
        _ev(7, "cell", "100.00", row=0, col=1, column_key="total", header=False),
        _ev(8, "row_close"),
        _ev(9, "table_close"),
    ]
    assert serialise(events, POLICY) == (
        "| Description | Amount |\n| --- | --- |\n| Consulting | 100.00 |"
    )


def test_an_empty_cell_uses_the_configured_token():
    events = [
        _ev(0, "table_open", None, columns=["date", "description"]),
        _ev(1, "row_open"),
        _ev(2, "cell", "", row=0, col=0, column_key="date", header=False),
        _ev(3, "cell", "Lunch", row=0, col=1, column_key="description", header=False),
        _ev(4, "row_close"),
        _ev(5, "table_close"),
    ]
    assert "|  | Lunch |" in serialise(events, POLICY)


def test_a_sub_line_folds_into_its_cell():
    events = [
        _ev(0, "table_open", None, columns=["description"]),
        _ev(1, "row_open"),
        _ev(2, "cell", "Consulting", row=0, col=0, column_key="description", header=False),
        _ev(3, "cell_sub_line", "Ref 8842", row=0, column_key="description"),
        _ev(4, "row_close"),
        _ev(5, "table_close"),
    ]
    assert "| Consulting Ref 8842 |" in serialise(events, POLICY)


def test_panels_and_splits_emit_no_visible_markup():
    events = [
        _ev(0, "panel_open"),
        _ev(1, "line", "inside"),
        _ev(2, "panel_close"),
    ]
    assert serialise(events, POLICY) == "inside"


def test_split_columns_serialise_in_dsl_order():
    events = [
        _ev(0, "split_open"),
        _ev(1, "column_open"),
        _ev(2, "line", "payer left"),
        _ev(3, "column_close"),
        _ev(4, "column_open"),
        _ev(5, "line", "metadata right"),
        _ev(6, "column_close"),
        _ev(7, "split_close"),
    ]
    assert serialise(events, POLICY) == "payer left\n\nmetadata right"


def test_no_emphasis_markers_are_ever_emitted():
    events = [_ev(0, "line", "Coastal Plumbing")]
    assert "**" not in serialise(events, POLICY)


def test_rules_and_spacers_contribute_nothing():
    assert serialise([_ev(0, "line", "a"), _ev(1, "line", "b")], POLICY) == "a\n\nb"
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `conda run -n docparse pytest tests/test_serialise.py -v`
Expected: FAIL — `ImportError: cannot import name 'serialise'`.

- [ ] **Step 3: Write the serialiser**

A single pass over the flat stream, accumulating blocks:

- `title` → `f"# {text}"` as its own block.
- `line` → `text` as its own block.
- `pair` → strip trailing colon and whitespace from `meta["label"]` when
  `pair_strip_trailing_colon`, then `f"{label}{pair_separator}{value}"`.
- `table_open` starts a table buffer holding `meta["columns"]`; `row_open`
  starts a row; `cell` appends `text or empty_cell_token`; `cell_sub_line`
  appends `cell_sub_line_join + text` to the row's cell matching its
  `column_key`; `row_close` commits the row; `table_close` renders the pipe
  table — header row, `| --- |` separator, then body rows — as one block.
- `panel_*`, `split_*`, `column_*` → structure only; emit nothing, and do not
  reorder. The stream is already in `column_major` order because
  `draw_split` walks columns in DSL order; assert the policy says
  `column_major` and raise `SerialisationError` if not, rather than silently
  ignoring a value nothing implements.
- Anything else → raise `SerialisationError` naming the unknown kind. A new
  event kind with no serialisation rule must fail loudly, not vanish.

Join blocks with `policy["block_separator"]`. Never normalise: no whitespace
collapsing, no case folding, no Unicode folding. §5 puts all of that in the
scoring tool so the corpus does not freeze a scoring policy.

- [ ] **Step 4: Run the tests**

Run: `conda run -n docparse pytest tests/test_serialise.py -v`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add generators/serialise.py
git commit -m "✨ feat: serialise events into the restricted Markdown subset"
```

---

### Task 10: The `serialise` and `preview` commands, and golden transcripts

**Files:**
- Modify: `generators/pipeline.py`
- Test: `tests/test_pipeline_transcripts.py`, `tests/golden/*.md`

**Interfaces:**
- Produces:
  - `pipeline serialise` — `derived/events.jsonl` + policy → `derived/transcripts/*.md`
  - `pipeline preview CASE_ID` — prints one transcript beside its image path

- [ ] **Step 1: Write the failing test**

```python
"""serialise and preview, end to end."""

import json
from pathlib import Path

from typer.testing import CliRunner

from generators.pipeline import app

runner = CliRunner()


def test_serialise_writes_one_transcript_per_events_record(tmp_path):
    derived = tmp_path / "derived"
    assert runner.invoke(
        app,
        ["generate", "--type", "invoices", "--limit", "3", "--output", str(tmp_path),
         "--derived", str(derived)],
    ).exit_code == 0
    result = runner.invoke(app, ["serialise", "--derived", str(derived)])
    assert result.exit_code == 0, result.output
    transcripts = sorted((derived / "transcripts").glob("*.md"))
    assert len(transcripts) == 3
    assert transcripts[0].read_text(encoding="utf-8").strip()


def test_a_transcript_is_named_for_its_image(tmp_path):
    derived = tmp_path / "derived"
    runner.invoke(
        app,
        ["generate", "--type", "invoices", "--limit", "1", "--output", str(tmp_path),
         "--derived", str(derived)],
    )
    runner.invoke(app, ["serialise", "--derived", str(derived)])
    record = json.loads((derived / "events.jsonl").read_text(encoding="utf-8").splitlines()[0])
    expected = Path(record["image_file"]).with_suffix(".md").name
    assert (derived / "transcripts" / expected).exists()


def test_serialise_is_pure_and_repeatable(tmp_path):
    """§6: the policy can change and every transcript re-emit without re-rendering."""
    derived = tmp_path / "derived"
    runner.invoke(
        app,
        ["generate", "--type", "receipts", "--limit", "3", "--output", str(tmp_path),
         "--derived", str(derived)],
    )
    runner.invoke(app, ["serialise", "--derived", str(derived)])
    first = {p.name: p.read_text(encoding="utf-8") for p in (derived / "transcripts").glob("*.md")}
    runner.invoke(app, ["serialise", "--derived", str(derived)])
    second = {p.name: p.read_text(encoding="utf-8") for p in (derived / "transcripts").glob("*.md")}
    assert first == second


def test_preview_prints_the_transcript_and_the_image_path(tmp_path):
    derived = tmp_path / "derived"
    runner.invoke(
        app,
        ["generate", "--type", "invoices", "--limit", "1", "--output", str(tmp_path),
         "--derived", str(derived)],
    )
    runner.invoke(app, ["serialise", "--derived", str(derived)])
    record = json.loads((derived / "events.jsonl").read_text(encoding="utf-8").splitlines()[0])
    result = runner.invoke(app, ["preview", record["case_id"], "--derived", str(derived)])
    assert result.exit_code == 0, result.output
    assert ".png" in result.output
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `conda run -n docparse pytest tests/test_pipeline_transcripts.py -v`
Expected: FAIL — `serialise` is not a registered command.

- [ ] **Step 3: Add the two commands**

`serialise` reads `derived/events.jsonl`, loads the policy from
`config/serialisation.yml`, and writes `derived/transcripts/<image stem>.md` per
record. It renders nothing and imports no renderer — keep it that way; that
separation is the whole point of the command existing.

`preview` takes a case id, finds its record, prints the image path and the
serialised transcript. It exists so the §8.5 visual check has a transcript to
check against.

- [ ] **Step 4: Run the tests, then the whole corpus**

```bash
conda run -n docparse pytest tests/ -v
conda run -n docparse python -m generators.pipeline generate
conda run -n docparse python -m generators.pipeline serialise
conda run -n docparse python -m generators.pipeline preview CASE001
```

Expected: 165 transcripts written.

- [ ] **Step 5: Read three transcripts against their pages, by eye**

Open `output/invoices/CASE001_invoices.png` beside
`derived/transcripts/CASE001_invoices.md`, and do the same for one receipt and
one bank statement.

Check specifically: is every visible string present; is the table's column
order right; is a grouped statement's repeat date blank in both; did a wrapped
address rejoin into one line.

This step cannot be automated and is not optional. §8.5 makes visual inspection
the acceptance gate, and a transcription corpus's correctness is ultimately
visual — a transcript that parses cleanly and describes the wrong page passes
every other check in this plan.

- [ ] **Step 6: Commit one golden transcript per layout**

For each layout in the three registries, copy its transcript to
`tests/golden/<layout_id>.md` and add a test asserting a fresh `serialise`
reproduces it byte for byte. `tests/` is gitignored, so these are local — note
in the commit message that the golden net does not travel with the repo until
CI exists (spec §8.3's reversal).

- [ ] **Step 7: Full gate and commit**

```bash
conda run -n docparse pytest tests/ --cov=generators --cov-report=term
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 generators/
conda run -n docparse ruff format .
conda run -n docparse mypy generators/ --ignore-missing-imports
git add generators/pipeline.py
git commit -m "✨ feat: add serialise and preview commands"
```

Coverage floor is 80%.

---

## Done when

- `generate` renders 165 pages and writes 165 `events.jsonl` records.
- The coverage invariant runs on every `generate` and passes on the whole corpus.
- `serialise` produces 165 transcripts, and running it twice is byte-identical.
- A suppressed block leaves no event; a blanked repeat date is captured blank.
- Split columns serialise in DSL order.
- No transcript contains `**`.
- Three pages have been read against their transcripts by eye.
- `ruff`, `mypy` and `pytest --cov` (≥80%) all pass.

## Next plan

`export` and the dated deliverable directory (§6.1): generic filenames, the
`manifest.jsonl` with sha256 image hashes, `prompt.md` stating the split-column
convention, and the `serialisation.yml` copy that ships with the data. Then the
§8.6 calibration pass — run two or three real parsers over a sample and separate
genuine reading errors from convention mismatches, before the corpus freezes.
