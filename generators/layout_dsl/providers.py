"""Row providers — the DSL's one sanctioned escape hatch.

Some table data is computed rather than stored: a bank statement's running
balance and opening row exist nowhere in ground truth. Rather than put
arithmetic in YAML, a table names a provider registered here, and the provider
returns row dicts. Providers return data only — they never draw or position.
"""

import hashlib
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from generators.common import fmt_amount

RowProvider = Callable[[dict, dict], list[dict]]

_REGISTRY: dict[str, RowProvider] = {}
# Each provider declares the top-level `params:` keys it reads, at
# registration time, alongside the function itself — the provider is the one
# place that knowledge belongs, since only it knows what it accepts. schema.py
# reads this via `provider_param_keys()` to reject an unknown params key (e.g.
# a typo like `opening_balnce`) at validate time rather than have it silently
# do nothing, indistinguishable from a param that was never set.
_PARAM_KEYS: dict[str, frozenset[str]] = {}


class ProviderError(RuntimeError):
    """Raised when a provider is unknown, duplicated, or given bad input."""


def row_provider(
    name: str, *, params: frozenset[str] = frozenset()
) -> Callable[[RowProvider], RowProvider]:
    """Register a row provider under `name`.

    Args:
        name: The name layouts use in a table's `rows:` key.
        params: The top-level `params:` keys this provider reads. Combinations
            the provider itself further restricts (e.g. two keys being
            mutually exclusive) stay a render-time `ProviderError` raised by
            the provider — this only declares which key *names* are valid,
            for schema.py to catch a typo before any provider runs.

    Returns:
        A decorator that registers and returns the function unchanged.

    Raises:
        ProviderError: If `name` is already registered.
    """

    def decorate(func: RowProvider) -> RowProvider:
        if name in _REGISTRY:
            msg = (
                f"Row provider '{name}' is already registered.\n"
                f"  Remediation: pick a distinct provider name."
            )
            raise ProviderError(msg)
        _REGISTRY[name] = func
        _PARAM_KEYS[name] = params
        return func

    return decorate


def get_provider(name: str) -> RowProvider:
    """Look up a registered row provider.

    Args:
        name: Provider name from a table's `rows:` key.

    Returns:
        The registered provider.

    Raises:
        ProviderError: If no provider is registered under `name`.
    """
    if name not in _REGISTRY:
        msg = (
            f"Unknown row provider.\n"
            f"  What:     no provider named '{name}' is registered.\n"
            f"  Where:    a table block's 'rows:' key.\n"
            f"  Expected: one of {sorted(_REGISTRY)}.\n"
            f"  Remediation: set rows: to a registered provider, or register a new "
            f"one with @row_provider in generators/layout_dsl/providers.py."
        )
        raise ProviderError(msg)
    return _REGISTRY[name]


def provider_names() -> list[str]:
    """Return the names of all registered providers, sorted."""
    return sorted(_REGISTRY)


def provider_param_keys(name: str) -> frozenset[str]:
    """Return the top-level `params:` keys a registered provider accepts.

    Args:
        name: A registered provider name (validate the name itself with
            `provider_names()`/`get_provider()` first; this returns an empty
            set for an unknown name rather than raising, since schema.py
            already reports the unknown-provider case with its own
            diagnostic before ever reaching a params check).

    Returns:
        The frozenset passed to this provider's `@row_provider(..., params=...)`.
    """
    return _PARAM_KEYS.get(name, frozenset())


