"""Draw-time transcript capture.

The recorder is appended to by the DSL primitives at the moment each resolves
its content and is about to draw it (design §4.1). Capturing here rather than
re-walking the block tree at export time means the code that suppresses a block
is the code that would have emitted its event, so a transcript cannot disagree
with its image about what was drawn.

What that does *not* guarantee is coverage: a primitive added later could put
text on the canvas and emit nothing. `TranscriptDraw` closes that hole by
refusing any text draw the recorder has not authorised — see `note_text_drawn`.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from PIL import ImageDraw


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
        """The captured events, in walk order (a copy; mutating it does nothing)."""
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

        `rule` with a `fill_char` paints a row of repeated glyphs rather than
        drawing a line, so it puts text on the canvas that design §4.3 says
        emits nothing. This is the only sanctioned way to do that.
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
            CoverageError: No event authorises this draw and it is not inside a
                `decoration()` scope.
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


class TranscriptDraw:
    """A PIL drawing surface that refuses unauthorised text.

    Forwards every attribute to the wrapped `ImageDraw.ImageDraw` unchanged
    except `text`, which first asks the recorder whether an event authorises it.
    This is the design §8.2 coverage invariant, and it runs on every `generate`
    rather than only under pytest: a test catches the case someone thought of, a
    runtime invariant catches the primitive nobody has written yet.

    Wrapping the surface once covers all of `common.py`'s text helpers, which
    know only a string and a box — too little to emit a meaningful event
    themselves, and therefore exactly where an unnoticed gap would open.
    """

    def __init__(self, draw: ImageDraw.ImageDraw, recorder: TranscriptRecorder) -> None:
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


# Either drawing surface the DSL may be handed: the bare PIL one on a plain
# render, or the checked proxy above when a transcript is being captured. The
# primitives and common.py's helpers genuinely accept both, so they say so
# rather than claiming an ImageDraw and being handed a proxy.
DrawSurface = ImageDraw.ImageDraw | TranscriptDraw
