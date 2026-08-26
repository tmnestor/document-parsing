"""Events plus policy to Markdown.

A pure function of the event stream and `config/serialisation.yml` — it imports
no PIL and renders nothing. That split is deliberate (design §6): the convention
is the risky, iterate-on-it part of this design, so it can change and every
transcript re-emit in seconds without re-rendering an image.

It takes plain dicts rather than `Event` objects so it can run straight off a
stored `events.jsonl` without importing the recorder.

**It never normalises.** No whitespace collapsing, no case folding, no Unicode
folding, no dash or quote substitution. Design §5 puts all of that in the
scoring tool so that scoring policy can change without regenerating the corpus;
baking any of it in here would freeze a policy into the data.
"""

from pathlib import Path

import yaml

from generators.tables import TableBuilder

REQUIRED_POLICY_KEYS: tuple[str, ...] = (
    "title_style",
    "pair_separator",
    "pair_strip_trailing_colon",
    "table_style",
    "empty_cell_token",
    "cell_sub_line_join",
    "cell_newline_join",
    "split_order",
    "headerless_table",
    "carry_group_key",
    "block_separator",
    "emphasis",
    "decoration_glyphs",
    "decoration_min_run",
)

# The floor on `decoration_min_run`. At one, a run is a single glyph and every
# decimal point, hyphen and underscore in the corpus is decoration — which
# would silently rewrite most amounts. Two is the lowest value that still
# means "repeated".
MIN_DECORATION_RUN = 2

# Enum-valued keys, each mapped to every value this serialiser implements. A
# value outside these is a configuration error, not a silently-ignored setting.
_ALLOWED: dict[str, tuple[str, ...]] = {
    "title_style": ("atx_h1",),
    "table_style": ("html",),
    "split_order": ("column_major",),
    "carry_group_key": ("down", "none"),
    "headerless_table": ("empty_header_row",),
    "emphasis": ("none",),
}

_EXAMPLES: dict[str, str] = {
    "title_style": "atx_h1",
    "pair_separator": '": "',
    "pair_strip_trailing_colon": "true",
    "table_style": "html",
    "empty_cell_token": '""',
    "cell_sub_line_join": '" "',
    "cell_newline_join": '" "',
    "split_order": "column_major",
    "headerless_table": "empty_header_row",
    "carry_group_key": "down",
    "block_separator": '"\\n\\n"',
    "emphasis": "none",
    "decoration_glyphs": '".-_=*"',
    "decoration_min_run": "4",
}

# Structural markers: they shape the stream but put nothing in the transcript.
_STRUCTURE = frozenset(
    {"panel_open", "panel_close", "split_open", "split_close", "column_open", "column_close"}
)


class SerialisationError(RuntimeError):
    """Raised when the serialisation policy or the event stream is unusable."""


def _err(what: str, *, path: Path | str, key: str, expected: str, recover: str) -> SerialisationError:
    """Build a four-element fail-fast diagnostic."""
    return SerialisationError(
        "Invalid serialisation policy.\n"
        f"  What:     {what}\n"
        f"  Where:    {path} -> {key}\n"
        f"  Expected: {expected}\n"
        f"  Recover:  {recover}"
    )


def load_serialisation_policy(path: Path) -> dict:
    """Load and validate the serialisation convention.

    Every key is required. Omitting one is an error, never a silent default,
    including the keys whose value is a no-op (design §4.4) — so that reading
    the file alone answers what a transcript looks like.

    Args:
        path: Path to `serialisation.yml`.

    Returns:
        The validated policy mapping.

    Raises:
        SerialisationError: The file is missing, unparseable, missing a required
            key, or gives an enum key a value this serialiser does not implement.
    """
    resolved = path.resolve()
    if not path.exists():
        raise _err(
            f"{path} does not exist.",
            path=resolved,
            key="(whole file)",
            expected="a YAML mapping declaring every key of "
            f"{list(REQUIRED_POLICY_KEYS)}, e.g.\n              title_style: atx_h1",
            recover="create config/serialisation.yml (see the copy shipped with any exported corpus).",
        )

    try:
        policy = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as err:
        raise _err(
            f"the file is not valid YAML: {err}",
            path=resolved,
            key="(whole file)",
            expected="parseable YAML, e.g.\n              emphasis: none",
            recover="fix the syntax error at the line named above.",
        ) from err

    if not isinstance(policy, dict):
        raise _err(
            f"expected a mapping, got {type(policy).__name__}.",
            path=resolved,
            key="(document root)",
            expected="a top-level mapping of policy keys, e.g.\n              split_order: column_major",
            recover="wrap the settings in a top-level mapping.",
        )

    for key in REQUIRED_POLICY_KEYS:
        if key not in policy:
            raise _err(
                f"'{key}' is not declared.",
                path=resolved,
                key=key,
                expected=f"every key of {list(REQUIRED_POLICY_KEYS)} present — none has a "
                f"Python default, including no-op values, e.g.\n              {key}: "
                f"{_EXAMPLES[key]}",
                recover=f"add '{key}:' to {path}.",
            )

    for key, allowed in _ALLOWED.items():
        if policy[key] not in allowed:
            raise _err(
                f"'{key}' is {policy[key]!r}, which this serialiser does not implement.",
                path=resolved,
                key=key,
                expected=f"one of {list(allowed)}, e.g.\n              {key}: {allowed[0]}",
                recover=f"set '{key}:' to a supported value, or implement it in generators/serialise.py.",
            )

    _validate_decoration_rule(policy, path=path, resolved=resolved)

    return policy