@row_provider("pipe_fields", params=frozenset({"fields", "decimal_keys"}))
def pipe_fields(entry: dict, params: dict) -> list[dict]:
    """Zip pipe-delimited list fields into row dicts.

    Lets a document type build a table from plain list fields with no Python.

    Args:
        entry: The ground-truth entry.
        params: Must carry `fields`, a mapping of row key to source field name.
            May also carry `decimal_keys`, a list of those row keys whose values
            are amounts and should be coerced to `Decimal`, so the table formats
            them as currency (`_cell_text`) rather than drawing the raw
            ground-truth string — the legacy invoice renderer printed
            `fmt_amount(Decimal(price))`, so a one-decimal-place amount must
            render `$9.50`, not `9.5`. Mirrors the coercion `bank_transactions`
            and `receipt_line_items` each do for their own fixed key names; this
            is the same thing for a table with no provider of its own. The
            absent sentinel and the empty string are left alone — `_cell_text`
            maps both to a cell that draws nothing.

    Returns:
        One dict per row, keyed by the `fields` mapping's keys.

    Raises:
        ProviderError: If `fields` is missing, the source lists differ in
            length, `decimal_keys` names a key absent from `fields`, or a
            value under one of those keys is neither a sentinel nor a valid
            amount.
    """
    mapping = params.get("fields")
    if not isinstance(mapping, dict) or not mapping:
        msg = (
            "pipe_fields provider needs a 'fields' mapping.\n"
            "  What:     the fields param is missing or empty in the table's params:\n"
            "  Where:    config/layouts/*.yml, a table block's params.fields key.\n"
            "  Expected: params: {fields: {row_key: SOURCE_FIELD, ...}}\n"
            "  Recover:  add a fields: mapping under the table's params: block."
        )
        raise ProviderError(msg) from None

    entry_fields = entry["fields"]
    columns: dict[str, list[str]] = {}
    for key, source in mapping.items():
        raw = str(entry_fields.get(source, ""))
        columns[key] = [part.strip() for part in raw.split("|")] if raw else []

    lengths = {key: len(values) for key, values in columns.items()}
    if len(set(lengths.values())) > 1:
        msg = (
            f"pipe_fields source lists differ in length: {lengths}.\n"
            f"  What:     pipe-delimited fields in one table have mismatched lengths.\n"
            f"  Where:    ground_truth/*.yml, the affected entry's "
            f"LINE_ITEM_* or TRANSACTION_* fields.\n"
            f"  Expected: all pipe-delimited fields to have the same count; "
            f"e.g. if DESCRIPTIONS has 3 entries, QUANTITIES must also have 3.\n"
            f"  Recover:  edit the entry in ground_truth/ to make all lists equal length."
        )
        raise ProviderError(msg) from None

    count = next(iter(lengths.values()), 0)
    # Annotated `dict`, not the inferred `dict[str, str]`: `decimal_keys` below
    # replaces some of those strings with Decimals in place, exactly as
    # bank_transactions and receipt_line_items already do to this same return
    # value once they have it.
    rows: list[dict] = [{key: columns[key][i] for key in columns} for i in range(count)]

    decimal_keys = params.get("decimal_keys") or []
    unknown = sorted(set(decimal_keys) - set(mapping))
    if unknown:
        msg = (
            f"pipe_fields decimal_keys names row key(s) {unknown} that its fields mapping "
            "does not define.\n"
            f"  What:     decimal_keys {sorted(decimal_keys)} must all appear in "
            f"fields, which defines {sorted(mapping)}.\n"
            "  Where:    config/layouts/*.yml, a table block's params.decimal_keys key.\n"
            "  Expected: params: {fields: {price: LINE_ITEM_PRICES}, decimal_keys: [price]}\n"
            f"  Recover:  fix the typo in decimal_keys, or add {unknown} to the table's "
            "params.fields mapping."
        )
        raise ProviderError(msg) from None

    for key in decimal_keys:
        for row in rows:
            if row[key] not in ("", "NOT_FOUND"):
                row[key] = _to_decimal(
                    row[key],
                    what=f"the {mapping[key]} list",
                    source="the entry's ground_truth/*.yml file",
                )
    return rows


# The two mutually-exclusive leading synthetic rows, by their boolean param
# name. Only the names live here: each row's printed label is a
# `<key>_label` param the layout must supply, so no description text this
# provider returns is decided by a Python literal.
_LEADING_SYNTHETIC_KEYS = ("opening_balance", "brought_forward")


