"""Authored ground truth to the flat form an extraction application reads.

The fourth projection of one truth, alongside `serialise`'s Markdown,
`tables`' HTML and `layout`'s annotations. It renders nothing, derives
nothing and runs no extractor: this repository emits IE ground truth and
leaves extraction to whoever consumes it.

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
    columns = yaml.safe_load(field_definitions.read_text(encoding="utf-8"))["all_columns"]

    manifest = [
        json.loads(line)
        for line in (corpus / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    in_corpus = {Path(row["image"]).stem for row in manifest}

    rows: list[dict] = []
    for path in sorted(ground_truth_dir.glob("*.yml")):
        rows += extraction_rows(load_ground_truth(path), columns, path.stem)

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

    return root
