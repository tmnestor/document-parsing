# Document parsing corpus

A generator for synthetic Australian business documents, each a pristine page
image paired with a **canonical Markdown transcript**. It exists to benchmark
full-page transcription — VLMs and dedicated document parsers — where the
scarce thing is not pages but *trustworthy labels*.

Current output: **165 pages** — 55 cases across invoices, receipts and bank
statements, in 18 layout variants.

```bash
conda env create -f environment.yml
conda activate docparse

python -m generators.pipeline validate    # ground truth, layouts, fit budgets
python -m generators.pipeline generate    # page images + capture events
python -m generators.pipeline serialise   # events + policy -> transcripts
python -m generators.pipeline preview CASE001
python -m generators.pipeline export      # the dated, hashed deliverable
```

Nothing is downloaded and nothing is installed after the environment: five
pure-Python runtime dependencies (`Pillow`, `PyYAML`, `typer`, `rich`, `Faker`).

## Where the data goes

**Outside the repository, under a date-stamped root.**

```
../document-parsing-data/synthetic_data_2026-08-25/
  output/       page images, one subdirectory per document type
  derived/      events.jsonl and transcripts/
  exports/      parsing_<YYYYMMDD>/, the deliverable
```

*Outside*, because a full run writes hundreds of megabytes and inside the
working tree the only thing between that and the git history is `.gitignore`.

*Date stamped*, because **a corpus is a vintage**. Scoring pairs images to
transcripts through a hashed manifest and refuses a mismatch, so images,
transcripts and export must describe the same run — and a fixed path silently
overwrites the previous corpus, invalidating every prediction already scored
against it.

One key sets all three, so a new vintage is one edit and they cannot drift apart:

```yaml
dataset_root: ../document-parsing-data/synthetic_data_2026-08-25
```

`~` and `${VAR}` are expanded, so a shared machine or CI job can point at a store
without editing tracked config. A relative path resolves against the
**repository root**, not the working directory, so it means the same thing
however the command was invoked; absolute paths are taken as given, and
`--output` / `--derived` / `--target` override for a single run.

Pinned by `tests/test_data_location.py`, which fails if the shipped default ever
resolves back inside the working tree.

## Why the labels can be trusted

**Ground truth is authored, not annotated.** The transcript is produced by the
renderer *at the moment it draws the page*. Nothing is OCR'd, estimated, or
labelled after the fact.

**Capture happens inside the drawing primitives.** Each DSL primitive appends a
structured event where it has resolved its content and is about to draw it — so
the label cannot disagree with the pixels about conditionals, suppression,
currency formatting or field lookup. Re-walking the layout tree at export time
to rebuild the transcript would mean two implementations of the same semantics,
and they drift.

**A coverage invariant runs on every `generate`, not just under test.** Every
call that reaches the canvas is tagged with the event that authorised it, and at
page end the renderer asserts that no untagged text was drawn. A new primitive
that puts ink on the page without emitting an event fails the run with a
diagnostic naming it.

## What it deliberately is not

Out of scope: layout/region detection, table-structure recognition, reading-order
labels, degraded or scanned pages as the *primary* corpus, multi-page documents,
flowing prose, and information extraction. The transcript is a whole-page
reading task and nothing else.

Emphasis (`**bold**`) is excluded from transcripts on purpose: the renderer knows
what is bold, but bold detection is a typographic judgement models make
inconsistently, and scoring it measures style recognition rather than reading.

## Layout

```
generators/      pipeline.py    the five commands
                 layout_dsl/    schema, walk engine, binding, primitives
                 transcript.py  draw-time capture and the coverage invariant
                 serialise.py   events + policy -> Markdown
                 export.py      manifest, README, directory assembly
                 degradation/   optional scan/photo degradation ladders
config/          generation_config.yml, field_definitions.yml,
                 serialisation.yml, prompt.md, data_pools.yml, layouts/
ground_truth/    invoices.yml, receipts.yml, bank_statements.yml
fonts/           Carlito + Liberation, with licences
tests/           mirrors the source tree
```

## Adding a document type

Usually **no Python**. Add the field set to `config/field_definitions.yml`, one
or more DSL layouts to `config/layouts/<type>.yml`, authored entries to
`ground_truth/<type>.yml`, and any new pools to `config/data_pools.yml`. Register
the type in `config/generation_config.yml` — nothing is discovered by filename.

Then: `validate` → `generate --type X` → **inspect a page against its transcript
with `preview`** → commit the YAML. The visual inspection is not optional; there
is no field-level check that catches a bad layout.

## The export

```
parsing_<YYYYMMDD>/
  images/CASE001_invoices.png
  transcripts/CASE001_invoices.md
  manifest.jsonl        {image, transcript, doc_type, sha256}
  prompt.md
  serialisation.yml
  README.md
```

Filenames are `{case_id}_{doc_type}`, never `{case_id}_{layout_id}`, so a model
cannot infer the layout template before reading a pixel. The policy and the
hashed manifest ship **with the data**: the image hashes make scoring against a
wrong corpus vintage impossible rather than merely detectable afterwards, and
the prompt travels alongside because prompt and ground truth are a matched pair.

## Quality gates

```bash
pytest tests/ --cov=generators --cov-report=term      # floor 80%
ruff check --fix --ignore ARG001,ARG002,F841 .
ruff format .
mypy generators --ignore-missing-imports
```

`validate` is worth running in CI on its own: a malformed layout YAML is the
likeliest contribution error and catching it needs no render.

## Determinism

The same inputs render byte-identical images — pinned by
`tests/test_pipeline.py::test_the_same_input_renders_byte_identical_images`. If a
change moves a pixel, that is a corpus revision and existing predictions scored
against it are no longer valid.

## Related

Scoring, parser runners and the calibration-pass analysis live in a separate
repository. The interface between them is the **exported corpus directory**
above, not shared code — which is why this repository has no scoring dependency
and its environment carries no parser.
