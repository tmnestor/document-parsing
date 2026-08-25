"""Startup validation for a layout's `body:` tree.

Every layout is fully checked before any rendering begins, per CLAUDE.md's
fail-fast rule: unknown primitives, missing keys, unknown field references and
unregistered row providers all fail here with a four-element diagnostic.
"""

import re

from generators.common import FONT_FAMILIES
from generators.layout_dsl.binding import referenced_fields
from generators.layout_dsl.defaults import PARAMETER_DEFAULTS
from generators.layout_dsl.field_providers import (
    collect_emit_collisions,
    field_provider_emits,
    field_provider_names,
    field_provider_param_keys,
)
from generators.layout_dsl.primitives_table import CELL_LINE_SPACINGS, COLUMN_ALIGNMENTS
from generators.layout_dsl.primitives_text import ALIGNMENTS, PAIR_CURRENCIES, PAIR_VALUE_ALIGNS
from generators.layout_dsl.providers import provider_names, provider_param_keys

# Matches a `{` opened but never closed, or a `}` closed but never opened --
# `referenced_fields`'s `\{([A-Z][A-Z0-9_]*)\}` only matches well-formed
# `{FIELD}`, so a typo like `{PAYER_NAME` (missing `}`) is invisible to it and
# draws as a silent literal instead of failing validation.
#
# The second alternative's lookbehind excludes `[A-Z0-9_{]`, not just `{`: a
# lone `(?<!\{)` only rejects a match starting immediately after `{`, but
# `.search()` also tries starting mid-identifier -- e.g. at the "A" in a
# well-formed "{PAYER_NAME}", where the preceding character is "P", not "{" --
# and would wrongly flag "AYER_NAME}" as an unopened placeholder. Excluding
# every identifier character too forces a match to start at a token's true
# beginning.
_UNBALANCED = re.compile(r"\{[A-Z][A-Z0-9_]*(?![A-Z0-9_]*\})|(?<![A-Z0-9_{])[A-Z][A-Z0-9_]*\}")

# `frame` and `grouping` are independent axes describing a table's row style.
#
# `frame` -- how rows and the header are decorated:
#   ruled    -- rule lines above/below the header.
#   bordered -- a bordered header box + interior column dividers, a rule
#               above every row but the first.
#   filled   -- a `fill_color` rectangle drawn behind the header bar and
#               behind each group's dedicated date row (NAB's light-blue bar).
#   plain    -- no header or row decoration at all.
#
# `grouping` -- how repeated transaction dates are handled:
#   none          -- dates repeat on every row.
#   dedicated_row -- a separate bold date sub-header row is inserted whenever
#                    the date changes (CBA's "grouped").
#   inline        -- the repeated date is blanked within the row and the row
#                    above a new group is ruled/bordered, without consuming a
#                    row of its own (Westpac premium's "bordered_grouped").
FRAMES = ("ruled", "bordered", "filled", "plain")
GROUPINGS = ("none", "dedicated_row", "inline")

# primitive -> (required keys, optional keys)
PRIMITIVES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # `content` and `from_layout` are mutually exclusive alternatives, not two
    # independent optional keys — see the "exactly one of" check in
    # _validate_block. Neither is in `required` because the choice between
    # them is validated there, with a diagnostic naming both options.
    "text": (
        (),
        (
            "content",
            "from_layout",
            "role",
            "align",
            "color",
            "field",
            "bold",
            "title",
            "suppress_if_equals",
            "family",
            "line_advance",
            "budget",
        ),
    ),
    "pair": (
        ("label", "value"),
        (
            "role",
            "color",
            "field",
            "family",
            "line_advance",
            "budget",
            "value_align",
            "min_gap",
            "separator",
            "bold",
            "currency",
        ),
    ),
    "block": (("lines",), ("role", "color", "heading", "family", "line_advance")),
    "rule": ((), ("color", "thickness", "pad_above", "pad_below", "fill_char")),
    "spacer": ((), ("height",)),
    "panel": (("children",), ("border_color", "padding", "height")),
    "split": (("children",), ("gap", "divider", "divider_color", "widths")),
    "banner": (
        ("height", "color"),
        ("content", "from_layout", "text_color", "role", "text_y", "bold", "family"),
    ),
    "table": (
        ("rows", "columns", "frame", "grouping"),
        (
            "params",
            "row_height",
            "header",
            "header_bold",
            "header_height",
            "dividers",
            "fill_color",
            "fill_inset",
            "fill_height",
            "label_inset_y",
            "row_inset_y",
            "cell_line_spacing",
            "group_gap",
            "synthetic_row_placement",
            "header_rule_top",
            "header_rule_gap",
            "family",
            "role",
            "line_advance",
        ),
    ),
}

_CONTAINERS = ("panel", "split")

# A table column's own allowed keys — the "one level down" counterpart to
# PRIMITIVES above. Without this, a typo (e.g. `feild` for `field`) is a
# silent no-op: the column simply never draws that cell, indistinguishable
# at a glance from the field genuinely being absent from the entry.
_COLUMN_KEYS = frozenset(
    {
        "key",
        "label",
        "align",
        "x",
        "x_right",
        # A header label whose anchor and alignment differ from its own cells'
        # — the legacy invoice renderer left-aligns "Unit Price" at the
        # column's left edge above amounts that right-align 200px further on.
        # Each falls back to the column's own `x`/`align` when absent.
        "label_x",
        "label_align",
        "budget",
        "field",
        "last_row_field",
        "currency",
        "currency_suffix",
        "sub_line",
    }
)

# Where a provider's leading synthetic row (opening_balance / brought_forward)
# renders relative to `grouping: dedicated_row`'s first date sub-header row:
# "leading" (default) renders it first, ahead of any group header -- CBA's
# Opening Balance. "after_first_group_header" defers it until that header has
# been drawn -- NAB's Brought-forward row, which sits *under* the first date.
SYNTHETIC_ROW_PLACEMENTS = ("leading", "after_first_group_header")


# Top-level layout keys the DSL itself reads off the layout dict, each named
# with the call site that reads it. A key absent from this set, from the page
# keys below, and from what the layout's own body references is a key nothing
# reads -- see `known_layout_keys`.
_ENGINE_LAYOUT_KEYS = frozenset(
    {
        "body",  # engine.render_body
        "defaults",  # defaults.resolve_param
        "field_budgets",  # layout_budgets.field_budget
        "field_providers",  # field_providers.apply_field_providers
        "font_sizes",  # primitives_text.resolve_role
        "row_height",  # primitives_table._resolve_row_height (layout fallback)
    }
)

# Page-box keys the per-document adapters (generators/{bank_statement,receipt,
# invoice}.py) read to build the canvas and content region before handing the
# layout to the engine. `page_dimensions` is read by the engine too, in
# primitives_text.draw_banner, which spans the full page rather than the region.
_PAGE_LAYOUT_KEYS = frozenset(
    {
        "margin",  # every adapter: Region.x and the starting y-cursor
        "content_width",  # every adapter: Region.width
        "page_dimensions",  # bank_statement.py, invoice.py, draw_banner
        "width",  # receipt.py: the thermal roll's pixel width
        "canvas_ceiling",  # receipt.py: the pre-crop canvas height
    }
)

