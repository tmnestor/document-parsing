# Corpus Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Assemble the dated deliverable directory — images, transcripts, a
hashed manifest, the prompt these transcripts assume, and the policy that
produced them — so a corpus stays interpretable and scoreable away from this
checkout.

**Architecture:** One pure assembly command. `export` reads the artifacts
`generate` and `serialise` already produced, copies them into
`parsing_<YYYYMMDD>/`, and writes three new files: `manifest.jsonl` carrying a
sha256 per image, a copy of `serialisation.yml`, and `prompt.md`. It renders
nothing and serialises nothing.

**Tech Stack:** Python 3.12, PyYAML, typer, rich. pytest, ruff, mypy. Conda env `docparse`.

**Spec:** `docs/superpowers/specs/2026-08-17-document-parsing-corpus-design.md` (§6.1)

**Prior plans:** `2026-08-17-dsl-port.md`, `2026-08-17-transcript-capture.md` — both complete.

## Scope

Delivers the export directory and the shipped prompt. Deliberately **not** here:

- The scoring tool and its normalisation (§5). It belongs with the consumer;
  the manifest is what lets a scorer verify it has the right corpus.
- The §8.6 calibration pass. It needs an exported corpus to run against, so it
  comes after this — and it is empirical work, budgeted separately.

## Global Constraints

Unchanged from the prior two plans; every task's requirements include them.

- Python 3.12, `X | Y` unions, no `from __future__ import annotations`.
- Line length 108. `pathlib.Path` everywhere. Google-style docstrings.
- `ruff check --fix --ignore ARG001,ARG002,F841`, `ruff format .`, `mypy .` clean.
- `from err` / `from None` inside `except` (B904).
- Dependencies stay: `Pillow`, `PyYAML`, `typer`, `rich`, `Faker`.
- Every config validation error carries all four diagnostic elements.
- `tests/` and `CLAUDE.md` gitignored; stage source and config only.
- **Never normalise.** The export copies transcripts byte for byte.

## Why the manifest exists

§6.1 is unusually specific about this, and it is the one requirement with a
named failure behind it: in the predecessor project a scoring run was pointed at
the wrong-vintage ground truth, matched 22 of 165 filenames, and still produced
a plausible number. Filename matching alone cannot detect that.

A sha256 per image makes the mismatch impossible to score around rather than
merely detectable afterwards — a scorer that checks hashes cannot silently
proceed against the wrong corpus. Two consequences for this plan:

- The hash is of the **image**, because the image is what a model reads.
- The manifest ships **with the data**, not only in this repo, along with the
  `serialisation.yml` copy — so a transcript stays interpretable independently
  of this checkout.

## File structure

| File | Responsibility |
|---|---|
| `generators/export.py` (new, ~150) | Manifest records, directory assembly, README text |
| `config/prompt.md` (new) | The prompt these transcripts assume; copied on export |
| `generators/pipeline.py` | New `export` command |

Export layout produced (§6.1):

```
parsing_<YYYYMMDD>/
  images/CASE001_invoices.png
  transcripts/CASE001_invoices.md
  manifest.jsonl        {image, transcript, doc_type, sha256}
  prompt.md
  serialisation.yml
  README.md
```

**One deviation from the spec's example, stated deliberately.** §6.1 writes
`CASE001_invoice.png`; this repo emits `CASE001_invoices.png`, using the
document-type key verbatim. The requirement is that a filename must not reveal
the layout template, which both satisfy; using the config key avoids a
singularisation table that would be a second source of truth for type names.

---

### Task 1: Manifest records

**Files:**
- Create: `generators/export.py`
- Test: `tests/test_export.py`

**Interfaces:**
- Produces:
  - `sha256_of(path: Path) -> str`
  - `manifest_record(image: Path, transcript: Path, doc_type: str) -> dict`
  - `ExportError(RuntimeError)`

- [ ] **Step 1: Write the failing test**

