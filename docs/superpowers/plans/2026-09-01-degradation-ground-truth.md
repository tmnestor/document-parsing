# Degradation Ground Truth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every page in the corpus say which intake family and severity produced it, so a model can be scored on identifying the degradation as well as transcribing through it.

**Architecture:** Three tasks. `manifest_record` gains two required keyword arguments, which makes all seven corpora self-describing and forces both call sites to state what they are. A pooled index is then built from those manifests and written beside `matrix.jsonl`. Finally the degrade CLI writes it, verified by a real `DEGRADE=yes` build.

**Tech Stack:** Python 3.12, PyYAML, typer, pytest, augraphy 8.2.6 + headless opencv (already installed in `docparse`).

**Spec:** `docs/superpowers/specs/2026-09-01-degradation-ground-truth-design.md`

## Global Constraints

- Run every command **from the repository root** `/Users/tod/Desktop/document-parsing`.
- Use `conda run -n docparse <command>`. **`pytest tests/` fails at collection** under `docparse` — always pass `--ignore=tests/scoring`.
- `tests/` is gitignored. Write and run tests; **never `git add` anything under `tests/`.**
- **YAML is the single source of truth.** No config value may be hardcoded as a Python default. The seven valid `(family, severity)` pairs come from `config/degradation.yml`, never from a literal in Python.
- Fail-fast diagnostics carry all four elements: **What / Where / Expected / Recover**.
- Line length 108. Google-style docstrings. Python 3.12 (`X | Y`, never `from __future__ import annotations`).
- Before every commit: `conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 .` then `conda run -n docparse ruff format .` then `conda run -n docparse mypy generators --ignore-missing-imports`.
- **Never** use `--no-verify` on a commit.
- **Never** use a bash heredoc (`<<EOF`) — it hangs this harness. Write files with the Write/Edit tools.
- `cat` returns EMPTY through the Bash tool. Use the Read tool, or `sed -n`/`head`/`grep`.
- `git` needs `GIT_PAGER=cat` and the chain should end with `< /dev/null`.
- **Do not `git push`.** The controller handles that.
- Determinism is a contract: identical inputs render byte-identical images.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `generators/export.py` | `manifest_record` gains `family`/`severity`; the clean export passes `clean`/`none` | 1 |
| `generators/degradation/cli.py` | the degrade call site passes the tier's family/severity; later writes the pooled file | 1, 3 |
| `generators/degradation/matrix.py` | builds and writes the pooled cross-corpus ground truth | 2 |

**Ordering.** Task 1 makes every corpus self-describing — nothing reads the new
fields yet. Task 2 adds a builder that reads them, unit-tested against synthetic
directories. Task 3 wires it into the CLI and proves it on a real 1,323-page run.

---

### Task 1: Every manifest record says what it is

**Files:**
- Modify: `generators/export.py` (`manifest_record`, and its call site at `generators/export.py:473`)
- Modify: `generators/degradation/cli.py:222-226` (the degrade call site)
- Test: `tests/test_export.py`

**Interfaces:**
- Produces: `manifest_record(image: Path, transcript: Path, doc_type: str, *, family: str, severity: str) -> dict`, returning `{image, transcript, doc_type, sha256, transcript_sha256, family, severity}`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_export.py`:

```python
def test_a_manifest_record_states_its_family_and_severity(tmp_path):
    from generators.export import manifest_record

    image = tmp_path / "CASE001_invoices.png"
    image.write_bytes(b"pixels")
    transcript = tmp_path / "CASE001_invoices.md"
    transcript.write_text("# TAX INVOICE\n", encoding="utf-8")

    row = manifest_record(image, transcript, "invoices", family="scan", severity="moderate")
    assert row["family"] == "scan"
    assert row["severity"] == "moderate"


def test_the_manifest_record_keys_are_pinned(tmp_path):
    """The manifest ships to consumers; its key set is an interface."""
    from generators.export import manifest_record

    image = tmp_path / "CASE001_invoices.png"
    image.write_bytes(b"pixels")
    transcript = tmp_path / "CASE001_invoices.md"
    transcript.write_text("# TAX INVOICE\n", encoding="utf-8")

    row = manifest_record(image, transcript, "invoices", family="clean", severity="none")
    assert set(row) == {
        "image", "transcript", "doc_type", "sha256", "transcript_sha256", "family", "severity",
    }


