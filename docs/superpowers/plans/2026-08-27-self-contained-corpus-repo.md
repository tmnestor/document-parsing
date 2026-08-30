# Self-Contained Corpus Repository Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make this repository something a third party can clone and run with one command, producing invoices, receipts and bank statements, their degradations, and IE ground truth.

**Architecture:** Collapse the two conda environments into one, replacing the `environment.yml` grep that kept them apart with tests that parse the code for the property it only approximated. Add `generators/extraction_export.py` as a fourth projection of the same authored truth — flat images plus `ground_truth.{jsonl,csv}`, no extractor. Replace the stale `regenerate_bank_statements.sh` with `build_corpus.sh`, which sets up the environment, runs the whole chain for all three document types, and refuses to overwrite an existing run.

**Tech Stack:** Python 3.12, Pillow, PyYAML, typer, rich, Faker, numpy, opencv-python-headless, augraphy. Conda. pytest.

**Spec:** `docs/superpowers/specs/2026-08-27-self-contained-corpus-repo-design.md`

## Global Constraints

- **Environment:** run everything through `conda run -n docparse <command>`, **from the repository root** — several modules resolve config with CWD-relative paths.
- **`tests/` is gitignored.** Write and run tests; **never `git add` anything under `tests/`**.
- **Never bypass pre-commit hooks** (`--no-verify` is forbidden). Every commit must pass: `pytest tests/ --ignore=tests/scoring`, `ruff check --fix --ignore ARG001,ARG002,F841 .`, `ruff format .`, `mypy generators --ignore-missing-imports`.
- `tests/scoring/` fails to collect in `docparse` (`ModuleNotFoundError: rapidfuzz`). Pre-existing and unrelated — always pass `--ignore=tests/scoring`.
- **Coverage floor is 80%.**
- **Line length 108.** Python 3.12 type hints (`X | Y`, no `from __future__ import annotations`, no `TYPE_CHECKING` guards for runtime signatures).
- **Fail-fast diagnostics use the four-element shape** — What / Where / Expected / Recover — asserted with `assert_diagnostic_error` from `tests/helpers.py`.
- **In `except` blocks always `raise ... from err` or `from None`** (B904).
- **Never write "ATO"** — use "PROD".
- **No Claude attribution in commit messages.** Gitmoji prefix (`✨ feat:`, `♻️ refactor:`, `📝 docs:`, `🔥 remove:`).
- **No image may move and no transcript may change.** This adds no page and revises no page. `tests/test_corpus_unchanged.py` and `test_the_same_input_renders_byte_identical_images` must stay green throughout.
- **Pins are exact, not preferences.** Degraded images are corpus data whose hashes go in a manifest; a version bump that moves one pixel invalidates every prediction against the old images.

---

## File Structure

| File | Responsibility after this plan |
|---|---|
| `environment.yml` | The single environment: the five pure-Python packages plus numpy, opencv-python-headless and augraphy's real transitive deps. |
| `environment-degrade.yml` | **Deleted.** Its contents merge into `environment.yml`. |
| `generators/extraction_export.py` | **New.** Ground truth + manifest → `ground_truth.jsonl`, `ground_truth.csv`, flat images. Renders nothing, runs no extractor. |
| `generators/pipeline.py` | Gains an `extract` command wrapping the above, so the CLI stays the single Python entry point. |
| `build_corpus.sh` | **New.** Environment setup → validate → generate → serialise → export → degrade → extract. |
| `regenerate_bank_statements.sh` | **Deleted.** Drives the dead `evaluation.*` package; superseded. |
| `make_degraded_statements.sh` | Unchanged. Already calls the current module. |
| `README.md` | Rewritten as orientation for someone who has just cloned. |
| `CLAUDE.md` | Scope wording: "we emit IE ground truth; we run no extractor". |
| `tests/test_env.py` | The `environment.yml` grep replaced by two tests that parse the code. |

---

## Task 1: One environment

Merge the two environment files and replace the guard that kept them apart. This task changes no behaviour — it changes what must be installed and what is asserted about the code.

**Files:**
- Modify: `environment.yml`
- Delete: `environment-degrade.yml`
- Test: `tests/test_env.py`

**Interfaces:**
- Consumes: nothing.
- Produces: a `docparse` environment in which `import cv2, augraphy, numpy` succeeds.

- [ ] **Step 1: Write the failing tests**

Replace `test_no_forbidden_dependencies` in `tests/test_env.py` with:

