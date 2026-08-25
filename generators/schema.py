"""Ground truth schema validation.

Validates YAML entries against field_definitions.yml: required fields per
document type, date formats, ABN checksums, pipe-delimited list consistency,
and GST arithmetic. Every rule is declared in that YAML rather than here, so
the schema is readable without reading Python, and a missing rule fails loudly
instead of silently checking nothing.
"""

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

import yaml

from generators.common import validate_abn

_DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
_DATE_RANGE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}\s*-\s*\d{2}/\d{2}/\d{4}$")
_AMOUNT_RE = re.compile(r"^\d+(\.\d{1,2})?$")

_FIELD_DEFS: dict | None = None
_FIELD_DEFS_PATH = Path("config/field_definitions.yml")


class SchemaError(Exception):
    """Raised when ground truth fails schema validation."""


def _load_field_defs() -> dict:
    """Load field definitions from config/field_definitions.yml."""
    global _FIELD_DEFS  # noqa: PLW0603
    if _FIELD_DEFS is not None:
        return _FIELD_DEFS
    path = _FIELD_DEFS_PATH
    if not path.exists():
        msg = (
            f"Field definitions not found at {path.resolve()}. "
            f"Expected YAML file with 'document_fields' mapping."
        )
        raise SchemaError(msg)
    _FIELD_DEFS = yaml.safe_load(path.read_text())
    return _FIELD_DEFS


def _doc_type_key(doc_type: str) -> str:
    """Map DOCUMENT_TYPE value to field_definitions key."""
    return doc_type.lower()


def field_names_for(doc_type: str) -> set[str]:
    """Return the field names a document type's ground truth may carry.

    Used by `pipeline validate` to build the `known_fields` set that DSL
    layout-body validation checks `{FIELD}` references against, so an
    unknown field reference fails at startup rather than part-way through
    a generate run.

    Args:
        doc_type: A generation_config.yml `document_types` key, e.g.
            'bank_statements' — the config's own plural convention.
            field_definitions.yml keys are singular, so a trailing 's' is
            stripped before lookup.

    Returns:
        The field names listed under this type's `document_fields` entry.

    Raises:
        SchemaError: If field_definitions.yml has no entry for this type.
    """
    defs = _load_field_defs()
    key = doc_type[:-1] if doc_type.endswith("s") else doc_type
    fields = defs["document_fields"].get(key)
    if fields is None:
        msg = (
            f"No field definitions for document type '{doc_type}' (looked up as "
            f"'{key}' in config/field_definitions.yml). "
            f"Expected a 'document_fields.{key}:' list of field names. "
            f"Add one to config/field_definitions.yml, or fix the document_types "
            f"key in config/generation_config.yml."
        )
        raise SchemaError(msg)
    return set(fields)


def _valid_doc_types() -> set[str]:
    """DOCUMENT_TYPE values allowed, from field_definitions.yml's document_type_values.

    Raises:
        SchemaError: If field_definitions.yml has no 'document_type_values' key.
    """
    defs = _load_field_defs()
    if "document_type_values" not in defs:
        msg = (
            f"  What:     'document_type_values' key not found in {_FIELD_DEFS_PATH}.\n"
            f"  Where:    {_FIELD_DEFS_PATH.resolve()} -> 'document_type_values'.\n"
            "  Expected: a top-level list of allowed DOCUMENT_TYPE values, e.g.\n"
            "            document_type_values:\n"
            "              - BANK_STATEMENT\n"
            "              - RECEIPT\n"
            f"  Recover:  add a 'document_type_values:' list to {_FIELD_DEFS_PATH}."
        )
        raise SchemaError(msg)
    return set(defs["document_type_values"])


def _field_type_group(group: str) -> set[str]:
    """Field names in a config/field_definitions.yml `field_types.<group>` list.

    Args:
        group: A key under `field_types:`, e.g. 'date', 'abn', 'amount'.

    Returns:
        The field names in that group, or an empty set if the group is absent
        (this is deliberate: not every field type is used by every document
        type, so an absent group is not a schema error).

    Raises:
        SchemaError: If field_definitions.yml has no 'field_types' key at all.
    """
    defs = _load_field_defs()
    if "field_types" not in defs:
        msg = (
            f"  What:     'field_types' key not found in {_FIELD_DEFS_PATH}.\n"
            f"  Where:    {_FIELD_DEFS_PATH.resolve()} -> 'field_types'.\n"
            "  Expected: a top-level mapping of field-type groups to field-name lists, e.g.\n"
            "            field_types:\n"
            "              date:\n"
            "                - STATEMENT_DATE\n"
            "              abn:\n"
            "                - SUPPLIER_ABN\n"
            f"  Recover:  add a 'field_types:' mapping to {_FIELD_DEFS_PATH}."
        )
        raise SchemaError(msg)
    return set(defs["field_types"].get(group, []))


