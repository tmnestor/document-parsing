# Line-Item Extraction Ground Truth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Emit `line_items.jsonl` — one row per transaction or line item, each carrying its own date and, for bank statements, its own balance — and move the running balance out of Python and into authored YAML where it belongs.

**Architecture:** Five tasks in a forced order. The balance is backfilled into `ground_truth/bank_statements.yml` from the provider's current computation, so every authored value is by construction what the renderer already draws. `validate` then enforces the chain arithmetic, the provider switches from computing to reading, and only then is the line-item projection added. `tests/test_corpus_unchanged.py` staying green is the proof no page moved.

**Tech Stack:** Python 3.12, PyYAML, typer, pytest. Conda env `docparse` for `generators/`; `docparse-score` for `scoring/` (not touched here).

**Spec:** `docs/superpowers/specs/2026-08-31-line-item-extraction-ground-truth-design.md`

## Global Constraints

- Run every command **from the repository root** `/Users/tod/Desktop/document-parsing`.
- Use `conda run -n docparse <command>`. **`pytest tests/` fails at collection** under `docparse` — always pass `--ignore=tests/scoring`.
- `tests/` is gitignored. Write and run tests; **never `git add` anything under `tests/`.**
- **YAML is the single source of truth.** No config value may be hardcoded as a Python default. Every key is required; a missing one fails fast rather than defaulting.
- Fail-fast diagnostics carry all four elements: **What / Where / Expected / Recover**. Assert them with `assert_diagnostic_error` from `tests/helpers.py`.
- Line length 108. Google-style docstrings. Python 3.12 (`X | Y`, never `from __future__ import annotations`).
- Before every commit: `conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 .` then `conda run -n docparse ruff format .` then `conda run -n docparse mypy generators --ignore-missing-imports`.
- **Never** use `--no-verify` on a commit.
- **Never** use a bash heredoc (`<<EOF`) — it hangs this harness. Write files with the Write/Edit tools.
- `cat` returns EMPTY through the Bash tool. Use the Read tool, or `sed -n`/`head`/`grep`.
- `git` needs `GIT_PAGER=cat` and the command chain should end with `< /dev/null`.
- **Determinism is a contract.** Identical inputs render byte-identical images.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `ground_truth/bank_statements.yml` | gains `TRANSACTION_BALANCES` on all 61 entries | 1 |
| `config/field_definitions.yml` | parallel group additions, `balance_consistency`, `line_item_column_names` | 2, 4 |
| `generators/schema.py` | validates the balance chain | 2 |
| `generators/layout_dsl/providers.py` | reads the authored balance instead of computing it | 3 |
| `generators/extraction_export.py` | the exploder, then writing `line_items.jsonl` | 4, 5 |

**Forced ordering.** Task 1 authors data nothing reads yet (inert). Task 2 proves that data is self-consistent (inert). Task 3 makes the renderer read it — the one task that could move a pixel, and it must not. Tasks 4 and 5 add the projection on top.

---

### Task 1: Backfill the authored balances

**Files:**
- Modify: `ground_truth/bank_statements.yml` (all 61 entries)
- Temporary: a backfill script, deleted before committing
- Test: `tests/test_ground_truth_balances.py`

**Interfaces:**
- Produces: a `TRANSACTION_BALANCES` field on every bank statement entry — pipe-delimited, one value per transaction, same length as the other transaction lists.

- [ ] **Step 1: Write the failing test**

Create `tests/test_ground_truth_balances.py`:

```python
"""The authored running balances, and the chain they must satisfy."""

from decimal import Decimal
from pathlib import Path

import yaml

GT = Path("ground_truth/bank_statements.yml")
FIELDS = ("TRANSACTION_DATES", "TRANSACTION_AMOUNTS_PAID",
          "TRANSACTION_AMOUNTS_RECEIVED", "TRANSACTION_BALANCES")


def _entries() -> dict:
    return yaml.safe_load(GT.read_text(encoding="utf-8"))


def _amount(value: str) -> Decimal:
    text = value.strip()
    return Decimal("0") if text == "NOT_FOUND" else Decimal(text)


def test_every_statement_authors_a_balance_per_transaction():
    missing = [cid for cid, e in _entries().items()
               if "TRANSACTION_BALANCES" not in e.get("fields", {})]
    assert not missing, f"no TRANSACTION_BALANCES on: {missing[:5]}"


def test_the_balance_list_is_parallel_with_the_others():
    ragged = []
    for cid, entry in _entries().items():
        fields = entry["fields"]
        lengths = {f: len(str(fields[f]).split("|")) for f in FIELDS}
        if len(set(lengths.values())) != 1:
            ragged.append((cid, lengths))
    assert not ragged, f"lists disagree in length: {ragged[:3]}"


def test_the_chain_closes_on_every_statement():
    """balance[i] == balance[i-1] - paid[i] + received[i], ending at ACCOUNT_BALANCE."""
    broken = []
    for cid, entry in _entries().items():
        fields = entry["fields"]
        bal = [Decimal(v.strip()) for v in str(fields["TRANSACTION_BALANCES"]).split("|")]
        paid = [_amount(v) for v in str(fields["TRANSACTION_AMOUNTS_PAID"]).split("|")]
        recv = [_amount(v) for v in str(fields["TRANSACTION_AMOUNTS_RECEIVED"]).split("|")]
        for i in range(1, len(bal)):
            if bal[i] != bal[i - 1] - paid[i] + recv[i]:
                broken.append((cid, i, str(bal[i])))
                break
        if bal[-1] != Decimal(str(fields["ACCOUNT_BALANCE"])):
            broken.append((cid, "closing", str(bal[-1])))
    assert not broken, f"chain does not close: {broken[:3]}"
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
conda run -n docparse pytest tests/test_ground_truth_balances.py -q
```

Expected: all three FAIL — `TRANSACTION_BALANCES` does not exist yet.

- [ ] **Step 3: Write the backfill script**

Create `/tmp/backfill_balances.py` with the Write tool. It reproduces the provider's current arithmetic exactly (`generators/layout_dsl/providers.py:349-353`: seed from `ACCOUNT_BALANCE`, walk in reverse, `balance = balance + debit - credit`), then inserts one line per entry by text so the file's comments and ordering survive:

```python
"""One-time backfill of TRANSACTION_BALANCES. Delete after running."""

from decimal import Decimal
from pathlib import Path

import yaml

GT = Path("ground_truth/bank_statements.yml")


def amount(value: str) -> Decimal:
    text = value.strip()
    return Decimal("0") if text == "NOT_FOUND" else Decimal(text)


def balances_for(fields: dict) -> list[Decimal]:
    """Reproduce providers.py's backward walk from the closing balance."""
    paid = [amount(v) for v in str(fields["TRANSACTION_AMOUNTS_PAID"]).split("|")]
    recv = [amount(v) for v in str(fields["TRANSACTION_AMOUNTS_RECEIVED"]).split("|")]
    running = Decimal(str(fields["ACCOUNT_BALANCE"]))
    out = [Decimal("0")] * len(paid)
    for i in range(len(paid) - 1, -1, -1):
        out[i] = running
        running = running + paid[i] - recv[i]
    return out


entries = yaml.safe_load(GT.read_text(encoding="utf-8"))
lines = GT.read_text(encoding="utf-8").splitlines()
out: list[str] = []
current: str | None = None

for line in lines:
    out.append(line)
    stripped = line.strip()
    if stripped.endswith(":") and not line.startswith(" "):
        current = stripped[:-1]
    if current and stripped.startswith("TRANSACTION_AMOUNTS_RECEIVED:"):
        fields = entries[current]["fields"]
        values = "|".join(str(b) for b in balances_for(fields))
        indent = line[: len(line) - len(line.lstrip())]
        out.append(f"{indent}TRANSACTION_BALANCES: {values}")

GT.write_text("\n".join(out) + "\n", encoding="utf-8")
print(f"backfilled {len(entries)} entries")
```

- [ ] **Step 4: Run the backfill**

```bash
conda run -n docparse python /tmp/backfill_balances.py
```