def test_family_and_severity_are_required(tmp_path):
    """No default: a corpus that cannot say what it is must not be writable."""
    import pytest
    from generators.export import manifest_record

    image = tmp_path / "CASE001_invoices.png"
    image.write_bytes(b"pixels")
    transcript = tmp_path / "CASE001_invoices.md"
    transcript.write_text("# TAX INVOICE\n", encoding="utf-8")

    with pytest.raises(TypeError):
        manifest_record(image, transcript, "invoices")
```

There is an existing test pinning the old key set — `tests/test_export.py:122`
asserts `set(record) == {"image", "transcript", "doc_type", "sha256", "transcript_sha256"}`.
Update it to the new set rather than leaving two tests disagreeing, and note in
its docstring that `family`/`severity` arrived on 2026-09-01 so every corpus
describes itself.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
conda run -n docparse pytest tests/test_export.py -q -k "family or severity or keys_are_pinned"
```

Expected: the first two FAIL with `TypeError: manifest_record() got an unexpected keyword argument 'family'`. The third PASSES already (the function currently takes three positional arguments, so calling it with three raises nothing) — it becomes meaningful only after Step 3 makes the new arguments required.

- [ ] **Step 3: Widen the signature**

In `generators/export.py`, change:

```python
def manifest_record(image: Path, transcript: Path, doc_type: str) -> dict:
```

to:

```python
def manifest_record(
    image: Path, transcript: Path, doc_type: str, *, family: str, severity: str
) -> dict:
```

Add to the `Args:` block:

```
        family: Intake family that produced this page — "clean" for the
            undegraded baseline, otherwise a family declared in
            config/degradation.yml, e.g. "scan".
        severity: Tier severity, "none" for the baseline, otherwise a tier name
            declared for that family, e.g. "moderate".
```

Update the `Returns:` line to name the two new keys, and add them to the returned
dict after `transcript_sha256`:

```python
        # Stated on every record so a corpus is self-describing: a degraded page
        # that cannot say what was done to it cannot be scored on identifying it.
        # Keyword-only and required — a default would let a corpus ship silently
        # mislabelled, and there is no value that is right for every caller.
        "family": family,
        "severity": severity,
```

- [ ] **Step 4: Update the clean export's call site**

`generators/export.py:473` currently reads:

```python
        row = manifest_record(source_image, source_transcript, doc_type)
```

Change it to:

```python
        # The clean export is the baseline the degraded tiers are compared
        # against, and it is one of the seven classes a model must recognise —
        # so it labels itself like any other corpus rather than being the one
        # that stays silent.
        row = manifest_record(
            source_image, source_transcript, doc_type, family="clean", severity="none"
        )
```

- [ ] **Step 5: Update the degrade call site**

`generators/degradation/cli.py:222-226` currently reads:

```python
                row = manifest_record(
                    target / "images" / image_name,
                    target / "transcripts" / transcript_name,
                    record["doc_type"],
                )
```

Change it to:

```python
                row = manifest_record(
                    target / "images" / image_name,
                    target / "transcripts" / transcript_name,
                    record["doc_type"],
                    family=tier.family,
                    severity=tier.name,
                )
```

`tier` is already in scope: this block sits inside the `for tier in tiers:` loop
that computes `target` from `tier.family` and `tier.name`.

- [ ] **Step 6: Run the tests**

```bash
conda run -n docparse pytest tests/test_export.py -q
conda run -n docparse pytest tests/ --ignore=tests/scoring -q
```

Expected: all pass. `manifest_record` has only the two call sites above, so
nothing else needs changing; if another caller appears, it is a caller the plan
did not know about — report it rather than giving it a default.

- [ ] **Step 7: Lint, type-check, commit**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 . && conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
git add generators/export.py generators/degradation/cli.py
git commit -m "✨ feat: label every manifest record with its family and severity"
```

---

### Task 2: Build the pooled cross-corpus index

**Files:**
- Modify: `generators/degradation/matrix.py`
- Test: `tests/test_degradation_matrix.py`

**Interfaces:**
- Consumes: manifest records carrying `family`/`severity` from Task 1.
- Produces:
  - `ground_truth_rows(corpus_dir: Path) -> list[dict]` — one row per image, keyed `corpus`, `image`, `transcript`, `case_id`, `doc_type`, `family`, `severity`.
  - `write_ground_truth(rows: list[dict], exports_dir: Path) -> Path` — writes `ground_truth.jsonl` beside `matrix.jsonl`.

**Why the labels are not arguments.** Task 1 already put `family` and `severity`
on every manifest record, so this function READS them rather than being told
them. Passing them in would create two representations of one fact that a
caller could silently make disagree — the spec requires they cannot drift, and
the cheapest way to guarantee that is to have only one source.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_degradation_matrix.py`:

```python
import json
from pathlib import Path

import pytest

from generators.degradation.matrix import MatrixError, ground_truth_rows, write_ground_truth


def _corpus(root: Path, name: str, records: list[dict]) -> Path:
    corpus = root / name
    (corpus / "images").mkdir(parents=True)
    (corpus / "transcripts").mkdir(parents=True)
    lines = "\n".join(json.dumps(r) for r in records)
    (corpus / "manifest.jsonl").write_text(lines + "\n", encoding="utf-8")
    return corpus


def _record(case_id: str, doc_type: str) -> dict:
    return {
        "image": f"images/{case_id}_{doc_type}.png",
        "transcript": f"transcripts/{case_id}_{doc_type}.md",
        "doc_type": doc_type,
        "sha256": "0" * 64,
        "transcript_sha256": "1" * 64,
        "family": "scan",
        "severity": "moderate",
    }


def test_one_row_per_image_carrying_both_labels(tmp_path):
    corpus = _corpus(
        tmp_path,
        "parsing_20260901_scan-moderate",
        [_record("CASE001", "bank_statements"), _record("CASE002", "invoices")],
    )
    rows = ground_truth_rows(corpus)

    assert len(rows) == 2
    assert rows[0] == {
        "corpus": "parsing_20260901_scan-moderate",
        "image": "images/CASE001_bank_statements.png",
        "transcript": "transcripts/CASE001_bank_statements.md",
        "case_id": "CASE001",
        "doc_type": "bank_statements",
        "family": "scan",
        "severity": "moderate",
    }


def test_the_labels_come_from_the_manifest_not_from_the_caller(tmp_path):
    """One source of truth: a row's labels are whatever its manifest record says,
    so the pooled index and the manifest cannot disagree."""
    record = _record("CASE001", "invoices")
    record["family"] = "photo"
    record["severity"] = "heavy"
    corpus = _corpus(tmp_path, "parsing_x", [record])

    rows = ground_truth_rows(corpus)
    assert rows[0]["family"] == "photo"
    assert rows[0]["severity"] == "heavy"


def test_a_manifest_record_without_labels_is_a_diagnostic(tmp_path):
    """An older corpus predating the labels must fail loudly, not silently omit."""
    from tests.helpers import assert_diagnostic_error

    record = _record("CASE001", "invoices")
    del record["family"]
    corpus = _corpus(tmp_path, "parsing_x", [record])

    with pytest.raises(MatrixError) as excinfo:
        ground_truth_rows(corpus)
    assert_diagnostic_error(str(excinfo.value), mentions=("family", "manifest.jsonl"))


def test_the_case_id_is_the_stem_without_its_doc_type(tmp_path):
    """Filenames are {case_id}_{doc_type}; stripping the suffix survives an
    underscore in a document type, which splitting on '_' would not."""
    corpus = _corpus(tmp_path, "c", [_record("CASE007", "bank_statements")])
    rows = ground_truth_rows(corpus)
    assert rows[0]["case_id"] == "CASE007"


def test_a_corpus_with_no_manifest_is_a_diagnostic(tmp_path):
    from tests.helpers import assert_diagnostic_error

    (tmp_path / "empty").mkdir()
    with pytest.raises(MatrixError) as excinfo:
        ground_truth_rows(tmp_path / "empty")
    assert_diagnostic_error(str(excinfo.value), mentions=("manifest.jsonl",))


def test_the_pooled_file_lands_beside_the_matrix(tmp_path):
    record = _record("CASE001", "invoices")
    record["family"], record["severity"] = "clean", "none"
    corpus = _corpus(tmp_path, "parsing_x", [record])
    rows = ground_truth_rows(corpus)
    path = write_ground_truth(rows, tmp_path)

    assert path == tmp_path / "ground_truth.jsonl"
    written = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert written == rows


def test_a_set_with_no_clean_baseline_is_refused(tmp_path):
    from tests.helpers import assert_diagnostic_error

    corpus = _corpus(tmp_path, "parsing_x_scan-heavy", [_record("CASE001", "invoices")])
    rows = ground_truth_rows(corpus)
    with pytest.raises(MatrixError) as excinfo:
        write_ground_truth(rows, tmp_path)
    assert_diagnostic_error(str(excinfo.value), mentions=("clean",))
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
conda run -n docparse pytest tests/test_degradation_matrix.py -q -k "row or case_id or pooled or baseline or manifest"
```

