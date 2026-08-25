"""The index that ties a clean corpus and its degraded tiers into one set.

Each tier is already a complete, independently scoreable export with its own
hashed manifest. What was missing is a statement that these seven directories
describe one run, so a comparison can iterate them without a human remembering
which belong together.

Deliberately imports nothing heavy. `generators/degradation/` is the one package
`docparse` cannot import, but that is because of augraphy, numpy and opencv --
none of which the index needs. Keeping this module light means the matrix can be
built and tested in `docparse`, where the rest of the config tests already run.
"""

import hashlib
import json
from pathlib import Path

_MATRIX_NAME = "matrix.jsonl"


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

    path = exports_dir / _MATRIX_NAME
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path