# Keys no render path reads, but which the corpus tooling around the renderers
# does. They are not dead: deleting one breaks seeding or a ground-truth
# invariant rather than a page.
#
#   bank               -- the full legal bank name a bank layout's letterhead
#                         stands for, holding SUPPLIER_NAME to it so the
#                         authored field cannot contradict the letterhead
#                         ("bank = f(layout)").
#   transaction_count  -- {min, max} row count a seeding pass sizes a
#                         statement's transaction list to, so a dense layout is
#                         seeded with enough rows to look dense.
_CORPUS_LAYOUT_KEYS = frozenset({"bank", "transaction_count"})

# Block keys whose *value* is the name of a layout key, rather than a `{FIELD}`
# template or a literal. Both are resolved against the layout dict at render
# time -- `from_layout` in draw_text_block/draw_banner, `suppress_if_equals` in
# draw_text_block -- and both are checked to exist by `_validate_geometry`.
_LAYOUT_KEY_REFERENCES = ("from_layout", "suppress_if_equals")


class LayoutSchemaError(RuntimeError):
    """Raised when a layout body fails structural validation."""


def _err(what: str, *, layout_path: str, key_path: str, expected: str, recover: str) -> LayoutSchemaError:
    """Build a four-element fail-fast diagnostic error.

    Args:
        what: What is wrong.
        layout_path: Path to the offending layout YAML.
        key_path: Dotted path to the offending key inside that file.
        expected: What a valid value looks like.
        recover: One-line remediation.

    Returns:
        The constructed error.
    """
    return LayoutSchemaError(
        "Invalid layout body.\n"
        f"  What:     {what}\n"
        f"  Where:    {layout_path} -> {key_path}\n"
        f"  Expected: {expected}\n"
        f"  Recover:  {recover}"
    )


def _check_braces(template: str, *, layout_path: str, key_path: str) -> None:
    """Reject a placeholder missing its opening or closing brace.

    `referenced_fields` only matches well-formed `{FIELD}`, so a typo like
    `{PAYER_NAME` is invisible to it and draws as a literal.
    """
    if _UNBALANCED.search(template):
        raise _err(
            f"template {template!r} contains an unbalanced placeholder brace.",
            layout_path=layout_path,
            key_path=key_path,
            expected='every placeholder written as {FIELD_NAME}, e.g. content: "Account: {PAYER_NAME}".',
            recover=f"add the missing brace in {key_path}.",
        )


def validate_body(
    body: list,
    *,
    layout_id: str,
    layout_path: str,
    known_fields: set[str],
) -> None:
    """Validate a layout's body tree, recursing into containers.

    Args:
        body: The layout's `body:` list of block dicts.
        layout_id: Layout id, used in diagnostics.
        layout_path: Path to the layout YAML, used in diagnostics.
        known_fields: Field names the document type may reference.

    Raises:
        LayoutSchemaError: On any structural or reference problem.
    """
    if not isinstance(body, list):
        raise _err(
            f"layout '{layout_id}' body is {type(body).__name__}, not a list.",
            layout_path=layout_path,
            key_path=f"{layout_id}.body",
            expected="a list of block mappings, each with a 'type' key.",
            recover=f"make {layout_id}.body a YAML list.",
        )
    _validate_blocks(
        body,
        layout_id=layout_id,
        layout_path=layout_path,
        known_fields=known_fields,
        key_path=f"{layout_id}.body",
    )


def _validate_blocks(
    blocks: list, *, layout_id: str, layout_path: str, known_fields: set[str], key_path: str
) -> None:
    """Validate a list of blocks at one nesting level."""
    for index, block in enumerate(blocks):
        here = f"{key_path}[{index}]"
        if not isinstance(block, dict):
            raise _err(
                f"block is {type(block).__name__}, not a mapping.",
                layout_path=layout_path,
                key_path=here,
                expected='a mapping such as {type: text, content: "{PAYER_NAME}"}.',
                recover="replace the entry with a block mapping.",
            )
        _validate_block(
            block, layout_id=layout_id, layout_path=layout_path, known_fields=known_fields, key_path=here
        )


def _validate_block(
    block: dict, *, layout_id: str, layout_path: str, known_fields: set[str], key_path: str
) -> None:
    """Validate one block and recurse into any children."""
    kind = block.get("type")
    if kind is None:
        raise _err(
            "block has no 'type' key.",
            layout_path=layout_path,
            key_path=key_path,
            expected=f"type: one of {sorted(PRIMITIVES)}.",
            recover="add a type: key naming the primitive to render.",
        )
    if kind not in PRIMITIVES:
        raise _err(
            f"unknown primitive '{kind}'.",
            layout_path=layout_path,
            key_path=f"{key_path}.type",
            expected=f"one of {sorted(PRIMITIVES)}.",
            recover="use a supported primitive, or add one to PRIMITIVES in "
            "generators/layout_dsl/schema.py.",
        )

    required, optional = PRIMITIVES[kind]
    missing = [key for key in required if key not in block]
    if missing:
        raise _err(
            f"'{kind}' block missing required key(s): {missing}.",
            layout_path=layout_path,
            key_path=key_path,
            expected=f"required {list(required)}; optional {list(optional)}.",
            recover=f"add {missing} to the {kind} block.",
        )
    allowed = set(required) | set(optional) | {"type", "when"}
    unknown = sorted(set(block) - allowed)
    if unknown:
        raise _err(
            f"'{kind}' block has unknown key(s): {unknown}.",
            layout_path=layout_path,
            key_path=key_path,
            expected=f"only {sorted(allowed)}.",
            recover=f"remove {unknown}, or add them to PRIMITIVES in generators/layout_dsl/schema.py.",
        )

    # A block-level `family:` override is caught here rather than at render
    # time: load_font would raise FontFamilyError on the first block that
    # happens to draw, which is both later than startup and harder to trace
    # back to the YAML that caused it.
    family = block.get("family")
    if family is not None and family not in FONT_FAMILIES:
        raise _err(
            f"unknown font family '{family}'.",
            layout_path=layout_path,
            key_path=f"{key_path}.family",
            expected=f"one of {sorted(FONT_FAMILIES)}.",
            recover=f"set family: to one of {sorted(FONT_FAMILIES)}, or vendor the new "
            "face in fonts/ and register it in FONT_FAMILIES in generators/common.py.",
        )

    if kind in ("text", "banner"):
        has_content = "content" in block
        has_from_layout = "from_layout" in block
        if has_content == has_from_layout:
            raise _err(
                f"'{kind}' block sets "
                + (
                    "both 'content' and 'from_layout'."
                    if has_content
                    else "neither 'content' nor 'from_layout'."
                ),
                layout_path=layout_path,
                key_path=key_path,
                expected="exactly one of: content: '{FIELD}' (an entry-field template) or "
                "from_layout: <layout_key> (a layout value read literally).",
                recover="set 'content:' or 'from_layout:', not both or neither.",
            )

    if kind == "text":
        align = block.get("align")
        if align is not None and align not in ALIGNMENTS:
            raise _err(
                f"unknown align '{align}'.",
                layout_path=layout_path,
                key_path=f"{key_path}.align",
                expected=f"one of {list(ALIGNMENTS)}.",
                recover=f"set align: to one of {list(ALIGNMENTS)}.",
            )
        # Checked as a type rather than left to `bool()`, which would read the
        # string "false" as True and silently promote a body line to the H1.
        title = block.get("title")
        if title is not None and not isinstance(title, bool):
            raise _err(
                f"title must be a boolean, got {type(title).__name__} ({title!r}).",
                layout_path=layout_path,
                key_path=f"{key_path}.title",
                expected="title: true on the one block carrying the page's own title, "
                "e.g. {type: text, content: 'TAX INVOICE', title: true}.",
                recover="set title: true or title: false, unquoted, or drop the key to "
                "take the layout's text_title default.",
            )

    if kind == "pair":
        value_align = block.get("value_align")
        if value_align is not None and value_align not in PAIR_VALUE_ALIGNS:
            raise _err(
                f"unknown value_align '{value_align}'.",
                layout_path=layout_path,
                key_path=f"{key_path}.value_align",
                expected=f"one of {list(PAIR_VALUE_ALIGNS)}.",
                recover=f"set value_align: to one of {list(PAIR_VALUE_ALIGNS)}.",
            )
        currency = block.get("currency")
        if currency is not None and currency not in PAIR_CURRENCIES:
            raise _err(
                f"unknown currency '{currency}'.",
                layout_path=layout_path,
                key_path=f"{key_path}.currency",
                expected=f"one of {list(PAIR_CURRENCIES)} — 'symbol' keeps the $ prefix, 'plain' drops it.",
                recover=f"set currency: to one of {list(PAIR_CURRENCIES)}, or remove it to "
                "draw the value exactly as it interpolates.",
            )

    _validate_references(block, layout_path=layout_path, known_fields=known_fields, key_path=key_path)

    if kind == "table":
        _validate_table(block, known_fields=known_fields, layout_path=layout_path, key_path=key_path)
    if kind in _CONTAINERS:
        _validate_children(
            block,
            layout_id=layout_id,
            layout_path=layout_path,
            known_fields=known_fields,
            key_path=key_path,
        )