def _require_param(params: dict, key: str, *, because: str) -> Any:
    """Read a params key the caller has made mandatory, or fail with a diagnostic.

    Args:
        params: The table block's resolved `params:` mapping.
        key: The params key that must be present.
        because: The condition that made it mandatory, phrased as a clause —
            e.g. "carried_forward is set" — so the message says which other
            key obliged this one.

    Returns:
        The value under `key`. Typed `Any` for the same reason
        `defaults.resolve_param` is: these are heterogeneous YAML values (a
        label is a str, a pad width an int) that each caller immediately casts
        to the type it needs, and `object` would only force an extra
        intermediate local at every call site without buying any safety.

    Raises:
        ProviderError: If `key` is absent from `params`.
    """
    if key not in params:
        msg = (
            f"bank_transactions provider requires a '{key}' param when {because}.\n"
            f"  What:     {because}, but the {key} param is missing from the table's params:\n"
            f"  Where:    config/layouts/bank_statements.yml, the transactions "
            f"table's params: key.\n"
            f"  Expected: params: {{{key}: <the text or number to print>}}, e.g.\n"
            f"            params: {{brought_forward: true, "
            f'brought_forward_label: "Balance Brought Forward"}}\n'
            f"  Recover:  add {key}: to that table's params: block, or drop the "
            f"key that requires it."
        )
        raise ProviderError(msg) from None
    return params[key]