def _required_section(key: str, example: str) -> dict:
    """Read a required top-level section of field_definitions.yml, or fail loudly.

    Args:
        key: The top-level key to read.
        example: A concrete YAML snippet shown in the error.

    Returns:
        The section's mapping.

    Raises:
        SchemaError: The key is absent or is not a mapping.
    """
    defs = _load_field_defs()
    section = defs.get(key)
    if not isinstance(section, dict):
        msg = (
            f"  What:     config/field_definitions.yml has no '{key}:' mapping, so "
            f"validate cannot check that invariant.\n"
            f"  Where:    {_FIELD_DEFS_PATH.resolve()}, top-level key '{key}'.\n"
            f"  Expected:\n{example}\n"
            f"  Recover:  add the block above to {_FIELD_DEFS_PATH}."
        )
        raise SchemaError(msg)
    return section


def _parallel_groups(doc_type: str) -> list[list[str]]:
    """Field groups that must carry equal item counts, for one document type."""
    example = (
        "            parallel_field_groups:\n"
        "              RECEIPT:\n"
        "                - [LINE_ITEM_DESCRIPTIONS, LINE_ITEM_PRICES]"
    )
    return _required_section("parallel_field_groups", example).get(doc_type, [])


def _gst_rule() -> dict:
    """The GST consistency rule: divisor, rounding, and the fields it relates."""
    example = (
        "            gst_consistency:\n"
        "              divisor: 11\n"
        "              decimals: 2\n"
        "              inclusive_field: IS_GST_INCLUDED\n"
        "              gst_field: GST_AMOUNT\n"
        "              total_field: TOTAL_AMOUNT"
    )
    rule = _required_section("gst_consistency", example)
    missing = [
        k for k in ("divisor", "decimals", "inclusive_field", "gst_field", "total_field") if k not in rule
    ]
    if missing:
        msg = (
            f"  What:     config/field_definitions.yml 'gst_consistency:' is missing "
            f"{', '.join(missing)}.\n"
            f"  Where:    {_FIELD_DEFS_PATH.resolve()}, under 'gst_consistency:'.\n"
            f"  Expected:\n{example}\n"
            f"  Recover:  add the missing key(s) to that block."
        )
        raise SchemaError(msg)
    return rule


def _check_gst(case_id: str, fields: dict) -> list[str]:
    """Check GST_AMOUNT against TOTAL_AMOUNT when the total is GST-inclusive.

    A wrong GST amount renders happily and exports happily — nothing downstream
    recomputes it — so an entry hand-edited to a plausible-looking figure would
    otherwise reach a model as ground truth.

    Args:
        case_id: The CASE ID key, for the message.
        fields: The entry's `fields` mapping.

    Returns:
        A list holding one error, or empty when the rule does not apply or holds.
    """
    rule = _gst_rule()
    if str(fields.get(rule["inclusive_field"], "")).strip().lower() != "true":
        return []
    if rule["gst_field"] not in fields or rule["total_field"] not in fields:
        return []

    try:
        total = Decimal(str(fields[rule["total_field"]]))
        stated = Decimal(str(fields[rule["gst_field"]]))
    except InvalidOperation:
        return []  # the amount-format check above already reports this

    places = Decimal(1).scaleb(-int(rule["decimals"]))
    expected = (total / Decimal(str(rule["divisor"]))).quantize(places, rounding=ROUND_HALF_UP)
    if stated == expected:
        return []
    return [
        f"{case_id}: {rule['gst_field']} is {stated} but {rule['total_field']} is {total} "
        f"and {rule['inclusive_field']} is true, so GST must be "
        f"{total} / {rule['divisor']} = {expected}. "
        f"Either correct {rule['gst_field']}, or set {rule['inclusive_field']} to false "
        f"if the total genuinely excludes GST."
    ]


def layout_field_names_for(layout_id: str) -> list[str]:
    """Field names required only by one layout.

    A layout that draws a block no other layout has still needs its data
    authored rather than invented in the layout file. `layout_fields` in
    field_definitions.yml declares those names; a layout with no entry has none.

    Args:
        layout_id: The layout id, e.g. 'westpac_premium'.

    Returns:
        The extra field names that layout requires, or an empty list.
    """
    defs = _load_field_defs()
    return list((defs.get("layout_fields") or {}).get(layout_id, []))


def _check_rewards(case_id: str, fields: dict) -> list[str]:
    """Check the rewards balance is derived, not asserted.

    Closing is opening + earned + bonus - redeemed. Enforced for the same
    reason GST is checked as one eleventh: a figure that appears in the
    transcript is scored, so it must be right, and "right" here is arithmetic
    rather than taste.

    Args:
        case_id: The CASE ID, for diagnostics.
        fields: The entry's fields.

    Returns:
        One error message if the arithmetic fails, else an empty list.
    """
    rule = _required_section("rewards_consistency", "  opening: REWARDS_OPENING_BALANCE")
    names = [rule[k] for k in ("opening", "earned", "bonus", "redeemed", "closing")]
    if any(name not in fields for name in names):
        return []

    try:
        opening, earned, bonus, redeemed, closing = (
            int(str(fields[name]).replace(",", "")) for name in names
        )
    except ValueError:
        return [
            f"{case_id}: rewards figures must be whole numbers, optionally comma-grouped; "
            f"got {[fields[name] for name in names]}."
        ]

    expected = opening + earned + bonus - redeemed
    if expected != closing:
        return [
            f"{case_id}: REWARDS_CLOSING_BALANCE is {fields[names[4]]!r}, but "
            f"opening + earned + bonus - redeemed = {expected:,}. "
            f"Fix the closing balance, or the figures it is derived from."
        ]
    return []