Expected: `backfilled 61 entries`.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
conda run -n docparse pytest tests/test_ground_truth_balances.py -q
conda run -n docparse pytest tests/ --ignore=tests/scoring -q
```

Expected: the three new tests pass, and the whole suite stays green — nothing reads this field yet, so `test_corpus_unchanged` must still pass. If it fails, the backfill corrupted the YAML; `git checkout ground_truth/bank_statements.yml` and investigate.

- [ ] **Step 6: Spot-check one entry by eye**

```bash
grep -A1 "TRANSACTION_AMOUNTS_RECEIVED" ground_truth/bank_statements.yml | head -4
```

Expected: a `TRANSACTION_BALANCES:` line immediately after, same indentation, pipe-delimited. `CASE001` must start `3896.62|3677.58|3554.54|` and end at `9650.99`.

- [ ] **Step 7: Delete the script and commit**

```bash
rm /tmp/backfill_balances.py
git add ground_truth/bank_statements.yml
git commit -m "🗃️ data: author the running balance on every transaction"
```

---

### Task 2: Enforce the balance chain in validate

**Files:**
- Modify: `config/field_definitions.yml` (`parallel_field_groups`, new `balance_consistency`)
- Modify: `generators/schema.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Consumes: `TRANSACTION_BALANCES` from Task 1.
- Produces: `validate` rejecting a statement whose balances do not close.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_schema.py`:

`validate_entry(case_id, entry) -> list[str]` **returns** error strings rather
than raising — that is the house style for per-entry data errors, as
`_check_gst` shows. Do not use `assert_diagnostic_error` here; the four-element
block is for config fail-fast, not for a bad value in an authored entry.

```python
import copy
from pathlib import Path

import yaml

from generators.schema import validate_entry

GT = Path("ground_truth/bank_statements.yml")


def test_a_broken_balance_chain_is_reported():
    """A balance that does not follow from its neighbours must be an error."""
    gt = yaml.safe_load(GT.read_text(encoding="utf-8"))
    case_id, entry = next(iter(gt.items()))
    broken = copy.deepcopy(entry)
    values = str(broken["fields"]["TRANSACTION_BALANCES"]).split("|")
    values[1] = "999999.99"
    broken["fields"]["TRANSACTION_BALANCES"] = "|".join(values)

    errors = validate_entry(case_id, broken)
    assert any("TRANSACTION_BALANCES" in e for e in errors), errors
    assert any("999999.99" in e for e in errors), errors


def test_a_closing_balance_mismatch_is_reported():
    gt = yaml.safe_load(GT.read_text(encoding="utf-8"))
    case_id, entry = next(iter(gt.items()))
    broken = copy.deepcopy(entry)
    broken["fields"]["ACCOUNT_BALANCE"] = "1.00"

    errors = validate_entry(case_id, broken)
    assert any("ACCOUNT_BALANCE" in e for e in errors), errors


def test_every_shipped_statement_satisfies_the_balance_rule():
    gt = yaml.safe_load(GT.read_text(encoding="utf-8"))
    offenders = {cid: validate_entry(cid, e) for cid, e in gt.items()}
    offenders = {cid: errs for cid, errs in offenders.items() if errs}
    assert not offenders, f"{len(offenders)} statement(s) fail validation: {list(offenders)[:3]}"
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
conda run -n docparse pytest tests/test_schema.py -q -k balance
```

Expected: `test_a_broken_balance_chain_is_rejected` FAILS — no rule rejects it yet.

- [ ] **Step 3: Declare the group additions and the rule**

In `config/field_definitions.yml`, extend the bank statement parallel group:

```yaml
  BANK_STATEMENT:
    - [TRANSACTION_DATES, TRANSACTION_DESCRIPTIONS, TRANSACTION_AMOUNTS_PAID,
       TRANSACTION_AMOUNTS_RECEIVED, TRANSACTION_BALANCES]
```

and add, beside `gst_consistency`:

```yaml
# The running balance printed on every statement row. Authored, not computed:
# it is ink on the page and it is scored, so it belongs here rather than in
# Python. This rule is what keeps the authored chain honest.
#
#   balance[i] == balance[i-1] - paid[i] + received[i]
#   balance[last] == ACCOUNT_BALANCE
#
# NOT_FOUND reads as zero. The opening balance the synthetic leading row prints
# is not in the list: it is balance[0] + paid[0] - received[0].
balance_consistency:
  balances_field: TRANSACTION_BALANCES
  paid_field: TRANSACTION_AMOUNTS_PAID
  received_field: TRANSACTION_AMOUNTS_RECEIVED
  closing_field: ACCOUNT_BALANCE
  decimals: 2