@row_provider(
    "bank_transactions",
    params=frozenset(
        {
            "opening_balance",
            "brought_forward",
            "opening_balance_label",
            "brought_forward_label",
            "opening_balance_bold",
            "brought_forward_bold",
            "carried_forward",
            "carried_forward_label",
            "references",
            "reference_prefix",
            "reference_pad_char",
            "reference_pad_width",
            "balance_suffix",
        }
    ),
)
def bank_transactions(entry: dict, params: dict) -> list[dict]:
    """Build bank statement rows with running balances computed backwards.

    Mirrors the legacy `_parse_transactions` / `_compute_running_balances`
    helpers: balances are derived from ACCOUNT_BALANCE (the closing balance) by
    walking the transactions in reverse.

    Args:
        entry: The ground-truth entry.
        params: Optional `opening_balance` or `brought_forward` booleans, which
            prepend a synthetic leading balance row (mutually exclusive with
            each other). Whichever is set **must** be paired with `<key>_label`
            (e.g. `brought_forward_label`), the row's printed description
            text — ANZ's leading row reads "BALANCE BROUGHT FORWARD" (all
            caps) where NAB's reads "Balance Brought Forward", and neither
            spelling is the provider's to choose: both rows share the same
            computed opening-balance value and differ only in what they
            print, so the text is the layout's. Each may also be paired with
            `<key>_bold` (e.g. `brought_forward_bold: ["description"]`), a
            collection of row keys to render bold on that one row — ANZ's
            leading row is the one legacy draws with mixed weight (its label
            bold, its balance value not); absent, the whole row stays
            regular, matching every other leading row. An independent
            optional `carried_forward` boolean appends a trailing synthetic
            closing-balance row, and likewise requires `carried_forward_label`
            for its printed text — it may combine with either leading option,
            matching NAB's legacy renderer, which shows both a "Brought
            forward" row (under the first date-group header) and a "Carried
            forward" row (after every transaction).
            An independent optional `references` boolean adds a `reference`
            key to every real (non-synthetic) row — NAB's dotted-leader
            reference number, computed exactly as the legacy renderer does:
            sha256 of the description, taken mod 10**10, zero-padded to 10
            digits — then prefixed with the required `reference_prefix` and
            suffixed with `reference_pad_width` copies of `reference_pad_char`
            (NAB: `"Ref: "`, 40, `"."`). All three are required whenever
            `references` is set: the leader glyph and its run length are as
            much a printed decision as the prefix, and none of the three is a
            property of the hash this provider computes.
            An independent optional `balance_suffix` dict — `{debit: "DR",
            credit: "CR"}` — replaces every row's Decimal `balance` with an
            already-formatted string carrying the sign-dependent suffix ANZ's
            legacy renderer picks (`_format_balance`): the amount's absolute
            value plus `debit` when negative, or the amount plus `credit`
            otherwise. Applied last, to every row (real and synthetic) that
            still carries a Decimal `balance` — a fixed `currency_suffix` on
            the column, as NAB uses, cannot express a suffix that depends on
            the value itself, so the provider computes the final display
            string instead and the column just draws it verbatim.

    Returns:
        One dict per row with keys `date`, `description`, `debit`, `credit`,
        `balance` (Decimal, or a pre-formatted string when `balance_suffix`
        is set), `synthetic` (bool); `reference` only on real rows, and only
        when `references` is set; `bold` (True) only on the `carried_forward`
        row, which legacy draws in `font_body_bold` unlike its leading-row
        counterparts.

    Raises:
        ProviderError: If both leading synthetic-row options are requested, or
            the transaction lists are ragged.
    """
    wants = [key for key in _LEADING_SYNTHETIC_KEYS if params.get(key)]
    if len(wants) > 1:
        msg = (
            "opening_balance and brought_forward are mutually exclusive; both were set.\n"
            "  Remediation: keep exactly one leading synthetic balance row on the table block."
        )
        raise ProviderError(msg)

    rows = pipe_fields(
        entry,
        {
            "fields": {
                "date": "TRANSACTION_DATES",
                "description": "TRANSACTION_DESCRIPTIONS",
                "debit": "TRANSACTION_AMOUNTS_PAID",
                "credit": "TRANSACTION_AMOUNTS_RECEIVED",
            }
        },
    )

    balance = _to_decimal(entry["fields"].get("ACCOUNT_BALANCE", "0"))
    for row in reversed(rows):
        row["balance"] = balance
        row["synthetic"] = False
        balance = balance + _to_decimal(row["debit"]) - _to_decimal(row["credit"])
        # Coerce real amounts to Decimal so the table formats them as currency, matching
        # the legacy renderer. The absent sentinel is left alone: legacy draws nothing
        # for it, and _cell_text maps it to the empty string.
        for key in ("debit", "credit"):
            if row[key] != "NOT_FOUND":
                row[key] = _to_decimal(row[key])

    if params.get("references"):
        because = "references is set"
        prefix = str(_require_param(params, "reference_prefix", because=because))
        pad_char = str(_require_param(params, "reference_pad_char", because=because))
        pad_width = int(_require_param(params, "reference_pad_width", because=because))
        for row in rows:
            digest = hashlib.sha256(row["description"].encode()).hexdigest()
            ref_num = str(int(digest, 16) % 10**10).zfill(10)
            row["reference"] = f"{prefix}{ref_num}" + pad_char * pad_width

    # The closing balance is exactly the last real row's own balance -- the
    # reversed loop above seeds it straight from ACCOUNT_BALANCE before any
    # adjustment -- captured now, before a leading synthetic row (if any)
    # shifts what rows[0] means, and before a trailing one is appended.
    closing_balance = rows[-1]["balance"] if rows else balance

    if wants and rows:
        key = wants[0]
        first = rows[0]
        opening = first["balance"] - _to_decimal(first["credit"]) + _to_decimal(first["debit"])
        rows.insert(
            0,
            {
                "date": "",
                "description": str(_require_param(params, f"{key}_label", because=f"{key} is set")),
                "debit": "NOT_FOUND",
                "credit": "NOT_FOUND",
                "balance": opening,
                "synthetic": True,
                # ANZ's "BALANCE BROUGHT FORWARD" is the one leading row
                # legacy draws with mixed weight -- its description bold, its
                # balance value not -- so `<key>_bold` names which columns
                # should render bold (see primitives_table._cell_bold);
                # absent, the whole row stays regular, matching every other
                # leading row (CBA's "Opening Balance", NAB's own "Brought
                # forward"), which legacy draws entirely unbolded.
                **({"bold": set(params[f"{key}_bold"])} if params.get(f"{key}_bold") else {}),
            },
        )

    if params.get("carried_forward") and rows:
        rows.append(
            {
                "date": "",
                "description": str(
                    _require_param(params, "carried_forward_label", because="carried_forward is set")
                ),
                "debit": "NOT_FOUND",
                "credit": "NOT_FOUND",
                "balance": closing_balance,
                "synthetic": True,
                # Legacy draws this row in font_body_bold, unlike the leading
                # Opening Balance / Brought Forward rows above, which stay
                # regular weight -- a fact about this specific row, not a
                # layout-level style choice, so it is set here rather than
                # exposed as a YAML key.
                "bold": True,
            }
        )

    suffix = params.get("balance_suffix")
    if suffix:
        for row in rows:
            bal = row.get("balance")
            if not isinstance(bal, Decimal):
                continue
            row["balance"] = (
                f"{fmt_amount(bal)} {suffix['credit']}"
                if bal >= 0
                else f"{fmt_amount(abs(bal))} {suffix['debit']}"
            )

    return rows