def validate_entry(case_id: str, entry: dict) -> list[str]:
    """Validate a single ground truth YAML entry.

    Args:
        case_id: The CASE ID key (e.g. "CASE001").
        entry: The YAML dict with 'layout' and 'fields'.

    Returns:
        List of error messages. Empty list means valid.
    """
    errors: list[str] = []

    if "layout" not in entry:
        errors.append(f"{case_id}: missing 'layout' key. Add 'layout: <layout_id>' to the entry.")
    if "fields" not in entry:
        errors.append(f"{case_id}: missing 'fields' key.")
        return errors

    fields = entry["fields"]

    doc_type = fields.get("DOCUMENT_TYPE")
    try:
        valid_doc_types = _valid_doc_types()
    except SchemaError as exc:
        errors.append(str(exc))
        return errors
    if doc_type not in valid_doc_types:
        errors.append(
            f"{case_id}: DOCUMENT_TYPE is '{doc_type}', expected one of {sorted(valid_doc_types)}."
        )
        return errors

    try:
        defs = _load_field_defs()
        required = defs["document_fields"].get(_doc_type_key(doc_type), [])
    except SchemaError as exc:
        errors.append(str(exc))
        return errors

    # A layout-specific block's data is required of the layouts that draw it and
    # of no others, so the requirement is looked up per entry rather than per type.
    layout_required = layout_field_names_for(str(entry.get("layout", "")))

    for field_name in [*required, *layout_required]:
        if field_name == "DOCUMENT_TYPE":
            continue
        if field_name not in fields:
            errors.append(
                f"{case_id}: missing required field '{field_name}' for {doc_type}. "
                f"Add '{field_name}: <value>' under 'fields:'."
            )

    errors.extend(_check_rewards(case_id, fields))

    try:
        date_fields = _field_type_group("date")
        date_range_fields = _field_type_group("date_range")
        abn_fields = _field_type_group("abn")
        amount_fields = _field_type_group("amount")
    except SchemaError as exc:
        errors.append(str(exc))
        return errors

    for field_name in date_fields:
        if field_name in fields:
            val = str(fields[field_name])
            if not _DATE_RE.match(val):
                errors.append(
                    f"{case_id}: field '{field_name}' has value '{val}', "
                    f"expected DD/MM/YYYY format (e.g. '15/03/2024')."
                )

    for field_name in date_range_fields:
        if field_name in fields:
            val = str(fields[field_name])
            if not _DATE_RANGE_RE.match(val):
                errors.append(
                    f"{case_id}: field '{field_name}' has value '{val}', "
                    f"expected 'DD/MM/YYYY - DD/MM/YYYY' format."
                )

    for field_name in abn_fields:
        if field_name in fields:
            val = str(fields[field_name])
            if not validate_abn(val):
                errors.append(
                    f"{case_id}: field '{field_name}' has value '{val}' "
                    f"which fails ABN checksum validation. "
                    f"Use generators.common.generate_abn() to create valid ABNs."
                )

    for field_name in amount_fields:
        if field_name in fields:
            val = str(fields[field_name])
            if not _AMOUNT_RE.match(val):
                errors.append(
                    f"{case_id}: field '{field_name}' has value '{val}', "
                    f"expected decimal without $ (e.g. '67.32')."
                )

    for group in _parallel_groups(doc_type):
        counts: dict[str, int] = {}
        for field_name in group:
            if field_name in fields:
                val = str(fields[field_name])
                counts[field_name] = len(val.split("|"))
        if len(set(counts.values())) > 1:
            detail = ", ".join(f"{k}={v}" for k, v in counts.items())
            errors.append(
                f"{case_id}: pipe-delimited field count mismatch: {detail}. "
                f"All fields in group {group} must have the same number of items."
            )

    errors.extend(_check_gst(case_id, fields))

    return errors


def validate_ground_truth_file(path: Path) -> list[str]:
    """Validate all entries in a ground truth YAML file.

    Args:
        path: Path to the YAML file.

    Returns:
        List of all error messages across all entries.
    """
    if not path.exists():
        return [f"Ground truth file not found: {path.resolve()}"]

    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        return [f"{path}: expected top-level YAML mapping, got {type(data).__name__}"]

    all_errors: list[str] = []
    for case_id, entry in data.items():
        all_errors.extend(validate_entry(str(case_id), entry))
    return all_errors
