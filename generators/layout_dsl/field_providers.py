"""Field providers -- the DSL's other sanctioned escape hatch.

Some values a receipt or invoice draws exist nowhere in ground truth: a
receipt number, POS time, register and staff name, and the sixteen EFTPOS
terminal-slip values -- today derived by SHA-256 inside `generators/receipt.py`
and `generators/payment_block.py`. A field provider is a registered Python
function returning a flat `dict[str, str]` that is merged into `entry["fields"]`
before a layout's body renders, so `{FIELD}` interpolation (`binding.py`) and
`when:` suppression (`engine.py`) reach these derived values with no change to
either.

Mirrors `providers.py`'s row-provider registry shape deliberately -- a reader
who knows one recognises the other. The one addition is `emits`, mandatory:
without it, `validate` could not resolve a `{FIELD}` naming a derived value,
and every placeholder check would degrade from a startup failure to a
render-time one.
"""

import hashlib
from collections.abc import Callable, Iterable
from decimal import Decimal
from pathlib import Path

import yaml

from generators.common import fmt_amount
from generators.content_engine import load_pools
from generators.payment_block import derive_payment, load_pos_pools

FieldProvider = Callable[[dict, dict], dict[str, str]]

_REGISTRY: dict[str, FieldProvider] = {}
# Mirrors providers.py's _PARAM_KEYS: the top-level `params:` keys a provider
# reads, declared at registration time so schema.py can reject a typo'd
# params key (e.g. `terminl_id`) at validate time.
_PARAM_KEYS: dict[str, frozenset[str]] = {}
# The one addition over providers.py's registry: every `fields` key a
# provider may return. schema.py unions this into `known_fields` (per
# provider actually referenced by a layout) so a `{FIELD}` naming a derived
# value resolves at validate time, and `apply_field_providers` uses it below
# to reject a provider that returns a key it never declared.
_EMITS: dict[str, tuple[str, ...]] = {}

_FIELD_DEFINITIONS_PATH = Path("config/field_definitions.yml")

# Loaded once, on first registration -- see _ground_truth_columns().
_GROUND_TRUTH_COLUMNS: set[str] | None = None


class FieldProviderError(RuntimeError):
    """Raised when a field provider is unknown, misregistered, or misbehaves."""


def _ground_truth_columns() -> set[str]:
    """Return every field name `config/field_definitions.yml` owns.

    Loaded once and cached: registration happens a handful of times, all at
    import time, so there is no render-time cost to worry about -- this just
    avoids re-reading and re-parsing the YAML on every `@field_provider` use.

    Returns:
        Every name in field_definitions.yml's `all_columns` list -- the field
        names the authored ground truth owns. A provider must not emit one of
        them: the layout would then have two sources for the same name and no
        way to say which the page drew.

    Raises:
        FieldProviderError: If the file is missing or has no `all_columns` list.
    """
    global _GROUND_TRUTH_COLUMNS  # noqa: PLW0603
    if _GROUND_TRUTH_COLUMNS is not None:
        return _GROUND_TRUTH_COLUMNS

    path = _FIELD_DEFINITIONS_PATH
    if not path.exists():
        msg = (
            "Cannot check a field provider's emits against the ground-truth field names.\n"
            f"  What:     field definitions file not found.\n"
            f"  Where:    {path.resolve()}\n"
            "  Expected: config/field_definitions.yml with an 'all_columns:' list.\n"
            "  Recover:  restore config/field_definitions.yml, or fix the path in "
            "generators/layout_dsl/field_providers.py's _FIELD_DEFINITIONS_PATH."
        )
        raise FieldProviderError(msg)

    data = yaml.safe_load(path.read_text())
    columns = data.get("all_columns") if isinstance(data, dict) else None
    if not isinstance(columns, list):
        msg = (
            "Cannot check a field provider's emits against the ground-truth field names.\n"
            f"  What:     {path} has no 'all_columns:' list.\n"
            f"  Where:    {path.resolve()} -> all_columns\n"
            "  Expected: all_columns:\n              - DOCUMENT_TYPE\n              - ...\n"
            f"  Recover:  add an 'all_columns:' list to {path}."
        )
        raise FieldProviderError(msg)

    _GROUND_TRUTH_COLUMNS = set(columns)
    return _GROUND_TRUTH_COLUMNS