```python
import ast
from pathlib import Path

# Modules whose weight is the reason `generators/` is worth keeping light.
HEAVY = {"numpy", "cv2", "augraphy", "numba", "skimage", "sklearn"}


def _imported_roots(tree: ast.AST) -> set[str]:
    """Every top-level module name imported anywhere in a parsed module."""
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_the_core_generator_imports_nothing_heavy():
    """`generators/` must stay importable from a checkout with nothing heavy
    installed.

    Replaces a guard that grepped environment.yml for the strings "numpy",
    "opencv" and "augraphy". That guard asserted nothing about the code —
    generators/ could import numpy throughout and it would still have passed,
    because it only read a YAML file's wording. Parsed with `ast` rather than
    grepped, so an aliased or re-exported import cannot slip past.
    """
    offenders = []
    for path in sorted(Path("generators").rglob("*.py")):
        if "degradation" in path.parts:
            continue
        roots = _imported_roots(ast.parse(path.read_text(encoding="utf-8")))
        offenders += [(str(path), name) for name in sorted(roots & HEAVY)]

    assert not offenders, (
        f"heavy imports outside generators/degradation/: {offenders}. "
        "The core generator must stay importable without them."
    )


def test_degradation_defers_its_heavy_imports():
    """`config/degradation.yml` must stay loadable, and validatable, from a
    checkout with no numpy — which is why `generators/degradation/__init__.py`
    keeps its heavy imports inside functions rather than at module level.
    """
    tree = ast.parse(Path("generators/degradation/__init__.py").read_text(encoding="utf-8"))
    module_level = _imported_roots(
        ast.Module(
            body=[n for n in tree.body if isinstance(n, ast.Import | ast.ImportFrom)],
            type_ignores=[],
        )
    )

    assert not module_level & HEAVY, (
        f"{sorted(module_level & HEAVY)} imported at module level in "
        "generators/degradation/__init__.py — config/degradation.yml would stop "
        "being loadable without them installed."
    )
```

- [ ] **Step 2: Run them to see them pass, then prove they can fail**

Run: `conda run -n docparse pytest tests/test_env.py -q`
Expected: PASS — the property already holds.

A test that has never been seen to fail is not yet a test. Temporarily add `import numpy` to the top of `generators/loader.py` and re-run.
Expected: FAIL, naming `generators/loader.py` and `numpy`. **Remove the import** and confirm PASS again.

Then temporarily move `generators/degradation/__init__.py`'s `import numpy as np` (currently inside a function at line 92) to module level and re-run.
Expected: `test_degradation_defers_its_heavy_imports` FAILS. **Move it back** and confirm PASS.

- [ ] **Step 3: Merge the environment files**

Rewrite `environment.yml`:

```yaml
# One environment. Everything the corpus needs: rendering, degradation, tests.
#
# AUGRAPHY IS NOT LISTED HERE. It declares `opencv-python` — the full GUI
# build — as a hard requirement, which would displace the headless build
# pinned below. Both provide `cv2`, and having both installed is a coin toss
# over which one wins: 2 of 9 degraded images come out different, and
# generators/degradation/geometry.py refuses to run in that state rather than
# writing a corpus that will not reproduce. `build_corpus.sh` installs it with
# --no-deps and verifies the result; conda's pip: section applies flags to the
# whole invocation, so it cannot be scoped to one package here.
#
# PINS ARE EXACT, NOT PREFERENCES. Degraded images are corpus data: their
# hashes go in a manifest, and a version bump that shifts one pixel
# invalidates every prediction made against the old images. numpy is
# additionally capped by numba, which augraphy imports.
name: docparse

channels:
  - conda-forge

dependencies:
  - python=3.12
  - pip
  - pip:
      - Pillow==12.2.0
      - PyYAML==6.0.3
      - typer==0.27.1
      - rich==14.3.0
      - faker

      # Degradation. augraphy itself is installed by build_corpus.sh.
      - numpy==2.3.5
      - opencv-python-headless==4.13.0.92
      - numba==0.66.0
      - scikit-image
      - scikit-learn

      # Tooling.
      - pytest
      - pytest-cov
      - ruff
      - mypy
```

Then delete `environment-degrade.yml`.

- [ ] **Step 4: Verify the merged environment actually works**

```bash
conda env update -f environment.yml --prune
conda run -n docparse pip install --no-deps augraphy==8.2.6
conda run -n docparse python -c "import cv2, augraphy, numpy; print(cv2.__version__, augraphy.__version__)"
conda run -n docparse pip list | grep -ci '^opencv-python '
```

Expected: the import line prints both versions, and the `grep -c` prints `0` — only `opencv-python-headless` is installed. If it prints `1`, run `pip uninstall -y opencv-python` and re-install augraphy with `--no-deps`.

- [ ] **Step 5: Run the full suite in the merged environment**

Run: `conda run -n docparse pytest tests/ --ignore=tests/scoring -q`
Expected: PASS. `tests/test_degradation.py` previously skipped when augraphy was absent; it now runs for real. If it fails, stop and report — a degradation test failing in the merged environment means the merge changed which cv2 is resolved.

- [ ] **Step 6: Commit**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
git add environment.yml
git rm environment-degrade.yml
git commit -m "♻️ refactor: collapse the two environments into one