def _validate_references(block: dict, *, layout_path: str, known_fields: set[str], key_path: str) -> None:
    """Check every {FIELD} placeholder, `when:`, and `field:` is a known field.

    `field:` named a block's captured bounding box in the predecessor's
    `derived/geometry.jsonl`. Events carry no geometry here (design §4.2), so
    no render path reads it and a typo changes no pixel and no transcript.
    It is still checked: the key survives in the ported layouts as the block's
    declared identity, and a name that resolves against nothing is a layout
    error worth catching at `validate` rather than leaving to rot.
    """
    texts: list[str] = []
    for key in ("content", "label", "value", "heading"):
        if isinstance(block.get(key), str):
            texts.append(block[key])
    for line in block.get("lines", []) or []:
        if isinstance(line, str):
            texts.append(line)

    for text in texts:
        _check_braces(text, layout_path=layout_path, key_path=key_path)
        for name in referenced_fields(text):
            if name not in known_fields:
                raise _err(
                    f"unknown field reference '{{{name}}}'.",
                    layout_path=layout_path,
                    key_path=key_path,
                    expected=f"a field defined for this document type: {sorted(known_fields)}.",
                    recover=f"fix the field name, or add '{name}' to config/field_definitions.yml.",
                )

    when = block.get("when")
    if when is not None and when not in known_fields:
        raise _err(
            f"'when' references unknown field '{when}'.",
            layout_path=layout_path,
            key_path=f"{key_path}.when",
            expected=f"a field defined for this document type: {sorted(known_fields)}.",
            recover=f"fix the field name, or add '{when}' to config/field_definitions.yml.",
        )

    field = block.get("field")
    if field is not None and field not in known_fields:
        raise _err(
            f"'field' references unknown field '{field}'.",
            layout_path=layout_path,
            key_path=f"{key_path}.field",
            expected=f"a field defined for this document type: {sorted(known_fields)}.",
            recover=f"fix the field name, or add '{field}' to config/field_definitions.yml.",
        )