```

- [ ] **Step 4: Implement the check**

In `generators/schema.py`, add a loader mirroring `_required_section("gst_consistency", …)` (see `generators/schema.py:185`), then the check itself. Call it from the same per-entry validation that runs `_parallel_groups` (around `generators/schema.py:397`), guarded to statements that declare the balances field:

```python
def _balance_rule() -> dict:
    """The declared balance-chain rule. Every key required."""
    example = (
        "            balance_consistency:\n"
        "              balances_field: TRANSACTION_BALANCES\n"
        "              paid_field: TRANSACTION_AMOUNTS_PAID\n"
        "              received_field: TRANSACTION_AMOUNTS_RECEIVED\n"
        "              closing_field: ACCOUNT_BALANCE\n"
        "              decimals: 2"
    )
    rule = _required_section("balance_consistency", example)
    missing = [
        k
        for k in ("balances_field", "paid_field", "received_field", "closing_field", "decimals")
        if k not in rule
    ]
    if missing:
        raise SchemaError(
            "config/field_definitions.yml 'balance_consistency:' is incomplete.\n"
            f"  What:     missing {', '.join(missing)}.\n"
            f"  Where:    {_FIELD_DEFS_PATH.resolve()}, under 'balance_consistency:'.\n"
            f"  Expected:\n{example}\n"
            "  Recover:  add the missing key(s) to that block."
        )
    return rule


def _check_balance_chain(case_id: str, fields: dict) -> list[str]:
    """Report any authored balance that does not follow from its amounts.

    Returns error strings rather than raising, matching `_check_gst` — a bad
    value in an authored entry is a finding to collect, not a startup failure.

    Args:
        case_id: The entry's case id, named in every message.
        fields: The entry's `fields` mapping.

    Returns:
        One message per broken link, plus one if the chain does not end at the
        closing balance. Empty when the statement is consistent, or carries no
        balances field at all.
    """
    rule = _balance_rule()
    if rule["balances_field"] not in fields:
        return []

    def amount(value: str) -> Decimal:
        text = str(value).strip()
        return Decimal("0") if text == "NOT_FOUND" else Decimal(text)

    balances = [Decimal(v.strip()) for v in str(fields[rule["balances_field"]]).split("|")]
    paid = [amount(v) for v in str(fields[rule["paid_field"]]).split("|")]
    received = [amount(v) for v in str(fields[rule["received_field"]]).split("|")]

    errors: list[str] = []
    for index in range(1, len(balances)):
        expected = balances[index - 1] - paid[index] + received[index]
        if balances[index] != expected:
            errors.append(
                f"{case_id}: {rule['balances_field']}[{index}] is {balances[index]}, but the "
                f"previous balance {balances[index - 1]} minus {rule['paid_field']} "
                f"{paid[index]} plus {rule['received_field']} {received[index]} is {expected}. "
                f"Either correct the balance at position {index}, or the amounts on that row."
            )

    closing = Decimal(str(fields[rule["closing_field"]]))
    if balances[-1] != closing:
        errors.append(
            f"{case_id}: {rule['balances_field']} ends at {balances[-1]} but "
            f"{rule['closing_field']} is {closing}. The last running balance is the closing "
            f"balance; either correct the final balance, or {rule['closing_field']}."
        )
    return errors
```

Wire it exactly as the GST check is wired, immediately after that line near the
end of `validate_entry` (`generators/schema.py:411`):

```python
    errors.extend(_check_gst(case_id, fields))
    errors.extend(_check_balance_chain(case_id, fields))

    return errors
```

Add `from decimal import Decimal` to the imports if absent.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
conda run -n docparse pytest tests/test_schema.py -q
conda run -n docparse python -m generators.pipeline validate
conda run -n docparse pytest tests/ --ignore=tests/scoring -q
```

Expected: all green, `validate` passes on all 61 statements, `test_corpus_unchanged` still passes. This task remains inert on rendering.

