"""The index that ties a clean corpus and its degraded tiers into one set.

Each tier is already a complete, independently scoreable export with its own
hashed manifest. What was missing is a statement that these seven directories
describe one run, so a comparison can iterate them without a human remembering
which belong together.

Deliberately imports nothing heavy. `numpy` and `opencv` are only needed to
degrade a page, so the rest of `generators/degradation/` defers them to
function scope, keeping `config/degradation.yml` loadable without paying for
those imports (see `generators/degradation/__init__.py`); this module needs
neither, so it can be built and tested in `docparse` like the rest of the
config tests.
"""

import hashlib
import json
from pathlib import Path

_MATRIX_NAME = "matrix.jsonl"
_GROUND_TRUTH_NAME = "ground_truth.jsonl"


class MatrixError(RuntimeError):
    """Raised when a corpus cannot be indexed, or the set is incomplete."""


def _err(what: str, *, path: Path, key: str, expected: str, recover: str) -> MatrixError:
    """Build a four-element fail-fast diagnostic."""
    return MatrixError(
        "Cannot build the corpus matrix.\n"
        f"  What:     {what}\n"
        f"  Where:    {path} -> {key}\n"
        f"  Expected: {expected}\n"
        f"  Recover:  {recover}"
    )


def matrix_row(corpus_dir: Path, *, family: str, severity: str) -> dict:
    """Describe one exported corpus as a matrix row.

    Args:
        corpus_dir: An exported corpus directory holding `manifest.jsonl`.
        family: Intake family, or `clean` for the undegraded baseline.
        severity: Tier severity, or `none` for the baseline.

    Returns:
        `{corpus, family, severity, pages, doc_types, manifest_sha256}`.

    Raises:
        MatrixError: The directory holds no manifest.
    """
    manifest = corpus_dir / "manifest.jsonl"
    if not manifest.exists():
        raise _err(
            f"{manifest.name} does not exist, so {corpus_dir.name} is not an export.",
            path=corpus_dir.resolve(),
            key="manifest.jsonl",
            expected="a directory written by `export` or by `degrade`, holding images/, "
            "transcripts/ and manifest.jsonl.",
            recover="run `python -m generators.pipeline export` first, or drop this "
            "directory from the run.",
        )

    records = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line]
    return {
        "corpus": corpus_dir.name,
        "family": family,
        "severity": severity,
        "pages": len(records),
        "doc_types": sorted({str(r["doc_type"]) for r in records}),
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
    }


def write_matrix(rows: list[dict], exports_dir: Path) -> Path:
    """Write the matrix index beside the corpora it describes.

    Args:
        rows: Rows from `matrix_row`, in the order they should be listed.
        exports_dir: The directory holding the corpora.

    Returns:
        The path written.

    Raises:
        MatrixError: No row describes the clean baseline.
    """
    if not any(row["family"] == "clean" for row in rows):
        raise _err(
            "no row has family 'clean', so the set has no undegraded baseline.",
            path=exports_dir.resolve(),
            key=_MATRIX_NAME,
            expected="one row per corpus INCLUDING the clean export, e.g.\n"
            '              {"corpus": "parsing_20260825", "family": "clean", '
            '"severity": "none", ...}',
            recover="include the clean corpus in the run; without it a comparison "
            "cannot separate a weak model from one the degradation hurt.",
        )

    for row in rows:
        if not (exports_dir / row["corpus"]).is_dir():
            raise _err(
                f"row '{row['corpus']}' names a directory that does not exist beside "
                f"the matrix at {exports_dir.resolve()}.",
                path=exports_dir.resolve(),
                key=row["corpus"],
                expected="every corpus a matrix indexes to be a directory beside "
                f"matrix.jsonl, e.g.\n              {exports_dir.resolve()}/{row['corpus']}",
                recover="write the matrix into the same directory as the corpora it "
                "indexes, e.g. pass --out pointing at the corpora's parent, or move "
                f"{row['corpus']!r} beside the matrix.",
            )

    path = exports_dir / _MATRIX_NAME
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def ground_truth_rows(corpus_dir: Path) -> list[dict]:
    """One labelled row per image in a corpus, for the identification task.

    Everything is read from the corpus's own manifest -- the page list AND the
    labels. The labels are deliberately not parameters: they already live on
    every manifest record, and taking them from the caller instead would create
    two representations of one fact that could silently disagree.

    Args:
        corpus_dir: An exported or degraded corpus holding `manifest.jsonl`.

    Returns:
        One dict per image, keyed `corpus`, `image`, `transcript`, `case_id`,
        `doc_type`, `family`, `severity`. `transcript` rides along because a
        degraded page's transcription label is the clean transcript, so one file
        serves both the identification and transcription tasks.

    Raises:
        MatrixError: The directory holds no manifest, or a record predates the
            labels.
    """
    manifest = corpus_dir / "manifest.jsonl"
    if not manifest.exists():
        raise _err(
            f"{manifest.name} does not exist, so {corpus_dir.name} is not an export.",
            path=corpus_dir.resolve(),
            key="manifest.jsonl",
            expected="a directory written by `export` or by `degrade`, holding images/, "
            "transcripts/ and manifest.jsonl.",
            recover="run `python -m generators.pipeline export` first, or drop this "
            "directory from the run.",
        )

    rows = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        record = json.loads(line)

        missing = [key for key in ("family", "severity") if key not in record]
        if missing:
            raise _err(
                f"a record in {corpus_dir.name}/manifest.jsonl has no "
                f"{', '.join(missing)}, so the page cannot be labelled.",
                path=manifest.resolve(),
                key=", ".join(missing),
                expected="every manifest record to carry family and severity, e.g.\n"
                '              {"image": "images/CASE001_invoices.jpg", '
                '"family": "scan", "severity": "moderate", ...}',
                recover="re-run `export` and `degrade` with the current code; a corpus "
                "written before 2026-09-01 predates these fields.",
            )

        doc_type = str(record["doc_type"])
        stem = Path(record["image"]).stem
        # Filenames are {case_id}_{doc_type}; strip the suffix rather than
        # splitting on "_", which would truncate `bank_statements`.
        suffix = f"_{doc_type}"
        if not stem.endswith(suffix):
            raise _err(
                f"image stem '{stem}' in {corpus_dir.name}/manifest.jsonl does not end in "
                f"'{suffix}', so no case_id can be recovered from it against doc_type "
                f"'{doc_type}'.",
                path=manifest.resolve(),
                key="image",
                expected="every image path to be {case_id}_{doc_type}.<ext>, matching its "
                "record's doc_type, e.g.\n"
                '              {"image": "images/CASE001_invoices.png", '
                '"doc_type": "invoices", ...}',
                recover="fix the manifest record's image path or doc_type so they agree, "
                "or re-run `export` and `degrade` to regenerate a consistent manifest.",
            )
        case_id = stem[: -len(suffix)]
        rows.append(
            {
                "corpus": corpus_dir.name,
                "image": record["image"],
                "transcript": record["transcript"],
                "case_id": case_id,
                "doc_type": doc_type,
                "family": record["family"],
                "severity": record["severity"],
            }
        )
    return rows