Expected: FAIL at import — `cannot import name 'ground_truth_rows' from 'generators.degradation.matrix'`.

- [ ] **Step 3: Implement the builder and writer**

Add to `generators/degradation/matrix.py`, beside the existing `matrix_row` and
`write_matrix` (this module already owns cross-corpus indexing):

```python
_GROUND_TRUTH_NAME = "ground_truth.jsonl"


def ground_truth_rows(corpus_dir: Path) -> list[dict]:
    """One labelled row per image in a corpus, for the identification task.

    Everything is read from the corpus's own manifest — the page list AND the
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
                '              {"image": "images/CASE001_invoices.png", '
                '"family": "scan", "severity": "moderate", ...}',
                recover="re-run `export` and `degrade` with the current code; a corpus "
                "written before 2026-09-01 predates these fields.",
            )

        doc_type = str(record["doc_type"])
        stem = Path(record["image"]).stem
        # Filenames are {case_id}_{doc_type}; strip the suffix rather than
        # splitting on "_", which would truncate `bank_statements`.
        suffix = f"_{doc_type}"
        case_id = stem[: -len(suffix)] if stem.endswith(suffix) else stem
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


def write_ground_truth(rows: list[dict], exports_dir: Path) -> Path:
    """Write the pooled identification ground truth beside the matrix.

    Args:
        rows: Rows from `ground_truth_rows`, in the order they should be listed.
        exports_dir: The directory holding the corpora and `matrix.jsonl`.

    Returns:
        The path written.

    Raises:
        MatrixError: No row describes the clean baseline.
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

    path = exports_dir / _GROUND_TRUTH_NAME
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path
```

- [ ] **Step 4: Run the tests**

```bash
conda run -n docparse pytest tests/test_degradation_matrix.py -q
conda run -n docparse pytest tests/ --ignore=tests/scoring -q
```

Expected: all pass. Nothing calls the new functions yet, so the whole suite stays green.

- [ ] **Step 5: Lint, type-check, commit**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 . && conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
git add generators/degradation/matrix.py
git commit -m "✨ feat: build the pooled degradation ground truth"
```

---

### Task 3: Write it, and prove it on a real run

**Files:**
- Modify: `generators/degradation/cli.py` (after the `write_matrix` call at `generators/degradation/cli.py:261`)
- Test: `tests/test_degradation_cli.py`

**Interfaces:**
- Consumes: `ground_truth_rows` and `write_ground_truth` from Task 2; `tier.family` / `tier.name` already in scope.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_degradation_cli.py`:

```python
def test_the_cli_module_writes_the_pooled_ground_truth():
    """Pins the wiring: the CLI must call the writer, not just import it."""
    source = Path("generators/degradation/cli.py").read_text(encoding="utf-8")
    assert "write_ground_truth(" in source
    assert "ground_truth_rows(" in source
```

- [ ] **Step 2: Run it to verify it fails**

```bash
conda run -n docparse pytest tests/test_degradation_cli.py -q -k pooled
```

Expected: FAIL — the CLI neither imports nor calls either function.

- [ ] **Step 3: Wire it into the CLI**

In `generators/degradation/cli.py`, extend the existing import:

```python
from generators.degradation.matrix import matrix_row, write_matrix
```

to:

```python
from generators.degradation.matrix import (
    ground_truth_rows,
    matrix_row,
    write_ground_truth,
    write_matrix,
)
```

Then, immediately after the `matrix_path = write_matrix(rows, plan.out)` line and
its `rprint`, add:

```python
    # The pooled index for the identification task. Built from the same seven
    # corpora the matrix names, and written beside it because it is the same kind
    # of thing: an index spanning corpora rather than describing one. Each row
    # also names the transcript, so a consumer scoring identification and
    # transcription reads one file rather than joining three.
    gt_rows = ground_truth_rows(corpus)
    for tier in tiers:
        gt_rows.extend(ground_truth_rows(plan.out / f"{corpus.name}_{tier.family}-{tier.name}"))
    gt_path = write_ground_truth(gt_rows, plan.out)
    rprint(f"[green]Ground truth written: {gt_path} ({len(gt_rows)} page(s))[/green]")
```

