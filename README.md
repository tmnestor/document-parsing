# Document parsing corpus

A generator for synthetic Australian business documents — invoices, receipts,
and bank statements — each a pristine page image paired with a **canonical
Markdown transcript**. It exists to benchmark full-page transcription — VLMs
and dedicated document parsers — where the scarce thing is not pages but
*trustworthy labels*.

**Ground truth is authored, not annotated.** The transcript is produced by the
renderer *at the moment it draws the page*. Nothing is OCR'd, estimated, or
labelled after the fact — each drawing primitive appends a structured event
where it has resolved its content and is about to draw it, so the label
cannot disagree with the pixels about conditionals, suppression, currency
formatting or field lookup. That is the reason to use this corpus rather than
a scanned or scraped one: the labels cannot drift from the page they describe.

## Quick start

```bash
conda env create -f environment.yml
conda activate docparse

./build_corpus.sh              # everything: clean corpus, degradations, IE ground truth
DEGRADE=no ./build_corpus.sh   # clean corpus and extraction only — much faster
```

One command, one environment. Everything is derived from files in this
repository — the authored ground truth, the layouts, the data pools, the
fonts and the degradation ladder. No image is stored in git and none needs to
be: every step is seeded, so `build_corpus.sh` reproduces a corpus **byte for
byte** rather than an equivalent one.

## What you get

A run writes a single dated directory, `evaluation_data/corpus_<stamp>/`,
holding three outputs:

```
evaluation_data/corpus_<stamp>/
  parsing_<stamp>/    the clean corpus     — for document parsing / transcription
  degraded/            scan and photo intake, three severities each, all
                        three document types — for robustness evaluation
  extraction_<stamp>/  flat images + ground_truth.{jsonl,csv} — for
                        information extraction
```

`parsing_<stamp>/` is the primary product: page images, canonical Markdown
transcripts, OmniDocBench-shaped layout annotations, table HTML, a hashed
manifest, the prompt, and the serialisation policy — see "The export" below.

`degraded/` reuses the clean transcripts byte for byte (degradation changes
how legible a page is, never what it says), so any score difference between
clean and degraded is attributable to image quality alone.

`extraction_<stamp>/` is a fourth projection of the same authored truth —
flat per-page images alongside `ground_truth.jsonl` and `ground_truth.csv` —
for information-extraction rather than full-page transcription. It is written
by `generators/extraction_export.py`.

Nothing is downloaded and nothing is installed beyond the environment and
(for `DEGRADE=yes`) `augraphy`, which `build_corpus.sh` installs and verifies
itself — see "Reproducibility" below for why that step exists.

## Document types and layouts

Three document types — invoices, receipts, and bank statements — each with
several layout variants authored in `config/layouts/<type>.yml` and driven by
entries in `ground_truth/<type>.yml`. Page counts are derived from the
authored ground truth at generation time rather than stated here, so this
file cannot go stale the way an earlier version of it did.

## What this deliberately does not do

This repository generates a corpus and emits ground truth. It runs no
extractor, no parser and no scorer. Parser runners, scoring and analysis live
in a **separate repository**; the interface between them is the **exported
corpus directory** above, not shared code — which is why nothing here depends
on a parser.

Also out of scope: layout/region detection and table-structure recognition as
a *modelling target* (the corpus ships that ground truth, it does not score
against it), reading-order labels beyond what the layout annotations already
carry, and multi-page documents. Degraded pages exist only as a secondary
product of an already-exported clean corpus, never as the primary one.

## Reproducibility

Every step is seeded. The same inputs render **byte-identical images**,
pinned by
`tests/test_pipeline.py::test_the_same_input_renders_byte_identical_images`.
If a change moves a single pixel, that is a corpus revision — predictions
already scored against the previous vintage are no longer valid.

Degradation is pinned the same way, down to the OpenCV build: `augraphy`
declares the GUI `opencv-python` as a hard requirement, which would displace
the pinned headless build and silently produce a different corpus under the
same seed (measured: 2 of 9 degraded images differ between builds).
`build_corpus.sh` installs `augraphy` with `--no-deps` and then verifies only
the headless build is present, rather than documenting the step and trusting
it was followed.

## Why the labels can be trusted

**Capture happens inside the drawing primitives.** Each DSL primitive appends
a structured event where it has resolved its content and is about to draw
it — so the label cannot disagree with the pixels about conditionals,
suppression, currency formatting or field lookup. Re-walking the layout tree
at export time to rebuild the transcript would mean two implementations of
the same semantics, and they drift.

**A coverage invariant runs on every `generate`, not just under test.** Every
call that reaches the canvas is tagged with the event that authorised it, and
at page end the renderer asserts that no untagged text was drawn. A new
primitive that puts ink on the page without emitting an event fails the run
with a diagnostic naming it.

## Where the data goes

**Outside the repository, under a date-stamped root.** `build_corpus.sh`
writes to `../evaluation_data/corpus_<stamp>/` beside this checkout (override
with `EVAL_ROOT=`). The intermediate `generate`/`serialise` stages that
`build_corpus.sh` drives write page images and transcript events under the
`dataset_root` configured in `config/generation_config.yml`
(`output_dir`/`derived_dir`, derived from one key so they cannot drift apart).