def _validate_table(block: dict, *, known_fields: set[str], layout_path: str, key_path: str) -> None:
    """Check a table's provider, params, row style, and column definitions."""
    rows = block["rows"]
    if rows not in provider_names():
        raise _err(
            f"unknown row provider '{rows}'.",
            layout_path=layout_path,
            key_path=f"{key_path}.rows",
            expected=f"one of {provider_names()}.",
            recover="set rows: to a registered provider, or register one with "
            "@row_provider in generators/layout_dsl/providers.py.",
        )

    params = block.get("params", {})
    accepted_params = provider_param_keys(rows)
    unknown_params = sorted(set(params) - accepted_params)
    if unknown_params:
        raise _err(
            f"table params names unknown key(s) {unknown_params} for provider '{rows}'.",
            layout_path=layout_path,
            key_path=f"{key_path}.params",
            expected=f"one of {sorted(accepted_params)} for provider '{rows}'.",
            recover=f"fix the typo, or add {unknown_params} to provider '{rows}''s "
            "params=frozenset({...}) in generators/layout_dsl/providers.py.",
        )

    frame = block["frame"]
    if frame not in FRAMES:
        raise _err(
            f"unknown frame '{frame}'.",
            layout_path=layout_path,
            key_path=f"{key_path}.frame",
            expected=f"one of {list(FRAMES)}.",
            recover=f"set frame: to one of {list(FRAMES)}.",
        )

    grouping = block["grouping"]
    if grouping not in GROUPINGS:
        raise _err(
            f"unknown grouping '{grouping}'.",
            layout_path=layout_path,
            key_path=f"{key_path}.grouping",
            expected=f"one of {list(GROUPINGS)}.",
            recover=f"set grouping: to one of {list(GROUPINGS)}.",
        )

    cell_line_spacing = block.get("cell_line_spacing")
    if cell_line_spacing is not None and cell_line_spacing not in CELL_LINE_SPACINGS:
        raise _err(
            f"unknown cell_line_spacing '{cell_line_spacing}'.",
            layout_path=layout_path,
            key_path=f"{key_path}.cell_line_spacing",
            expected=f"one of {list(CELL_LINE_SPACINGS)} — 'row_height' advances a budgeted "
            "cell by the table's own row pitch, 'font' by the fitted font's own line height.",
            recover=f"set cell_line_spacing: to one of {list(CELL_LINE_SPACINGS)}, or remove it "
            "to use the layout's table_cell_line_spacing default.",
        )

    if frame == "filled" and "fill_color" not in block:
        raise _err(
            "frame: filled requires fill_color, which this table block does not set.",
            layout_path=layout_path,
            key_path=f"{key_path}.fill_color",
            expected='a hex color string, e.g. fill_color: "#E8F0FE".',
            recover=f"add fill_color: to the table block, or use a different frame ({list(FRAMES)}).",
        )
    if frame != "filled" and "fill_color" in block:
        raise _err(
            f"fill_color is set but frame is '{frame}', which never draws it.",
            layout_path=layout_path,
            key_path=f"{key_path}.fill_color",
            expected="fill_color only alongside frame: filled.",
            recover="remove fill_color, or set frame: filled.",
        )

    for filled_only_key in ("fill_height", "fill_inset"):
        if frame != "filled" and filled_only_key in block:
            raise _err(
                f"{filled_only_key} is set but frame is '{frame}', which never reads it — only "
                "frame: filled draws a fill for it to adjust.",
                layout_path=layout_path,
                key_path=f"{key_path}.{filled_only_key}",
                expected=f"{filled_only_key} only alongside frame: filled.",
                recover=f"remove {filled_only_key}, or set frame: filled.",
            )

    if grouping != "dedicated_row" and "group_gap" in block:
        raise _err(
            "group_gap is set but grouping is not 'dedicated_row', which never reads it — "
            "only dedicated_row inserts date sub-header rows with a gap between them.",
            layout_path=layout_path,
            key_path=f"{key_path}.group_gap",
            expected="group_gap only alongside grouping: dedicated_row.",
            recover="remove group_gap, or set grouping: dedicated_row.",
        )

    if "synthetic_row_placement" in block:
        if grouping != "dedicated_row":
            raise _err(
                "synthetic_row_placement is set but grouping is not 'dedicated_row', which "
                "never reads it — there is no group header for a synthetic row to be placed "
                "relative to.",
                layout_path=layout_path,
                key_path=f"{key_path}.synthetic_row_placement",
                expected="synthetic_row_placement only alongside grouping: dedicated_row.",
                recover="remove synthetic_row_placement, or set grouping: dedicated_row.",
            )
        if block["synthetic_row_placement"] not in SYNTHETIC_ROW_PLACEMENTS:
            raise _err(
                f"unknown synthetic_row_placement {block['synthetic_row_placement']!r}.",
                layout_path=layout_path,
                key_path=f"{key_path}.synthetic_row_placement",
                expected=f"one of {list(SYNTHETIC_ROW_PLACEMENTS)}.",
                recover=f"set synthetic_row_placement: to one of {list(SYNTHETIC_ROW_PLACEMENTS)}.",
            )

    if frame != "bordered" and "dividers" in block:
        raise _err(
            f"dividers is set but frame is '{frame}', which never draws them — only "
            "frame: bordered cuts the header/body into columns with dividers.",
            layout_path=layout_path,
            key_path=f"{key_path}.dividers",
            expected="dividers only alongside frame: bordered.",
            recover="remove dividers, or set frame: bordered.",
        )

    for ruled_only_key in ("header_rule_top", "header_rule_gap"):
        if frame != "ruled" and ruled_only_key in block:
            raise _err(
                f"{ruled_only_key} is set but frame is '{frame}', which never draws a header rule "
                "to adjust — only frame: ruled draws one.",
                layout_path=layout_path,
                key_path=f"{key_path}.{ruled_only_key}",
                expected=f"{ruled_only_key} only alongside frame: ruled.",
                recover=f"remove {ruled_only_key}, or set frame: ruled.",
            )

    columns = block["columns"]
    if not isinstance(columns, list) or not columns:
        raise _err(
            "table has no columns.",
            layout_path=layout_path,
            key_path=f"{key_path}.columns",
            expected="a non-empty list of {key, label, align, x|x_right} mappings.",
            recover="add at least one column.",
        )
    for index, column in enumerate(columns):
        for required in ("key", "label"):
            if not isinstance(column, dict) or required not in column:
                raise _err(
                    f"column {index} missing '{required}'.",
                    layout_path=layout_path,
                    key_path=f"{key_path}.columns[{index}]",
                    expected="{key: date, label: Date, align: left, x: 0}.",
                    recover=f"add {required}: to the column.",
                )

        unknown_col_keys = sorted(set(column) - _COLUMN_KEYS)
        if unknown_col_keys:
            raise _err(
                f"column {index} has unknown key(s) {unknown_col_keys}.",
                layout_path=layout_path,
                key_path=f"{key_path}.columns[{index}]",
                expected=f"only {sorted(_COLUMN_KEYS)}.",
                recover=f"remove {unknown_col_keys}, or add them to _COLUMN_KEYS in "
                "generators/layout_dsl/schema.py.",
            )

        for align_key in ("align", "label_align"):
            col_align = column.get(align_key)
            if col_align is not None and col_align not in COLUMN_ALIGNMENTS:
                raise _err(
                    f"column {index} has unknown {align_key} '{col_align}'.",
                    layout_path=layout_path,
                    key_path=f"{key_path}.columns[{index}].{align_key}",
                    expected=f"one of {list(COLUMN_ALIGNMENTS)} (table columns do not support 'center').",
                    recover=f"set {align_key}: to one of {list(COLUMN_ALIGNMENTS)}.",
                )

        label_x = column.get("label_x")
        if label_x is not None and (not isinstance(label_x, int) or isinstance(label_x, bool)):
            raise _err(
                f"column {index} label_x is {label_x!r}, not an int.",
                layout_path=layout_path,
                key_path=f"{key_path}.columns[{index}].label_x",
                expected="an integer offset from the region's left edge, e.g. label_x: 1050.",
                recover="set label_x: to an int, or remove it so the header label sits at the "
                "column's own anchor.",
            )

        for field_key in ("field", "last_row_field"):
            name = column.get(field_key)
            if name is not None and name not in known_fields:
                raise _err(
                    f"column {index} {field_key!r} references unknown field '{name}'.",
                    layout_path=layout_path,
                    key_path=f"{key_path}.columns[{index}].{field_key}",
                    expected=f"a field defined for this document type: {sorted(known_fields)}.",
                    recover=f"fix the field name, or add '{name}' to config/field_definitions.yml.",
                )

        sub_line = column.get("sub_line")
        if sub_line is not None and (not isinstance(sub_line, dict) or "key" not in sub_line):
            raise _err(
                f"column {index} sub_line is missing 'key'.",
                layout_path=layout_path,
                key_path=f"{key_path}.columns[{index}].sub_line",
                expected="{key: reference, role: sub_description, color: '#999999', "
                "offset_y: 34, height: 32} — only 'key' is required.",
                recover="add key: to the column's sub_line, naming the row field it reads.",
            )
        if "x" not in column and "x_right" not in column:
            raise _err(
                f"column {index} has neither 'x' nor 'x_right'.",
                layout_path=layout_path,
                key_path=f"{key_path}.columns[{index}]",
                expected="x: <offset from region left> or x_right: <offset from region right>.",
                recover="add x: or x_right: to position the column.",
            )

    for index, divider in enumerate(block.get("dividers", [])):
        if not isinstance(divider, dict) or ("x" not in divider and "x_right" not in divider):
            raise _err(
                f"divider {index} has neither 'x' nor 'x_right'.",
                layout_path=layout_path,
                key_path=f"{key_path}.dividers[{index}]",
                expected="x: <offset from region left> or x_right: <offset from region right>.",
                recover="add x: or x_right: to position the divider, e.g. {x_right: -320}.",
            )