def field_provider(
    name: str, *, params: frozenset[str] = frozenset(), emits: tuple[str, ...]
) -> Callable[[FieldProvider], FieldProvider]:
    """Register a field provider under `name`.

    Args:
        name: The name a layout's `field_providers:` entry uses.
        params: The top-level `params:` keys this provider reads (see
            `row_provider`'s identical rationale in `providers.py`).
        emits: Every `fields` key this provider may return. Mandatory --
            without it, `validate` could not resolve a `{FIELD}` naming a
            derived value, and `apply_field_providers` could not catch a
            provider returning a key it never declared.

    Returns:
        A decorator that registers and returns the function unchanged.

    Raises:
        FieldProviderError: If `name` is already registered, or an emitted
            name collides with a field `config/field_definitions.yml` owns.
    """

    def decorate(func: FieldProvider) -> FieldProvider:
        if name in _REGISTRY:
            msg = (
                "Cannot register field provider.\n"
                f"  What:     field provider '{name}' is already registered.\n"
                "  Where:    generators/layout_dsl/field_providers.py, "
                f"@field_provider('{name}', ...)\n"
                "  Expected: a distinct provider name.\n"
                "  Recover:  pick a distinct provider name."
            )
            raise FieldProviderError(msg)

        collisions = sorted(set(emits) & _ground_truth_columns())
        if collisions:
            msg = (
                "Cannot register field provider.\n"
                f"  What:     field provider '{name}' declares emits {collisions} that collide "
                "with ground-truth field name(s) config/field_definitions.yml owns.\n"
                "  Where:    generators/layout_dsl/field_providers.py, "
                f"@field_provider('{name}', emits=...)\n"
                "  Expected: emits names distinct from every name in "
                "config/field_definitions.yml's all_columns list.\n"
                f"  Recover:  rename {collisions} in '{name}''s emits= to a name the ground "
                "truth does not own, e.g. prefix it POS_ or TERMINAL_."
            )
            raise FieldProviderError(msg)

        _REGISTRY[name] = func
        _PARAM_KEYS[name] = params
        _EMITS[name] = emits
        return func

    return decorate


def get_field_provider(name: str) -> FieldProvider:
    """Look up a registered field provider.

    Args:
        name: Provider name from a layout's `field_providers:` list.

    Returns:
        The registered provider.

    Raises:
        FieldProviderError: If no provider is registered under `name`.
    """
    if name not in _REGISTRY:
        msg = (
            "Unknown field provider.\n"
            f"  What:     no field provider named '{name}' is registered.\n"
            "  Where:    a layout's 'field_providers:' list.\n"
            f"  Expected: one of {sorted(_REGISTRY)}.\n"
            "  Recover:  set the entry's name: to a registered field provider, or register a "
            "new one with @field_provider in generators/layout_dsl/field_providers.py."
        )
        raise FieldProviderError(msg)
    return _REGISTRY[name]


def field_provider_names() -> list[str]:
    """Return the names of all registered field providers, sorted."""
    return sorted(_REGISTRY)


def field_provider_emits(name: str) -> tuple[str, ...]:
    """Return the `fields` keys a registered field provider may emit.

    Args:
        name: A registered field provider name (validate the name itself with
            `field_provider_names()`/`get_field_provider()` first; this
            returns an empty tuple for an unknown name rather than raising,
            since schema.py already reports the unknown-provider case with
            its own diagnostic before ever reaching an emits check).

    Returns:
        The tuple passed to this provider's `@field_provider(..., emits=...)`.
    """
    return _EMITS.get(name, ())


