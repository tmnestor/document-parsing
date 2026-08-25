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

A `pair` event carries no top-level `text` — `draw_pair` records its label and
value separately in `meta` instead, deliberately: `pair_strip_trailing_colon`
is the serialiser's policy, not something baked into the corpus, so the raw
drawn label survives in `meta['label']` rather than a joined string
(`primitives_text.py:562-567`). `text` is synthesised from `meta` for exactly
this one kind — never from spans, which stay reserved for the post-wrap ink.
"""

from generators.layout_dsl.categories import SPAN_CATEGORY
from generators.tables import TableHtmlError, table_html

# Categories whose ink is real but which no metric should score.
_IGNORED_CATEGORIES = frozenset({"abandon"})


def _block_text(event: dict) -> str:
    """Recover the block's pre-wrap text, including a `pair`'s label and value.

    Every other annotatable kind (`line`, `title`) carries its full pre-wrap
    string on `event["text"]` directly. `pair` is the one exception: its label
    and value are recorded separately in `meta` so that how they join —
    `pair_strip_trailing_colon` — stays the serialiser's policy rather than a
    choice baked into the corpus. This reconstructs the same content `pair`
    actually drew (`primitives_text.py:560-567, 620`), from the event's own
    `meta`, never from spans.

    Args:
        event: One event dict.

    Returns:
        The block's text, or "" when the event carries none (e.g. `table`,
        whose content lives in `html` instead).
    """
    text = event.get("text")
    if text is None and event.get("kind") == "pair":
        meta = event.get("meta") or {}
        text = f"{meta.get('label', '')}{meta.get('value', '')}"
    return text or ""


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
    try:
        tables = iter(table_html(events))
    except TableHtmlError:
        # An unbalanced table stream cannot be rendered at all. Fall through to
        # an empty iterator so the per-table check below raises our own
        # LayoutError, naming which annotation is missing its html (spec §9.5),
        # instead of letting `table_html`'s stream-level error escape here.
        tables = iter(())
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
            "text": _block_text(event),
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