```python
"""Manifest records identify a corpus by content, not by filename."""

import hashlib

import pytest

from generators.export import ExportError, manifest_record, sha256_of


def test_sha256_matches_hashlib(tmp_path):
    path = tmp_path / "a.png"
    path.write_bytes(b"pixels")
    assert sha256_of(path) == hashlib.sha256(b"pixels").hexdigest()


def test_a_record_carries_the_four_declared_keys(tmp_path):
    image = tmp_path / "CASE001_invoices.png"
    transcript = tmp_path / "CASE001_invoices.md"
    image.write_bytes(b"pixels")
    transcript.write_text("# TAX INVOICE", encoding="utf-8")
    record = manifest_record(image, transcript, "invoices")
    assert set(record) == {"image", "transcript", "doc_type", "sha256"}
    assert record["image"] == "images/CASE001_invoices.png"
    assert record["transcript"] == "transcripts/CASE001_invoices.md"
    assert record["doc_type"] == "invoices"
    assert record["sha256"] == hashlib.sha256(b"pixels").hexdigest()


def test_a_record_hashes_the_image_not_the_transcript(tmp_path):
    """The image is what a model reads, so it is what identifies the case."""
    image = tmp_path / "a.png"
    transcript = tmp_path / "a.md"
    image.write_bytes(b"pixels")
    transcript.write_text("text", encoding="utf-8")
    assert manifest_record(image, transcript, "invoices")["sha256"] != sha256_of(transcript)


def test_a_missing_transcript_fails_with_a_four_element_diagnostic(tmp_path):
    image = tmp_path / "a.png"
    image.write_bytes(b"pixels")
    with pytest.raises(ExportError) as excinfo:
        manifest_record(image, tmp_path / "absent.md", "invoices")
    message = str(excinfo.value)
    for label in ("What:", "Where:", "Expected:", "Recover:"):
        assert label in message
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `conda run -n docparse pytest tests/test_export.py -v`
Expected: FAIL — no module named `generators.export`.

- [ ] **Step 3: Write the module**

`sha256_of` reads in chunks. `manifest_record` checks both paths exist, failing
with a four-element diagnostic naming the absent one, and returns the four keys
§6.1 declares, with the paths written relative to the export root
(`images/…`, `transcripts/…`) so the manifest is portable.

- [ ] **Step 4: Run the tests**

Run: `conda run -n docparse pytest tests/test_export.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add generators/export.py
git commit -m "✨ feat: build hashed manifest records for an exported corpus"
```

---

### Task 2: The shipped prompt

**Files:**
- Create: `config/prompt.md`
- Test: `tests/test_prompt.py`

**Why it ships versioned with the data.** §6.1: prompt and ground truth are a
matched pair, and if they drift the benchmark silently measures the wrong thing.
Every convention the prompt states must be one `serialisation.yml` actually
implements — the test below pins that correspondence, because a prompt promising
a convention the serialiser does not follow is exactly the silent drift.

- [ ] **Step 1: Write `config/prompt.md`**

It must state, at minimum: transcribe the whole page; emit the restricted
Markdown subset; tables as pipe tables; **read a split into columns left to
right, finishing one column before starting the next, never across visual rows**;
no bold or italic; `Label: value` for labelled pairs; rejoin a wrapped line.

The split-column instruction is the load-bearing one (§4.3): it is the single
place competent models genuinely disagree, and no normalisation can repair an
ordering mismatch.

- [ ] **Step 2: Write the failing test**

```python
"""The prompt and the policy that produced the transcripts must agree."""

from pathlib import Path

from generators.serialise import load_serialisation_policy

PROMPT = Path("config/prompt.md")
POLICY = load_serialisation_policy(Path("config/serialisation.yml"))


def test_the_prompt_exists_and_is_substantive():
    assert PROMPT.exists()
    assert len(PROMPT.read_text(encoding="utf-8").split()) > 80


def test_the_prompt_states_the_split_column_convention():
    """§4.3: the one convention competent models genuinely disagree on."""
    text = PROMPT.read_text(encoding="utf-8").lower()
    assert "column" in text
    assert "left to right" in text or "left-to-right" in text


def test_the_prompt_forbids_the_emphasis_the_policy_excludes():
    assert POLICY["emphasis"] == "none"
    text = PROMPT.read_text(encoding="utf-8").lower()
    assert "bold" in text or "emphasis" in text


def test_the_prompt_asks_for_the_table_style_the_policy_emits():
    assert POLICY["table_style"] == "pipe_with_header_rule"
    assert "|" in PROMPT.read_text(encoding="utf-8")


def test_the_prompt_asks_for_rejoined_wrapping():
    """§4.2 captures pre-wrap, so a model must not be penalised for rejoining."""
    text = PROMPT.read_text(encoding="utf-8").lower()
    assert "wrap" in text
```

- [ ] **Step 3: Run the tests**

Run: `conda run -n docparse pytest tests/test_prompt.py -v`
Expected: 5 passed. If one fails, fix the prompt — these assert the matched pair,
not incidental wording.

- [ ] **Step 4: Commit**

```bash
git add config/prompt.md
git commit -m "📝 docs: add the prompt these transcripts assume"
```

---

### Task 3: Directory assembly and the `export` command

**Files:**
- Modify: `generators/export.py`, `generators/pipeline.py`
- Test: `tests/test_export_dir.py`

**Interfaces:**
- Produces:
  - `export_corpus(records, *, derived_dir, output_dir, policy_path, prompt_path, target) -> dict`
  - `pipeline export [--date YYYYMMDD] [--target DIR]`

- [ ] **Step 1: Write the failing test**

```python
"""The exported directory is self-contained and hash-verified."""

import hashlib
import json
from pathlib import Path

from typer.testing import CliRunner

from generators.pipeline import app

runner = CliRunner()


def _corpus(tmp_path):
    derived = tmp_path / "derived"
    assert runner.invoke(
        app,
        ["generate", "--type", "invoices", "--limit", "3", "--output", str(tmp_path / "out"),
         "--derived", str(derived)],
    ).exit_code == 0
    assert runner.invoke(app, ["serialise", "--derived", str(derived)]).exit_code == 0
    result = runner.invoke(
        app,
        ["export", "--derived", str(derived), "--output", str(tmp_path / "out"),
         "--target", str(tmp_path / "ship"), "--date", "20260817"],
    )
    assert result.exit_code == 0, result.output
    return tmp_path / "ship" / "parsing_20260817"