def _validate_field_providers(layout: dict, *, layout_id: str, layout_path: str) -> list[str]:
    """Check a layout's `field_providers:` entries and return their combined emits.

    Presence of the `field_providers` key itself is checked by the caller
    (`validate_layout`, alongside `body` and `content_width`) -- this assumes
    the key exists and validates each entry's shape and content.

    Args:
        layout: The resolved layout dict, carrying `field_providers`.
        layout_id: Layout id, used in diagnostics.
        layout_path: Path to the layout YAML, used in diagnostics.

    Returns:
        Every `emits` name from every provider this layout's
        `field_providers:` references -- the derived-field vocabulary
        `validate_body`'s `known_fields` must additionally accept for this
        one layout. Deliberately scoped to providers *this* layout actually
        references, not every provider ever registered: unioning in every
        registered provider's emits regardless of whether this layout uses it
        would let a `{FIELD}` typo that happens to collide with some other
        layout's derived field silently resolve, weakening the very check
        this exists to perform.

    Raises:
        LayoutSchemaError: If an entry names an unregistered provider, an
            entry's `params` key is not among that provider's declared
            params, or two providers on this layout declare an overlapping
            `emits` name.
    """
    emits: list[str] = []
    # Maps an emitted name to the provider that declared it, so a second
    # provider on this same layout declaring the same name is caught here --
    # statically, from the emits= declarations alone, no provider call
    # needed. This is the primary defence against two providers silently
    # overwriting one another's value; apply_field_providers in
    # field_providers.py keeps a second, defensive check at merge time for
    # callers that build a layout dict by hand and never go through
    # validate_layout -- both call the shared collect_emit_collisions() so
    # the two diagnostics cannot drift apart the way an earlier round of
    # this task found them doing.
    emitted_by: dict[str, str] = {}
    for index, spec in enumerate(layout["field_providers"]):
        here = f"{layout_id}.field_providers[{index}]"
        name = spec.get("name") if isinstance(spec, dict) else None
        if name is None:
            raise _err(
                "field_providers entry has no 'name' key.",
                layout_path=layout_path,
                key_path=here,
                expected="{name: <registered field provider>, params: {...}}.",
                recover=f"add a name: key to {here}.",
            )
        if name not in field_provider_names():
            raise _err(
                f"unknown field provider '{name}'.",
                layout_path=layout_path,
                key_path=f"{here}.name",
                expected=f"one of {field_provider_names()}.",
                recover="set name: to a registered field provider, or register one with "
                "@field_provider in generators/layout_dsl/field_providers.py.",
            )

        params = spec.get("params", {})
        accepted_params = field_provider_param_keys(name)
        unknown_params = sorted(set(params) - accepted_params)
        if unknown_params:
            raise _err(
                f"field_providers entry names unknown param(s) {unknown_params} for provider '{name}'.",
                layout_path=layout_path,
                key_path=f"{here}.params",
                expected=f"one of {sorted(accepted_params)} for provider '{name}'.",
                recover=f"fix the typo, or add {unknown_params} to provider '{name}''s "
                "params=frozenset({...}) in generators/layout_dsl/field_providers.py.",
            )

        provider_emits = field_provider_emits(name)
        collision = collect_emit_collisions(emitted_by, str(name), provider_emits)
        if collision is not None:
            what, expected, recover = collision
            raise _err(
                what,
                layout_path=layout_path,
                key_path=f"{here}.name",
                expected=expected,
                recover=recover,
            )
        emitted_by.update(dict.fromkeys(provider_emits, str(name)))

        emits.extend(provider_emits)

    return emits


def _validate_split_widths(widths: object, *, children: list, layout_path: str, key_path: str) -> None:
    """Check a split's explicit `widths` list is a non-empty list of positive
    ints, one per column.

    `widths` replaces equal division for columns that must not be equal --
    invoice totals' fixed 400px column at the right edge -- so a mismatch
    with the actual column count (e.g. a column added without updating
    widths) is an authoring error, not silently absorbed by falling back to
    equal division.
    """
    if not isinstance(widths, list) or not widths:
        raise _err(
            f"split widths must be a non-empty list, got {widths!r}.",
            layout_path=layout_path,
            key_path=f"{key_path}.widths",
            expected="a non-empty list of positive ints, one per column, e.g. widths: [1300, 400].",
            recover=f"set widths: to a list of {len(children)} positive ints, one per child column.",
        )
    non_positive = [w for w in widths if not isinstance(w, int) or isinstance(w, bool) or w <= 0]
    if non_positive:
        raise _err(
            f"split widths must all be positive ints, got {widths!r}.",
            layout_path=layout_path,
            key_path=f"{key_path}.widths",
            expected="a list of positive ints, e.g. widths: [1300, 400].",
            recover=f"fix the non-positive or non-int entr{'y' if len(non_positive) == 1 else 'ies'} "
            "in widths.",
        )
    if len(widths) != len(children):
        raise _err(
            f"split widths has {len(widths)} entries but children has {len(children)} columns.",
            layout_path=layout_path,
            key_path=f"{key_path}.widths",
            expected=f"widths: a list of exactly {len(children)} ints, matching children's column count.",
            recover=f"add or remove entries so widths has exactly {len(children)} values, "
            "one per child column.",
        )


def _validate_children(
    block: dict, *, layout_id: str, layout_path: str, known_fields: set[str], key_path: str
) -> None:
    """Recurse into a container's children.

    `panel` takes a flat list of blocks; `split` takes a list of such lists,
    one per column.
    """
    children = block["children"]
    if block["type"] == "split":
        if not isinstance(children, list) or len(children) < 2:
            raise _err(
                "split needs at least two child columns.",
                layout_path=layout_path,
                key_path=f"{key_path}.children",
                expected="a list of at least two lists of blocks.",
                recover="add a second column, or use panel for a single column.",
            )
        if "widths" in block:
            _validate_split_widths(
                block["widths"], children=children, layout_path=layout_path, key_path=key_path
            )
        for index, column in enumerate(children):
            _validate_blocks(
                column,
                layout_id=layout_id,
                layout_path=layout_path,
                known_fields=known_fields,
                key_path=f"{key_path}.children[{index}]",
            )
    else:
        _validate_blocks(
            children,
            layout_id=layout_id,
            layout_path=layout_path,
            known_fields=known_fields,
            key_path=f"{key_path}.children",
        )


# Primitive kinds whose own `role:` key (explicit, or the layout's default
# role when omitted) is resolved through line_advance() at render time --
# text/pair/block draw a line and advance the y-cursor by it. A table block
# also resolves its own advance this way (for its header row / multi-line
# header labels), through its own `role:` key when it carries one -- a table
# draws every label and cell at that role's size -- and the layout's default
# role otherwise.
# banner is deliberately excluded: its own `role:` key selects banner_role's
# font size, but draw_banner never calls line_advance() -- it always leaves
# the y-cursor unchanged -- so a banner's role has no bearing on this check.
_LINE_ADVANCE_ROLE_PRIMITIVES = ("text", "pair", "block")