def collect_emit_collisions(
    emitted_by: dict[str, str], name: str, keys: Iterable[str]
) -> tuple[str, str, str] | None:
    """Check `keys` against every key an earlier provider on this layout already claimed.

    Shared by two callers so their diagnostics cannot drift apart the way an
    earlier round of this task found them doing when the same
    build-the-map/set-intersect/format-the-detail logic was hand-copied into
    both places:

    - `generators/layout_dsl/schema.py`'s `_validate_field_providers`, the
      primary, validate-time check -- called with each provider's *declared*
      `emits`, so a whole layout is checked statically, before any provider
      ever runs.
    - `apply_field_providers` below, a defensive, merge-time check for a
      caller that builds a layout dict by hand and never calls
      `validate_layout` first -- called with a provider's *actual* returned
      keys, always a subset of its declared emits by the point this runs
      (the undeclared-emit check above already enforced that).

    Args:
        emitted_by: Accumulated key -> provider-name map for providers
            already checked on this layout. Not mutated -- the caller
            updates it once it has decided whether to raise, so a call about
            to raise leaves the map exactly as it was.
        name: The provider currently being checked.
        keys: The keys to check `name` against -- declared `emits` or actual
            output, per caller (see above).

    Returns:
        `None` if `keys` is disjoint from `emitted_by` -- no collision.
        Otherwise a `(what, expected, recover)` string triple: the three
        diagnostic elements common to both callers' four-element errors.
        Deliberately excludes WHERE: `_validate_field_providers` knows the
        offending layout's id and a dotted YAML key path;
        `apply_field_providers` is given neither (its own signature carries
        only `layout` and `entry`, no id or path), so each caller supplies
        its own WHERE around this shared text rather than being forced to
        fabricate one it does not have.
    """
    collisions = sorted(set(keys) & set(emitted_by))
    if not collisions:
        return None

    detail = ", ".join(f"'{key}' (already emitted by '{emitted_by[key]}')" for key in collisions)
    other_names = sorted({emitted_by[key] for key in collisions})
    what = (
        f"field provider '{name}' emits key(s) that collide with another provider on this layout: {detail}."
    )
    expected = (
        "every field_providers: entry on one layout to emit keys disjoint from every other "
        "provider on that same layout."
    )
    recover = (
        f"rename the colliding key(s) in one provider's emits=, or remove one of '{name}' / "
        f"{other_names} from this layout's field_providers:."
    )
    return what, expected, recover


def field_provider_param_keys(name: str) -> frozenset[str]:
    """Return the top-level `params:` keys a registered field provider accepts.

    Args:
        name: A registered field provider name (see `field_provider_emits`'s
            identical note on validating the name first).

    Returns:
        The frozenset passed to this provider's `@field_provider(..., params=...)`.
    """
    return _PARAM_KEYS.get(name, frozenset())


def apply_field_providers(layout: dict, entry: dict) -> dict:
    """Merge every field provider a layout declares into a copy of `entry`.

    Args:
        layout: The resolved layout dict, carrying `field_providers` -- a
            required key (see `validate_layout` in `generators/layout_dsl/
            schema.py`), so a layout deriving nothing declares
            `field_providers: []` explicitly rather than omitting the key.
        entry: The ground-truth entry.

    Returns:
        A new entry dict -- never a mutation of `entry` -- whose `fields` is
        `entry["fields"]` merged with every provider's derived output.
        `pipeline.generate` reuses entries across the clean and degraded
        passes, so mutating the caller's entry would leak derived values
        between the two.

    Raises:
        FieldProviderError: If a `field_providers:` entry names an unknown
            provider, a provider returns a key it did not declare in its own
            `emits`, or two providers on this layout emit the same key.
    """
    derived: dict[str, str] = {}
    # Tracks which provider already emitted each key, so a second provider
    # colliding on it is caught here even when validate_layout's static
    # (emits-declaration-only) check in schema.py was skipped or bypassed --
    # e.g. a caller that builds a layout dict by hand and calls this function
    # directly, as several tests in this module do. A silent dict.update()
    # overwrite here would make one provider's value vanish from the page
    # with no error, which is worse than either provider failing loudly.
    emitted_by: dict[str, str] = {}
    for spec in layout["field_providers"]:
        name = spec["name"]
        provider = get_field_provider(name)
        result = provider(entry, spec.get("params", {}))

        declared = set(field_provider_emits(name))
        undeclared = sorted(set(result) - declared)
        if undeclared:
            msg = (
                "Field provider emitted an undeclared key.\n"
                f"  What:     field provider '{name}' returned key(s) {undeclared} not listed "
                "in its own emits=.\n"
                "  Where:    generators/layout_dsl/field_providers.py, "
                f"@field_provider('{name}', emits=...)\n"
                f"  Expected: emits including {undeclared}.\n"
                f"  Recover:  add {undeclared} to '{name}''s emits= tuple, or stop returning "
                "them from the provider."
            )
            raise FieldProviderError(msg)

        collision = collect_emit_collisions(emitted_by, name, result)
        if collision is not None:
            what, expected, recover = collision
            msg = (
                "Two field providers on one layout emit the same key.\n"
                f"  What:     {what}\n"
                "  Where:    this layout's 'field_providers:' list.\n"
                f"  Expected: {expected}\n"
                f"  Recover:  {recover}"
            )
            raise FieldProviderError(msg)

        derived.update(result)
        emitted_by.update(dict.fromkeys(result, name))

    return {**entry, "fields": {**entry["fields"], **derived}}


