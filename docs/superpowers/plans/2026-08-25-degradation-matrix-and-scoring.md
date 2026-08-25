# Degradation Matrix and Text Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote image degradation from a hand-run shell script to a YAML-driven corpus axis, and build a text-scoring package that compares competing document-parsing models across the resulting matrix.

**Architecture:** Three environments, three responsibilities, artifacts passed as directories on disk. `docparse` generates the clean corpus; `docparse-degrade` renders six degraded tiers plus a `matrix.jsonl` index; a new `docparse-score` environment holds a `scoring/` package that reads exported corpus directories and model prediction files and emits per-page rows, then aggregates them. `scoring/` never imports `generators/`.

**Tech Stack:** Python 3.12, PyYAML, typer, rich, rapidfuzz (new), Pillow + numpy + opencv + augraphy (degrade env only), pytest, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-08-25-degradation-matrix-and-scoring-design.md`

## Global Constraints

- **`tests/` is gitignored in this repository.** Tests are written and run but **never committed**. Every `git add` in this plan lists source and config files only. `git add tests/...` will silently add nothing — do not include it.
- **Never bypass pre-commit hooks.** No `--no-verify`, ever.
- **No Claude attribution in commit messages.** No `Co-Authored-By`, no "Generated with".
- Commit messages use gitmoji + conventional type, matching repo history: `✨ feat:`, `🔧 chore:`, `📝 docs:`, `🏗️ arch:`.
- **Line length 108.** `ruff check --fix --ignore ARG001,ARG002,F841 .` then `ruff format .` then `mypy <pkg> --ignore-missing-imports` must pass before every commit.
- **Python 3.12 typing:** `X | Y`, never `Union[X, Y]`. No `from __future__ import annotations`. **Never use `TYPE_CHECKING` guards for types used in runtime signatures.**
- **`pathlib.Path` for all file paths.** Google-style docstrings.
- **In `except` blocks always `raise ... from err` or `from None`** (B904). `raise typer.Exit(1) from None`.
- **YAML is the single source of truth.** No Python default may shadow a config value. Every config key is **required**; a missing key fails fast. To ship a no-op, commit the explicit value.
- **Every fail-fast error carries four elements:** What / Where (absolute path + dotted key) / Expected (a concrete YAML example) / Recover (a one-line remediation). Tests assert all four via `tests/helpers.py::assert_diagnostic_error`.
- **`environment.yml` (`docparse`) must never gain numpy, opencv or augraphy** — `tests/test_env.py` enforces this. Nothing in this plan adds a dependency to it.
- **Never write "ATO"** — use "PROD".
- Run tooling through conda: `conda run -n docparse <cmd>`, or `conda run -n docparse-score <cmd>` for Phase 2 tasks 5 onward.

---

## File Structure

**Phase 1 — corpus matrix (`docparse` / `docparse-degrade`)**

| File | Responsibility |
|---|---|
| `config/degradation.yml` (modify) | gains a required `corpus:` block naming doc types and families |
| `generators/degradation/tiers.py` (modify) | `load_corpus_selection()` — parse and validate that block |
| `generators/degradation/matrix.py` (create) | build and write `matrix.jsonl`; **imports no numpy/cv2** so it loads in `docparse` |
| `generators/degradation/cli.py` (modify) | defaults from config, `--out` defaults to `exports_dir`, writes the matrix |

**Phase 2 — scoring (`docparse-score`)**

| File | Responsibility |
|---|---|
| `environment-score.yml` (create) | declares `docparse-score` |
| `config/scoring.yml` (create) | normalisation and reporting policy |
| `scoring/errors.py` (create) | `ScoringError` + the four-element `_err` builder |
| `scoring/policy.py` (create) | load and validate `config/scoring.yml` |
| `scoring/normalise.py` (create) | policy-driven text normalisation |
| `scoring/metrics.py` (create) | edit distance, CER, WER |
| `scoring/corpus.py` (create) | load an exported corpus; verify image hashes |
| `scoring/predictions.py` (create) | load a prediction set; verify prompt and vintage hashes |
| `scoring/score.py` (create) | CLI — emit one JSONL row per page |
| `scoring/report.py` (create) | CLI — aggregate rows into a comparison table |
| `generators/export.py` (modify) | shipped README normalisation prose generated from `config/scoring.yml` |

Tests mirror the source: `tests/test_degradation_config.py`, `tests/test_degradation_matrix.py`, `tests/scoring/test_*.py`.

---

# PHASE 1 — Corpus matrix

## Task 1: `corpus:` selection block in `config/degradation.yml`

**Files:**
- Modify: `config/degradation.yml`
- Modify: `generators/degradation/tiers.py`
- Test: `tests/test_degradation_config.py`

**Interfaces:**
- Consumes: `TierConfigError` from `generators/degradation/tiers.py`.
- Produces: `load_corpus_selection(config_path: Path) -> CorpusSelection`, where
  `CorpusSelection` is a frozen dataclass with `document_types: tuple[str, ...]`
  and `families: tuple[str, ...]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_degradation_config.py`:

```python
"""The degrade run's selection is configuration, not a shell variable."""

from pathlib import Path

import pytest
import yaml

from generators.degradation.tiers import TierConfigError, load_corpus_selection
from tests.helpers import assert_diagnostic_error

CONFIG = Path("config/degradation.yml")


def _config_without(tmp_path, dotted):
    data = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    section, _, key = dotted.partition(".")
    del data[section][key]
    path = tmp_path / "degradation.yml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_the_shipped_config_selects_all_three_types_and_both_families():
    selection = load_corpus_selection(CONFIG)

    assert selection.document_types == ("bank_statements", "receipts", "invoices")
    assert selection.families == ("scan", "photo")


@pytest.mark.parametrize("key", ["corpus.document_types", "corpus.families"])
def test_an_omitted_selection_key_fails_fast(tmp_path, key):
    with pytest.raises(TierConfigError) as err:
        load_corpus_selection(_config_without(tmp_path, key))

    assert_diagnostic_error(str(err.value), mentions=(key.split(".")[1], str(tmp_path)))


def test_an_empty_type_list_is_rejected_rather_than_treated_as_all(tmp_path):
    """`[]` reads as "degrade nothing", which is never what an operator meant."""
    data = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    data["corpus"]["document_types"] = []
    path = tmp_path / "degradation.yml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(TierConfigError) as err:
        load_corpus_selection(path)

    assert_diagnostic_error(str(err.value), mentions=("document_types",))