def _line_advance_roles(blocks: list, *, default_role: str) -> set[str]:
    """Collect every role a layout's body resolves through line_advance().

    Recurses into panel/split children (the same reach `_validate_blocks`/
    `_validate_children` give every other structural check in this module)
    and into each table column's `sub_line` spec -- a role buried inside a
    nested container or a sub_line must not be invisible to this walk, the
    same way `bank_statements.yml`'s `sub_line: {role: sub_description}` on
    a table column would be to a naive top-level-only scan.

    Args:
        blocks: A list of block dicts (a body, or one nesting level of it).
        default_role: The layout's own `defaults.role`, used wherever a
            block (or sub_line) resolving through line_advance omits its
            own `role:` key.

    A block (or sub_line) carrying its own bare-integer `line_advance:`
    override is skipped: `line_advance()`'s block-key-wins-over-layout
    resolution means that block's role is never actually looked up in the
    layout's mapping at render time, so requiring the layout to cover it
    anyway would make this check stricter than the code it is guarding.

    Returns:
        Every role name this subtree resolves through line_advance().
    """
    roles: set[str] = set()
    for block in blocks:
        kind = block.get("type")
        if kind in _LINE_ADVANCE_ROLE_PRIMITIVES:
            if "line_advance" not in block:
                roles.add(str(block.get("role", default_role)))
        elif kind == "table":
            if "line_advance" not in block:
                roles.add(str(block.get("role", default_role)))
            for column in block.get("columns", []):
                sub_line = column.get("sub_line") if isinstance(column, dict) else None
                if isinstance(sub_line, dict) and "line_advance" not in sub_line:
                    roles.add(str(sub_line.get("role", default_role)))
        elif kind in _CONTAINERS:
            children = block.get("children", [])
            if kind == "split":
                for column in children:
                    roles |= _line_advance_roles(column, default_role=default_role)
            else:
                roles |= _line_advance_roles(children, default_role=default_role)
    return roles


def _layout_key_references(blocks: list) -> set[str]:
    """Collect every layout key this subtree names through a block key.

    Recurses into `panel` and `split` children -- the same reach
    `_validate_blocks`/`_validate_children` give every other structural check
    in this module -- so a `from_layout:` buried in a nested container is not
    invisible to it. A `table` block is deliberately not descended into: its
    columns hold no blocks, and a column's `sub_line: {key: ...}` names a key
    of the *row* the provider yields, not a layout key. `_COLUMN_KEYS` admits
    no `from_layout` at any depth below a table, so there is nothing there to
    find.

    Args:
        blocks: A list of block dicts (a body, or one nesting level of it).

    Returns:
        Every layout key named by a `from_layout:` or `suppress_if_equals:`
        in this subtree. Whether each key actually exists on the layout is
        `_validate_geometry`'s job, not this one's.
    """
    names: set[str] = set()
    for block in blocks:
        if not isinstance(block, dict):
            continue
        for key in _LAYOUT_KEY_REFERENCES:
            value = block.get(key)
            if isinstance(value, str):
                names.add(value)
        if block.get("type") in _CONTAINERS:
            children = block.get("children", []) or []
            if block.get("type") == "split":
                for column in children:
                    if isinstance(column, list):
                        names |= _layout_key_references(column)
            elif isinstance(children, list):
                names |= _layout_key_references(children)
    return names


def known_layout_keys(layout: dict) -> frozenset[str]:
    """Return the top-level keys this layout is permitted to carry.

    The permitted set cannot be hand-listed, because `from_layout:` (and
    `suppress_if_equals:`) let a `text` or `banner` block name an *arbitrary*
    layout key as its content -- `bank_statements.yml`'s `logo_text`,
    `receipts.yml`'s `footer_text`. So it is the fixed set of keys the engine,
    the page adapters and the corpus tooling read, widened by whatever this
    layout's own body actually references. A layout adding a new
    `from_layout:` target needs no change here.

    Args:
        layout: The resolved layout dict.

    Returns:
        Every key this layout may carry. Anything else it carries is read by
        nothing, and `validate_layout` rejects it.
    """
    return (
        _ENGINE_LAYOUT_KEYS
        | _PAGE_LAYOUT_KEYS
        | _CORPUS_LAYOUT_KEYS
        | frozenset(_layout_key_references(layout.get("body", []) or []))
    )