*Outside*, because a full run writes hundreds of megabytes and inside the
working tree the only thing between that and the git history is
`.gitignore`.

*Date stamped*, because **a corpus is a vintage**. Scoring pairs images to
transcripts through a hashed manifest and refuses a mismatch, so images,
transcripts and export must describe the same run — and a fixed path silently
overwrites the previous corpus, invalidating every prediction already scored
against it. `build_corpus.sh` itself refuses to write into an existing
`corpus_<stamp>/` rather than merge into it silently.

`~` and `${VAR}` are expanded in `dataset_root`, so a shared machine or CI job
can point at a store without editing tracked config. A relative path resolves
against the **repository root**, not the working directory, so it means the
same thing however the command was invoked; absolute paths are taken as
given, and `--output` / `--derived` / `--target` override for a single run of
the underlying `python -m generators.pipeline` commands.

Pinned by `tests/test_data_location.py`, which fails if the shipped default
ever resolves back inside the working tree.

## Layout

```
generators/      pipeline.py    the pipeline commands (validate, generate,
                                 serialise, preview, export, extract)
                 layout_dsl/    schema, walk engine, binding, primitives
                 transcript.py  draw-time capture and the coverage invariant
                 serialise.py   events + policy -> Markdown
                 layout.py      events + policy -> OmniDocBench layout_dets
                 tables.py      events + policy -> table HTML, for TEDS
                 extraction_export.py  authored ground truth -> IE ground_truth.{jsonl,csv}
                 export.py      manifest, README, directory assembly
                 degradation/   scan/photo degradation ladders
config/          generation_config.yml, field_definitions.yml,
                 serialisation.yml, prompt.md, data_pools.yml, layouts/
ground_truth/    invoices.yml, receipts.yml, bank_statements.yml
fonts/           Carlito + Liberation, with licences
```

Tests are local and not committed — a TDD tool for whoever is editing the
generator rather than part of what the corpus ships. Write and run them; the
gates below assume they exist.

## Adding a document type

Usually **no Python**. Add the field set to `config/field_definitions.yml`,
one or more DSL layouts to `config/layouts/<type>.yml`, authored entries to
`ground_truth/<type>.yml`, and any new pools to `config/data_pools.yml`.
Register the type in `config/generation_config.yml` — nothing is discovered
by filename.

Then: `validate` → `generate --type X` → **inspect a page against its
transcript with `preview`** → commit the YAML. The visual inspection is not
optional; there is no field-level check that catches a bad layout.

## The export

```
parsing_<YYYYMMDD>/
  images/CASE001_invoices.png
  transcripts/CASE001_invoices.md
  layout/CASE001_invoices.json
  tables/CASE001_invoices.html      # only when the page has a table
  manifest.jsonl        {image, transcript, doc_type, sha256, layout, [tables]}
  prompt.md
  serialisation.yml
  README.md
```

Filenames are `{case_id}_{doc_type}`, never `{case_id}_{layout_id}`, so a
model cannot infer the layout template before reading a pixel. The policy and
the hashed manifest ship **with the data**: the image hashes make scoring
against a wrong corpus vintage impossible rather than merely detectable
afterwards, and the prompt travels alongside because prompt and ground truth
are a matched pair.

`layout/{stem}.json` carries OmniDocBench-shaped `layout_dets`: element
boxes, block categories, reading order, and — for every `table` annotation —
that table's own HTML in an `html` field. `tables/{stem}.html` is the same
table HTML, written out separately so a TEDS scorer need not parse layout
JSON to reach it; it is present only when the page has at least one table.
Pages with more than one table (both `anz_standard` and `anz_modern` bank
statement layouts) write every table into that one `tables/{stem}.html` file,
one root `<table>` after another in page order — `layout/{stem}.json` is
authoritative per table when a scorer needs each one addressed on its own.

## Quality gates

```bash
pytest tests/ --cov=generators --cov-report=term      # floor 80%
ruff check --fix --ignore ARG001,ARG002,F841 .
ruff format .
mypy generators --ignore-missing-imports
```

`validate` is worth running in CI on its own: a malformed layout YAML is the
likeliest contribution error and catching it needs no render.

## Related

Parser runners and the extraction benchmark that *consumes*
`extraction_<stamp>/ground_truth.{jsonl,csv}` live in a separate repository.
The interface between them is the **exported corpus directory** above, not
shared code.

**Text scoring now lives here**, in `scoring/`, for an internal model
comparison — one operator, and a second repository would have been
coordination overhead with no compensating isolation. The isolation that
mattered is kept by package and environment boundaries instead:

- `scoring/` **never imports `generators/`**. It reads an exported corpus
  directory and nothing else, so "the interface is the directory" still holds
  inside one repository. `tests/scoring/test_boundaries.py` enforces this
  with an AST scan rather than a convention.
- It runs in its own environment, `docparse-score`
  (`environment-score.yml`). `docparse` gains no dependency, and still
  carries no parser — inference runs on the remote GPU host and predictions
  arrive here as files on disk.

See `docs/superpowers/specs/2026-08-25-degradation-matrix-and-scoring-design.md`
§3 for why that reversal was made and what replaces it.