- [ ] **Step 6: Lint, type-check, commit**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 . && conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
git add config/field_definitions.yml generators/schema.py
git commit -m "✨ feat: enforce the authored balance chain"
```

---

### Task 3: The provider reads the authored balance

This is the only task that can move a pixel, and it must not.

**Files:**
- Modify: `generators/layout_dsl/providers.py:337-353`
- Test: `tests/layout_dsl/test_field_providers.py`

**Interfaces:**
- Consumes: `TRANSACTION_BALANCES` (Task 1), validated (Task 2).
- Produces: `bank_transactions` rows whose `balance` comes from ground truth.

- [ ] **Step 1: Write the failing test**

Add to `tests/layout_dsl/test_field_providers.py`:

```python
def test_the_row_balance_comes_from_the_authored_field():
    """Editing the authored balance must change the row; computing would ignore it."""
    import copy
    from decimal import Decimal
    from pathlib import Path
    import yaml
    from generators.layout_dsl.providers import bank_transactions

    gt = yaml.safe_load(Path("ground_truth/bank_statements.yml").read_text(encoding="utf-8"))
    entry = copy.deepcopy(next(iter(gt.values())))
    values = str(entry["fields"]["TRANSACTION_BALANCES"]).split("|")
    values[0] = "12345.67"
    entry["fields"]["TRANSACTION_BALANCES"] = "|".join(values)

    rows = bank_transactions(entry, {})
    assert rows[0]["balance"] == Decimal("12345.67")
```

- [ ] **Step 2: Run it to verify it fails**

```bash
conda run -n docparse pytest tests/layout_dsl/test_field_providers.py -q -k authored_field
```

Expected: FAIL — the provider computes from `ACCOUNT_BALANCE` and ignores the authored value.

- [ ] **Step 3: Switch the provider from computing to reading**

In `generators/layout_dsl/providers.py`, add `balance` to the `pipe_fields` mapping:

```python
    rows = pipe_fields(
        entry,
        {
            "fields": {
                "date": "TRANSACTION_DATES",
                "description": "TRANSACTION_DESCRIPTIONS",
                "debit": "TRANSACTION_AMOUNTS_PAID",
                "credit": "TRANSACTION_AMOUNTS_RECEIVED",
                "balance": "TRANSACTION_BALANCES",
            }
        },
    )
```

Then replace the backward-walking loop:

```python
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
```

with:

```python
    # The running balance is AUTHORED, not computed here. It is ink on the page
    # and it is scored, so it lives in ground_truth/ like every other drawn value
    # -- YAML is the single source of truth. `balance_consistency` in
    # config/field_definitions.yml keeps the authored chain honest, and the
    # closing balance is checked against ACCOUNT_BALANCE there rather than being
    # the seed for a backward walk here.
    for row in rows:
        row["balance"] = _to_decimal(row["balance"])
        row["synthetic"] = False
        # Coerce real amounts to Decimal so the table formats them as currency, matching
        # the legacy renderer. The absent sentinel is left alone: legacy draws nothing
        # for it, and _cell_text maps it to the empty string.
        for key in ("debit", "credit"):
            if row[key] != "NOT_FOUND":
                row[key] = _to_decimal(row[key])
```

Leave everything after this untouched. In particular the opening-balance
derivation already reads `first["balance"]` and needs no change:

```python
        opening = first["balance"] - _to_decimal(first["credit"]) + _to_decimal(first["debit"])
```

Also update the function's docstring, which currently says balances are "derived
from ACCOUNT_BALANCE (the closing balance) by walking the transactions in
reverse", and the module docstring's claim that "balance and opening row exist
nowhere in ground truth" — the balance now does. The opening row still does not.

- [ ] **Step 4: Run the tests — the byte-identical check is the point**

```bash
conda run -n docparse pytest tests/layout_dsl/test_field_providers.py -q
conda run -n docparse pytest tests/ --ignore=tests/scoring -q
```

Expected: fully green, **including `tests/test_corpus_unchanged.py`**. That is the load-bearing result: the authored values reproduce the drawn page exactly. If it fails, Task 1's backfill did not match the computation — do not adjust the renderer to fit, fix the backfill.

- [ ] **Step 5: Confirm the page by eye**

```bash
conda run -n docparse python -m generators.pipeline generate --type bank_statements \
    --output /tmp/bal/out --derived /tmp/bal/derived
```

Open `/tmp/bal/out/CASE001_bank_statements.png` with the Read tool. Confirm the Balance column still reads `$4,224.77` on the Opening Balance row and `$3,896.62` on the first transaction.

- [ ] **Step 6: Lint, type-check, commit**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 . && conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
git add generators/layout_dsl/providers.py
git commit -m "♻️ refactor: read the running balance from ground truth"
```

---

### Task 4: The line-item exploder

**Files:**
- Modify: `config/field_definitions.yml` (new `line_item_column_names`)
- Modify: `generators/extraction_export.py`
- Test: `tests/test_extraction_export.py`