def test_a_family_not_declared_in_the_tier_list_is_rejected(tmp_path):
    data = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    data["corpus"]["families"] = ["scan", "fax"]
    path = tmp_path / "degradation.yml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(TierConfigError) as err:
        load_corpus_selection(path)

    assert_diagnostic_error(str(err.value), mentions=("fax", "families"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n docparse python -m pytest tests/test_degradation_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'load_corpus_selection'`

- [ ] **Step 3: Add the config block**

Append to `config/degradation.yml`:

```yaml
# WHICH PAGES A FULL RUN DEGRADES. Both keys are required: reading this file
# alone must answer "what does `degrade` with no flags produce?", and a missing
# key is an error rather than a silent "all of them".
#
# All three document types, despite receipts scoring at ceiling on clean pages.
# Creating headroom where clean pages have none is exactly what this axis is
# for -- a degraded receipt can separate models that a clean one cannot.
corpus:
  document_types: [bank_statements, receipts, invoices]
  families: [scan, photo]
```

- [ ] **Step 4: Implement the loader**

Add to `generators/degradation/tiers.py`:

```python
@dataclass(frozen=True)
class CorpusSelection:
    """Which pages a full degrade run covers.

    Attributes:
        document_types: Document types to degrade, in config order.
        families: Intake families to render, in config order.
    """

    document_types: tuple[str, ...]
    families: tuple[str, ...]


def load_corpus_selection(config_path: Path) -> CorpusSelection:
    """Read and validate the `corpus:` block.

    Args:
        config_path: Path to `degradation.yml`.

    Returns:
        The validated selection.

    Raises:
        TierConfigError: The block or either key is missing, a list is empty, or
            a named family has no tiers declared in this file.
    """
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    resolved = config_path.resolve()

    block = data.get("corpus")
    if not isinstance(block, dict):
        raise _corpus_err(
            "the 'corpus:' block is missing or is not a mapping.",
            path=resolved,
            key="corpus",
            expected="a mapping with 'document_types' and 'families', e.g.\n"
            "              corpus:\n"
            "                document_types: [bank_statements, receipts, invoices]\n"
            "                families: [scan, photo]",
            recover=f"add a 'corpus:' block to {config_path}.",
        )

    declared = tuple(data.get("families", {}))
    values: dict[str, tuple[str, ...]] = {}
    for key, example in (
        ("document_types", "[bank_statements, receipts, invoices]"),
        ("families", "[scan, photo]"),
    ):
        raw = block.get(key)
        if not isinstance(raw, list) or not raw or not all(isinstance(v, str) for v in raw):
            raise _corpus_err(
                f"'{key}' is {raw!r}, which is not a non-empty list of names.",
                path=resolved,
                key=f"corpus.{key}",
                expected=f"a non-empty list of names, e.g.\n              {key}: {example}",
                recover=f"set 'corpus.{key}:' in {config_path} to a non-empty list.",
            )
        values[key] = tuple(raw)

    unknown = [f for f in values["families"] if f not in declared]
    if unknown:
        raise _corpus_err(
            f"'families' names {unknown}, which have no tiers declared in this file.",
            path=resolved,
            key="corpus.families",
            expected=f"a subset of the declared families {list(declared)}, e.g.\n"
            "              families: [scan, photo]",
            recover=f"remove {unknown} from 'corpus.families', or declare tiers for them "
            f"under 'families:' in {config_path}.",
        )

    return CorpusSelection(document_types=values["document_types"], families=values["families"])


def _corpus_err(what: str, *, path: Path, key: str, expected: str, recover: str) -> TierConfigError:
    """Build a four-element fail-fast diagnostic for the corpus block."""
    return TierConfigError(
        "Invalid degradation config.\n"
        f"  What:     {what}\n"
        f"  Where:    {path} -> {key}\n"
        f"  Expected: {expected}\n"
        f"  Recover:  {recover}"
    )
```

Add `from dataclasses import dataclass` and `from pathlib import Path` to the imports if absent.

- [ ] **Step 5: Run tests to verify they pass**

Run: `conda run -n docparse python -m pytest tests/test_degradation_config.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Quality gates**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
conda run -n docparse python -m pytest tests/ -q
```
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add config/degradation.yml generators/degradation/tiers.py
git commit -m "✨ feat: declare the degrade run's page selection in YAML"
```

---

## Task 2: `matrix.jsonl` builder

**Files:**
- Create: `generators/degradation/matrix.py`
- Test: `tests/test_degradation_matrix.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `matrix_row(corpus_dir: Path, *, family: str, severity: str) -> dict`
  - `write_matrix(rows: list[dict], exports_dir: Path) -> Path`
  - `MatrixError(RuntimeError)`

**Note:** this module must import only `hashlib`, `json`, `pathlib` and
`collections` — no numpy, no cv2, no Pillow — so it loads in `docparse` and its
tests run without augraphy installed.

- [ ] **Step 1: Write the failing test**

Create `tests/test_degradation_matrix.py`:

```python
"""The matrix ties the clean corpus and its tiers into one comparable set."""

import hashlib
import json

import pytest

from generators.degradation.matrix import MatrixError, matrix_row, write_matrix
from tests.helpers import assert_diagnostic_error


def _corpus(tmp_path, name, doc_types=("invoices",), pages=2):
    root = tmp_path / name
    (root / "images").mkdir(parents=True)
    rows = []
    for doc_type in doc_types:
        for i in range(pages):
            case = f"CASE{i + 1:03d}"
            image = root / "images" / f"{case}_{doc_type}.png"
            image.write_bytes(f"{name}-{case}-{doc_type}".encode())
            rows.append(
                {
                    "image": f"images/{case}_{doc_type}.png",
                    "transcript": f"transcripts/{case}_{doc_type}.md",
                    "doc_type": doc_type,
                    "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                }
            )
    (root / "manifest.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    return root


def test_a_row_describes_one_corpus(tmp_path):
    root = _corpus(tmp_path, "parsing_20260825", doc_types=("invoices", "receipts"), pages=3)

    row = matrix_row(root, family="clean", severity="none")

    assert row["corpus"] == "parsing_20260825"
    assert row["family"] == "clean"
    assert row["severity"] == "none"
    assert row["pages"] == 6
    assert row["doc_types"] == ["invoices", "receipts"]


def test_the_row_hashes_the_manifest_not_the_images(tmp_path):
    """One value ties a row to an exact vintage; per-image hashes live inside."""
    root = _corpus(tmp_path, "parsing_20260825")
    expected = hashlib.sha256((root / "manifest.jsonl").read_bytes()).hexdigest()

    assert matrix_row(root, family="clean", severity="none")["manifest_sha256"] == expected


def test_a_changed_page_changes_the_row_hash(tmp_path):
    """The guard is not vacuous: a different corpus must produce a different row."""
    a = matrix_row(_corpus(tmp_path / "a", "parsing_1"), family="clean", severity="none")
    b = matrix_row(_corpus(tmp_path / "b", "parsing_2"), family="clean", severity="none")

    assert a["manifest_sha256"] != b["manifest_sha256"]


def test_a_directory_without_a_manifest_fails_fast(tmp_path):
    bare = tmp_path / "not_an_export"
    bare.mkdir()

    with pytest.raises(MatrixError) as err:
        matrix_row(bare, family="clean", severity="none")

    assert_diagnostic_error(str(err.value), mentions=("manifest.jsonl", str(bare)))


def test_write_matrix_emits_one_line_per_corpus(tmp_path):
    rows = [
        matrix_row(_corpus(tmp_path, "parsing_20260825"), family="clean", severity="none"),
        matrix_row(
            _corpus(tmp_path, "parsing_20260825_scan-light"), family="scan", severity="light"
        ),
    ]

    path = write_matrix(rows, tmp_path)

    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert path.name == "matrix.jsonl"
    assert [r["family"] for r in lines] == ["clean", "scan"]


def test_write_matrix_refuses_a_set_with_no_clean_baseline(tmp_path):
    """Without the clean row you cannot tell a weak model from a hurt one."""
    rows = [
        matrix_row(
            _corpus(tmp_path, "parsing_20260825_scan-light"), family="scan", severity="light"
        )
    ]

    with pytest.raises(MatrixError) as err:
        write_matrix(rows, tmp_path)

    assert_diagnostic_error(str(err.value), mentions=("clean",))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n docparse python -m pytest tests/test_degradation_matrix.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'generators.degradation.matrix'`

- [ ] **Step 3: Write the implementation**

Create `generators/degradation/matrix.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n docparse python -m pytest tests/test_degradation_matrix.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Quality gates**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
conda run -n docparse python -m pytest tests/ -q
```

- [ ] **Step 6: Commit**

```bash
git add generators/degradation/matrix.py
git commit -m "✨ feat: index a clean corpus and its tiers as one matrix"
```

---

## Task 3: Wire the degrade CLI to config and the matrix

**Files:**
- Modify: `generators/degradation/cli.py`
- Test: `tests/test_degradation_cli_defaults.py`

**Interfaces:**
- Consumes: `load_corpus_selection` (Task 1), `matrix_row` / `write_matrix` (Task 2),
  `load_generation_config` from `generators/loader.py`.
- Produces: `resolve_run(config_path, generation_config_path, *, family, doc_type, out) -> RunPlan`,
  a frozen dataclass with `families: tuple[str, ...]`, `doc_types: tuple[str, ...]`, `out: Path`.

The resolution logic is extracted into a pure function so it is testable in
`docparse`, where augraphy is absent and the CLI's degrade loop cannot run.

- [ ] **Step 1: Write the failing test**

Create `tests/test_degradation_cli_defaults.py`:

```python
"""Flags override config; config supplies the default; neither is a Python literal."""

from pathlib import Path

from generators.degradation.cli import resolve_run

CONFIG = Path("config/degradation.yml")
GENERATION = Path("config/generation_config.yml")


def test_with_no_flags_everything_comes_from_config():
    plan = resolve_run(CONFIG, GENERATION, family=None, doc_type=None, out=None)

    assert plan.families == ("scan", "photo")
    assert plan.doc_types == ("bank_statements", "receipts", "invoices")


def test_the_output_defaults_beside_the_generated_data_not_the_cwd():
    """Tiers are generated data; they belong with the rest of it."""
    plan = resolve_run(CONFIG, GENERATION, family=None, doc_type=None, out=None)

    assert plan.out.is_absolute()
    assert plan.out.name == "exports"
    assert Path.cwd() not in plan.out.parents and plan.out != Path.cwd()


def test_a_family_flag_overrides_the_configured_families():
    plan = resolve_run(CONFIG, GENERATION, family=["scan"], doc_type=None, out=None)

    assert plan.families == ("scan",)
    assert plan.doc_types == ("bank_statements", "receipts", "invoices")


def test_a_type_flag_overrides_the_configured_types():
    plan = resolve_run(CONFIG, GENERATION, family=None, doc_type=["receipts"], out=None)

    assert plan.doc_types == ("receipts",)


def test_an_explicit_out_wins(tmp_path):
    plan = resolve_run(CONFIG, GENERATION, family=None, doc_type=None, out=tmp_path)

    assert plan.out == tmp_path
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n docparse python -m pytest tests/test_degradation_cli_defaults.py -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_run'`

- [ ] **Step 3: Implement `resolve_run`**

Add to `generators/degradation/cli.py`, above the `degrade` command:

```python
@dataclass(frozen=True)
class RunPlan:
    """What a degrade invocation will actually do.

    Attributes:
        families: Intake families to render.
        doc_types: Document types to include.
        out: Directory the tier corpora are written into.
    """

    families: tuple[str, ...]
    doc_types: tuple[str, ...]
    out: Path


def resolve_run(
    config_path: Path,
    generation_config_path: Path,
    *,
    family: list[str] | None,
    doc_type: list[str] | None,
    out: Path | None,
) -> RunPlan:
    """Fold flags over configuration to decide what this run covers.

    Extracted from the command body so it can be tested in `docparse`, where
    augraphy is absent and the degrade loop itself cannot run.

    Args:
        config_path: Path to `degradation.yml`.
        generation_config_path: Path to `generation_config.yml`, which resolves
            where generated data lives.
        family: `--family` values, or None to use the configured families.
        doc_type: `--type` values, or None to use the configured types.
        out: `--out`, or None to default beside the generated data.

    Returns:
        The resolved plan.
    """
    selection = load_corpus_selection(config_path)
    generation = load_generation_config(generation_config_path)
    return RunPlan(
        families=tuple(family) if family else selection.families,
        doc_types=tuple(doc_type) if doc_type else selection.document_types,
        out=out if out is not None else Path(generation["exports_dir"]),
    )
```

Add these imports at the top of `cli.py`:

```python
from dataclasses import dataclass

from generators.degradation.matrix import matrix_row, write_matrix
from generators.degradation.tiers import load_corpus_selection
from generators.loader import load_generation_config
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n docparse python -m pytest tests/test_degradation_cli_defaults.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Use the plan in the command body**

In `generators/degradation/cli.py`, change the `degrade` command signature so
`out` defaults to `None`:

```python
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Where the degraded corpora are written. Defaults beside the data."),
    ] = None,
    generation_config: Annotated[
        Path, typer.Option("--generation-config", help="Path to generation_config.yml")
    ] = Path("config/generation_config.yml"),
```

Replace the `tiers = load_tiers(config, families=family)` line and the `records`
filter with:

```python
    plan = resolve_run(config, generation_config, family=family, doc_type=doc_type, out=out)
    tiers = load_tiers(config, families=list(plan.families))
    records = [json.loads(line) for line in (corpus / "manifest.jsonl").read_text().splitlines() if line]
    records = [r for r in records if r["doc_type"] in plan.doc_types]
```

Replace every later use of `out` with `plan.out`, and `plan.out.mkdir(parents=True, exist_ok=True)`
before the tier loop.

At the end of the command, after the tier loop, collect and write the matrix:

```python
    # The clean corpus is a row like any other: without that baseline a
    # comparison cannot separate a weak model from one the degradation hurt.
    rows = [matrix_row(corpus, family="clean", severity="none")]
    rows.extend(
        matrix_row(plan.out / f"{corpus.name}_{tier.family}-{tier.name}", family=tier.family, severity=tier.name)
        for tier in tiers
    )
    matrix_path = write_matrix(rows, plan.out)
    rprint(f"[green]Matrix written: {matrix_path} ({len(rows)} corpora)[/green]")
```

- [ ] **Step 6: Verify the full suite still passes**

Run: `conda run -n docparse python -m pytest tests/ -q`
Expected: PASS. `tests/test_degradation.py` still skips when augraphy is absent.

- [ ] **Step 7: Quality gates**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
```

- [ ] **Step 8: Commit**

```bash
git add generators/degradation/cli.py
git commit -m "✨ feat: drive degrade from config and index its output"
```

- [ ] **Step 9: Smoke-test the real run (requires `docparse-degrade`)**

```bash
conda run -n docparse-degrade python -m generators.degradation.cli \
    --corpus <exports_dir>/parsing_20260825 --limit 2
```
Expected: six tier directories plus `matrix.jsonl` with 7 rows, all under the
configured `exports_dir`. If augraphy is unavailable on this machine, note it and
move on — Tasks 1–3 are fully covered by tests that do not need it.

> **PHASE BOUNDARY.** Phase 1 is independently useful: the matrix exists and the
> corpora are indexed. Stop here if you want to reassess before building the
> scorer.

---

# PHASE 2 — Text scoring

## Task 4: The `docparse-score` environment and the import boundary

**Files:**
- Create: `environment-score.yml`
- Create: `scoring/__init__.py`
- Test: `tests/scoring/__init__.py`, `tests/scoring/test_boundaries.py`

**Interfaces:**
- Produces: an importable `scoring` package and the `docparse-score` environment.

- [ ] **Step 1: Write the failing test**

Create `tests/scoring/__init__.py` (empty file) and `tests/scoring/test_boundaries.py`:

```python
"""The interface between generation and scoring is a directory, not shared code.

Scoring moved into this repository for one operator's convenience. The isolation
that mattered is kept by package and environment boundaries instead, and this is
the test that keeps the first of them honest.
"""

import ast
from pathlib import Path

SCORING = Path("scoring")


def test_the_scoring_package_exists():
    """Guards the test below against passing vacuously over an empty directory."""
    modules = list(SCORING.glob("*.py"))

    assert modules, "no modules under scoring/; the boundary test would prove nothing"


def test_no_scoring_module_imports_generators():
    offenders = []
    for module in sorted(SCORING.glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders += [
                    f"{module}: import {a.name}" for a in node.names if a.name.startswith("generators")
                ]
            elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("generators"):
                offenders.append(f"{module}: from {node.module} import ...")

    assert not offenders, (
        "scoring reads an exported corpus directory and nothing else — "
        f"these imports break that boundary: {offenders}"
    )


def test_the_score_environment_carries_no_parser_or_inference_stack():
    """Inference runs on the remote GPU host; the scorer runs on a laptop.

    Parses the dependency list rather than grepping the file: the comments
    explain *why* torch is excluded, and a raw substring search would trip over
    its own rationale.
    """
    import yaml

    env = yaml.safe_load(Path("environment-score.yml").read_text(encoding="utf-8"))
    names = []
    for entry in env["dependencies"]:
        if isinstance(entry, str):
            names.append(entry.split("=")[0].split(">")[0].split("<")[0].strip())
        elif isinstance(entry, dict):  # a pip: block
            names += [str(p).split("=")[0].strip() for p in entry.get("pip", [])]

    forbidden = {"torch", "pytorch", "vllm", "transformers", "augraphy", "opencv", "opencv-python"}
    assert not forbidden & set(names), f"{forbidden & set(names)} does not belong in docparse-score"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n docparse python -m pytest tests/scoring/test_boundaries.py -v`
Expected: FAIL — `test_the_scoring_package_exists` fails, "no modules under scoring/"

- [ ] **Step 3: Create the environment file**

Create `environment-score.yml`:

```yaml
# Scoring. A THIRD environment, alongside docparse and docparse-degrade, and for
# the same reason they are separate: its dependencies are incompatible with
# keeping `docparse` small.
#
# `docparse` is five pure-Python packages by design and must never grow. This
# environment adds an edit-distance library, which the generator has no use for.
#
# It carries NO parser and NO inference stack. Model inference runs on the remote
# GPU host; predictions arrive here as files on disk. Keeping torch out is what
# lets scoring run on a laptop, and tests/scoring/test_boundaries.py enforces it.
#
#   conda env create -f environment-score.yml
#   conda activate docparse-score
#   python -m scoring.score --matrix <exports>/matrix.jsonl \
#       --predictions-root predictions/ --out rows.jsonl

name: docparse-score

channels:
  - conda-forge

dependencies:
  - python=3.12
  - pyyaml
  - typer
  - rich
  - rapidfuzz
  - pytest
  - pytest-cov
  - ruff
  - mypy
```

- [ ] **Step 4: Create the package**

Create `scoring/__init__.py`:

```python
"""Score model predictions against an exported corpus.

Reads an exported corpus directory and a directory of prediction files, and
emits one row per page. It imports nothing from `generators`: the interface
between generation and scoring is the exported directory, exactly as it was when
the two lived in separate repositories.
"""
```

- [ ] **Step 5: Create the environment and run the tests**

```bash
conda env create -f environment-score.yml
conda run -n docparse-score python -m pytest tests/scoring/test_boundaries.py -v
```
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add environment-score.yml scoring/__init__.py
git commit -m "🔧 chore: add the docparse-score environment and package"
```

---

## Task 5: Scoring policy in YAML

**Files:**
- Create: `config/scoring.yml`
- Create: `scoring/errors.py`
- Create: `scoring/policy.py`
- Test: `tests/scoring/test_policy.py`

**Interfaces:**
- Produces:
  - `ScoringError(RuntimeError)` and
    `diagnostic(what, *, path, key, expected, recover) -> ScoringError` in `scoring/errors.py`
  - `load_scoring_policy(path: Path) -> dict` and
    `REQUIRED_POLICY_KEYS: tuple[str, ...]` in `scoring/policy.py`

- [ ] **Step 1: Write the failing test**

Create `tests/scoring/test_policy.py`:

```python
"""What "normalised" means is configuration, not code."""

from pathlib import Path

import pytest
import yaml

from scoring.errors import ScoringError
from scoring.policy import REQUIRED_POLICY_KEYS, load_scoring_policy
from tests.helpers import assert_diagnostic_error

POLICY_PATH = Path("config/scoring.yml")


def _policy_with(tmp_path, section, **overrides):
    data = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    data[section].update(overrides)
    path = tmp_path / "scoring.yml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _policy_without(tmp_path, section, key):
    data = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    del data[section][key]
    path = tmp_path / "scoring.yml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_the_shipped_policy_loads():
    policy = load_scoring_policy(POLICY_PATH)

    assert policy["normalisation"]["unicode_form"] == "NFKC"
    assert policy["reporting"]["degenerate_length_multiple"] == 3.0


def test_case_folding_is_off_and_says_so():
    """Reading identifiers with correct case is part of transcription."""
    assert load_scoring_policy(POLICY_PATH)["normalisation"]["fold_case"] is False


def test_the_shipped_policy_declares_every_required_key_and_no_others():
    data = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    declared = {f"{section}.{key}" for section, block in data.items() for key in block}

    assert declared == set(REQUIRED_POLICY_KEYS)


@pytest.mark.parametrize("dotted", REQUIRED_POLICY_KEYS)
def test_an_omitted_key_fails_fast(tmp_path, dotted):
    section, _, key = dotted.partition(".")

    with pytest.raises(ScoringError) as err:
        load_scoring_policy(_policy_without(tmp_path, section, key))

    assert_diagnostic_error(str(err.value), mentions=(key, str(tmp_path)))


def test_an_unknown_unicode_form_is_rejected(tmp_path):
    with pytest.raises(ScoringError) as err:
        load_scoring_policy(_policy_with(tmp_path, "normalisation", unicode_form="NFKD_PLUS"))

    assert_diagnostic_error(str(err.value), mentions=("unicode_form", "NFKC"))


def test_a_non_boolean_switch_is_rejected(tmp_path):
    with pytest.raises(ScoringError) as err:
        load_scoring_policy(_policy_with(tmp_path, "normalisation", fold_dashes="yes"))

    assert_diagnostic_error(str(err.value), mentions=("fold_dashes",))


def test_a_degenerate_multiple_below_one_is_rejected(tmp_path):
    """At or below 1.0 every correct prediction is 'degenerate'."""
    with pytest.raises(ScoringError) as err:
        load_scoring_policy(_policy_with(tmp_path, "reporting", degenerate_length_multiple=0.9))

    assert_diagnostic_error(str(err.value), mentions=("degenerate_length_multiple",))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n docparse-score python -m pytest tests/scoring/test_policy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scoring.errors'`

- [ ] **Step 3: Create the policy file**

Create `config/scoring.yml`:

```yaml
# How a prediction is compared with a transcript. This file is the whole scoring
# convention: reading it should answer what "normalised" means without consulting
# Python. Every key is required -- the loader fails fast on any omission,
# including the keys whose value is a no-op.
#
# Two numbers are reported and neither is the real one on its own. NORMALISED
# runs both sides through the steps below and measures *reading*. STRICT compares
# the raw forms and measures reading plus adherence to the conventions in
# config/serialisation.yml. A model can read a page perfectly and score badly on
# strict; that is the point of reporting both.

normalisation:
  # Unicode compatibility composition, applied first so every later step sees
  # one representation of each character.
  unicode_form: NFKC

  # Runs of whitespace become a single space. Applied last, so syntax stripped
  # above cannot leave doubled gaps behind.
  collapse_whitespace: true

  # En dash, em dash, figure dash and horizontal bar fold to a hyphen; curly
  # quotes fold to their ASCII forms. Typographic substitution is not a reading
  # error, and penalising it would measure font handling.
  fold_dashes: true
  fold_quotes: true

  # Heading markers, table pipes and separator rows, emphasis markers and code
  # backticks are removed from both sides. The normalised metric measures what
  # the model read, not how it marked it up; the strict metric is where markup
  # adherence is scored.
  strip_markdown: true

  # OFF, deliberately, and written out rather than omitted. Reading an account
  # name or a reference with the correct case is legitimately part of
  # transcription, so folding case would hide a real error.
  fold_case: false

reporting:
  # A prediction longer than this multiple of its reference is counted as
  # degenerate. Set from observed behaviour, not taste: a calibration run had one
  # model emit 128,768 characters for a ~2,400-character page -- 50x. Three
  # separates that failure from ordinary verbosity.
  degenerate_length_multiple: 3.0

  # Reported per group, alongside the mean. Median first because CER is unbounded
  # above, so one runaway page can dominate a mean; 100 is the worst case, which
  # a mean hides in the other direction.
  percentiles: [50, 90, 100]
```

- [ ] **Step 4: Write `scoring/errors.py`**

```python
"""The four-element fail-fast diagnostic, shared across the scoring package.

Mirrors `generators/serialise.py`'s `_err`, deliberately rather than importing
it: `scoring/` reads an exported directory and imports nothing from
`generators/` (see tests/scoring/test_boundaries.py). Twelve duplicated lines
are the price of that boundary, and a cheap one.
"""

from pathlib import Path


class ScoringError(RuntimeError):
    """Raised when a corpus, a prediction set or the policy is unusable."""


def diagnostic(what: str, *, path: Path | str, key: str, expected: str, recover: str) -> ScoringError:
    """Build a four-element fail-fast diagnostic.

    Args:
        what: What is wrong.
        path: Absolute path of the offending file.
        key: The dotted key or filename inside it.
        expected: A concrete example of a valid value.
        recover: A one-line remediation step.

    Returns:
        The error, ready to raise.
    """
    return ScoringError(
        "Cannot score.\n"
        f"  What:     {what}\n"
        f"  Where:    {path} -> {key}\n"
        f"  Expected: {expected}\n"
        f"  Recover:  {recover}"
    )
```

- [ ] **Step 5: Write `scoring/policy.py`**

```python
"""Load and validate config/scoring.yml."""

import unicodedata
from pathlib import Path

import yaml

from scoring.errors import ScoringError, diagnostic

REQUIRED_POLICY_KEYS: tuple[str, ...] = (
    "normalisation.unicode_form",
    "normalisation.collapse_whitespace",
    "normalisation.fold_dashes",
    "normalisation.fold_quotes",
    "normalisation.strip_markdown",
    "normalisation.fold_case",
    "reporting.degenerate_length_multiple",
    "reporting.percentiles",
)

_UNICODE_FORMS = ("NFC", "NFD", "NFKC", "NFKD")
_BOOL_KEYS = (
    "collapse_whitespace",
    "fold_dashes",
    "fold_quotes",
    "strip_markdown",
    "fold_case",
)
_EXAMPLES: dict[str, str] = {
    "normalisation.unicode_form": "NFKC",
    "normalisation.collapse_whitespace": "true",
    "normalisation.fold_dashes": "true",
    "normalisation.fold_quotes": "true",
    "normalisation.strip_markdown": "true",
    "normalisation.fold_case": "false",
    "reporting.degenerate_length_multiple": "3.0",
    "reporting.percentiles": "[50, 90, 100]",
}


def load_scoring_policy(path: Path) -> dict:
    """Read and validate the scoring convention.

    Args:
        path: Path to `scoring.yml`.

    Returns:
        The validated policy mapping.

    Raises:
        ScoringError: The file is missing or unparseable, a key is absent, or a
            value is outside what this scorer implements.
    """
    resolved = path.resolve()
    if not path.exists():
        raise diagnostic(
            f"{path} does not exist.",
            path=resolved,
            key="(whole file)",
            expected=f"a YAML mapping declaring every key of {list(REQUIRED_POLICY_KEYS)}, e.g.\n"
            "              normalisation:\n                unicode_form: NFKC",
            recover="create config/scoring.yml.",
        )

    try:
        policy = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as err:
        raise diagnostic(
            f"the file is not valid YAML: {err}",
            path=resolved,
            key="(whole file)",
            expected="parseable YAML, e.g.\n              normalisation:\n                fold_case: false",
            recover="fix the syntax error at the line named above.",
        ) from err

    if not isinstance(policy, dict):
        raise diagnostic(
            f"expected a mapping, got {type(policy).__name__}.",
            path=resolved,
            key="(document root)",
            expected="a top-level mapping with 'normalisation:' and 'reporting:' sections.",
            recover="wrap the settings in a top-level mapping.",
        )

    for dotted in REQUIRED_POLICY_KEYS:
        section, _, key = dotted.partition(".")
        if not isinstance(policy.get(section), dict) or key not in policy[section]:
            raise diagnostic(
                f"'{dotted}' is not declared.",
                path=resolved,
                key=dotted,
                expected=f"every key of {list(REQUIRED_POLICY_KEYS)} present — none has a Python "
                f"default, e.g.\n              {section}:\n                {key}: {_EXAMPLES[dotted]}",
                recover=f"add '{key}:' under '{section}:' in {path}.",
            )

    _validate_values(policy, path=path, resolved=resolved)
    return policy


def _validate_values(policy: dict, *, path: Path, resolved: Path) -> None:
    """Range- and type-check every declared value.

    Args:
        policy: The policy mapping, known to declare every required key.
        path: The path as given, used in the recovery step.
        resolved: The absolute path, used to locate the file.

    Raises:
        ScoringError: A value is the wrong type or outside the allowed range.
    """
    form = policy["normalisation"]["unicode_form"]
    if form not in _UNICODE_FORMS:
        raise diagnostic(
            f"'unicode_form' is {form!r}, which is not a Unicode normalisation form.",
            path=resolved,
            key="normalisation.unicode_form",
            expected=f"one of {list(_UNICODE_FORMS)}, e.g.\n              unicode_form: NFKC",
            recover=f"set 'normalisation.unicode_form:' in {path} to one of those forms.",
        )
    unicodedata.normalize(form, "")

    for key in _BOOL_KEYS:
        value = policy["normalisation"][key]
        if not isinstance(value, bool):
            raise diagnostic(
                f"'{key}' is {value!r}, which is not a boolean.",
                path=resolved,
                key=f"normalisation.{key}",
                expected=f"true or false, e.g.\n              {key}: true",
                recover=f"set 'normalisation.{key}:' in {path} to true or false.",
            )

    multiple = policy["reporting"]["degenerate_length_multiple"]
    if isinstance(multiple, bool) or not isinstance(multiple, int | float) or multiple <= 1:
        raise diagnostic(
            f"'degenerate_length_multiple' is {multiple!r}, which is not a number above 1.",
            path=resolved,
            key="reporting.degenerate_length_multiple",
            expected="a number greater than 1 — at or below 1 every correct prediction counts "
            "as degenerate, e.g.\n              degenerate_length_multiple: 3.0",
            recover=f"raise 'reporting.degenerate_length_multiple:' in {path} above 1.",
        )

    percentiles = policy["reporting"]["percentiles"]
    if (
        not isinstance(percentiles, list)
        or not percentiles
        or not all(isinstance(p, int) and 0 < p <= 100 for p in percentiles)
    ):
        raise diagnostic(
            f"'percentiles' is {percentiles!r}, which is not a non-empty list of 1-100 integers.",
            path=resolved,
            key="reporting.percentiles",
            expected="a non-empty list of integers in 1..100, e.g.\n              percentiles: [50, 90, 100]",
            recover=f"set 'reporting.percentiles:' in {path} to such a list.",
        )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `conda run -n docparse-score python -m pytest tests/scoring/test_policy.py -v`
Expected: PASS (13 tests)

- [ ] **Step 7: Quality gates and commit**

```bash
conda run -n docparse-score ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse-score ruff format .
conda run -n docparse-score mypy scoring --ignore-missing-imports
git add config/scoring.yml scoring/errors.py scoring/policy.py
git commit -m "✨ feat: declare the scoring convention in YAML"
```

---

## Task 6: Text normalisation

**Files:**
- Create: `scoring/normalise.py`
- Test: `tests/scoring/test_normalise.py`

**Interfaces:**
- Consumes: `load_scoring_policy` (Task 5).
- Produces: `normalise(text: str, policy: dict) -> str`, where `policy` is the
  **`normalisation` section**, not the whole document.

- [ ] **Step 1: Write the failing test**

Create `tests/scoring/test_normalise.py`:

```python
"""Each normalisation step is policy, and each is provably doing something."""

from pathlib import Path

import pytest

from scoring.normalise import normalise
from scoring.policy import load_scoring_policy

SHIPPED = load_scoring_policy(Path("config/scoring.yml"))["normalisation"]


def _with(**overrides):
    return {**SHIPPED, **overrides}


def test_curly_quotes_fold_to_ascii():
    assert normalise("\u201cRobin\u2019s\u201d", SHIPPED) == '"Robin\'s"'


def test_disabling_quote_folding_keeps_them():
    """The switch is real: with it off, the result differs."""
    assert normalise("\u201cRobin\u201d", _with(fold_quotes=False)) == "\u201cRobin\u201d"


def test_dashes_fold_to_a_hyphen():
    assert normalise("01/09/2023 \u2013 23/09/2023", SHIPPED) == "01/09/2023 - 23/09/2023"


def test_disabling_dash_folding_keeps_them():
    assert "\u2013" in normalise("a \u2013 b", _with(fold_dashes=False))


def test_whitespace_runs_collapse():
    assert normalise("Total:      $12.00", SHIPPED) == "Total: $12.00"


def test_disabling_whitespace_collapse_keeps_the_run():
    assert normalise("a      b", _with(collapse_whitespace=False)) == "a      b"


def test_markdown_syntax_is_stripped():
    assert normalise("# Tax Invoice", SHIPPED) == "Tax Invoice"


def test_a_pipe_table_becomes_its_cell_text():
    table = "| Date | Amount |\n| --- | --- |\n| 01/09/2023 | $12.00 |"

    assert normalise(table, SHIPPED) == "Date Amount 01/09/2023 $12.00"


def test_disabling_markdown_stripping_keeps_the_syntax():
    assert normalise("# Tax Invoice", _with(strip_markdown=False)) == "# Tax Invoice"


def test_case_is_not_folded_at_the_shipped_policy():
    """Reading an identifier with the right case is part of the task."""
    assert normalise("Robin Wood", SHIPPED) == "Robin Wood"


def test_case_folding_applies_when_switched_on():
    assert normalise("Robin Wood", _with(fold_case=True)) == "robin wood"


def test_compatibility_composition_applies():
    assert normalise("\ufb01ne", SHIPPED) == "fine"


def test_an_empty_string_survives():
    assert normalise("", SHIPPED) == ""


@pytest.mark.parametrize("marker", ["**bold**", "_under_", "`code`", "*star*"])
def test_emphasis_and_code_markers_are_removed(marker):
    assert normalise(marker, SHIPPED) in ("bold", "under", "code", "star")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n docparse-score python -m pytest tests/scoring/test_normalise.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scoring.normalise'`

- [ ] **Step 3: Write the implementation**

Create `scoring/normalise.py`:

```python
"""Policy-driven text normalisation.

Order is load-bearing and is fixed here rather than configurable: compose first
so every later step sees one representation of each character, strip markup
before collapsing whitespace so removed syntax cannot leave doubled gaps, and
fold case last if at all. Which steps run is policy; the order they run in is a
correctness property.
"""

import re
import unicodedata

_DASHES = str.maketrans({"\u2013": "-", "\u2014": "-", "\u2012": "-", "\u2015": "-", "\u2212": "-"})
_QUOTES = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
    }
)

# A pipe table's separator row carries no text: every cell is dashes and colons.
_SEPARATOR_ROW = re.compile(r"^\s*\|?[\s:\-|]+\|?\s*$", re.MULTILINE)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_EMPHASIS = re.compile(r"(\*{1,3}|_{1,3}|`+)")
_PIPES = re.compile(r"\|")
_WHITESPACE = re.compile(r"\s+")


def normalise(text: str, policy: dict) -> str:
    """Apply the configured normalisation steps, in fixed order.

    Args:
        text: The raw prediction or transcript.
        policy: The `normalisation` section of `config/scoring.yml`.

    Returns:
        The normalised string.
    """
    result = unicodedata.normalize(policy["unicode_form"], text)

    if policy["fold_dashes"]:
        result = result.translate(_DASHES)
    if policy["fold_quotes"]:
        result = result.translate(_QUOTES)

    if policy["strip_markdown"]:
        result = _SEPARATOR_ROW.sub(" ", result)
        result = _HEADING.sub("", result)
        result = _EMPHASIS.sub("", result)
        result = _PIPES.sub(" ", result)

    if policy["collapse_whitespace"]:
        result = _WHITESPACE.sub(" ", result).strip()
    if policy["fold_case"]:
        result = result.casefold()

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n docparse-score python -m pytest tests/scoring/test_normalise.py -v`
Expected: PASS (17 tests)

- [ ] **Step 5: Quality gates and commit**

```bash
conda run -n docparse-score ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse-score ruff format .
conda run -n docparse-score mypy scoring --ignore-missing-imports
git add scoring/normalise.py
git commit -m "✨ feat: normalise predictions and transcripts by policy"
```

---

## Task 7: Metrics

**Files:**
- Create: `scoring/metrics.py`
- Test: `tests/scoring/test_metrics.py`

**Interfaces:**
- Produces:
  - `edit_distance(a: str, b: str) -> int`
  - `character_error_rate(reference: str, prediction: str) -> float`
  - `word_error_rate(reference: str, prediction: str) -> float`
  - `is_degenerate(reference: str, prediction: str, multiple: float) -> bool`

- [ ] **Step 1: Write the failing test**

Create `tests/scoring/test_metrics.py`:

```python
"""Metric arithmetic, checked against values computable by eye."""

import pytest

from scoring.metrics import character_error_rate, edit_distance, is_degenerate, word_error_rate


def test_identical_strings_have_no_distance():
    assert edit_distance("Tax Invoice", "Tax Invoice") == 0


def test_one_substitution_is_one_edit():
    assert edit_distance("cat", "cot") == 1


def test_one_deletion_is_one_edit():
    assert edit_distance("cart", "cat") == 1


def test_cer_is_edits_over_reference_length():
    """'cat' -> 'cot' is one edit over three reference characters."""
    assert character_error_rate("cat", "cot") == pytest.approx(1 / 3)


def test_cer_is_unbounded_above():
    """A runaway prediction exceeds 1.0 rather than saturating at it."""
    assert character_error_rate("ab", "ab" + "x" * 100) > 1.0


def test_cer_of_two_empty_strings_is_zero():
    assert character_error_rate("", "") == 0.0


def test_cer_against_an_empty_reference_is_one():
    """An empty reference cannot divide; any output against it is wholly wrong."""
    assert character_error_rate("", "anything") == 1.0


def test_an_empty_prediction_scores_a_total_miss():
    assert character_error_rate("Tax Invoice", "") == 1.0


def test_wer_counts_words_not_characters():
    assert word_error_rate("the total is due", "the total was due") == pytest.approx(1 / 4)


def test_wer_of_identical_text_is_zero():
    assert word_error_rate("a b c", "a b c") == 0.0


def test_a_prediction_within_the_multiple_is_not_degenerate():
    assert is_degenerate("a" * 100, "a" * 299, 3.0) is False


def test_a_prediction_beyond_the_multiple_is_degenerate():
    assert is_degenerate("a" * 100, "a" * 301, 3.0) is True


def test_an_empty_reference_makes_any_output_degenerate():
    assert is_degenerate("", "x", 3.0) is True


def test_an_empty_prediction_is_never_degenerate():
    """It is a total miss, which the error rate already records."""
    assert is_degenerate("a" * 100, "", 3.0) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n docparse-score python -m pytest tests/scoring/test_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scoring.metrics'`

- [ ] **Step 3: Write the implementation**

Create `scoring/metrics.py`:

```python
"""Edit-distance metrics over normalised or raw text.

CER is deliberately NOT capped at 1.0. A model that emits 128,768 characters for
a 2,400-character page has failed in a way a capped score would render identical
to ordinary transcription error, and that difference is exactly what a
comparison needs to surface. `report` handles the consequence by leading with
the median rather than the mean.
"""

from rapidfuzz.distance import Levenshtein


def edit_distance(a: str, b: str) -> int:
    """Return the Levenshtein distance between two strings.

    Args:
        a: First string.
        b: Second string.

    Returns:
        The number of single-character insertions, deletions and substitutions.
    """
    return int(Levenshtein.distance(a, b))


def character_error_rate(reference: str, prediction: str) -> float:
    """Return edits per reference character.

    Args:
        reference: The ground-truth transcript.
        prediction: The model's output.

    Returns:
        `edit_distance / len(reference)`, uncapped. When the reference is empty:
        0.0 if the prediction is also empty, else 1.0 — there is nothing to
        divide by, and any output against no reference is wholly wrong.
    """
    if not reference:
        return 0.0 if not prediction else 1.0
    return edit_distance(reference, prediction) / len(reference)


def word_error_rate(reference: str, prediction: str) -> float:
    """Return word-level edits per reference word.

    Args:
        reference: The ground-truth transcript.
        prediction: The model's output.

    Returns:
        Word-level Levenshtein distance over the reference's word count, with
        the same empty-reference rule as `character_error_rate`.
    """
    ref_words = reference.split()
    pred_words = prediction.split()
    if not ref_words:
        return 0.0 if not pred_words else 1.0
    return int(Levenshtein.distance(ref_words, pred_words)) / len(ref_words)


def is_degenerate(reference: str, prediction: str, multiple: float) -> bool:
    """Report whether a prediction has run away rather than merely erred.

    Args:
        reference: The ground-truth transcript.
        prediction: The model's output.
        multiple: `reporting.degenerate_length_multiple` from the policy.

    Returns:
        True when the prediction is longer than `multiple` times the reference.
        An empty prediction is never degenerate — that is a total miss, already
        recorded by the error rate.
    """
    if not prediction:
        return False
    if not reference:
        return True
    return len(prediction) > multiple * len(reference)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n docparse-score python -m pytest tests/scoring/test_metrics.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Quality gates and commit**

```bash
conda run -n docparse-score ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse-score ruff format .
conda run -n docparse-score mypy scoring --ignore-missing-imports
git add scoring/metrics.py
git commit -m "✨ feat: add edit-distance metrics with degenerate detection"
```

---

## Task 8: Corpus loading and vintage verification

**Files:**
- Create: `scoring/corpus.py`
- Test: `tests/scoring/test_corpus.py`

**Interfaces:**
- Consumes: `ScoringError`, `diagnostic` (Task 5).
- Produces:
  - `CorpusPage` — frozen dataclass: `case_id: str`, `doc_type: str`,
    `image: Path`, `transcript: Path`, `sha256: str`, `stem: str`
  - `Corpus` — frozen dataclass: `root: Path`, `pages: tuple[CorpusPage, ...]`,
    `prompt_sha256: str`, `manifest_sha256: str`
  - `load_corpus(root: Path) -> Corpus`
  - `verify_images(corpus: Corpus) -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/scoring/test_corpus.py`:

```python
"""A corpus is loaded from its manifest, and its vintage is proved, not assumed."""

import hashlib
import json

import pytest

from scoring.corpus import load_corpus, verify_images
from scoring.errors import ScoringError
from tests.helpers import assert_diagnostic_error


def make_corpus(root, pages=2, prompt="Transcribe the page.\n"):
    """Build a minimal exported corpus on disk. Returns its root."""
    (root / "images").mkdir(parents=True)
    (root / "transcripts").mkdir(parents=True)
    (root / "prompt.md").write_text(prompt, encoding="utf-8")
    records = []
    for i in range(pages):
        stem = f"CASE{i + 1:03d}_invoices"
        image = root / "images" / f"{stem}.png"
        image.write_bytes(f"image-{stem}".encode())
        (root / "transcripts" / f"{stem}.md").write_text(f"# Invoice {i + 1}\n", encoding="utf-8")
        records.append(
            {
                "image": f"images/{stem}.png",
                "transcript": f"transcripts/{stem}.md",
                "doc_type": "invoices",
                "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
            }
        )
    (root / "manifest.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )
    return root


def test_a_corpus_loads_its_pages(tmp_path):
    corpus = load_corpus(make_corpus(tmp_path / "parsing_1"))

    assert [p.stem for p in corpus.pages] == ["CASE001_invoices", "CASE002_invoices"]
    assert corpus.pages[0].case_id == "CASE001"
    assert corpus.pages[0].doc_type == "invoices"


def test_the_prompt_is_hashed_on_load(tmp_path):
    root = make_corpus(tmp_path / "parsing_1")
    expected = hashlib.sha256((root / "prompt.md").read_bytes()).hexdigest()

    assert load_corpus(root).prompt_sha256 == expected


def test_a_directory_without_a_manifest_is_not_an_export(tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()

    with pytest.raises(ScoringError) as err:
        load_corpus(bare)

    assert_diagnostic_error(str(err.value), mentions=("manifest.jsonl", str(bare)))


def test_a_corpus_without_a_prompt_fails(tmp_path):
    root = make_corpus(tmp_path / "parsing_1")
    (root / "prompt.md").unlink()

    with pytest.raises(ScoringError) as err:
        load_corpus(root)

    assert_diagnostic_error(str(err.value), mentions=("prompt.md",))


def test_an_intact_corpus_verifies(tmp_path):
    verify_images(load_corpus(make_corpus(tmp_path / "parsing_1")))


def test_a_tampered_image_is_caught(tmp_path):
    """The guard is not vacuous: corrupt a page and verification must fail."""
    root = make_corpus(tmp_path / "parsing_1")
    corpus = load_corpus(root)
    (root / "images" / "CASE001_invoices.png").write_bytes(b"different bytes")

    with pytest.raises(ScoringError) as err:
        verify_images(corpus)

    assert_diagnostic_error(str(err.value), mentions=("CASE001_invoices.png",))


def test_a_missing_image_is_caught(tmp_path):
    root = make_corpus(tmp_path / "parsing_1")
    corpus = load_corpus(root)
    (root / "images" / "CASE002_invoices.png").unlink()

    with pytest.raises(ScoringError) as err:
        verify_images(corpus)

    assert_diagnostic_error(str(err.value), mentions=("CASE002_invoices.png",))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n docparse-score python -m pytest tests/scoring/test_corpus.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scoring.corpus'`

- [ ] **Step 3: Write the implementation**

Create `scoring/corpus.py`:

```python
"""Read an exported corpus, and prove it is the vintage it claims to be.

The shipped corpus README instructs a human to verify image hashes before
scoring. Doing it here instead is the entire point of shipping the hashes: a
score computed against the wrong vintage is not merely wrong, it is plausible.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from scoring.errors import diagnostic


@dataclass(frozen=True)
class CorpusPage:
    """One page and its reference transcript.

    Attributes:
        case_id: The case identifier, e.g. `CASE001`.
        doc_type: The document type, e.g. `bank_statements`.
        image: Absolute path to the page image.
        transcript: Absolute path to the reference transcript.
        sha256: The image hash recorded in the manifest.
        stem: The shared filename stem, which pairs a prediction to this page.
    """

    case_id: str
    doc_type: str
    image: Path
    transcript: Path
    sha256: str
    stem: str


@dataclass(frozen=True)
class Corpus:
    """An exported corpus directory.

    Attributes:
        root: The corpus directory.
        pages: Its pages, in manifest order.
        prompt_sha256: Hash of the shipped `prompt.md`.
        manifest_sha256: Hash of `manifest.jsonl`, identifying the vintage.
    """

    root: Path
    pages: tuple[CorpusPage, ...]
    prompt_sha256: str
    manifest_sha256: str


def _sha256(path: Path) -> str:
    """Return the hex sha256 of a file, read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_corpus(root: Path) -> Corpus:
    """Load an exported corpus from its manifest.

    Args:
        root: The corpus directory.

    Returns:
        The loaded corpus. Images are not read here; call `verify_images`.

    Raises:
        ScoringError: The directory holds no manifest or no prompt.
    """
    manifest = root / "manifest.jsonl"
    if not manifest.exists():
        raise diagnostic(
            f"{manifest.name} does not exist, so {root.name} is not an exported corpus.",
            path=root.resolve(),
            key="manifest.jsonl",
            expected="a directory written by `export` or `degrade`, holding images/, "
            "transcripts/, prompt.md and manifest.jsonl.",
            recover="point --corpus at an export, or run "
            "`python -m generators.pipeline export` to create one.",
        )

    prompt = root / "prompt.md"
    if not prompt.exists():
        raise diagnostic(
            "prompt.md does not exist, so the prompt a prediction used cannot be checked.",
            path=root.resolve(),
            key="prompt.md",
            expected="the prompt shipped with the corpus — prompt and transcripts are a "
            "matched pair and travel together.",
            recover="re-export the corpus, which copies config/prompt.md into it.",
        )

    pages = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        record = json.loads(line)
        stem = Path(record["image"]).stem
        pages.append(
            CorpusPage(
                case_id=stem.split("_")[0],
                doc_type=str(record["doc_type"]),
                image=root / record["image"],
                transcript=root / record["transcript"],
                sha256=str(record["sha256"]),
                stem=stem,
            )
        )

    return Corpus(
        root=root,
        pages=tuple(pages),
        prompt_sha256=_sha256(prompt),
        manifest_sha256=_sha256(manifest),
    )


def verify_images(corpus: Corpus) -> None:
    """Check every image against its manifest hash.

    Args:
        corpus: The loaded corpus.

    Raises:
        ScoringError: An image is missing, or its bytes do not match.
    """
    for page in corpus.pages:
        if not page.image.exists():
            raise diagnostic(
                f"{page.image.name} is named in the manifest but is not on disk.",
                path=corpus.root.resolve(),
                key=f"images/{page.image.name}",
                expected="every image the manifest lists, present and unmodified.",
                recover="restore the missing page, or re-export the corpus.",
            )
        actual = _sha256(page.image)
        if actual != page.sha256:
            raise diagnostic(
                f"{page.image.name} does not match its manifest hash — this is a different "
                f"corpus vintage. Expected {page.sha256[:12]}…, found {actual[:12]}….",
                path=corpus.root.resolve(),
                key=f"images/{page.image.name}",
                expected="image bytes matching the sha256 recorded in manifest.jsonl.",
                recover="score against the corpus the predictions were produced from; a "
                "score across vintages is meaningless.",
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n docparse-score python -m pytest tests/scoring/test_corpus.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Quality gates and commit**

```bash
conda run -n docparse-score ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse-score ruff format .
conda run -n docparse-score mypy scoring --ignore-missing-imports
git add scoring/corpus.py
git commit -m "✨ feat: load an exported corpus and verify its vintage"
```

---

## Task 9: Prediction loading and the matched-pair guards

**Files:**
- Create: `scoring/predictions.py`
- Test: `tests/scoring/test_predictions.py`

**Interfaces:**
- Consumes: `Corpus`, `CorpusPage` (Task 8); `diagnostic`, `ScoringError` (Task 5).
- Produces:
  - `RunMetadata` — frozen dataclass: `model_id`, `model_revision`,
    `prompt_sha256`, `corpus`, `corpus_manifest_sha256`, `generated_at`, `host`
    (all `str`)
  - `PredictionSet` — frozen dataclass: `run: RunMetadata`,
    `texts: dict[str, str | None]` keyed by page stem, `None` when absent
  - `load_predictions(root: Path, corpus: Corpus, *, allow_missing: bool = False) -> PredictionSet`

- [ ] **Step 1: Write the failing test**

Create `tests/scoring/test_predictions.py`:

```python
"""A prediction set is bound to one prompt and one corpus vintage, or it is refused."""

import json

import pytest

from scoring.corpus import load_corpus
from scoring.errors import ScoringError
from scoring.predictions import load_predictions
from tests.helpers import assert_diagnostic_error
from tests.scoring.test_corpus import make_corpus

REQUIRED_RUN_KEYS = (
    "model_id",
    "model_revision",
    "prompt_sha256",
    "corpus",
    "corpus_manifest_sha256",
    "generated_at",
    "host",
)


def make_predictions(root, corpus, *, texts=None, **overrides):
    """Build a prediction directory that matches `corpus` unless overridden."""
    root.mkdir(parents=True)
    run = {
        "model_id": "test-model",
        "model_revision": "abc123",
        "prompt_sha256": corpus.prompt_sha256,
        "corpus": corpus.root.name,
        "corpus_manifest_sha256": corpus.manifest_sha256,
        "generated_at": "2026-08-26T09:14:03Z",
        "host": "gpu-01",
    }
    run.update(overrides)
    (root / "run.json").write_text(json.dumps(run), encoding="utf-8")
    for page in corpus.pages:
        body = (texts or {}).get(page.stem, page.transcript.read_text(encoding="utf-8"))
        if body is not None:
            (root / f"{page.stem}.md").write_text(body, encoding="utf-8")
    return root


def test_a_matching_set_loads(tmp_path):
    corpus = load_corpus(make_corpus(tmp_path / "parsing_1"))
    predictions = load_predictions(make_predictions(tmp_path / "pred", corpus), corpus)

    assert predictions.run.model_id == "test-model"
    assert set(predictions.texts) == {p.stem for p in corpus.pages}


def test_a_wrong_prompt_hash_is_refused(tmp_path):
    """Prompt and transcripts are a matched pair; a different prompt measures something else."""
    corpus = load_corpus(make_corpus(tmp_path / "parsing_1"))
    root = make_predictions(tmp_path / "pred", corpus, prompt_sha256="0" * 64)

    with pytest.raises(ScoringError) as err:
        load_predictions(root, corpus)

    assert_diagnostic_error(str(err.value), mentions=("prompt_sha256",))


def test_a_wrong_corpus_vintage_is_refused(tmp_path):
    """Recorded by the runner on the remote host; checked here."""
    corpus = load_corpus(make_corpus(tmp_path / "parsing_1"))
    root = make_predictions(tmp_path / "pred", corpus, corpus_manifest_sha256="0" * 64)

    with pytest.raises(ScoringError) as err:
        load_predictions(root, corpus)

    assert_diagnostic_error(str(err.value), mentions=("corpus_manifest_sha256",))


@pytest.mark.parametrize("key", REQUIRED_RUN_KEYS)
def test_an_incomplete_run_json_is_refused(tmp_path, key):
    corpus = load_corpus(make_corpus(tmp_path / "parsing_1"))
    root = make_predictions(tmp_path / "pred", corpus)
    run = json.loads((root / "run.json").read_text(encoding="utf-8"))
    del run[key]
    (root / "run.json").write_text(json.dumps(run), encoding="utf-8")

    with pytest.raises(ScoringError) as err:
        load_predictions(root, corpus)

    assert_diagnostic_error(str(err.value), mentions=(key,))


def test_a_missing_prediction_is_an_error_by_default(tmp_path):
    """Scoring 1 of 2 pages and reporting the mean flatters the failure."""
    corpus = load_corpus(make_corpus(tmp_path / "parsing_1"))
    root = make_predictions(tmp_path / "pred", corpus, texts={"CASE002_invoices": None})

    with pytest.raises(ScoringError) as err:
        load_predictions(root, corpus)

    assert_diagnostic_error(str(err.value), mentions=("CASE002_invoices", "--allow-missing"))


def test_allow_missing_records_the_absence_as_none(tmp_path):
    corpus = load_corpus(make_corpus(tmp_path / "parsing_1"))
    root = make_predictions(tmp_path / "pred", corpus, texts={"CASE002_invoices": None})

    predictions = load_predictions(root, corpus, allow_missing=True)

    assert predictions.texts["CASE002_invoices"] is None
    assert predictions.texts["CASE001_invoices"] is not None


def test_an_empty_prediction_is_a_value_not_an_absence(tmp_path):
    """A model that answered with nothing is different from one that did not answer."""
    corpus = load_corpus(make_corpus(tmp_path / "parsing_1"))
    root = make_predictions(tmp_path / "pred", corpus, texts={"CASE002_invoices": ""})

    predictions = load_predictions(root, corpus)

    assert predictions.texts["CASE002_invoices"] == ""


def test_a_directory_without_run_json_is_refused(tmp_path):
    corpus = load_corpus(make_corpus(tmp_path / "parsing_1"))
    root = make_predictions(tmp_path / "pred", corpus)
    (root / "run.json").unlink()

    with pytest.raises(ScoringError) as err:
        load_predictions(root, corpus)

    assert_diagnostic_error(str(err.value), mentions=("run.json",))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n docparse-score python -m pytest tests/scoring/test_predictions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scoring.predictions'`

- [ ] **Step 3: Write the implementation**

Create `scoring/predictions.py`:

```python
"""Load a model's predictions, bound to one prompt and one corpus vintage.

Two hashes are checked, and they close different holes. `prompt_sha256` catches
a prediction produced with a different prompt: prompt and transcripts are a
matched pair, and a model told to do something else is not being measured on
this benchmark. `corpus_manifest_sha256` is recorded by the runner on the remote
host and catches a prediction produced against a different corpus vintage --
verifying local images proves only that the local copy is intact, which says
nothing about what the GPU box actually read.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from scoring.corpus import Corpus
from scoring.errors import diagnostic

_REQUIRED_RUN_KEYS: tuple[str, ...] = (
    "model_id",
    "model_revision",
    "prompt_sha256",
    "corpus",
    "corpus_manifest_sha256",
    "generated_at",
    "host",
)


@dataclass(frozen=True)
class RunMetadata:
    """Provenance for one model's pass over one corpus.

    Attributes:
        model_id: The model identifier used in reports.
        model_revision: Checkpoint or revision, for reproducibility.
        prompt_sha256: Hash of the prompt the runner used.
        corpus: Name of the corpus directory the runner read.
        corpus_manifest_sha256: Hash of that corpus's manifest, from the runner.
        generated_at: ISO-8601 timestamp of the run.
        host: Where inference ran.
    """

    model_id: str
    model_revision: str
    prompt_sha256: str
    corpus: str
    corpus_manifest_sha256: str
    generated_at: str
    host: str


@dataclass(frozen=True)
class PredictionSet:
    """One model's output for one corpus.

    Attributes:
        run: The run's provenance.
        texts: Prediction text by page stem; None where the file was absent and
            `allow_missing` was set.
    """

    run: RunMetadata
    texts: dict[str, str | None]


def load_predictions(root: Path, corpus: Corpus, *, allow_missing: bool = False) -> PredictionSet:
    """Load and verify a prediction directory against a corpus.

    Args:
        root: Directory holding `run.json` and one `.md` per page stem.
        corpus: The corpus these predictions claim to be for.
        allow_missing: Record an absent prediction as None instead of failing.

    Returns:
        The verified prediction set.

    Raises:
        ScoringError: `run.json` is absent or incomplete, either hash disagrees
            with the corpus, or a prediction is missing without `allow_missing`.
    """
    run_path = root / "run.json"
    if not run_path.exists():
        raise diagnostic(
            "run.json does not exist, so this prediction set carries no provenance.",
            path=root.resolve(),
            key="run.json",
            expected="a JSON object with "
            f"{list(_REQUIRED_RUN_KEYS)}, written by the runner beside its predictions.",
            recover="have the runner emit run.json, recording the prompt and corpus "
            "manifest hashes it actually read.",
        )

    data = json.loads(run_path.read_text(encoding="utf-8"))
    for key in _REQUIRED_RUN_KEYS:
        if key not in data:
            raise diagnostic(
                f"'{key}' is missing from run.json.",
                path=run_path.resolve(),
                key=key,
                expected=f"every key of {list(_REQUIRED_RUN_KEYS)}; provenance is not optional, "
                f'e.g.\n              "{key}": "…"',
                recover=f"add '{key}' to run.json in the runner.",
            )
    run = RunMetadata(**{key: str(data[key]) for key in _REQUIRED_RUN_KEYS})

    if run.prompt_sha256 != corpus.prompt_sha256:
        raise diagnostic(
            "the predictions were produced with a different prompt than this corpus ships.",
            path=run_path.resolve(),
            key="prompt_sha256",
            expected=f"{corpus.prompt_sha256[:12]}… — the hash of {corpus.root.name}/prompt.md; "
            f"found {run.prompt_sha256[:12]}….",
            recover="re-run the model with the prompt shipped in the corpus; prompt and "
            "transcripts are a matched pair and scoring across them measures something else.",
        )

    if run.corpus_manifest_sha256 != corpus.manifest_sha256:
        raise diagnostic(
            "the predictions were produced against a different corpus vintage.",
            path=run_path.resolve(),
            key="corpus_manifest_sha256",
            expected=f"{corpus.manifest_sha256[:12]}… — the hash of {corpus.root.name}/"
            f"manifest.jsonl; found {run.corpus_manifest_sha256[:12]}….",
            recover="score against the corpus the runner actually read, or re-run the model "
            "against this one.",
        )

    texts: dict[str, str | None] = {}
    for page in corpus.pages:
        path = root / f"{page.stem}.md"
        if path.exists():
            texts[page.stem] = path.read_text(encoding="utf-8")
            continue
        if not allow_missing:
            raise diagnostic(
                f"no prediction for {page.stem}.",
                path=root.resolve(),
                key=f"{page.stem}.md",
                expected="one prediction file per page in the corpus — scoring a subset and "
                "reporting the mean flatters a model that failed to answer.",
                recover="produce the missing prediction, or pass --allow-missing to record "
                "the absence in the output.",
            )
        texts[page.stem] = None

    return PredictionSet(run=run, texts=texts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n docparse-score python -m pytest tests/scoring/test_predictions.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Quality gates and commit**

```bash
conda run -n docparse-score ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse-score ruff format .
conda run -n docparse-score mypy scoring --ignore-missing-imports
git add scoring/predictions.py
git commit -m "✨ feat: bind a prediction set to one prompt and one vintage"
```

---

## Task 10: The `score` command

**Files:**
- Create: `scoring/score.py`
- Test: `tests/scoring/test_score.py`

**Interfaces:**
- Consumes: everything from Tasks 5–9.
- Produces:
  - `score_page(reference: str, prediction: str | None, policy: dict, *, verified: bool) -> dict`
    returning the metric fields of a row
  - `score_corpus(corpus, predictions, policy, *, family, severity, verified) -> list[dict]`
  - a typer app exposing `--matrix` / `--corpus`, `--predictions-root` / `--predictions`,
    `--out`, `--allow-missing`, `--skip-verify`, `--policy`

**Guard coverage:** spec §7 guard 7 — "a `matrix.jsonl` row names a corpus not on
disk" — surfaces here through `load_corpus`, which raises guard 1's diagnostic
naming the absent directory. It needs no separate check; guard 1's behaviour is
already tested in Task 8.

- [ ] **Step 1: Write the failing test**

Create `tests/scoring/test_score.py`:

```python
"""One row per page, carrying enough to re-slice the comparison without re-scoring."""

import json
from pathlib import Path

from scoring.corpus import load_corpus
from scoring.policy import load_scoring_policy
from scoring.predictions import load_predictions
from scoring.score import score_corpus, score_page
from tests.scoring.test_corpus import make_corpus
from tests.scoring.test_predictions import make_predictions

POLICY = load_scoring_policy(Path("config/scoring.yml"))


def test_a_perfect_prediction_scores_zero():
    row = score_page("# Tax Invoice\n", "# Tax Invoice\n", POLICY, verified=True)

    assert row["strict_edit_distance"] == 0
    assert row["normalised_cer"] == 0.0
    assert row["prediction_present"] is True
    assert row["degenerate"] is False


def test_markup_differences_cost_strict_but_not_normalised():
    """The point of reporting both numbers."""
    row = score_page("# Tax Invoice", "Tax Invoice", POLICY, verified=True)

    assert row["strict_edit_distance"] > 0
    assert row["normalised_edit_distance"] == 0


def test_a_missing_prediction_has_null_distances():
    row = score_page("# Tax Invoice", None, POLICY, verified=True)

    assert row["prediction_present"] is False
    assert row["strict_edit_distance"] is None
    assert row["normalised_cer"] is None


def test_an_empty_prediction_is_scored_as_a_total_miss():
    row = score_page("# Tax Invoice", "", POLICY, verified=True)

    assert row["prediction_present"] is True
    assert row["normalised_cer"] == 1.0


def test_a_runaway_prediction_is_flagged_degenerate():
    row = score_page("short reference", "x" * 500, POLICY, verified=True)

    assert row["degenerate"] is True


def test_the_verified_flag_is_carried_onto_the_row():
    assert score_page("a", "a", POLICY, verified=False)["verified"] is False


def test_score_corpus_emits_one_row_per_page(tmp_path):
    corpus = load_corpus(make_corpus(tmp_path / "parsing_1"))
    predictions = load_predictions(make_predictions(tmp_path / "pred", corpus), corpus)

    rows = score_corpus(
        corpus, predictions, POLICY, family="clean", severity="none", verified=True
    )

    assert len(rows) == len(corpus.pages)
    assert {r["case_id"] for r in rows} == {"CASE001", "CASE002"}
    assert all(r["model"] == "test-model" for r in rows)
    assert all(r["family"] == "clean" and r["severity"] == "none" for r in rows)
    assert all(r["doc_type"] == "invoices" for r in rows)


def test_rows_round_trip_as_jsonl(tmp_path):
    corpus = load_corpus(make_corpus(tmp_path / "parsing_1"))
    predictions = load_predictions(make_predictions(tmp_path / "pred", corpus), corpus)

    rows = score_corpus(
        corpus, predictions, POLICY, family="clean", severity="none", verified=True
    )
    text = "".join(json.dumps(r) + "\n" for r in rows)

    assert [json.loads(line) for line in text.splitlines()] == rows
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n docparse-score python -m pytest tests/scoring/test_score.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scoring.score'`

- [ ] **Step 3: Write the implementation**

Create `scoring/score.py`:

```python
"""Score predictions against a corpus, one JSONL row per page.

Scoring and aggregation are separate commands for the same reason `generate` and
`serialise` are: capture once, interpret many times. Re-slicing a comparison by
document type, tier or model then costs seconds instead of re-reading a thousand
pages.
"""

import json
from pathlib import Path
from typing import Annotated

import typer
from rich import print as rprint

from scoring.corpus import Corpus, load_corpus, verify_images
from scoring.errors import ScoringError, diagnostic
from scoring.metrics import character_error_rate, edit_distance, is_degenerate, word_error_rate
from scoring.normalise import normalise
from scoring.policy import load_scoring_policy
from scoring.predictions import PredictionSet, load_predictions

app = typer.Typer(add_completion=False, help="Score model predictions against an exported corpus.")

_DEFAULT_POLICY = Path("config/scoring.yml")


def score_page(reference: str, prediction: str | None, policy: dict, *, verified: bool) -> dict:
    """Score one prediction against one reference.

    Args:
        reference: The corpus transcript.
        prediction: The model's output, or None when the file was absent.
        policy: The whole validated policy mapping.
        verified: Whether the corpus hashes were checked for this run.

    Returns:
        The metric fields of a row. Every distance is None when the prediction
        is absent, so an absence is carried in the data rather than dropped.
    """
    if prediction is None:
        return {
            "ref_chars": len(reference),
            "pred_chars": None,
            "prediction_present": False,
            "verified": verified,
            "strict_edit_distance": None,
            "strict_cer": None,
            "normalised_edit_distance": None,
            "normalised_cer": None,
            "normalised_wer": None,
            "degenerate": False,
        }

    rules = policy["normalisation"]
    ref_norm = normalise(reference, rules)
    pred_norm = normalise(prediction, rules)
    return {
        "ref_chars": len(reference),
        "pred_chars": len(prediction),
        "prediction_present": True,
        "verified": verified,
        "strict_edit_distance": edit_distance(reference, prediction),
        "strict_cer": character_error_rate(reference, prediction),
        "normalised_edit_distance": edit_distance(ref_norm, pred_norm),
        "normalised_cer": character_error_rate(ref_norm, pred_norm),
        "normalised_wer": word_error_rate(ref_norm, pred_norm),
        "degenerate": is_degenerate(
            reference, prediction, float(policy["reporting"]["degenerate_length_multiple"])
        ),
    }


def score_corpus(
    corpus: Corpus,
    predictions: PredictionSet,
    policy: dict,
    *,
    family: str,
    severity: str,
    verified: bool,
) -> list[dict]:
    """Score every page of one corpus for one model.

    Args:
        corpus: The loaded corpus.
        predictions: The verified prediction set.
        policy: The whole validated policy mapping.
        family: Intake family from the matrix row.
        severity: Tier severity from the matrix row.
        verified: Whether image hashes were checked.

    Returns:
        One row per page, in manifest order.
    """
    rows = []
    for page in corpus.pages:
        reference = page.transcript.read_text(encoding="utf-8")
        row = {
            "model": predictions.run.model_id,
            "corpus": corpus.root.name,
            "case_id": page.case_id,
            "doc_type": page.doc_type,
            "family": family,
            "severity": severity,
        }
        row.update(score_page(reference, predictions.texts[page.stem], policy, verified=verified))
        rows.append(row)
    return rows


def _matrix_rows(matrix_path: Path) -> list[dict]:
    """Read matrix.jsonl, failing fast when it is absent.

    Args:
        matrix_path: Path to `matrix.jsonl`.

    Returns:
        The matrix rows.

    Raises:
        ScoringError: The file does not exist.
    """
    if not matrix_path.exists():
        raise diagnostic(
            f"{matrix_path.name} does not exist.",
            path=matrix_path.resolve(),
            key="matrix.jsonl",
            expected="the index written by `degrade`, listing the clean corpus and its tiers.",
            recover="run `python -m generators.degradation.cli --corpus <clean export>` "
            "to build it, or pass --corpus to score a single corpus.",
        )
    return [json.loads(line) for line in matrix_path.read_text(encoding="utf-8").splitlines() if line]


@app.command()
def score(
    matrix: Annotated[
        Path | None, typer.Option("--matrix", help="matrix.jsonl; scores every corpus it lists.")
    ] = None,
    corpus_dir: Annotated[
        Path | None, typer.Option("--corpus", help="A single exported corpus to score.")
    ] = None,
    predictions_root: Annotated[
        Path | None,
        typer.Option("--predictions-root", help="Holds <model>/<corpus>/ directories."),
    ] = None,
    predictions: Annotated[
        Path | None, typer.Option("--predictions", help="One model's predictions for --corpus.")
    ] = None,
    out: Annotated[Path, typer.Option("--out", help="Where rows are written.")] = Path("rows.jsonl"),
    policy_path: Annotated[
        Path, typer.Option("--policy", help="Path to scoring.yml")
    ] = _DEFAULT_POLICY,
    allow_missing: Annotated[
        bool, typer.Option("--allow-missing", help="Record absent predictions instead of failing.")
    ] = False,
    skip_verify: Annotated[
        bool, typer.Option("--skip-verify", help="Skip image hash verification (records it).")
    ] = False,
) -> None:
    """Score one corpus, or every corpus in a matrix.

    Raises:
        typer.Exit: With code 1 on any verification or configuration failure.
    """
    try:
        policy = load_scoring_policy(policy_path)
        rows: list[dict] = []

        if matrix is not None:
            if predictions_root is None:
                raise diagnostic(
                    "--matrix was given without --predictions-root.",
                    path=matrix.resolve(),
                    key="--predictions-root",
                    expected="a directory holding <model>/<corpus>/ prediction directories.",
                    recover="pass --predictions-root, or score one corpus with --corpus.",
                )
            for entry in _matrix_rows(matrix):
                loaded = load_corpus(matrix.parent / entry["corpus"])
                if not skip_verify:
                    verify_images(loaded)
                for model_dir in sorted(p for p in predictions_root.iterdir() if p.is_dir()):
                    set_dir = model_dir / entry["corpus"]
                    if not set_dir.is_dir():
                        continue
                    loaded_predictions = load_predictions(
                        set_dir, loaded, allow_missing=allow_missing
                    )
                    rows.extend(
                        score_corpus(
                            loaded,
                            loaded_predictions,
                            policy,
                            family=str(entry["family"]),
                            severity=str(entry["severity"]),
                            verified=not skip_verify,
                        )
                    )
        else:
            if corpus_dir is None or predictions is None:
                raise diagnostic(
                    "neither --matrix nor --corpus/--predictions were given.",
                    path=Path.cwd(),
                    key="--matrix | --corpus",
                    expected="either --matrix with --predictions-root, or --corpus with "
                    "--predictions.",
                    recover="pass one of those pairs.",
                )
            loaded = load_corpus(corpus_dir)
            if not skip_verify:
                verify_images(loaded)
            loaded_predictions = load_predictions(predictions, loaded, allow_missing=allow_missing)
            rows = score_corpus(
                loaded,
                loaded_predictions,
                policy,
                family="clean",
                severity="none",
                verified=not skip_verify,
            )
    except ScoringError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None

    out.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    missing = sum(1 for row in rows if not row["prediction_present"])
    rprint(f"[green]Scored {len(rows)} page(s) into {out}.[/green]")
    if missing:
        rprint(f"[yellow]{missing} page(s) had no prediction and were recorded as absent.[/yellow]")
    if skip_verify:
        rprint("[yellow]Image hashes were NOT verified; rows record verified=false.[/yellow]")


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n docparse-score python -m pytest tests/scoring/test_score.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Quality gates and commit**

```bash
conda run -n docparse-score ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse-score ruff format .
conda run -n docparse-score mypy scoring --ignore-missing-imports
git add scoring/score.py
git commit -m "✨ feat: score a corpus or a whole matrix into JSONL rows"
```

---

## Task 11: The `report` command

**Files:**
- Create: `scoring/report.py`
- Test: `tests/scoring/test_report.py`

**Interfaces:**
- Consumes: rows produced by Task 10; `load_scoring_policy` (Task 5).
- Produces:
  - `aggregate(rows: list[dict], group_by: tuple[str, ...], policy: dict) -> list[dict]`
  - `render_markdown(groups: list[dict], group_by: tuple[str, ...]) -> str`
  - `render_csv(groups: list[dict], group_by: tuple[str, ...]) -> str`
  - a typer app exposing `--rows`, `--group-by`, `--format`, `--out`

- [ ] **Step 1: Write the failing test**

Create `tests/scoring/test_report.py`:

```python
"""Aggregation leads with the median, and never hides a catastrophe in a mean."""

from pathlib import Path

from scoring.policy import load_scoring_policy
from scoring.report import aggregate, render_csv, render_markdown

POLICY = load_scoring_policy(Path("config/scoring.yml"))


def _row(model="m", doc_type="invoices", family="clean", severity="none", cer=0.1, **extra):
    row = {
        "model": model,
        "corpus": "parsing_1",
        "case_id": "CASE001",
        "doc_type": doc_type,
        "family": family,
        "severity": severity,
        "ref_chars": 100,
        "pred_chars": 100,
        "prediction_present": True,
        "verified": True,
        "strict_edit_distance": 10,
        "strict_cer": cer,
        "normalised_edit_distance": 10,
        "normalised_cer": cer,
        "normalised_wer": cer,
        "degenerate": False,
    }
    row.update(extra)
    return row


def test_rows_group_by_the_requested_keys():
    rows = [_row(model="a"), _row(model="b"), _row(model="a")]

    groups = aggregate(rows, ("model",), POLICY)

    assert sorted(g["model"] for g in groups) == ["a", "b"]
    assert next(g for g in groups if g["model"] == "a")["pages"] == 2


def test_the_median_is_reported_and_resists_one_runaway():
    """Nine good pages and one catastrophe: the median stays good, the mean does not."""
    rows = [_row(cer=0.1) for _ in range(9)] + [_row(cer=50.0)]

    group = aggregate(rows, ("model",), POLICY)[0]

    assert group["normalised_cer_p50"] < 0.2
    assert group["normalised_cer_mean"] > 1.0


def test_the_worst_case_is_reported():
    rows = [_row(cer=0.1), _row(cer=0.9)]

    assert aggregate(rows, ("model",), POLICY)[0]["normalised_cer_p100"] == 0.9


def test_degenerate_pages_are_counted_not_dropped():
    rows = [_row(cer=0.1), _row(cer=50.0, degenerate=True)]

    group = aggregate(rows, ("model",), POLICY)[0]

    assert group["degenerate"] == 1
    assert group["pages"] == 2


def test_absent_predictions_are_counted_and_excluded_from_the_distribution():
    rows = [
        _row(cer=0.2),
        _row(prediction_present=False, normalised_cer=None, strict_cer=None),
    ]

    group = aggregate(rows, ("model",), POLICY)[0]

    assert group["missing"] == 1
    assert group["pages"] == 2
    assert group["normalised_cer_p50"] == 0.2


def test_a_group_with_no_scored_pages_reports_nulls_not_a_crash():
    rows = [_row(prediction_present=False, normalised_cer=None, strict_cer=None)]

    group = aggregate(rows, ("model",), POLICY)[0]

    assert group["missing"] == 1
    assert group["normalised_cer_p50"] is None


def test_an_unverified_group_is_flagged():
    rows = [_row(verified=False)]

    assert aggregate(rows, ("model",), POLICY)[0]["verified"] is False


def test_markdown_renders_a_table_with_a_header_rule():
    out = render_markdown(aggregate([_row()], ("model", "doc_type"), POLICY), ("model", "doc_type"))

    assert out.splitlines()[0].startswith("| model | doc_type |")
    assert set(out.splitlines()[1].replace("|", "").replace(" ", "")) <= {"-", ":"}


def test_csv_renders_a_header_and_one_line_per_group():
    out = render_csv(aggregate([_row(model="a"), _row(model="b")], ("model",), POLICY), ("model",))

    assert out.splitlines()[0].startswith("model,")
    assert len(out.strip().splitlines()) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n docparse-score python -m pytest tests/scoring/test_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scoring.report'`

- [ ] **Step 3: Write the implementation**

Create `scoring/report.py`:

```python
"""Aggregate scored rows into a comparison.

Median first, and the mean reported beside it rather than instead of it. CER is
`edits / reference length` and is unbounded above, so one runaway page can
dominate a mean; equally a mean can bury two catastrophic failures behind a
hundred good pages. Reporting the median, the percentiles, the mean and an
explicit degenerate count keeps both failure modes visible.

Absent predictions are counted separately and excluded from the distribution: a
page the model never answered is not a score of anything, but it must not
disappear either.
"""

import csv
import io
import json
from pathlib import Path
from typing import Annotated

import typer
from rich import print as rprint

from scoring.errors import ScoringError, diagnostic
from scoring.policy import load_scoring_policy

app = typer.Typer(add_completion=False, help="Aggregate scored rows into a comparison.")

_METRICS = ("normalised_cer", "strict_cer", "normalised_wer")
_GROUP_KEYS = ("model", "corpus", "doc_type", "family", "severity")


def _percentile(values: list[float], p: int) -> float:
    """Return the p-th percentile by nearest-rank, on a sorted copy.

    Nearest-rank rather than interpolation: with as few as five pages in a
    group, an interpolated "worst case" is a value no page actually scored.

    Args:
        values: The sample; must be non-empty.
        p: Percentile in 1..100.

    Returns:
        The selected value.
    """
    ordered = sorted(values)
    rank = max(1, -(-p * len(ordered) // 100))
    return ordered[rank - 1]


def aggregate(rows: list[dict], group_by: tuple[str, ...], policy: dict) -> list[dict]:
    """Group rows and summarise each group.

    Args:
        rows: Rows emitted by `scoring.score`.
        group_by: Row keys to group on, e.g. `("model", "doc_type")`.
        policy: The validated policy, read for `reporting.percentiles`.

    Returns:
        One summary per group, ordered by the group key tuple.
    """
    percentiles = [int(p) for p in policy["reporting"]["percentiles"]]
    buckets: dict[tuple, list[dict]] = {}
    for row in rows:
        buckets.setdefault(tuple(row[key] for key in group_by), []).append(row)

    groups = []
    for key, bucket in sorted(buckets.items(), key=lambda item: tuple(str(v) for v in item[0])):
        scored = [r for r in bucket if r["prediction_present"]]
        group: dict = dict(zip(group_by, key, strict=True))
        group["pages"] = len(bucket)
        group["missing"] = len(bucket) - len(scored)
        group["degenerate"] = sum(1 for r in bucket if r["degenerate"])
        group["verified"] = all(r["verified"] for r in bucket)
        for metric in _METRICS:
            values = [float(r[metric]) for r in scored if r[metric] is not None]
            for p in percentiles:
                group[f"{metric}_p{p}"] = _percentile(values, p) if values else None
            group[f"{metric}_mean"] = sum(values) / len(values) if values else None
        groups.append(group)
    return groups


def _columns(group_by: tuple[str, ...], groups: list[dict]) -> list[str]:
    """Return the column order: group keys first, then every summary field."""
    if not groups:
        return list(group_by)
    return list(group_by) + [k for k in groups[0] if k not in group_by]


def _cell(value: object) -> str:
    """Format one cell: floats to four places, None as an em dash."""
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def render_markdown(groups: list[dict], group_by: tuple[str, ...]) -> str:
    """Render groups as a Markdown pipe table.

    Args:
        groups: Summaries from `aggregate`.
        group_by: The keys grouped on, rendered as the leading columns.

    Returns:
        The table, header rule included.
    """
    columns = _columns(group_by, groups)
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    lines += ["| " + " | ".join(_cell(g.get(c)) for c in columns) + " |" for g in groups]
    return "\n".join(lines) + "\n"


def render_csv(groups: list[dict], group_by: tuple[str, ...]) -> str:
    """Render groups as CSV.

    Args:
        groups: Summaries from `aggregate`.
        group_by: The keys grouped on, rendered as the leading columns.

    Returns:
        CSV text with a header row.
    """
    columns = _columns(group_by, groups)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for group in groups:
        writer.writerow({c: group.get(c) for c in columns})
    return buffer.getvalue()


@app.command()
def report(
    rows_paths: Annotated[list[Path], typer.Option("--rows", help="Row files; repeatable.")],
    group_by: Annotated[
        str, typer.Option("--group-by", help="Comma-separated row keys.")
    ] = "model,doc_type,family,severity",
    output_format: Annotated[str, typer.Option("--format", help="markdown or csv")] = "markdown",
    policy_path: Annotated[
        Path, typer.Option("--policy", help="Path to scoring.yml")
    ] = Path("config/scoring.yml"),
    out: Annotated[Path | None, typer.Option("--out", help="Write here instead of stdout.")] = None,
) -> None:
    """Aggregate one or more row files into a comparison table.

    Raises:
        typer.Exit: With code 1 on an unknown group key or format.
    """
    try:
        policy = load_scoring_policy(policy_path)
        keys = tuple(k.strip() for k in group_by.split(",") if k.strip())
        unknown = [k for k in keys if k not in _GROUP_KEYS]
        if unknown:
            raise diagnostic(
                f"--group-by names {unknown}, which are not row keys.",
                path=Path.cwd(),
                key="--group-by",
                expected=f"a comma-separated subset of {list(_GROUP_KEYS)}, e.g.\n"
                "              --group-by model,doc_type,family,severity",
                recover="use only those keys.",
            )
        if output_format not in ("markdown", "csv"):
            raise diagnostic(
                f"--format is {output_format!r}.",
                path=Path.cwd(),
                key="--format",
                expected="markdown or csv, e.g.\n              --format markdown",
                recover="pass one of those two values.",
            )

        rows: list[dict] = []
        for path in rows_paths:
            if not path.exists():
                raise diagnostic(
                    f"{path} does not exist.",
                    path=path.resolve(),
                    key="--rows",
                    expected="a JSONL file written by `python -m scoring.score --out`.",
                    recover="run scoring.score first, or correct the path.",
                )
            rows += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except ScoringError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None

    groups = aggregate(rows, keys, policy)
    text = render_markdown(groups, keys) if output_format == "markdown" else render_csv(groups, keys)

    if out is not None:
        out.write_text(text, encoding="utf-8")
        rprint(f"[green]Wrote {len(groups)} group(s) to {out}.[/green]")
    else:
        print(text)

    if any(not g["verified"] for g in groups):
        rprint("[yellow]Some groups were scored without hash verification.[/yellow]")


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n docparse-score python -m pytest tests/scoring/test_report.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Check coverage clears the floor**

Run: `conda run -n docparse-score python -m pytest tests/scoring/ -q --cov=scoring --cov-report=term`
Expected: `scoring` at or above 80%. If below, add tests for the uncovered branches
before committing — do not lower the floor.

- [ ] **Step 6: Quality gates and commit**

```bash
conda run -n docparse-score ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse-score ruff format .
conda run -n docparse-score mypy scoring --ignore-missing-imports
git add scoring/report.py
git commit -m "✨ feat: aggregate scored rows into a model comparison"
```

---

## Task 12: Generate the shipped README's normalisation prose from the policy

**Files:**
- Modify: `generators/export.py`
- Test: `tests/test_export.py`

**Interfaces:**
- Consumes: `config/scoring.yml` (Task 5) — read as YAML, **not** by importing
  `scoring`, which lives in another environment.
- Produces: `_normalisation_sentence(policy: dict) -> str` in `generators/export.py`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_export.py`:

```python
def test_the_readme_describes_the_normalisation_the_scorer_applies():
    """The shipped description and config/scoring.yml cannot disagree."""
    import yaml

    from generators.export import _normalisation_sentence

    policy = yaml.safe_load(Path("config/scoring.yml").read_text(encoding="utf-8"))
    sentence = _normalisation_sentence(policy)

    assert "NFKC" in sentence
    assert "collapse whitespace runs" in sentence
    assert "do not fold case" in sentence.lower()


def test_disabling_a_step_changes_the_shipped_description():
    """The generated prose tracks the policy rather than restating it."""
    import copy

    import yaml

    from generators.export import _normalisation_sentence

    policy = yaml.safe_load(Path("config/scoring.yml").read_text(encoding="utf-8"))
    without = copy.deepcopy(policy)
    without["normalisation"]["fold_dashes"] = False

    assert _normalisation_sentence(policy) != _normalisation_sentence(without)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n docparse python -m pytest tests/test_export.py -k normalisation -v`
Expected: FAIL — `ImportError: cannot import name '_normalisation_sentence'`

- [ ] **Step 3: Write the implementation**

Add to `generators/export.py`:

```python
_NORMALISATION_STEPS: tuple[tuple[str, str], ...] = (
    ("collapse_whitespace", "collapse whitespace runs"),
    ("fold_dashes", "fold dashes to ASCII"),
    ("fold_quotes", "fold quotes to ASCII"),
    ("strip_markdown", "strip Markdown syntax"),
)


def _normalisation_sentence(policy: dict) -> str:
    """Describe the normalisation policy in the prose the corpus ships.

    Generated rather than written out, so the shipped README and
    `config/scoring.yml` cannot drift apart about what "normalised" means. Read
    as plain YAML: `scoring/` lives in another environment and is deliberately
    not importable from here.

    Args:
        policy: The parsed `config/scoring.yml`.

    Returns:
        One sentence naming the Unicode form, the enabled steps, and the
        case-folding decision.
    """
    rules = policy["normalisation"]
    steps = [label for key, label in _NORMALISATION_STEPS if rules[key]]
    joined = ", ".join(steps) if steps else "apply no further steps"
    tail = "fold case" if rules["fold_case"] else "do not fold case"
    return f"Unicode {rules['unicode_form']}, {joined}, and {tail}"
```

Then wire it into `readme_text` (`generators/export.py:85`). At the top of that
function's body, before the f-string, add:

```python
    normalisation = _normalisation_sentence(
        yaml.safe_load(_SCORING_POLICY_PATH.read_text(encoding="utf-8"))
    )
```

and at module scope, beside the other module constants:

```python
_SCORING_POLICY_PATH = Path("config/scoring.yml")
```

Add `import yaml` to the imports if absent. Then in the README f-string, replace
lines 142–143, which currently read:

```
   (Unicode NFKC, collapse whitespace runs, fold dashes and quotes to ASCII,
   strip Markdown syntax), then compute normalised edit distance and
```

with:

```
   ({normalisation}), then compute normalised edit distance and
```

Keep the surrounding sentence intact; only the parenthetical becomes generated.
Note the f-string already contains literal `|` table rows — do not disturb them,
and check no other brace in the f-string is newly unescaped.

- [ ] **Step 4: Run tests to verify they pass**

```bash
conda run -n docparse python -m pytest tests/test_export.py -v
```
Expected: PASS.

- [ ] **Step 5: Verify the corpus is unchanged**

The export README text changes, so re-export and confirm images and transcripts
are byte-identical and only the README differs:

```bash
conda run -n docparse python -m generators.pipeline export --date 20260825
```
Expected: images and transcripts unchanged; `manifest.jsonl` unchanged (it hashes
images, not the README).

- [ ] **Step 6: Full gates and commit**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 .
conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
conda run -n docparse python -m pytest tests/ -q
git add generators/export.py
git commit -m "✨ feat: generate the shipped normalisation prose from scoring.yml"
```

---

## Final verification

- [ ] **Both suites pass**

```bash
conda run -n docparse python -m pytest tests/ -q --cov=generators --cov-report=term
conda run -n docparse-score python -m pytest tests/scoring/ -q --cov=scoring --cov-report=term
```
Expected: all green; `scoring` at or above 80%. `generators` sits at 78% from before
this work — do not fold fixing that into this plan.

- [ ] **End-to-end on the real corpus** (needs `docparse-degrade` and real predictions)

```bash
conda run -n docparse-degrade python -m generators.degradation.cli --corpus <exports>/parsing_20260825
conda run -n docparse-score python -m scoring.score \
    --matrix <exports>/matrix.jsonl --predictions-root predictions/ --out rows.jsonl
conda run -n docparse-score python -m scoring.report --rows rows.jsonl
```

- [ ] **Check success criterion 5 from the spec:** does at least one tier separate
  at least two models on at least one document type? If every tier leaves them
  tied, report that — the conclusion is that this corpus cannot discriminate them,
  and subsystem B (structural realism) becomes necessary rather than optional.
