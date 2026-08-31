"""Authored ground truth to the flat form an extraction application reads.

The fourth projection of one truth, alongside `serialise`'s Markdown,
`tables`' HTML and `layout`'s annotations. It renders nothing, derives
nothing and runs no extractor: this repository emits IE ground truth and
leaves extraction to whoever consumes it.

It emits two grains. `ground_truth.{jsonl,csv}` is one row per document.
`line_items.jsonl` is one row per line item — per bank transaction, invoice
line or receipt line — and joins back to the document-level file by
`case_id`.

**Why the columns come from `field_definitions.yml`.** `all_columns` is
documented there as "the union of the three document_fields lists above, in
the order the derived CSV writes them" — this file is that CSV's writer. The
schema outlived the writer; taking the order from anywhere else would let the
two drift.

`layout_fields` entries are deliberately absent: a column only one layout
carries would be NOT_FOUND on almost every row.
"""

import csv
import json
import shutil
from pathlib import Path

import yaml

from generators.loader import load_ground_truth

#: What the corpus already writes where a document type has no such field.
SENTINEL = "NOT_FOUND"


class ExtractionExportError(RuntimeError):
    """Raised when the extraction export and the clean corpus disagree."""


def extraction_rows(ground_truth: dict[str, dict], columns: list[str], doc_type: str) -> list[dict]:
    """Flatten one document type's ground truth into one row per case.

    Args:
        ground_truth: Entries keyed by case id, as `load_ground_truth` returns.
        columns: The field order, from `all_columns`.
        doc_type: The document type key, e.g. "invoices".

    Returns:
        One dict per case, keyed `case_id`, `doc_type`, `image`, then `columns`.
    """
    rows = []
    for case_id in sorted(ground_truth):
        fields = ground_truth[case_id].get("fields", {})
        row = {
            "case_id": case_id,
            "doc_type": doc_type,
            "image": f"{case_id}_{doc_type}.png",
        }
        for column in columns:
            row[column] = str(fields.get(column, SENTINEL))
        rows.append(row)
    return rows


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
        ExtractionExportError: `line_item_column_names` or `parallel_field_groups`
            is missing from `definitions`, or a group field has no
            `line_item_column_names` entry.
    """
    if "line_item_column_names" not in definitions:
        raise ExtractionExportError(
            "Cannot write the line-item export: a required config key is missing.\n"
            "  What:     'line_item_column_names' is absent from the parsed field "
            "definitions, but line_item_rows needs it to name each exploded column.\n"
            "  Where:    config/field_definitions.yml, top-level key "
            "'line_item_column_names:'\n"
            "  Expected: a mapping from each plural grouped field to its singular "
            "column name, e.g.\n"
            "              line_item_column_names:\n"
            "                TRANSACTION_DATES: TRANSACTION_DATE\n"
            "  Recover:  add a 'line_item_column_names:' block to "
            "config/field_definitions.yml."
        )
    if "parallel_field_groups" not in definitions:
        raise ExtractionExportError(
            "Cannot write the line-item export: a required config key is missing.\n"
            "  What:     'parallel_field_groups' is absent from the parsed field "
            "definitions, but line_item_rows needs it to know which fields explode "
            "together per document type.\n"
            "  Where:    config/field_definitions.yml, top-level key "
            "'parallel_field_groups:'\n"
            "  Expected: a mapping from DOCUMENT_TYPE to a list of grouped-field "
            "lists, e.g.\n"
            "              parallel_field_groups:\n"
            "                BANK_STATEMENT:\n"
            "                  - [TRANSACTION_DATES, TRANSACTION_DESCRIPTIONS]\n"
            "  Recover:  add a 'parallel_field_groups:' block to "
            "config/field_definitions.yml."
        )

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


def export_extraction(
    *,
    corpus: Path,
    ground_truth_dir: Path,
    field_definitions: Path,
    target: Path,
    date_stamp: str,
) -> Path:
    """Write the extraction corpus beside an exported clean one.

    Args:
        corpus: An exported clean corpus (the `parsing_*` directory).
        ground_truth_dir: The directory holding `<doc_type>.yml`.
        field_definitions: `config/field_definitions.yml`.
        target: Directory to create the export inside.
        date_stamp: Corpus date, YYYYMMDD.

    Returns:
        The created export root.

    Raises:
        ExtractionExportError: A case is in the ground truth but not the
            corpus, or vice versa.
    """
    definitions = yaml.safe_load(field_definitions.read_text(encoding="utf-8"))
    columns = definitions["all_columns"]

    manifest = [
        json.loads(line)
        for line in (corpus / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    in_corpus = {Path(row["image"]).stem for row in manifest}

    rows: list[dict] = []
    item_rows: list[dict] = []
    for path in sorted(ground_truth_dir.glob("*.yml")):
        entries = load_ground_truth(path)
        rows += extraction_rows(entries, columns, path.stem)
        item_rows += line_item_rows(entries, path.stem, definitions)

    in_rows = {Path(row["image"]).stem for row in rows}
    if in_rows != in_corpus:
        missing = sorted(in_rows - in_corpus)
        extra = sorted(in_corpus - in_rows)
        raise ExtractionExportError(
            "Cannot write the extraction export: it disagrees with the clean corpus.\n"
            f"  What:     {len(missing)} case(s) in the ground truth are absent from the "
            f"corpus{': ' + ', '.join(missing[:5]) if missing else ''}; "
            f"{len(extra)} in the corpus are absent from the ground truth"
            f"{': ' + ', '.join(extra[:5]) if extra else ''}.\n"
            f"  Where:    {corpus / 'manifest.jsonl'} against {ground_truth_dir}\n"
            "  Expected: the two to describe the same set of cases — they are two "
            "projections of one authored truth, e.g. every CASE in ground_truth/"
            "invoices.yml having an images/CASE_invoices.png row in the manifest.\n"
            "  Recover:  re-run `python -m generators.pipeline generate` and `export` so "
            "the corpus covers every authored case, then export extraction again."
        )

    root = target / f"extraction_{date_stamp}"
    (root / "images").mkdir(parents=True, exist_ok=True)
    for row in rows:
        shutil.copy2(corpus / "images" / row["image"], root / "images" / row["image"])

    with (root / "ground_truth.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")

    with (root / "ground_truth.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    # JSONL only, deliberately. A row's columns differ per document type -- a
    # statement has TRANSACTION_BALANCE where an invoice has LINE_ITEM_QUANTITY
    # -- and JSONL rows need not share keys, so the two coexist in one file.
    # CSV would need a fixed header, forcing either one sparse table implying a
    # schema that does not exist, or a file per type.
    with (root / "line_items.jsonl").open("w", encoding="utf-8") as handle:
        for row in item_rows:
            handle.write(json.dumps(row) + "\n")

    return root