def validate_layout(layout: dict, *, layout_id: str, layout_path: str, known_fields: set[str]) -> None:
    """Validate a whole layout: its body tree plus geometry-dependent checks.

    Adds the checks that need the surrounding layout and cannot be made from
    the body alone — column budgets against column geometry, nested container
    widths against their parent, and each `field_providers:` entry against the
    field-provider registry. `known_fields` is widened by the emits of every
    provider this layout actually references before `validate_body` runs, so
    a `{FIELD}` naming a derived value resolves while a typo still fails.

    Args:
        layout: The resolved layout dict, carrying `body`, `content_width`,
            `field_budgets`, and `field_providers` (required — a layout that
            derives no fields must set `field_providers: []` explicitly).
        layout_id: Layout id, used in diagnostics.
        layout_path: Path to the layout YAML, used in diagnostics.
        known_fields: Field names the document type may reference.

    Raises:
        LayoutSchemaError: On any structural, reference, or geometry problem.
    """
    missing = sorted(PARAMETER_DEFAULTS - set(layout.get("defaults", {})))
    if missing:
        raise _err(
            f"layout '{layout_id}' declares no default for: {', '.join(missing)}.",
            layout_path=layout_path,
            key_path=f"{layout_id}.defaults",
            expected="a defaults: mapping covering every parameter a primitive can read: "
            f"{sorted(PARAMETER_DEFAULTS)}.",
            recover=f"add the missing keys under {layout_id}.defaults, sharing a common "
            "block through a YAML anchor as field_budgets already does.",
        )

    # `defaults:` only has to *carry* every key; nothing above checks the
    # values are meaningful. A bad family is worth catching here for the same
    # reason line_advance's shape is: load_font would otherwise raise on the
    # first block that draws, long after startup.
    default_family = layout["defaults"]["family"]
    if default_family not in FONT_FAMILIES:
        raise _err(
            f"layout '{layout_id}' declares an unknown defaults.family ({default_family!r}).",
            layout_path=layout_path,
            key_path=f"{layout_id}.defaults.family",
            expected=f"one of {sorted(FONT_FAMILIES)}, e.g.\n"
            "              defaults:\n"
            "                family: carlito",
            recover=f"set {layout_id}.defaults.family to one of {sorted(FONT_FAMILIES)}, "
            "or vendor the new face in fonts/ and register it in FONT_FAMILIES in "
            "generators/common.py.",
        )

    # line_advance replaces the old int(size * 1.4) ratio, which varied by
    # the drawing block's own role -- a single flat number cannot express
    # that a header-role line and a footer-role line need different
    # advances, so `resolve_param` alone (which only checks the key is
    # present, not its shape) cannot catch a layout that mistypes this as
    # one number. Checked here, at validate time, rather than left for
    # line_advance() to discover per-block at render time.
    line_advance_defaults = layout["defaults"]["line_advance"]
    if not isinstance(line_advance_defaults, dict):
        raise _err(
            f"layout '{layout_id}' declares defaults.line_advance as a single number "
            f"({line_advance_defaults!r}), not a per-role mapping.",
            layout_path=layout_path,
            key_path=f"{layout_id}.defaults.line_advance",
            expected="a mapping of role -> pixels, one entry per font_sizes role this "
            "layout's body draws (each int(font_sizes.<role> * 1.4)), e.g.\n"
            "              defaults:\n"
            "                line_advance:\n"
            "                  header: 61\n"
            "                  body: 44\n"
            "                  footer: 25\n"
            "            A single number cannot express that a header-role line and a "
            "footer-role line need different advances.",
            recover=f"replace {layout_id}.defaults.line_advance with a role: pixels "
            "mapping covering every role this layout's body uses.",
        )

    for key, example in (
        ("body", "a list of block mappings"),
        ("content_width", "1600"),
        (
            "field_providers",
            "a list of {name, params} mappings, or field_providers: [] if this layout derives no fields",
        ),
    ):
        if key not in layout:
            raise _err(
                f"layout '{layout_id}' has no '{key}' key.",
                layout_path=layout_path,
                key_path=f"{layout_id}.{key}",
                expected=f"{key}: {example}.",
                recover=f"add a '{key}:' key to {layout_id}, or do not pass this layout "
                f"to validate_layout.",
            )

    # A layout key nothing reads is worse than no key at all: it tells an
    # operator the document is configured a way it is not. The invoice YAML
    # carried `minimum_amount: 10000` on tax_invoice_high_value long after
    # layout assignment stopped consulting it, so the file read as though the
    # corpus routed large invoices there when it round-robins them.
    permitted = known_layout_keys(layout)
    unread = sorted(set(layout) - permitted)
    if unread:
        raise _err(
            f"layout '{layout_id}' carries key(s) {unread} that no code path reads.",
            layout_path=layout_path,
            key_path=layout_id,
            expected=f"only {sorted(permitted)} — the engine's and the page adapters' own "
            "keys, plus every layout key this body names through a 'from_layout:' or "
            "'suppress_if_equals:'.",
            recover=f"remove {unread} from {layout_id}; or, if something genuinely reads "
            "one now, add it to _ENGINE_LAYOUT_KEYS / _PAGE_LAYOUT_KEYS / "
            "_CORPUS_LAYOUT_KEYS in generators/layout_dsl/schema.py, naming the reader.",
        )

    provider_emits = _validate_field_providers(layout, layout_id=layout_id, layout_path=layout_path)
    validate_body(
        layout["body"],
        layout_id=layout_id,
        layout_path=layout_path,
        known_fields=known_fields | set(provider_emits),
    )

    # Converts a render-time DefaultsError on one unlucky document (the
    # first entry whose body happens to reach an uncovered role) into a
    # startup failure naming the layout and the role, for every layout, not
    # just the ones whose sampled ground truth exercises it.
    default_role = str(layout["defaults"]["role"])
    used_roles = _line_advance_roles(layout["body"], default_role=default_role)
    missing_roles = sorted(used_roles - set(line_advance_defaults))
    if missing_roles:
        raise _err(
            f"layout '{layout_id}' body uses role(s) {missing_roles} that "
            "defaults.line_advance does not cover.",
            layout_path=layout_path,
            key_path=f"{layout_id}.defaults.line_advance",
            expected=f"a defaults.line_advance entry for every role in {sorted(used_roles)}, e.g.\n"
            "              defaults:\n"
            "                line_advance:\n"
            + "".join(
                f"                  {role}: <int(font_sizes.{role} * 1.4)>\n" for role in missing_roles
            ),
            recover=f"add {missing_roles} to {layout_id}.defaults.line_advance.",
        )

    content_width = int(layout["content_width"])
    _validate_geometry(
        layout["body"],
        layout=layout,
        layout_path=layout_path,
        width=content_width,
        key_path=f"{layout_id}.body",
    )


def _column_anchor(column: dict, width: int) -> int:
    """Resolve a column's anchor as an offset from the region's left edge."""
    return int(column["x"]) if "x" in column else width + int(column["x_right"])


def _validate_geometry(blocks: list, *, layout: dict, layout_path: str, width: int, key_path: str) -> None:
    """Recursively check budgets, container widths, and layout-key references."""
    for index, block in enumerate(blocks):
        here = f"{key_path}[{index}]"
        kind = block["type"]

        # `from_layout` and `suppress_if_equals` (text/banner) each hold a
        # layout key name directly (not a `{FIELD}` template) — check the
        # key actually exists here, where the layout dict is in scope, since
        # `_validate_references` only knows entry fields. Each is validated
        # against its own value at its own key_path — do not conflate the
        # two: `from_layout`'s value never lives in `content`.
        if "from_layout" in block:
            key = block["from_layout"]
            if key not in layout:
                raise _err(
                    f"'from_layout' names layout key '{key}', which this layout does not define.",
                    layout_path=layout_path,
                    key_path=f"{here}.from_layout",
                    expected=f"a key present in the layout, e.g. {key}: <value>.",
                    recover=f"add '{key}:' to the layout, or fix the key name.",
                )
        suppress_key = block.get("suppress_if_equals")
        if suppress_key is not None and suppress_key not in layout:
            raise _err(
                f"'suppress_if_equals' names layout key '{suppress_key}', which this layout "
                "does not define.",
                layout_path=layout_path,
                key_path=f"{here}.suppress_if_equals",
                expected=f"a key present in the layout, e.g. {suppress_key}: <value>.",
                recover=f"add '{suppress_key}:' to the layout, or fix the key name.",
            )

        if kind == "table":
            _validate_column_budgets(
                block, layout=layout, layout_path=layout_path, width=width, key_path=here
            )
        elif kind in ("text", "pair"):
            _validate_text_budget(block, layout=layout, layout_path=layout_path, width=width, key_path=here)
        elif kind == "panel":
            # Resolved the way draw_panel resolves it (primitives_container.py):
            # the block's own `padding:` if it has one, else the layout's
            # `defaults.panel_padding`. Reading `block.get("padding", 0)`
            # instead would check a layout declaring `panel_padding: 40`
            # against a padding of 0 and pass a budget the renderer cannot
            # honour. Same reasoning as `_validate_text_budget` below, which
            # already reads `defaults.pair_value_align` this way.
            padding = int(block.get("padding", layout["defaults"]["panel_padding"]))
            inner = width - 2 * padding
            if inner < 1:
                raise _err(
                    f"panel padding {padding} leaves width {inner} inside a {width}px region.",
                    layout_path=layout_path,
                    key_path=f"{here}.padding",
                    expected=f"padding below {width // 2}, e.g. padding: 10.",
                    recover="reduce the panel's padding, or widen content_width.",
                )
            declared = block.get("height")
            if declared is not None and int(declared) < 2 * padding:
                raise _err(
                    f"panel declares height {int(declared)} but its padding alone needs {2 * padding}px.",
                    layout_path=layout_path,
                    key_path=f"{here}.height",
                    expected=f"height >= {2 * padding}, or padding <= {int(declared) // 2}.",
                    recover="raise the panel's height, or reduce its padding.",
                )
            _validate_geometry(
                block["children"],
                layout=layout,
                layout_path=layout_path,
                width=inner,
                key_path=f"{here}.children",
            )
        elif kind == "split":
            columns = block["children"]
            # As with panel padding above: draw_split resolves `gap:` against
            # `defaults.split_gap`, so validation must too.
            gap = int(block.get("gap", layout["defaults"]["split_gap"]))
            widths = block.get("widths")
            if widths is not None:
                # Explicit per-column widths (invoice's fixed 400px totals
                # column) replace equal division entirely -- nested budget
                # checks below must see each column's real width, not a
                # fraction of the region. _validate_children (called earlier
                # by _validate_block, from the same top-down walk) has
                # already checked widths is a list of len(columns) positive
                # ints, so this only re-checks the geometry fit.
                column_widths = [int(w) for w in widths]
                total = sum(column_widths) + gap * (len(column_widths) - 1)
                if total > width:
                    raise _err(
                        f"split widths {column_widths} with gap {gap} need {total}px but "
                        f"this region is only {width}px.",
                        layout_path=layout_path,
                        key_path=f"{here}.widths",
                        expected=f"sum(widths) + gap * (n - 1) <= {width}.",
                        recover="reduce a width in split.widths, or reduce the split's gap.",
                    )
            else:
                inner = (width - gap * (len(columns) - 1)) // len(columns)
                if inner < 1:
                    raise _err(
                        f"split of {len(columns)} columns with gap {gap} leaves column "
                        f"width {inner} inside a {width}px region.",
                        layout_path=layout_path,
                        key_path=f"{here}.gap",
                        expected=f"gap below {width // max(len(columns) - 1, 1)}, e.g. gap: 30.",
                        recover="reduce the gap or the column count.",
                    )
                column_widths = [inner] * len(columns)
            for column_index, child_blocks in enumerate(columns):
                _validate_geometry(
                    child_blocks,
                    layout=layout,
                    layout_path=layout_path,
                    width=column_widths[column_index],
                    key_path=f"{here}.children[{column_index}]",
                )