@field_provider(
    "receipt_pos",
    params=frozenset(),
    emits=("POS_TIME", "POS_REGISTER", "POS_STAFF", "RECEIPT_NUMBER"),
)
def receipt_pos(entry: dict, params: dict) -> dict[str, str]:
    """Derive a receipt's POS time, register, staff name, and receipt number.

    Moved the arithmetic here verbatim from the receipt renderer's since-deleted
    `_derive_receipt_details` / `_derive_receipt_number` -- same two digests,
    same hex slices -- reading its pools from
    `generators.payment_block.load_pos_pools()` instead of the module-level
    staff-name list and inline hour/register ranges those functions used.
    `payment_block.derive_payment` consumes hex chars 10-40 of the same
    `pos:` digest this reads 0-8 of; the two must never collide.

    Args:
        entry: The ground-truth entry. Reads `entry["case_id"]` and
            `entry["fields"]["INVOICE_DATE"]`.
        params: Unused. This provider accepts no params: it always loads the
            `pos_terminal` pool, so a `pools_key` naming it would read as a
            switch and be none -- and a layout key no code path reads is worse
            than no key at all, since it tells an operator the document is
            configured a way it is not.

    Returns:
        `{"POS_TIME": ..., "POS_REGISTER": ..., "POS_STAFF": ..., "RECEIPT_NUMBER": ...}`.
    """
    pools = load_pos_pools()
    case_id = entry.get("case_id", "")
    invoice_date = entry["fields"].get("INVOICE_DATE", "")

    pos_digest = hashlib.sha256(f"{case_id}:pos:{invoice_date}".encode()).hexdigest()
    hour = pools["hour_min"] + int(pos_digest[0:2], 16) % pools["hour_span"]
    minute = int(pos_digest[2:4], 16) % 60
    register = pools["register_min"] + int(pos_digest[4:6], 16) % pools["register_span"]
    staff_names = pools["staff_names"]
    staff = staff_names[int(pos_digest[6:8], 16) % len(staff_names)]

    number_digest = hashlib.sha256(f"{case_id}:{invoice_date}".encode()).hexdigest()
    digest_length = pools["receipt_number_digest_length"]
    receipt_number = f"{pools['receipt_number_prefix']}{number_digest[:digest_length].upper()}"

    return {
        "POS_TIME": f"{hour:02d}:{minute:02d}",
        "POS_REGISTER": f"{register:02d}",
        "POS_STAFF": staff,
        "RECEIPT_NUMBER": receipt_number,
    }


def _or_not_found(value: str) -> str:
    """Convert an empty PaymentDetails string into the `NOT_FOUND` sentinel.

    `is_present` (`generators/layout_dsl/binding.py`) already treats
    `NOT_FOUND` and empty string as absent -- that's the mechanism a future
    receipt body uses to pick its card/wallet/cash variant via `when:` -- but
    `derive_payment` itself returns `""` for a field that does not apply to a
    given `PaymentDetails.kind` (e.g. `aid` for cash), never the sentinel. This
    normalises every such gap to the one spelling `when:` checks for.
    """
    return value if value else "NOT_FOUND"