**Interfaces:**
- Produces: `line_item_rows(ground_truth: dict[str, dict], doc_type: str, definitions: dict) -> list[dict]`, returning dicts keyed `case_id`, `doc_type`, `image`, `line_no`, then the singularised group columns.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_extraction_export.py`:

```python
def test_a_statement_explodes_into_one_row_per_transaction():
    from pathlib import Path
    import yaml
    from generators.extraction_export import line_item_rows

    definitions = yaml.safe_load(Path("config/field_definitions.yml").read_text(encoding="utf-8"))
    gt = yaml.safe_load(Path("ground_truth/bank_statements.yml").read_text(encoding="utf-8"))
    rows = line_item_rows({"CASE001": gt["CASE001"]}, "bank_statements", definitions)

    expected = len(str(gt["CASE001"]["fields"]["TRANSACTION_DATES"]).split("|"))
    assert len(rows) == expected
    assert rows[0]["case_id"] == "CASE001"
    assert rows[0]["line_no"] == 0
    assert rows[0]["image"] == "CASE001_bank_statements.png"
    assert rows[0]["TRANSACTION_DATE"] == "01/09/2023"
    assert rows[0]["TRANSACTION_BALANCE"] == "3896.62"


def test_a_credit_row_keeps_a_sentinel_paid_amount():
    """Credit rows must survive, not be dropped as another consumer does."""
    from pathlib import Path
    import yaml
    from generators.extraction_export import line_item_rows

    definitions = yaml.safe_load(Path("config/field_definitions.yml").read_text(encoding="utf-8"))
    gt = yaml.safe_load(Path("ground_truth/bank_statements.yml").read_text(encoding="utf-8"))
    rows = line_item_rows({"CASE001": gt["CASE001"]}, "bank_statements", definitions)

    credits = [r for r in rows if r["TRANSACTION_AMOUNT_PAID"] == "NOT_FOUND"]
    assert credits, "no credit row survived the explode"
    assert all(r["TRANSACTION_AMOUNT_RECEIVED"] != "NOT_FOUND" for r in credits)


def test_a_document_with_no_line_items_yields_no_rows():
    from pathlib import Path
    import yaml
    from generators.extraction_export import line_item_rows

    definitions = yaml.safe_load(Path("config/field_definitions.yml").read_text(encoding="utf-8"))
    entry = {"fields": {"DOCUMENT_TYPE": "BANK_STATEMENT",
                        "TRANSACTION_DATES": "NOT_FOUND",
                        "TRANSACTION_DESCRIPTIONS": "NOT_FOUND",
                        "TRANSACTION_AMOUNTS_PAID": "NOT_FOUND",
                        "TRANSACTION_AMOUNTS_RECEIVED": "NOT_FOUND",
                        "TRANSACTION_BALANCES": "NOT_FOUND"}}
    assert line_item_rows({"CASE999": entry}, "bank_statements", definitions) == []


def test_an_unmapped_group_field_fails_fast():
    from pathlib import Path
    import copy
    import pytest
    import yaml
    from generators.extraction_export import ExtractionExportError, line_item_rows
    from tests.helpers import assert_diagnostic_error

    definitions = copy.deepcopy(
        yaml.safe_load(Path("config/field_definitions.yml").read_text(encoding="utf-8"))
    )
    del definitions["line_item_column_names"]["TRANSACTION_DATES"]
    gt = yaml.safe_load(Path("ground_truth/bank_statements.yml").read_text(encoding="utf-8"))

    with pytest.raises(ExtractionExportError) as excinfo:
        line_item_rows({"CASE001": gt["CASE001"]}, "bank_statements", definitions)
    assert_diagnostic_error(str(excinfo.value), mentions=("TRANSACTION_DATES",
                                                          "line_item_column_names"))