- [ ] **Step 4: Run the tests**

```bash
conda run -n docparse pytest tests/ --ignore=tests/scoring -q
```

Expected: all pass.

- [ ] **Step 5: Prove it on a real degradation run**

This is the only step that exercises the whole chain. `augraphy 8.2.6` and a
headless `cv2 4.13.0` are already installed in `docparse`, so it will run.

```bash
DATE_STAMP=20260902 ./build_corpus.sh
```

It takes several minutes: it validates, generates 189 pages, serialises,
exports, writes the extraction corpus, then degrades into six tiers.

Expected on completion: `../evaluation_data/corpus_20260902/degraded/` holds six
tier directories, a symlink to the clean corpus, `matrix.jsonl`, and the new
`ground_truth.jsonl`.

- [ ] **Step 6: Verify the artifact**

```bash
GT=../evaluation_data/corpus_20260902/degraded/ground_truth.jsonl
wc -l < "$GT"
head -1 "$GT"
conda run -n docparse python -c "
import json
from collections import Counter
from pathlib import Path
rows = [json.loads(l) for l in Path('$GT').read_text().splitlines() if l]
print('rows:', len(rows))
print('per corpus:', Counter(r['corpus'] for r in rows))
print('label pairs:', sorted({(r['family'], r['severity']) for r in rows}))
"
```

Expected: **1,323 rows**, **189 per corpus across seven corpora**, and exactly
seven label pairs — `('clean','none')` plus each of `scan`/`photo` × `light`/`moderate`/`heavy`.

- [ ] **Step 7: Verify every path in it resolves**

A ground truth naming a file that is not there is worse than none.

```bash
conda run -n docparse python -c "
import json
from pathlib import Path
root = Path('../evaluation_data/corpus_20260902/degraded')
rows = [json.loads(l) for l in (root / 'ground_truth.jsonl').read_text().splitlines() if l]
missing = [
    f\"{r['corpus']}/{p}\"
    for r in rows for p in (r['image'], r['transcript'])
    if not (root / r['corpus'] / p).exists()
]
print('rows:', len(rows), 'missing paths:', len(missing))
print(missing[:5])
"
```

Expected: `missing paths: 0`.

- [ ] **Step 8: Verify a degraded transcript still matches its clean original**

This is what makes the transcription label reusable.

```bash
C=../evaluation_data/corpus_20260902
cmp "$C/parsing_20260902/transcripts/CASE001_bank_statements.md" \
    "$C/degraded/parsing_20260902_scan-heavy/transcripts/CASE001_bank_statements.md" \
    && echo "transcript byte-identical through degradation"
```

Expected: the message prints. If it does not, degradation is altering
transcripts, which would invalidate the whole transcription half of the task —
stop and report.

- [ ] **Step 9: Look at a degraded page**

Open `$C/degraded/parsing_20260902_photo-heavy/images/CASE001_bank_statements.png`
with the Read tool, which renders PNGs. Confirm it is damaged but still a
recognisable bank statement — `config/degradation.yml` says a heavy tier should
sit "at the edge of legibility for a human, not past it". Report what you saw.

- [ ] **Step 10: Lint, type-check, commit**

```bash
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 . && conda run -n docparse ruff format .
conda run -n docparse mypy generators --ignore-missing-imports
git add generators/degradation/cli.py
git commit -m "✨ feat: emit degradation ground truth beside the matrix"
```

---

## Notes for the executor

- **Tasks 1 and 2 change no image and no transcript.** `tests/test_corpus_unchanged.py` must stay green throughout. If it goes red, something unrelated broke.
- Task 1 widens an artifact consumers already read. It is additive — no existing manifest key moves or changes meaning — but the clean corpus's manifest does gain two fields, and that is intended, not a leak from the degradation work.
- Task 3 Step 5 builds a **new corpus vintage** at `corpus_20260902`. It does not touch `corpus_20260901`; `build_corpus.sh` refuses to overwrite an existing target.
- The severity rungs were tuned for legibility rather than for being visually distinguishable (`config/degradation.yml`). If Step 9's `photo-heavy` page looks unreadable rather than merely damaged, that is worth reporting — but it is a property of the ladders, not a fault in this change.