@row_provider("bank_transaction_totals", params=frozenset({"label"}))
def bank_transaction_totals(entry: dict, params: dict) -> list[dict]:
    """Build a single trailing row summing every transaction's debits and credits.

    ANZ draws a "Totals at end of period" row below its transaction table,
    after a fresh rule -- not as part of the same row run `bank_transactions`
    produces. Appending it there instead would move `last_row_field`'s "last
    row" off the real closing-balance row and onto this one, which never
    carries a balance in legacy. A second table block, using this provider
    and the same column geometry, keeps the two concerns apart.

    Args:
        entry: The ground-truth entry.
        params: Must carry `label`, the text printed in the row's description column.

    Returns:
        A single-row list: `date` and `balance` empty (legacy draws neither
        for this row), `description` the label, `debit`/`credit` the summed
        Decimal totals, `synthetic: True` (never recorded — this row has no
        ground-truth field of its own), `bold: True` (legacy draws it in
        `font_body_bold`), `rule_above: True` (legacy rules above it after a
        fresh 12px gap, unlike an ordinary continued row).

    Raises:
        ProviderError: If `label` is missing from params.
    """
    if "label" not in params:
        msg = (
            "bank_transaction_totals provider requires a 'label' param.\n"
            "  What:     the label param is missing from the table's params:\n"
            "  Where:    config/layouts/bank_statements.yml, the totals table's params: key.\n"
            '  Expected: params: {label: "Totals at end of period"}\n'
            '  Recover:  add label: "Totals at end of period" to the totals table\'s '
            "params: block."
        )
        raise ProviderError(msg) from None

    rows = pipe_fields(
        entry,
        {"fields": {"debit": "TRANSACTION_AMOUNTS_PAID", "credit": "TRANSACTION_AMOUNTS_RECEIVED"}},
    )
    total_debits = sum((_to_decimal(row["debit"]) for row in rows), Decimal("0"))
    total_credits = sum((_to_decimal(row["credit"]) for row in rows), Decimal("0"))
    return [
        {
            "date": "",
            "description": params["label"],
            "debit": total_debits,
            "credit": total_credits,
            "synthetic": True,
            "bold": True,
            "rule_above": True,
        }
    ]