def _amount_or_not_found(value: Decimal | None) -> str:
    """Format a cash Decimal as the slip prints it, or `NOT_FOUND`.

    Already carries `fmt_amount`'s `$` and thousands separators, so the cash
    `pair` blocks in config/layouts/receipts.yml deliberately declare no
    `currency:` of their own -- formatting an already-formatted string twice
    would raise `CurrencyError`.

    `tendered`/`change` are `None` unless `PaymentDetails.kind == "cash"`.
    """
    return fmt_amount(value) if value is not None else "NOT_FOUND"


@field_provider(
    "receipt_payment",
    params=frozenset(),
    emits=(
        "PAYMENT_KIND",
        "PAYMENT_METHOD",
        "PAYMENT_SCHEME_DISPLAY",
        "PAYMENT_ACCOUNT_TYPE",
        "PAYMENT_ACQUIRER",
        "PAYMENT_AID",
        "PAYMENT_MASKED_PAN",
        "PAYMENT_ENTRY_MODE",
        "PAYMENT_PSN",
        "PAYMENT_ATC",
        "PAYMENT_TERMINAL_ID",
        "PAYMENT_TRANSACTION_REF",
        "PAYMENT_TIMESTAMP",
        "PAYMENT_WALLET_LABEL",
        "PAYMENT_TENDERED",
        "PAYMENT_CHANGE",
    ),
)
def receipt_payment(entry: dict, params: dict) -> dict[str, str]:
    """Derive a receipt's EFTPOS terminal-slip values.

    Wraps `generators.payment_block.derive_payment` exactly as the receipt
    renderer's since-deleted `payment` section did -- same POS time (reusing
    `receipt_pos`'s derivation, hex chars 0-8 of the
    `{case_id}:pos:{invoice_date}` digest, so this never duplicates that
    arithmetic).

    `bank_description` is always None here. The predecessor repo looked the
    receipt up in `transaction_links.yml` so a linked receipt's card scheme
    came from its bank row; that file does not cross into this repo (design
    §7), and no transcription metric scores which scheme a slip shows. Passing
    None is the documented "no link" path: `derive_payment` then picks the
    method from the weighted pool.

    Deliberately never emits a purchase total: the slip's `Purchase   AUD`
    line binds `{TOTAL_AMOUNT}` directly, so a second, provider-derived copy
    of a scored value could silently drift from it. `PaymentDetails` carries
    no purchase total either, for the same reason -- see its docstring in
    payment_block.py.

    The three slip variants -- card, wallet, cash -- are selected by the
    receipt body's `when:` on these emitted keys, not by a branch here: every value
    `PaymentDetails` leaves as `""` or `None` for the current `kind` becomes
    the literal `"NOT_FOUND"`, which `is_present` already treats as absent.

    Args:
        entry: The ground-truth entry. Reads `entry["case_id"]`,
            `entry["layout"]`, `entry["fields"]["INVOICE_DATE"]`, and
            `entry["fields"]["TOTAL_AMOUNT"]`.
        params: Unused, for the same reason as `receipt_pos` above:
            `derive_payment` always loads the `payment_terminal` pool, so
            nothing here could branch on a `pools_key`.

    Returns:
        The sixteen `PAYMENT_*` fields listed in this provider's `emits`.
    """
    case_id = entry.get("case_id", "")
    fields = entry["fields"]
    invoice_date = fields.get("INVOICE_DATE", "")
    time_str = receipt_pos(entry, {})["POS_TIME"]

    details = derive_payment(
        case_id,
        invoice_date,
        fields.get("TOTAL_AMOUNT", "0"),
        time_str,
        bank_description=None,
    )

    return {
        "PAYMENT_KIND": details.kind,
        "PAYMENT_METHOD": details.method,
        "PAYMENT_SCHEME_DISPLAY": _or_not_found(details.scheme_display),
        "PAYMENT_ACCOUNT_TYPE": _or_not_found(details.account_type),
        "PAYMENT_ACQUIRER": _or_not_found(details.acquirer),
        "PAYMENT_AID": _or_not_found(details.aid),
        "PAYMENT_MASKED_PAN": _or_not_found(details.masked_pan),
        "PAYMENT_ENTRY_MODE": _or_not_found(details.entry_mode),
        "PAYMENT_PSN": _or_not_found(details.psn),
        "PAYMENT_ATC": _or_not_found(details.atc),
        "PAYMENT_TERMINAL_ID": _or_not_found(details.terminal_id),
        "PAYMENT_TRANSACTION_REF": _or_not_found(details.transaction_ref),
        "PAYMENT_TIMESTAMP": _or_not_found(details.timestamp),
        "PAYMENT_WALLET_LABEL": _or_not_found(details.wallet_label),
        "PAYMENT_TENDERED": _amount_or_not_found(details.tendered),
        "PAYMENT_CHANGE": _amount_or_not_found(details.change),
    }