def test_the_export_has_the_declared_shape(tmp_path):
    root = _corpus(tmp_path)
    assert (root / "images").is_dir()
    assert (root / "transcripts").is_dir()
    for name in ("manifest.jsonl", "prompt.md", "serialisation.yml", "README.md"):
        assert (root / name).exists(), name


def test_every_manifest_hash_matches_its_shipped_image(tmp_path):
    """The structural fix for scoring against the wrong-vintage ground truth."""
    root = _corpus(tmp_path)
    records = [json.loads(line) for line in (root / "manifest.jsonl").read_text().splitlines()]
    assert records
    for record in records:
        image = root / record["image"]
        assert image.exists()
        assert hashlib.sha256(image.read_bytes()).hexdigest() == record["sha256"]


def test_every_manifest_row_has_both_its_files(tmp_path):
    root = _corpus(tmp_path)
    records = [json.loads(line) for line in (root / "manifest.jsonl").read_text().splitlines()]
    for record in records:
        assert (root / record["image"]).exists()
        assert (root / record["transcript"]).exists()


def test_the_shipped_policy_is_a_verbatim_copy(tmp_path):
    root = _corpus(tmp_path)
    assert (root / "serialisation.yml").read_text(encoding="utf-8") == Path(
        "config/serialisation.yml"
    ).read_text(encoding="utf-8")


def test_transcripts_are_copied_byte_for_byte(tmp_path):
    root = _corpus(tmp_path)
    derived = tmp_path / "derived" / "transcripts"
    for shipped in (root / "transcripts").glob("*.md"):
        assert shipped.read_bytes() == (derived / shipped.name).read_bytes()


def test_no_filename_reveals_its_layout_template(tmp_path):
    """§6.1: a model must not infer the template before reading a pixel."""
    root = _corpus(tmp_path)
    for path in (root / "images").iterdir():
        for template in ("acme", "standard", "high_value", "mixed", "thermal", "cba", "nab", "anz"):
            assert template not in path.stem, path.name


def test_exporting_before_serialising_fails_with_a_diagnostic(tmp_path):
    derived = tmp_path / "derived"
    runner.invoke(
        app,
        ["generate", "--type", "invoices", "--limit", "1", "--output", str(tmp_path / "out"),
         "--derived", str(derived)],
    )
    result = runner.invoke(
        app, ["export", "--derived", str(derived), "--target", str(tmp_path / "ship")]
    )
    assert result.exit_code == 1
    assert "serialise" in result.output
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `conda run -n docparse pytest tests/test_export_dir.py -v`
Expected: FAIL — `export` is not a registered command.

- [ ] **Step 3: Implement assembly and the command**

`export_corpus` copies each image and transcript into the export root, writes
`manifest.jsonl`, copies `serialisation.yml` and `prompt.md` verbatim, and
writes a `README.md` naming the corpus date, the document-type counts, and how
to score against it — specifically that the manifest hashes should be verified
before scoring.

`--date` defaults to today; passing it explicitly is what makes the test
deterministic. Missing transcripts fail with a diagnostic naming `serialise`,
since running `export` before `serialise` is the obvious ordering mistake.

- [ ] **Step 4: Run the tests**

Run: `conda run -n docparse pytest tests/test_export_dir.py -v`
Expected: 7 passed.

- [ ] **Step 5: Export the real corpus and inspect it**

```bash
conda run -n docparse python -m generators.pipeline generate
conda run -n docparse python -m generators.pipeline serialise
conda run -n docparse python -m generators.pipeline export
```

Then read the generated `README.md` and one shipped transcript. Check the README
would actually tell someone with no context what this is and how to use it —
that is what "self-contained" has to mean in practice.

- [ ] **Step 6: Full gate and commit**

```bash
conda run -n docparse pytest tests/ --cov=generators --cov-report=term
conda run -n docparse ruff check --fix --ignore ARG001,ARG002,F841 generators/
conda run -n docparse ruff format .
conda run -n docparse mypy generators/ --ignore-missing-imports
git add generators/export.py generators/pipeline.py
git commit -m "✨ feat: assemble the dated corpus export with a hashed manifest"
```

Coverage floor 80%.

---

## Done when

- `export` produces `parsing_<YYYYMMDD>/` with all six declared artifacts.
- Every manifest hash matches its shipped image.
- The shipped `serialisation.yml` is byte-identical to the repo's.
- Transcripts are copied byte for byte — the export never re-serialises.
- No shipped filename contains a layout template name.
- Running `export` before `serialise` fails naming the missing step.
- `ruff`, `mypy` and `pytest --cov` (≥80%) pass.

## Next

The §8.6 calibration pass: run two or three real parsers over a sample of the
exported corpus and separate genuine reading errors from convention mismatches.
If a model reads a page perfectly and still scores badly, the convention is
wrong, and `config/serialisation.yml` is where it is fixed — without
regenerating a single image. This is budgeted work to do *before* the corpus
freezes, not a discovery to make after it ships.