def assert_matched_pages(rows: list[dict], exports_dir: Path) -> None:
    """Verify every family/severity group in a pooled ground truth covers the same pages.

    The pooled file assumes a comparison across tiers isolates image quality: a
    row's `family`/`severity` names which corpus produced it, and that only
    isolates image quality if every corpus renders the same `(case_id,
    doc_type)` pages. Nothing checked that before this -- a caller that filters
    the clean corpus differently from the tiers (e.g. by re-reading its whole
    manifest instead of the run's own `--type`/`--limit` selection) would pool
    mismatched page counts silently.

    Args:
        rows: Rows from `ground_truth_rows`, pooled across every corpus.
        exports_dir: The directory the pooled file is written into, named in
            the diagnostic.

    Raises:
        MatrixError: Some family/severity group covers a different set of
            `(case_id, doc_type)` pages than another.
    """
    grouped: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for row in rows:
        key = (str(row["family"]), str(row["severity"]))
        grouped.setdefault(key, set()).add((str(row["case_id"]), str(row["doc_type"])))

    if len(grouped) <= 1:
        return

    (reference_key, reference_pages), *rest = grouped.items()
    for key, pages in rest:
        if pages != reference_pages:
            raise _err(
                f"{key[0]}-{key[1]} covers {len(pages)} page(s), but {reference_key[0]}-"
                f"{reference_key[1]} covers {len(reference_pages)}, so the pooled ground "
                "truth does not describe one matched page set across corpora.",
                path=exports_dir.resolve(),
                key=_GROUND_TRUTH_NAME,
                expected="every family/severity group to cover the same (case_id, doc_type) "
                "pages as every other, since a score comparison across tiers assumes only "
                "image quality differs.",
                recover="build the pooled rows from the same page selection for every "
                "corpus -- restrict the clean corpus to the pages the degraded corpora "
                "actually cover, rather than its whole manifest.",
            )


def write_ground_truth(rows: list[dict], exports_dir: Path) -> Path:
    """Write the pooled identification ground truth beside the matrix.

    Args:
        rows: Rows from `ground_truth_rows`, in the order they should be listed.
        exports_dir: The directory holding the corpora and `matrix.jsonl`.

    Returns:
        The path written.

    Raises:
        MatrixError: No row describes the clean baseline, or a row names a corpus
            directory that is not beside `exports_dir`.
    """
    if not any(row["family"] == "clean" for row in rows):
        raise _err(
            "no row has family 'clean', so the set has no undegraded baseline and a "
            "model cannot be scored on recognising one.",
            path=exports_dir.resolve(),
            key=_GROUND_TRUTH_NAME,
            expected="rows covering every corpus INCLUDING the clean export, e.g.\n"
            '              {"corpus": "parsing_20260901", "family": "clean", '
            '"severity": "none", ...}',
            recover="include the clean corpus in the run; without it 'clean' is a class "
            "no page demonstrates.",
        )

    for row in rows:
        if not (exports_dir / row["corpus"]).is_dir():
            raise _err(
                f"row '{row['corpus']}' names a directory that does not exist beside "
                f"the pooled ground truth at {exports_dir.resolve()}.",
                path=exports_dir.resolve(),
                key=row["corpus"],
                expected="every corpus a pooled row references to be a directory beside "
                f"{_GROUND_TRUTH_NAME}, since `image` and `transcript` resolve as "
                f"exports_dir/corpus/image, e.g.\n"
                f"              {exports_dir.resolve()}/{row['corpus']}",
                recover="write the pooled ground truth into the same directory as the "
                "corpora it indexes, e.g. pass --out pointing at the corpora's parent, "
                f"or move {row['corpus']!r} beside it.",
            )

    path = exports_dir / _GROUND_TRUTH_NAME
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path