def test_every_document_type_explodes_to_the_expected_total():
    from pathlib import Path
    import yaml
    from generators.extraction_export import line_item_rows

    definitions = yaml.safe_load(Path("config/field_definitions.yml").read_text(encoding="utf-8"))
    totals = {"bank_statements": 1641, "invoices": 224, "receipts": 184}
    for doc_type, expected in totals.items():
        gt = yaml.safe_load(Path(f"ground_truth/{doc_type}.yml").read_text(encoding="utf-8"))
        assert len(line_item_rows(gt, doc_type, definitions)) == expected
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
conda run -n docparse pytest tests/test_extraction_export.py -q -k "explode or credit or line_item or unmapped or document_type"
```

Expected: FAIL with `ImportError: cannot import name 'line_item_rows'`.

- [ ] **Step 3: Declare the column-name mapping**

Add to `config/field_definitions.yml`:

```yaml
# The singular name each parallel field takes in line_items.jsonl, where a
# row holds ONE value. A row reading `TRANSACTION_DATES: 01/09/2023` would
# misstate its own grain. Every field in every parallel_field_groups entry must
# appear here; a group field with no mapping is a startup error, so a newly
# grouped field cannot ship un-named.
line_item_column_names:
  TRANSACTION_DATES: TRANSACTION_DATE
  TRANSACTION_DESCRIPTIONS: TRANSACTION_DESCRIPTION
  TRANSACTION_AMOUNTS_PAID: TRANSACTION_AMOUNT_PAID
  TRANSACTION_AMOUNTS_RECEIVED: TRANSACTION_AMOUNT_RECEIVED
  TRANSACTION_BALANCES: TRANSACTION_BALANCE
  LINE_ITEM_DESCRIPTIONS: LINE_ITEM_DESCRIPTION
  LINE_ITEM_QUANTITIES: LINE_ITEM_QUANTITY
  LINE_ITEM_PRICES: LINE_ITEM_PRICE
  LINE_ITEM_TOTAL_PRICES: LINE_ITEM_TOTAL_PRICE
```

- [ ] **Step 4: Implement the exploder**

Add to `generators/extraction_export.py`:

```python
def line_item_rows(ground_truth: dict[str, dict], doc_type: str, definitions: dict) -> list[dict]:
    """Explode each case's parallel field lists into one row per line item.

    The group is looked up by the entry's own authored `DOCUMENT_TYPE`, exactly
    as `validate` does before checking the same lists (generators/schema.py:317),
    so no document type is named here.

    Args:
        ground_truth: Entries keyed by case id, as `load_ground_truth` returns.
        doc_type: The document type key, e.g. "bank_statements".
        definitions: The parsed `field_definitions.yml`.

    Returns:
        One dict per line item, keyed `case_id`, `doc_type`, `image`, `line_no`,
        then the singularised group columns. A case whose group fields are absent
        or wholly `NOT_FOUND` contributes no rows.

    Raises:
        ExtractionExportError: A group field has no `line_item_column_names` entry.
    """
    names = definitions["line_item_column_names"]
    groups = definitions["parallel_field_groups"]
    rows: list[dict] = []

    for case_id in sorted(ground_truth):
        fields = ground_truth[case_id].get("fields", {})
        for group in groups.get(str(fields.get("DOCUMENT_TYPE", "")), []):
            unmapped = [field for field in group if field not in names]
            if unmapped:
                raise ExtractionExportError(
                    "Cannot write the line-item export: a grouped field has no column name.\n"
                    f"  What:     {', '.join(unmapped)} appear in parallel_field_groups but "
                    "not in line_item_column_names, so an exploded row would have no name "
                    "for that column.\n"
                    "  Where:    config/field_definitions.yml, under "
                    "'line_item_column_names:'\n"
                    "  Expected: one singular name per grouped field, e.g.\n"
                    "              line_item_column_names:\n"
                    "                TRANSACTION_DATES: TRANSACTION_DATE\n"
                    f"  Recover:  add {', '.join(unmapped)} to that block."
                )

            values = {field: str(fields.get(field, SENTINEL)).split("|") for field in group}
            width = len(next(iter(values.values())))
            if width == 1 and all(v[0].strip() == SENTINEL for v in values.values()):
                continue

            for index in range(width):
                row = {
                    "case_id": case_id,
                    "doc_type": doc_type,
                    "image": f"{case_id}_{doc_type}.png",
                    "line_no": index,
                }
                for field in group:
                    row[names[field]] = values[field][index].strip()
                rows.append(row)
    return rows
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
conda run -n docparse pytest tests/test_extraction_export.py -q
```

Expected: all pass, with the totals 1641 / 224 / 184.

- [ ] **Step 6: Lint, type-check, commit**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 . && conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
git add config/field_definitions.yml generators/extraction_export.py
git commit -m "✨ feat: explode parallel field groups into line items"
```

---

### Task 5: Write line_items.jsonl

**Files:**
- Modify: `generators/extraction_export.py` (`export_extraction`)
- Test: `tests/test_extraction_export.py`