@row_provider("receipt_line_items", params=frozenset({"fields", "quantity_prefix_format"}))
def receipt_line_items(entry: dict, params: dict) -> list[dict]:
    """Build receipt line-item rows with quantity-prefixed descriptions.

    Zips pipe-delimited list fields into row dicts, then prefixes each
    description with its quantity when the quantity is not 1. Matches the
    legacy receipt renderer's behaviour at lines 265-268.

    Args:
        entry: The ground-truth entry.
        params: Must carry `fields`, a mapping of row key to source field name,
            and `quantity_prefix_format`, a format string (e.g. "{quantity}x ")
            to apply when the quantity is above 1.

    Returns:
        One dict per row, keyed by the `fields` mapping's keys, with the
        description prefixed when quantity != "1", plus:

        - `quantity_prefix`: the prefix actually applied to this row's
          description ("" when none). The prefix is a ground-truth value in
          its own right -- the legacy receipt renderer recorded a separate
          `LINE_ITEM_QUANTITIES[i]` box for it -- and once it has been
          concatenated into `description` there is no way to recover its
          extent, so it is re-exposed here for a column's `prefix_key` to
          name (see `_draw_row` in primitives_table.py).
        - `price`/`total`, when present, coerced to `Decimal` so a column
          declaring `currency:` formats them as amounts. Mirrors
          `bank_transactions` above, which coerces `debit`/`credit` for the
          same reason: the legacy renderer printed `f"{Decimal(total):,.2f}"`,
          not the raw ground-truth string, so a one-decimal-place amount would
          otherwise render as `9.5` rather than `9.50`. The absent sentinel
          and the empty string are left alone -- `_cell_text` maps both to a
          cell that draws nothing.

    Raises:
        ProviderError: If `fields` or `quantity_prefix_format` is missing, or
            a price/total is neither a sentinel nor a valid amount.
    """
    if "quantity_prefix_format" not in params:
        msg = (
            "receipt_line_items provider requires a 'quantity_prefix_format' param.\n"
            "  What:     the quantity_prefix_format param is missing from the table's params:\n"
            "  Where:    config/layouts/receipts.yml (or the active layout), the line-items "
            "table's params: key.\n"
            '  Expected: params: {quantity_prefix_format: "{quantity}x "}\n'
            '  Recover:  add quantity_prefix_format: "{quantity}x " to the line-items table\'s '
            "params: block."
        )
        raise ProviderError(msg) from None

    rows = pipe_fields(entry, {"fields": params.get("fields")})

    for row in rows:
        qty = row.get("quantity", "")
        prefix = ""
        if qty and qty != "1":
            prefix = params["quantity_prefix_format"].format(quantity=qty)
            row["description"] = f"{prefix}{row['description']}"
        row["quantity_prefix"] = prefix
        for key in ("price", "total"):
            if key in row and row[key] not in ("", "NOT_FOUND"):
                row[key] = _to_decimal(
                    row[key], what="a receipt line item", source="ground_truth/receipts.yml"
                )

    return rows


def _to_decimal(
    value: str,
    *,
    what: str = "a bank transaction",
    source: str = "ground_truth/bank_statements.yml",
) -> Decimal:
    """Parse an amount, treating only the absent-value sentinels as zero.

    A malformed amount is a ground-truth defect and must fail loudly: coercing
    it to zero would corrupt every running balance below it and emit a
    plausible-looking but wrong statement.

    Args:
        value: An amount string from ground truth.
        what: Where in the document the amount came from, for the diagnostic --
            the two providers using this read different fields of different
            document types, and a receipt author must not be pointed at
            bank_statements.yml.
        source: The ground-truth file to fix it in, for the diagnostic.

    Returns:
        The parsed Decimal, or Decimal("0") for the absent-value sentinels.

    Raises:
        ProviderError: If the value is neither a sentinel nor a valid amount.
    """
    if value in ("", "NOT_FOUND"):
        return Decimal("0")
    try:
        return Decimal(value)
    except (ArithmeticError, TypeError) as err:
        msg = (
            f"Malformed amount in {what}.\n"
            f"  What:     {value!r} is not a decimal amount.\n"
            f"  Where:    {source}, the affected entry's amount fields.\n"
            f"  Expected: a decimal string without a currency sign, e.g. '137.73'.\n"
            f"  Recover:  fix the amount in {source}."
        )
        raise ProviderError(msg) from err