def _validate_decoration_rule(policy: dict, *, path: Path, resolved: Path) -> None:
    """Check the decoration rule can be compiled into a run pattern.

    Neither key is an enum — any glyph set and any threshold above the floor is
    a legitimate convention — so they are range-checked rather than matched
    against a list.

    Args:
        policy: The policy mapping, already known to declare every required key.
        path: The path as given, used in the recovery step.
        resolved: The absolute path, used to locate the file.

    Raises:
        SerialisationError: A key has the wrong type, or a threshold below
            `MIN_DECORATION_RUN`.
    """
    glyphs = policy["decoration_glyphs"]
    if not isinstance(glyphs, str) or not glyphs:
        raise _err(
            f"'decoration_glyphs' is {glyphs!r}, which is not a non-empty string.",
            path=resolved,
            key="decoration_glyphs",
            expected="the characters a run may be built from, as one string, e.g.\n"
            '              decoration_glyphs: ".-_=*"',
            recover=f"set 'decoration_glyphs:' in {path} to a non-empty string of glyphs.",
        )

    min_run = policy["decoration_min_run"]
    # `bool` is an `int` in Python. Left to the isinstance check below,
    # `decoration_min_run: true` would read as a threshold of one and quietly
    # turn every decimal point in the corpus into decoration.
    if isinstance(min_run, bool) or not isinstance(min_run, int):
        raise _err(
            f"'decoration_min_run' is {min_run!r}, which is not an integer.",
            path=resolved,
            key="decoration_min_run",
            expected="a whole number of consecutive glyphs, e.g.\n              decoration_min_run: 4",
            recover=f"set 'decoration_min_run:' in {path} to an integer of {MIN_DECORATION_RUN} or more.",
        )

    if min_run < MIN_DECORATION_RUN:
        raise _err(
            f"'decoration_min_run' is {min_run}, below the floor of {MIN_DECORATION_RUN}.",
            path=resolved,
            key="decoration_min_run",
            expected=f"an integer of {MIN_DECORATION_RUN} or more — at one a 'run' is a single "
            "glyph, so every decimal point and hyphen on the page becomes decoration, e.g.\n"
            "              decoration_min_run: 4",
            recover=f"raise 'decoration_min_run:' in {path} to {MIN_DECORATION_RUN} or more.",
        )


def pair_text(meta: dict, policy: dict) -> str:
    """Join a `pair` event's label and value under the serialisation policy.

    Shared with `generators.layout`'s `text_block` projection, so a `pair`
    annotation's text and its line in the Markdown transcript agree by
    construction — one implementation of the join convention, not two kept in
    step by hand (design §4: `text` is "exactly as the transcript records
    it").

    Args:
        meta: The event's `meta`, carrying `label` and `value`.
        policy: The validated serialisation policy.

    Returns:
        The joined text, e.g. "Total: $157.39".
    """
    label = str(meta["label"])
    if policy["pair_strip_trailing_colon"]:
        label = label.rstrip().removesuffix(":").rstrip()
    return f"{label}{policy['pair_separator']}{meta['value']}"


def serialise(events: list[dict], policy: dict) -> str:
    """Turn one document's event stream into its Markdown transcript.

    Args:
        events: The document's events, in walk order, as plain dicts.
        policy: The validated policy from `load_serialisation_policy`.

    Returns:
        The transcript. Never normalised — exactly what was drawn, arranged
        under the policy's conventions.

    Raises:
        SerialisationError: An event kind has no serialisation rule. A new kind
            must fail loudly rather than vanish from every transcript.
    """
    blocks: list[str] = []
    builder = TableBuilder(policy)

    for event in events:
        kind = event["kind"]
        text = event["text"]
        meta = event.get("meta", {})

        if kind in TableBuilder.KINDS:
            # One walk, driven by both projections. Placement stays a property
            # of the walk: the table lands where its table_close occurred, and
            # its bytes are the bytes `tables/{stem}.html` ships.
            html = builder.feed(event)
            if html is not None:
                blocks.append(html)
            continue

        if kind in _STRUCTURE:
            # Structure only. Column order is already `column_major` in the
            # stream because `draw_split` walks columns in DSL order, so there
            # is nothing to reorder here — see the policy's `split_order`.
            continue

        if kind == "title":
            blocks.append(f"# {text}")
        elif kind == "line":
            blocks.append(str(text))
        elif kind == "pair":
            blocks.append(pair_text(meta, policy))
        else:
            raise SerialisationError(
                "Unknown event kind.\n"
                f"  What:     no serialisation rule for event kind '{kind}' "
                f"(seq {event.get('seq')}).\n"
                "  Where:    generators/serialise.py -> serialise()\n"
                "  Expected: a branch for every kind a primitive emits, e.g. "
                '`elif kind == "line": blocks.append(text)`.\n'
                "  Recover:  add a rule for this kind, and a matching row to the "
                "event table in the design doc's §4.3."
            )

    # Raises TableHtmlError with the same four-element shape for an unclosed
    # table, so the bespoke SerialisationError this replaced was a second
    # implementation of one diagnostic.
    builder.finish()

    return policy["block_separator"].join(blocks)