The guard that kept them apart grepped environment.yml for the strings
numpy, opencv and augraphy, so it asserted nothing about the code:
generators/ could import numpy throughout and it would still pass. Its
justification cites a runners/ package this repo does not have, inherited
from the predecessor like the dead evaluation.cli in the regenerate
script.

Replaced by two tests that parse the code for the property that actually
matters: no heavy import outside generators/degradation/, and
degradation's own heavy imports staying inside functions so
config/degradation.yml remains loadable without them.

augraphy still needs --no-deps; build_corpus.sh does it and verifies,
rather than a README instruction a human can skip."
```

---

## Task 2: The IE extraction export

A fourth projection of the same authored truth. It reads `ground_truth/*.yml` and a clean export's manifest, and writes flat images plus `ground_truth.jsonl` and `ground_truth.csv`.

**Files:**
- Create: `generators/extraction_export.py`
- Test: `tests/test_extraction_export.py`

**Interfaces:**
- Consumes: `generators.loader.load_ground_truth(path: Path) -> dict`.
- Produces:
  - `generators.extraction_export.ExtractionExportError(RuntimeError)`
  - `generators.extraction_export.extraction_rows(ground_truth: dict[str, dict], columns: list[str], doc_type: str) -> list[dict]`
  - `generators.extraction_export.export_extraction(*, corpus: Path, ground_truth_dir: Path, field_definitions: Path, target: Path, date_stamp: str) -> Path` — keyword-only, since five same-typed paths in a row are trivial to transpose positionally

- [ ] **Step 1: Write the failing tests**

Create `tests/test_extraction_export.py`:

```python
"""The IE projection: flat images beside one row per case.

This repo emits IE ground truth and runs no extractor. The rows must agree
with the clean corpus exactly — a case in one and not the other means two
projections of one truth have drifted.
"""

import csv
import json

import pytest

from generators.extraction_export import (
    ExtractionExportError,
    export_extraction,
    extraction_rows,
)
from tests.helpers import assert_diagnostic_error

COLUMNS = ["DOCUMENT_TYPE", "SUPPLIER_NAME", "TOTAL_AMOUNT", "ACCOUNT_BALANCE"]

GROUND_TRUTH = {
    "CASE001": {
        "layout": "tax_invoice_standard",
        "fields": {
            "DOCUMENT_TYPE": "INVOICE",
            "SUPPLIER_NAME": "Kirkbride Joinery",
            "TOTAL_AMOUNT": "157.39",
            "LINE_ITEM_CATEGORIES": "Labour|Labour",
        },
    },
    "CASE002": {
        "layout": "tax_invoice_standard",
        "fields": {"DOCUMENT_TYPE": "INVOICE", "SUPPLIER_NAME": "Amberley Roofing"},
    },
}


def test_one_row_per_case_in_column_order():
    rows = extraction_rows(GROUND_TRUTH, COLUMNS, "invoices")

    assert [row["case_id"] for row in rows] == ["CASE001", "CASE002"]
    assert list(rows[0]) == ["case_id", "doc_type", "image", *COLUMNS]
    assert rows[0]["doc_type"] == "invoices"
    assert rows[0]["image"] == "CASE001_invoices.png"


def test_an_absent_field_uses_the_corpus_sentinel():
    """NOT_FOUND is the convention the corpus already uses for an absent value."""
    rows = extraction_rows(GROUND_TRUTH, COLUMNS, "invoices")

    assert rows[0]["ACCOUNT_BALANCE"] == "NOT_FOUND"
    assert rows[1]["TOTAL_AMOUNT"] == "NOT_FOUND"


def test_layout_only_fields_are_excluded():
    """LINE_ITEM_CATEGORIES belongs to one layout, so it is NOT_FOUND on almost
    every row. `all_columns` omits it and so does this."""
    rows = extraction_rows(GROUND_TRUTH, COLUMNS, "invoices")

    assert "LINE_ITEM_CATEGORIES" not in rows[0]


def test_a_case_missing_from_the_corpus_fails_fast(tmp_path):
    """Two projections of one truth must not disagree about which cases exist."""
    corpus = _corpus(tmp_path, cases=["CASE001"])
    gt_dir = _ground_truth_dir(tmp_path, {"invoices": GROUND_TRUTH})

    with pytest.raises(ExtractionExportError) as excinfo:
        export_extraction(
            corpus=corpus,
            ground_truth_dir=gt_dir,
            field_definitions=_field_definitions(tmp_path, COLUMNS),
            target=tmp_path / "out",
            date_stamp="20260827",
        )

    assert_diagnostic_error(str(excinfo.value), mentions=("CASE002",))


def test_the_jsonl_and_csv_agree_row_for_row(tmp_path):
    corpus = _corpus(tmp_path, cases=["CASE001", "CASE002"])
    root = export_extraction(
        corpus=corpus,
        ground_truth_dir=_ground_truth_dir(tmp_path, {"invoices": GROUND_TRUTH}),
        field_definitions=_field_definitions(tmp_path, COLUMNS),
        target=tmp_path / "out",
        date_stamp="20260827",
    )

    lines = [json.loads(line) for line in (root / "ground_truth.jsonl").read_text().splitlines()]
    with (root / "ground_truth.csv").open(encoding="utf-8") as handle:
        csv_rows = list(csv.DictReader(handle))

    assert lines == csv_rows
    assert len(lines) == 2


def test_images_are_copied_flat(tmp_path):
    """An IE consumer indexes by case_id and should not need to know this
    corpus's internal per-type layout."""
    corpus = _corpus(tmp_path, cases=["CASE001", "CASE002"])
    root = export_extraction(
        corpus=corpus,
        ground_truth_dir=_ground_truth_dir(tmp_path, {"invoices": GROUND_TRUTH}),
        field_definitions=_field_definitions(tmp_path, COLUMNS),
        target=tmp_path / "out",
        date_stamp="20260827",
    )

    names = sorted(p.name for p in (root / "images").glob("*.png"))
    assert names == ["CASE001_invoices.png", "CASE002_invoices.png"]
    assert not any(p.is_dir() for p in (root / "images").iterdir())


# --- fixtures -----------------------------------------------------------------


def _corpus(tmp_path, cases):
    """A minimal exported clean corpus: images plus a manifest."""
    root = tmp_path / "parsing_20260827"
    (root / "images").mkdir(parents=True)
    with (root / "manifest.jsonl").open("w", encoding="utf-8") as handle:
        for case in cases:
            name = f"{case}_invoices.png"
            (root / "images" / name).write_bytes(b"pixels")
            handle.write(
                json.dumps(
                    {"image": f"images/{name}", "doc_type": "invoices", "sha256": "x"}
                )
                + "\n"
            )
    return root


def _ground_truth_dir(tmp_path, by_type):
    import yaml

    directory = tmp_path / "ground_truth"
    directory.mkdir(exist_ok=True)
    for doc_type, entries in by_type.items():
        (directory / f"{doc_type}.yml").write_text(yaml.safe_dump(entries), encoding="utf-8")
    return directory


def _field_definitions(tmp_path, columns):
    import yaml

    path = tmp_path / "field_definitions.yml"
    path.write_text(yaml.safe_dump({"all_columns": columns}), encoding="utf-8")
    return path
```

- [ ] **Step 2: Run to verify they fail**

Run: `conda run -n docparse pytest tests/test_extraction_export.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'generators.extraction_export'`.

- [ ] **Step 3: Implement**

Create `generators/extraction_export.py`:

```python
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
```

- [ ] **Step 4: Run to verify they pass**

Run: `conda run -n docparse pytest tests/test_extraction_export.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full suite and the gates**

```bash
conda run -n docparse pytest tests/ --ignore=tests/scoring -q
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
```

- [ ] **Step 6: Commit**

```bash
git add generators/extraction_export.py
git commit -m "✨ feat: emit IE ground truth as a fourth projection

Flat images beside ground_truth.jsonl and ground_truth.csv, so an
extraction application can consume this corpus without writing its own
converter from ground_truth/*.yml.

The column order comes from field_definitions.yml's all_columns, already
documented there as 'the order the derived CSV writes them' — the schema
outlived the writer. layout_fields entries stay out: a column one layout
carries would be NOT_FOUND on almost every row.

This repo emits IE ground truth and runs no extractor."
```

---

## Task 3: Wire it into the CLI

The pipeline is the single Python entry point; extraction should not be the one step that requires a different invocation.

**Files:**
- Modify: `generators/pipeline.py` (add a command beside `export`, which begins at line 431)
- Test: `tests/test_pipeline_extract.py`

**Interfaces:**
- Consumes: `generators.extraction_export.export_extraction` (Task 2).
- Produces: `python -m generators.pipeline extract --corpus <dir> --target <dir> [--date YYYYMMDD]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_pipeline_extract.py`:

```python
"""`extract` is a pipeline command like the others, not a separate entry point."""

import json

from typer.testing import CliRunner

from generators.pipeline import app


def test_extract_writes_an_extraction_corpus(tmp_path, monkeypatch):
    corpus = tmp_path / "parsing_20260827"
    (corpus / "images").mkdir(parents=True)
    (corpus / "images" / "CASE001_invoices.png").write_bytes(b"pixels")
    (corpus / "manifest.jsonl").write_text(
        json.dumps({"image": "images/CASE001_invoices.png", "doc_type": "invoices"}) + "\n",
        encoding="utf-8",
    )

    ground_truth = tmp_path / "ground_truth"
    ground_truth.mkdir()
    (ground_truth / "invoices.yml").write_text(
        "CASE001:\n  layout: tax_invoice_standard\n  fields:\n    DOCUMENT_TYPE: INVOICE\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "extract",
            "--corpus", str(corpus),
            "--ground-truth", str(ground_truth),
            "--target", str(tmp_path / "out"),
            "--date", "20260827",
        ],
    )

    assert result.exit_code == 0, result.output
    root = tmp_path / "out" / "extraction_20260827"
    assert (root / "ground_truth.jsonl").exists()
    assert (root / "ground_truth.csv").exists()
    assert (root / "images" / "CASE001_invoices.png").exists()


def test_extract_reports_a_disagreement_without_a_traceback(tmp_path):
    """A four-element diagnostic, not a stack trace, is what a user should see."""
    corpus = tmp_path / "parsing_20260827"
    (corpus / "images").mkdir(parents=True)
    (corpus / "manifest.jsonl").write_text("", encoding="utf-8")

    ground_truth = tmp_path / "ground_truth"
    ground_truth.mkdir()
    (ground_truth / "invoices.yml").write_text(
        "CASE001:\n  layout: tax_invoice_standard\n  fields:\n    DOCUMENT_TYPE: INVOICE\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "extract",
            "--corpus", str(corpus),
            "--ground-truth", str(ground_truth),
            "--target", str(tmp_path / "out"),
            "--date", "20260827",
        ],
    )

    assert result.exit_code == 1
    assert "CASE001" in result.output
    assert "Traceback" not in result.output
```

- [ ] **Step 2: Run to verify it fails**

Run: `conda run -n docparse pytest tests/test_pipeline_extract.py -q`
Expected: FAIL — `extract` is not a command (typer exits 2, "No such command").

- [ ] **Step 3: Implement**

Add to `generators/pipeline.py`, after the `export` command. Match the surrounding commands' style for options and error reporting — read `export` (line 431) first and follow it rather than inventing a second convention.

```python
@app.command()
def extract(
    corpus: Annotated[Path, typer.Option("--corpus", help="An exported clean corpus.")],
    target: Annotated[Path, typer.Option("--target", help="Where the extraction corpus is written.")],
    ground_truth: Annotated[
        Path, typer.Option("--ground-truth", help="Directory of <doc_type>.yml files.")
    ] = Path("ground_truth"),
    field_definitions: Annotated[
        Path, typer.Option("--field-definitions", help="Path to field_definitions.yml.")
    ] = Path("config/field_definitions.yml"),
    date: Annotated[str | None, typer.Option("--date", help="Corpus date, YYYYMMDD.")] = None,
) -> None:
    """Write the flat images and ground_truth.{jsonl,csv} an extractor reads."""
    from generators.extraction_export import ExtractionExportError, export_extraction

    stamp = date or datetime.now().strftime("%Y%m%d")
    try:
        root = export_extraction(
            corpus=corpus,
            ground_truth_dir=ground_truth,
            field_definitions=field_definitions,
            target=target,
            date_stamp=stamp,
        )
    except ExtractionExportError as err:
        typer.echo(str(err), err=True)
        raise typer.Exit(1) from None

    rows = sum(1 for _ in (root / "ground_truth.jsonl").open(encoding="utf-8"))
    typer.echo(f"Extraction corpus: {root} ({rows} case(s)).")
```

If `datetime` is not already imported in `pipeline.py`, add `from datetime import datetime` beside the existing imports; if the module already derives a default date stamp for `export`, reuse that helper instead of adding a second one.

- [ ] **Step 4: Run to verify it passes**

Run: `conda run -n docparse pytest tests/test_pipeline_extract.py -q`
Expected: PASS.

- [ ] **Step 5: Full suite, gates, commit**

```bash
conda run -n docparse pytest tests/ --ignore=tests/scoring -q
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
git add generators/pipeline.py
git commit -m "✨ feat: add the extract command to the pipeline CLI

Extraction should not be the one step needing a different invocation."
```

---

## Task 4: `build_corpus.sh`

The front door. One command from a fresh clone to every artifact.

**Files:**
- Create: `build_corpus.sh`
- Delete: `regenerate_bank_statements.sh`
- Test: `tests/test_build_script.py`

**Interfaces:**
- Consumes: the `extract` command (Task 3), the merged environment (Task 1).
- Produces: `evaluation_data/corpus_<stamp>/{parsing_<stamp>,degraded,extraction_<stamp>}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_build_script.py`. This tests the script's *contracts* — the parts that silently ruin a corpus if wrong — without running the full chain, which is far too slow for a unit test.

```python
"""Contracts of the build script that a wrong edit would silently break."""

from pathlib import Path

SCRIPT = Path("build_corpus.sh")
TEXT = SCRIPT.read_text(encoding="utf-8")


def test_the_script_exists_and_is_executable():
    assert SCRIPT.exists()
    assert SCRIPT.stat().st_mode & 0o111, "build_corpus.sh must be executable"


def test_it_drives_the_current_cli():
    """The script it replaces called `evaluation.cli`, a package that has not
    existed in this repo for its whole history."""
    assert "generators.pipeline" in TEXT
    assert "evaluation.cli" not in TEXT
    assert "evaluation.eval_export_cli" not in TEXT


def test_it_generates_every_document_type():
    """Its predecessor rendered only bank statements while exporting all pages,
    so a full run silently shipped stale images for the other two types."""
    assert "--type bank_statements" not in TEXT, (
        "generate must not be restricted to one document type"
    )


def test_it_refuses_to_overwrite_an_existing_run():
    """A corpus is identified by the hashes in its manifest; writing over one
    leaves predictions already scored pointing at images that no longer exist."""
    assert "already exists" in TEXT


def test_it_verifies_the_opencv_build_rather_than_instructing_a_human():
    """Both cv2 builds installed is a coin toss over which wins, and 2 of 9
    degraded images come out different."""
    assert "--no-deps" in TEXT
    assert "opencv-python-headless" in TEXT or "opencv-python " in TEXT


def test_it_runs_every_stage():
    for stage in ("validate", "generate", "serialise", "export", "degradation.cli", "extract"):
        assert stage in TEXT, f"build_corpus.sh does not run {stage}"
```

- [ ] **Step 2: Run to verify it fails**

Run: `conda run -n docparse pytest tests/test_build_script.py -q`
Expected: FAIL — `build_corpus.sh` does not exist.

- [ ] **Step 3: Write the script**

Create `build_corpus.sh` and `chmod +x` it. Read `regenerate_bank_statements.sh` first and keep its two good properties — the overwrite refusal and resolving paths from the script's own location — while fixing what it got wrong.

```bash
#!/usr/bin/env bash
# Build the whole corpus: clean pages, degradations, and IE ground truth.
#
# Everything is derived from files in this repository -- the authored ground
# truth, the layouts, the data pools, the fonts and the degradation ladder. No
# image is stored in git and none needs to be: every step is seeded, so this
# reproduces a corpus BYTE FOR BYTE rather than producing an equivalent one.
#
#   ./build_corpus.sh              everything
#   DEGRADE=no ./build_corpus.sh   clean corpus and extraction only (much faster)
#
set -uo pipefail

ENV_NAME=${ENV_NAME:-docparse}
DATE_STAMP=${DATE_STAMP:-$(date +%Y%m%d)}
DEGRADE=${DEGRADE:-yes}
AUGRAPHY_VERSION=8.2.6

# Derived from the SCRIPT's location, not the working directory, so it resolves
# the same however the script is invoked.
REPO_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
EVAL_ROOT=${EVAL_ROOT:-$(cd -- "$REPO_DIR/.." && pwd)/evaluation_data}
TARGET="$EVAL_ROOT/corpus_$DATE_STAMP"

fail() { echo "!! $*" >&2; exit 1; }
step() { echo; echo "=== $* ==="; }

cd "$REPO_DIR" || fail "cannot enter $REPO_DIR"
command -v conda >/dev/null || fail "conda is not on PATH"

# Refuse to overwrite. A corpus is identified by the hashes in its manifest,
# and quietly writing over one would leave any predictions already scored
# against it pointing at images that no longer exist.
[[ -e $TARGET ]] && fail "$TARGET already exists.
   Use it as it stands, pass DATE_STAMP= for a new directory, or remove it
   deliberately."

step "environment"
if ! conda env list | grep -qE "^${ENV_NAME}\s"; then
    echo "creating '$ENV_NAME'"
    conda env create -f environment.yml || fail "could not create '$ENV_NAME'"
fi

# augraphy declares the GUI opencv-python as a hard requirement, which would
# displace the headless build. Both provide cv2, and having both installed is a
# coin toss over which one wins -- 2 of 9 degraded images come out different,
# and generators/degradation/geometry.py refuses to run in that state rather
# than writing a corpus that will not reproduce. So: install it without its
# dependencies, then VERIFY, rather than telling a human to check.
if ! conda run -n "$ENV_NAME" python -c 'import augraphy' >/dev/null 2>&1; then
    echo "installing augraphy==$AUGRAPHY_VERSION --no-deps"
    conda run -n "$ENV_NAME" pip install --no-deps "augraphy==$AUGRAPHY_VERSION" ||
        fail "could not install augraphy"
fi
gui=$(conda run -n "$ENV_NAME" pip list 2>/dev/null | grep -ci '^opencv-python ' || true)
[[ $gui -eq 0 ]] || fail "both opencv builds are installed in '$ENV_NAME'.
   cv2 would resolve unpredictably and degraded images would not reproduce.
   Fix:  conda run -n $ENV_NAME pip uninstall -y opencv-python
         conda run -n $ENV_NAME pip install --no-deps augraphy==$AUGRAPHY_VERSION"
conda run -n "$ENV_NAME" python -c 'import cv2, augraphy, numpy' ||
    fail "'$ENV_NAME' cannot import the degradation stack"

mkdir -p "$TARGET" || fail "cannot create $TARGET"
echo "destination: $TARGET"

step "validate — ground truth, layouts, fit budgets"
conda run -n "$ENV_NAME" python -m generators.pipeline validate ||
    fail "validation failed; fix the ground truth or layouts before rendering"

step "generate — page images and draw-time transcript events"
conda run -n "$ENV_NAME" python -m generators.pipeline generate || fail "generation failed"

step "serialise — events to Markdown"
conda run -n "$ENV_NAME" python -m generators.pipeline serialise || fail "serialisation failed"

step "export — the clean corpus"
conda run -n "$ENV_NAME" python -m generators.pipeline export \
    --date "$DATE_STAMP" --target "$TARGET" || fail "export failed"

corpus="$TARGET/parsing_${DATE_STAMP}"
pages=$(find "$corpus/images" -name '*.png' | wc -l | tr -d ' ')
echo "  $corpus: $pages page(s)"

step "extract — flat images and ground_truth.{jsonl,csv}"
conda run -n "$ENV_NAME" python -m generators.pipeline extract \
    --corpus "$corpus" --target "$TARGET" --date "$DATE_STAMP" || fail "extraction export failed"

if [[ $DEGRADE != yes ]]; then
    echo
    echo "Skipping degradation (DEGRADE=$DEGRADE)."
else
    step "degrade — scan and photo intake, three severities each"
    conda run -n "$ENV_NAME" python -m generators.degradation.cli \
        --corpus "$corpus" --out "$TARGET/degraded" || fail "degradation failed"
fi

echo
echo "=== done: $TARGET ==="
find "$TARGET" -maxdepth 1 -mindepth 1 -type d | sort | while read -r d; do
    printf "  %-30s %4s image(s)\n" "$(basename "$d")" \
        "$(find "$d" -name '*.png' -o -name '*.jpg' | wc -l | tr -d ' ')"
done
```

Then: `git rm regenerate_bank_statements.sh`.

- [ ] **Step 4: Run the script tests**

```bash
chmod +x build_corpus.sh
conda run -n docparse pytest tests/test_build_script.py -q
```
Expected: PASS.

- [ ] **Step 5: Run the script for real, into a scratch target**

```bash
DEGRADE=no EVAL_ROOT=/tmp/b3 DATE_STAMP=20990101 ./build_corpus.sh
```

Expected: succeeds, and `/tmp/b3/corpus_20990101/` holds `parsing_20990101/` (189 pages) and `extraction_20990101/` with `ground_truth.jsonl`, `ground_truth.csv` and 189 flat images.

Then confirm the overwrite refusal:

```bash
DEGRADE=no EVAL_ROOT=/tmp/b3 DATE_STAMP=20990101 ./build_corpus.sh
```
Expected: fails with "already exists", writing nothing.

Then the full run including degradation, which is slow:

```bash
EVAL_ROOT=/tmp/b3 DATE_STAMP=20990102 ./build_corpus.sh
```
Expected: succeeds, and `degraded/` holds six corpora across all three document types.

- [ ] **Step 6: Confirm no existing page moved**

```bash
conda run -n docparse pytest tests/test_corpus_unchanged.py tests/test_pipeline.py -q
```
Expected: PASS. This task regenerates the data root; if an image moved, stop — that is a corpus revision, not a build-script change.

- [ ] **Step 7: Commit**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse ruff format .
git add build_corpus.sh
git rm regenerate_bank_statements.sh
git commit -m "✨ feat: one command builds the whole corpus

build_corpus.sh replaces regenerate_bank_statements.sh, which drove the
dead evaluation.cli package, rendered only bank statements while
exporting all pages, and printed environment instructions instead of
following them.

It keeps the two things its predecessor got right: it refuses to
overwrite an existing run, and it resolves paths from the script's own
location rather than the working directory."
```

---

## Task 5: README and CLAUDE.md

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Test: `tests/test_readme.py`

**Interfaces:**
- Consumes: `build_corpus.sh` (Task 4).
- Produces: nothing importable.

- [ ] **Step 1: Write the failing test**

Create `tests/test_readme.py`:

```python
"""The README is the first thing an outside user reads. It must not lie."""

from pathlib import Path

import yaml

README = Path("README.md").read_text(encoding="utf-8")


def _authored_pages() -> int:
    total = 0
    for path in Path("ground_truth").glob("*.yml"):
        total += len(yaml.safe_load(path.read_text(encoding="utf-8")))
    return total


def test_the_readme_states_no_stale_page_count():
    """It claimed 165 pages across a corpus that had grown to 189 — the same
    defect as the shipped README's '14 of 165'. Either state the true count or
    state none."""
    stale = {str(n) for n in range(100, 400)} - {str(_authored_pages())}
    for number in stale:
        assert f"{number} pages" not in README, (
            f"README claims '{number} pages'; the ground truth holds {_authored_pages()}"
        )


def test_the_readme_names_the_one_command():
    assert "build_corpus.sh" in README


def test_the_readme_describes_all_three_outputs():
    for output in ("parsing", "degraded", "extraction"):
        assert output in README, f"the README does not mention the {output} output"


def test_the_readme_does_not_reference_the_deleted_script():
    assert "regenerate_bank_statements" not in README
    assert "environment-degrade" not in README
```

- [ ] **Step 2: Run to verify it fails**

Run: `conda run -n docparse pytest tests/test_readme.py -q`
Expected: FAIL — the README says "165 pages", names no `build_corpus.sh`, and mentions neither `extraction` nor the deleted files.

- [ ] **Step 3: Rewrite the README**

Structure it for someone who has just cloned and knows nothing:

1. **What this is** — a generator for synthetic Australian business documents, each a pristine page paired with a canonical transcript. State that ground truth is *authored and captured at draw time*, never OCR'd, because that is the reason to use it.
2. **Quick start** — `conda env create -f environment.yml` then `./build_corpus.sh`.
3. **What you get** — the three output directories and who each is for: `parsing_*` for document parsing, `degraded/` for robustness, `extraction_*` for information extraction.
4. **Document types and layouts** — the three types; derive counts or omit them.
5. **What this deliberately does not do** — no extractor, no parser, no scorer; scoring lives in a separate repository and the interface is the exported corpus, not shared code. No layout/region detection, table-structure recognition, reading-order labels or multi-page documents.
6. **Reproducibility** — every step seeded; same inputs give byte-identical images; a change that moves a pixel is a corpus revision that invalidates predictions already scored.

Do not restate a page count as a literal anywhere.

> **Correction (2026-08-31).** Item 5 above instructs the README to say "no
> scorer; scoring lives in a separate repository". That was already false when
> written — `scoring/` landed on 2026-08-25 (`6df9759`, `ebedfcb`), under the
> reversal in `2026-08-25-degradation-matrix-and-scoring-design.md` §3 — and the
> instruction was followed, so the README carried the untrue claim until
> `53daaea` corrected it on 2026-08-31.
>
> What item 5 should have said: this repository runs no extractor and no parser;
> parser runners and analysis live in `tmnestor/bank-statement-error-analysis`;
> **text scoring lives here**, in `scoring/`, which never imports `generators/`
> so the interface is still the exported directory. The rest of item 5 — the
> out-of-scope list — stands as written.

- [ ] **Step 4: Update CLAUDE.md's scope wording**

In the "What this is" section, the out-of-scope list currently ends "...multi-page documents, and information extraction." Change that clause to record the boundary chosen in the spec:

```
Out of scope, deliberately: layout/region detection, table-structure
recognition, reading-order labels, and multi-page documents. Information
extraction is a consumer, not a feature: the corpus *emits* IE ground truth
(`extraction_*/ground_truth.{jsonl,csv}`, written by
`generators/extraction_export.py`) and runs no extractor of its own.
```