@field_provider("computed_totals", params=frozenset(), emits=("SUBTOTAL_AMOUNT",))
def computed_totals(entry: dict, params: dict) -> dict[str, str]:
    """Derive `SUBTOTAL_AMOUNT` as `TOTAL_AMOUNT` minus `GST_AMOUNT`.

    Matches the receipt renderer's since-deleted totals section, which computed
    `str(Decimal(total) - Decimal(gst))` and printed it with `,.2f` (the body's
    SUBTOTAL pair now declares `currency: plain` for that half).
    Emits nothing -- not even a zero -- when either input is absent
    or `NOT_FOUND`, so a `{SUBTOTAL_AMOUNT}` placeholder is suppressed by
    `when:` rather than rendering a fabricated value; `emits` is an upper
    bound on what a provider may return, not a promise every call returns it
    (see `apply_field_providers`'s undeclared-key check above, which only
    rejects extra keys, never absent ones).

    Args:
        entry: The ground-truth entry. Reads `entry["fields"]["TOTAL_AMOUNT"]`
            and `entry["fields"]["GST_AMOUNT"]`.
        params: Unused; this provider derives purely from `entry["fields"]`.

    Returns:
        `{"SUBTOTAL_AMOUNT": ...}`, or `{}` when either input is missing.
    """
    fields = entry["fields"]
    total = fields.get("TOTAL_AMOUNT")
    gst = fields.get("GST_AMOUNT")
    if not total or not gst or total == "NOT_FOUND" or gst == "NOT_FOUND":
        return {}
    return {"SUBTOTAL_AMOUNT": str(Decimal(total) - Decimal(gst))}


@field_provider("invoice_terms", params=frozenset(), emits=("PAYMENT_TERMS", "DELIVERY_TERMS"))
def invoice_terms(entry: dict, params: dict) -> dict[str, str]:
    """Derive a tax invoice's payment terms and delivery line.

    The predecessor rendered any trailing section it had no branch for as its
    bare label, so every invoice drew `Payment Terms:` -- and the high-value
    layout also `Delivery:` -- with nothing after it. The transcripts recorded
    the blank faithfully; it was the page that was wrong.

    Derived rather than authored because these are page furniture, not scored
    ground truth: nothing checks them, and `config/field_definitions.yml` owns
    no column for either. The pick is a SHA-256 of the case id, so it is stable
    across runs and independent of the digests `receipt_pos` and
    `derive_payment` consume -- those are keyed on `"{case_id}:pos:{date}"` and
    read from a different pool, so the streams cannot collide.

    Args:
        entry: The ground-truth entry. Reads `entry["case_id"]` only -- the
            terms are independent of every value on the page, which is why
            they can be derived at all.
        params: Unused. This provider accepts no params: it always reads the
            `invoice_terms` pool, so a `pools_key` naming it would read as a
            switch and be none (see `receipt_pos`'s identical rationale).

    Returns:
        `{"PAYMENT_TERMS": ..., "DELIVERY_TERMS": ...}`.
    """
    pools = load_pools()["invoice_terms"]
    digest = hashlib.sha256(f"{entry.get('case_id', '')}:invoice_terms".encode()).hexdigest()
    payment = pools["payment"]
    delivery = pools["delivery"]
    return {
        "PAYMENT_TERMS": payment[int(digest[0:4], 16) % len(payment)],
        "DELIVERY_TERMS": delivery[int(digest[4:8], 16) % len(delivery)],
    }