def _validate_text_budget(
    block: dict, *, layout: dict, layout_path: str, width: int, key_path: str
) -> None:
    """Check a `text`/`pair` block's fit budget, if any, exists and fits its region.

    The simpler counterpart to `_validate_column_budgets`: a text/pair block
    has no column anchors to reason about (it is not one of several columns
    sharing a row), so the only geometry question is whether the declared
    budget width fits inside the block's own region -- `width`, the same
    value `_validate_geometry` already threads through panel/split narrowing
    for every other check at this nesting level.

    A right-aligned `pair` (`value_align: right`) additionally may not carry a
    non-zero `min_gap`, and that is rejected here rather than checked. The
    three keys have no combined meaning: `draw_pair`'s budgeted path draws the
    label first and fits the value into whatever region is left, so it never
    performs the render-time label repositioning `min_gap` exists to request
    (see `draw_pair`'s own docstring, which says so). A layout declaring all
    three would get a gap it asked for and did not receive, on a page where
    nothing shows that it was dropped -- the same class of defect as a layout
    key no code path reads. This was recorded as a constraint while the
    primitive was being built and honoured by every layout since; the check is
    that constraint made enforceable, so the next author to reach for the
    combination is told at validate time instead of discovering it in pixels.
    """
    name = block.get("budget")
    if name is None:
        return
    budgets = layout.get("field_budgets", {})
    if name not in budgets:
        raise _err(
            f"budget '{name}' is not defined by this layout.",
            layout_path=layout_path,
            key_path=f"{key_path}.budget",
            expected=f"a key present in field_budgets: {sorted(budgets)}.",
            recover=f"add '{name}: {{width, fit, min_font, max_lines}}' to field_budgets.",
        )
    declared = int(budgets[name]["width"])

    if block.get("type") == "pair":
        value_align = block.get("value_align", layout["defaults"]["pair_value_align"])
        if value_align == "right":
            min_gap = int(block.get("min_gap", layout["defaults"]["pair_min_gap"]))
            if min_gap > 0:
                raise _err(
                    f"pair combines budget '{name}' with value_align: right and min_gap "
                    f"{min_gap}; the budgeted path does not apply min_gap, so the gap "
                    "would be silently dropped.",
                    layout_path=layout_path,
                    key_path=f"{key_path}.min_gap",
                    expected="a budgeted right-aligned pair to resolve min_gap to 0, e.g.\n"
                    "              {type: pair, label: 'Total', value: '{TOTAL_AMOUNT}',\n"
                    "               value_align: right, budget: TOTALS, min_gap: 0}",
                    recover="set min_gap: 0 on this block, or drop its budget: and let "
                    "min_gap reposition the label, or left-align the value.",
                )

    if declared > width:
        raise _err(
            f"budget '{name}' declares width {declared}px but this block's region is only {width}px.",
            layout_path=layout_path,
            key_path=f"{key_path}.budget",
            expected=f"field_budgets.{name}.width <= {width}.",
            recover=f"set field_budgets.{name}.width to {width} or less, or widen the region "
            "this block draws into.",
        )


def _validate_column_budgets(
    block: dict, *, layout: dict, layout_path: str, width: int, key_path: str
) -> None:
    """Check each budgeted column's declared width fits its column geometry.

    The budget is validated, never derived: a mismatch is an authoring error the
    operator must fix in YAML, so the intended width stays visible in the file.
    """
    budgets = layout.get("field_budgets", {})
    columns = block["columns"]
    anchors = sorted(_column_anchor(column, width) for column in columns)

    for index, column in enumerate(columns):
        name = column.get("budget")
        if name is None:
            continue
        if name not in budgets:
            raise _err(
                f"column {index} names budget '{name}', which the layout does not define.",
                layout_path=layout_path,
                key_path=f"{key_path}.columns[{index}].budget",
                expected=f"a key present in field_budgets: {sorted(budgets)}.",
                recover=f"add '{name}: {{width, fit, min_font, max_lines}}' to field_budgets.",
            )

        anchor = _column_anchor(column, width)
        if column.get("align") == "right":
            # Right-aligned text extends LEFT from its anchor (the anchor is
            # its right edge), so the room available is measured back to the
            # previous anchor -- or the region's own left edge (0) if this is
            # the leftmost column -- the mirror image of the left-aligned
            # case below, where text grows right from the anchor instead.
            preceding = [value for value in anchors if value < anchor]
            available = anchor - (max(preceding) if preceding else 0)
            direction = "before the previous column"
        else:
            following = [value for value in anchors if value > anchor]
            available = (min(following) if following else width) - anchor
            direction = "before the next column"
        declared = int(budgets[name]["width"])
        if declared > available:
            raise _err(
                f"column {index} budget '{name}' declares width {declared}px but only "
                f"{available}px is available {direction}.",
                layout_path=layout_path,
                key_path=f"{key_path}.columns[{index}]",
                expected=f"field_budgets.{name}.width <= {available}.",
                recover=f"set field_budgets.{name}.width to {available} or less, or move "
                f"the {'previous' if column.get('align') == 'right' else 'following'} column.",
            )
