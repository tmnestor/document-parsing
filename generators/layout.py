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
value separately in `meta` instead, deliberately: how they join is
`serialise.py`'s policy (`pair_separator`, `pair_strip_trailing_colon`), not
something baked into the corpus (`primitives_text.py:562-567`). `text` is
synthesised for exactly this one kind via `serialise.pair_text` — the same
join `serialise.py` itself calls, so a `pair` annotation's `text` is byte-
identical to its line in the Markdown transcript, never a second hand-kept
implementation of the join. Never from spans, which stay reserved for the
post-wrap ink.
"""

from generators.layout_dsl.categories import SPAN_CATEGORY
from generators.serialise import pair_text
from generators.tables import TableHtmlError, table_html

# Categories whose ink is real but which no metric should score.
_IGNORED_CATEGORIES = frozenset({"abandon"})


def _block_text(event: dict, policy: dict) -> str:
    """Recover the block's pre-wrap text, including a `pair`'s label and value.

    Every other annotatable kind (`line`, `title`) carries its full pre-wrap
    string on `event["text"]` directly. `pair` is the one exception: its label
    and value are recorded separately in `meta`, joined here by
    `serialise.pair_text` under the same policy `serialise.py` itself uses —
    so the annotation's text is exactly what the transcript records (design
    §4), not a second, independently-drifting join. Never from spans, which
    stay reserved for the post-wrap ink.

    Args:
        event: One event dict.
        policy: The validated serialisation policy (§`load_serialisation_policy`).

    Returns:
        The block's text, or "" when the event carries none (e.g. `table`,
        whose content lives in `html` instead).
    """
    text = event.get("text")
    if text is None and event.get("kind") == "pair":
        text = pair_text(event.get("meta") or {}, policy)
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


def layout_dets(events: list[dict], *, attribute: dict, policy: dict) -> dict:
    """Build one page's annotations.

    Args:
        events: One page's events, as stored in `events.jsonl`.
        attribute: Key-value classification carried onto every annotation —
            doc type, layout id and degradation tier.
        policy: The validated serialisation policy — needed only to join a
            `pair` event's label and value the same way `serialise.py` does.

    Returns:
        `{"layout_dets": [...]}`, annotations in reading order.

    Raises:
        LayoutError: A table's event stream is unbalanced and cannot be
            rendered to HTML at all (spec §9.5), a box is degenerate or off
            the page (spec §9.3), or the reading order has a duplicate or a
            gap (spec §9.4).
    """
    try:
        tables = iter(table_html(events))
    except TableHtmlError as err:
        # Raised immediately, naming the real defect (the unclosed table_open
        # or row_open `table_html` found), rather than deferred to whichever
        # `category_type == "table"` annotation happens to come first below —
        # on a page with more than one table, that would blame the wrong one.
        raise _err(
            f"the page's tables could not be rendered to html: {err}",
            key="table html",
            expected="a balanced table stream for every table on the page — each "
            "table_open closed by a table_close, each row_open by a row_close, e.g.\n"
            "              table_open, row_open, cell..., row_close, ..., table_close",
            recover="the table's row and cell events are missing or unbalanced; "
            "regenerate the corpus; a truncated stream means `generate` did not "
            "finish, and a re-run repairs it.",
        ) from err
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
            "text": _block_text(event, policy),
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
                # `table_html` succeeded overall but produced fewer tables than
                # this page has `table`-category annotations — a defensive
                # guard, since every table_open sets category_type="table"
                # (categories.py) and table_html emits exactly one string per
                # balanced table_open/table_close pair.
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