Also update the Environment section: one env, not two, and `environment-degrade.yml` no longer exists.

- [ ] **Step 5: Run to verify it passes**

Run: `conda run -n docparse pytest tests/test_readme.py -q`
Expected: PASS.

- [ ] **Step 6: Full suite, coverage, gates, commit**

```bash
conda run -n docparse pytest tests/ --ignore=tests/scoring -q --cov=generators --cov-report=term
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
git add README.md CLAUDE.md
git commit -m "📝 docs: orient the README at someone who just cloned

One command, three outputs and who each is for, and what the repo
deliberately does not do. The page count is derived or absent rather than
restated: it claimed 165 across a corpus that had grown to 189, the same
defect as the shipped README's '14 of 165'.

CLAUDE.md records the scope line this settled: IE is a consumer, not a
feature. The corpus emits IE ground truth and runs no extractor."
```

---

## Notes for the executor

- **Task 1 Step 2 is not optional ceremony.** Both new tests pass the moment they are written, because the property already holds. A test never seen to fail is not yet a test — break each one deliberately, watch it fail, then restore.
- **Task 4 Step 5 runs the real pipeline over the real data root.** It regenerates 189 pages. Step 6 exists because of that: if any existing image moves, stop.
- **`--target` is mandatory for `export`.** `--output` and `--derived` are *sources*; passing only those sends the deliverable into the configured `exports_dir` while appearing sandboxed.
- **If the merged environment changes any degraded image**, stop and report rather than adjusting a pin. Degraded images are corpus data and the pins are the reason they reproduce.