**Interfaces:**
- Consumes: `line_item_rows(...)` from Task 4.
- Produces: `line_items.jsonl` inside `extraction_<stamp>/`. No CSV — a row's columns differ per document type, and JSONL rows need not share keys.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_extraction_export.py`:

```python
import json
from pathlib import Path

import pytest

from generators.extraction_export import export_extraction


def test_the_export_writes_both_grains(tmp_path):
    """Document-level and line-item files sit side by side, joinable by case_id."""
    exports = sorted(Path("../evaluation_data").glob("corpus_*/parsing_*"))
    if not exports:
        pytest.skip("no exported corpus on this machine")

    root = export_extraction(
        corpus=exports[-1],
        ground_truth_dir=Path("ground_truth"),
        field_definitions=Path("config/field_definitions.yml"),
        target=tmp_path,
        date_stamp="20260101",
    )

    doc_rows = [json.loads(line) for line in (root / "ground_truth.jsonl").read_text().splitlines()]
    item_rows = [json.loads(line) for line in (root / "line_items.jsonl").read_text().splitlines()]

    assert len(doc_rows) == 189
    assert len(item_rows) == 2049

    # JSONL only — no line-item CSV, because a row's columns differ per document
    # type and JSONL rows need not share keys.
    assert not list(root.glob("line_items*.csv"))

    doc_cases = {r["case_id"] for r in doc_rows}
    item_cases = {r["case_id"] for r in item_rows}
    assert item_cases <= doc_cases, "a line item names a case the document file lacks"
```

- [ ] **Step 2: Run it to verify it fails**

```bash
conda run -n docparse pytest tests/test_extraction_export.py -q -k both_grains
```

Expected: FAIL — `line_items.jsonl` does not exist.

- [ ] **Step 3: Write the two files**

In `export_extraction`, accumulate line-item rows alongside the document rows
(wherever `rows +=` builds the document-level list, add the parallel call), then
after the existing `ground_truth.csv` block add:

```python
    # JSONL only, deliberately. A row's columns differ per document type -- a
    # statement has TRANSACTION_BALANCE where an invoice has LINE_ITEM_QUANTITY
    # -- and JSONL rows need not share keys, so the two coexist in one file.
    # CSV would need a fixed header, forcing either one sparse table implying a
    # schema that does not exist, or a file per type.
    with (root / "line_items.jsonl").open("w", encoding="utf-8") as handle:
        for row in item_rows:
            handle.write(json.dumps(row) + "\n")
```

No CSV. The document-level `ground_truth.csv` stays as it is — that file has a
single fixed column set and existing consumers; it is not this plan's to change.

- [ ] **Step 4: Update the module docstring**

`generators/extraction_export.py` opens by calling itself "the fourth projection
of one truth". It now emits two grains; say so, and note that the line-item file
is joined to the document-level one by `case_id`.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
conda run -n docparse pytest tests/test_extraction_export.py -q
conda run -n docparse pytest tests/ --ignore=tests/scoring -q
```

Expected: all green.

- [ ] **Step 6: Produce the real artifact and inspect it**

```bash
conda run -n docparse python -m generators.pipeline extract \
    --corpus ../evaluation_data/corpus_20260901/parsing_20260901 \
    --target /tmp/li --date 20260901
head -2 /tmp/li/extraction_20260901/line_items.jsonl
wc -l /tmp/li/extraction_20260901/line_items.jsonl
```

Expected: 2049 lines, and the first row reading `CASE001`, `line_no` 0,
`TRANSACTION_DATE` `01/09/2023`, `TRANSACTION_BALANCE` `3896.62`.

- [ ] **Step 7: Lint, type-check, commit**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 . && conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
git add generators/extraction_export.py
git commit -m "✨ feat: emit line-item ground truth beside the document-level file"
```

---

## Notes for the executor

- **Task 3 is the risky one.** `tests/test_corpus_unchanged.py` must stay green through it. If it fails, the fault is in Task 1's backfill, not the renderer — never adjust drawing to fit the data.
- Tasks 1 and 2 are inert by design: nothing reads `TRANSACTION_BALANCES` until Task 3. If the suite goes red during them, something else broke.
- The corpus is **not** rebuilt by this plan. `line_items.*` is produced by `extract`, which reads YAML and an existing export; no regeneration is needed and no new vintage is cut.
