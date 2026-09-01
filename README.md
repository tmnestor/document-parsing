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
                          matrix.jsonl        one row per corpus (clean + six tiers)
                          ground_truth.jsonl  one row per image across all seven
  extraction_<stamp>/  flat images + ground_truth.{jsonl,csv} — for
                        information extraction
```

`parsing_<stamp>/` is the primary product: page images, canonical Markdown
transcripts, OmniDocBench-shaped layout annotations, table HTML, a hashed
manifest, the prompt, and the serialisation policy — see "The export" below.

`degraded/` reuses the clean transcripts byte for byte (degradation changes
how legible a page is, never what it says), so any score difference between
clean and degraded is attributable to image quality alone. Every image across
the seven corpora — the clean export plus scan/photo × light/moderate/heavy —
carries a `family`/`severity` label (`"clean"`/`"none"` for the undegraded
baseline), stated on the image's own manifest record and pooled into two index
files written beside the tier directories:

- `matrix.jsonl` — one row per corpus: `{corpus, family, severity, pages,
  doc_types, manifest_sha256}`. Names the seven corpora as one set and pins the
  manifest hash each was built from, so `scoring/score.py --matrix` can score
  every corpus in one run and refuse a corpus that has since been re-exported.
- `ground_truth.jsonl` — one row per image across all seven corpora:
  `{corpus, image, transcript, case_id, doc_type, family, severity}`. It is
  the label set for degradation as a scored dimension — which intake channel
  and severity produced this image — and names the matching transcript so
  scoring transcription and identification both read from one file.

Both are written by `generators/degradation/matrix.py`, driven from
`generators/degradation/cli.py`.

`extraction_<stamp>/` is a fourth projection of the same authored truth —
flat per-page images alongside `ground_truth.jsonl` and `ground_truth.csv` —
for information-extraction rather than full-page transcription. It is written
by `generators/extraction_export.py`.

Nothing is downloaded and nothing is installed beyond the environment.
`environment.yml` pins every runtime dependency directly, degradation
included, with no separate install step and no post-install verification to
run.

## Document types and layouts

Three document types — invoices, receipts, and bank statements — each with
several layout variants authored in `config/layouts/<type>.yml` and driven by
entries in `ground_truth/<type>.yml`. Page counts are derived from the
authored ground truth at generation time rather than stated here, so this
file cannot go stale the way an earlier version of it did.

## What this deliberately does not do

This repository generates a corpus and emits ground truth. It runs no
extractor and no parser. Parser runners and analysis live in a **separate
repository** ([bank-statement-error-analysis](https://github.com/tmnestor/bank-statement-error-analysis));
the interface between them is the **exported corpus directory** above, not
shared code — which is why nothing here depends on a parser.

Text scoring is the one exception and lives here, in `scoring/`. It reads an
exported corpus directory and never imports `generators/`, so the interface is
still the directory even inside one repository — see [Related](#related) for
why it was brought in and what enforces that boundary.

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

Reproducibility has to hold **across machines**, not just across runs on one —
this corpus is built by more than one team, on more than one host. Two
defects, once found, threatened that: Pillow silently choosing a different
text-layout engine per platform, and the degradation pipeline computing pixels
with functions IEEE-754 does not guarantee are portable. Both are closed:

- **Text layout is pinned to `Layout.BASIC`** (`generators/common.py`). Left
  unset, Pillow picks Raqm when the wheel bundles it and Basic when it does
  not, so the same font laid the same text out differently on macOS and Linux
  — different glyph shaping, different pixels, before degradation even runs.
- **The degraded pixel path uses only `+ - * /`, `sqrt`, comparisons and
  integer arithmetic** — the operations IEEE-754 requires every conforming
  platform to round identically — and never `exp`, `log`, `pow`, `sin` or
  `cos`, which come from the platform's libm and were measured to differ in
  their final bits between glibc and Apple's. The ink and paper effects
  (`InkBleed`, `LightingGradient`, `ShadowCast`) are this project's own
  re-derivation for exactly that reason, not a third-party library's — see
  `generators/degradation/effects.py` and
  `docs/superpowers/specs/2026-09-01-cross-machine-determinism-design.md`.

**This has been measured, not just engineered.** On 2026-09-02 the same corpus
was built from this commit on `arm64 Darwin` and on `x86_64 Linux`, and all
**1,323 images across the seven corpora came out pixel-identical**, as did every
transcript. The six degraded corpora matched byte for byte as well.

The clean corpus is the one place the *files* differ, and the reason is worth
knowing because it decides which hash means what. Clean pages are PNG, PNG is
lossless, and the two hosts carry different zlib builds (zlib-ng 1.3.1 against
zlib 1.3.2) — so the same pixels compress into different byte streams. The
degraded corpora are JPEG and both hosts carry the same `libjpeg-turbo`, which
is why those matched even at the byte level.

So a corpus is identified by **`pixels_sha256`**, the hash of the decoded RGB
pixels, which is stable across encoders and architectures. `sha256` remains in
the manifest and keeps its own job: catching a truncated or corrupted transfer,
which is a property of the file that arrived rather than of the image it
carries. A vintage check compares the former; a download check compares the
latter.

Reproduce the measurement with `compare_vintages.py --fingerprint` on each host
and compare the printed digests. `probe_phases.py` and `probe_augmentations.py`
localise a divergence to a single stage if one ever appears.

One caveat on the claim's scope: two architectures were tested, at identical
pinned dependency versions. That is what "portable" is warranted to mean here.

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
given, and `--output` / `--derived` override for a single run of the underlying
`python -m generators.pipeline` commands.

`dataset_root` covers the working data — images and transcripts. It does not
cover the export: `export --target` is required and has no configured default,
because the deliverable is a hand-off — to `scoring/` here, and to the parser
runners elsewhere — and its destination belongs to whoever asks for it.
`build_corpus.sh` passes it.

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
  manifest.jsonl        {image, transcript, doc_type, sha256, transcript_sha256,
                         family, severity, layout, [tables]}
  prompt.md
  serialisation.yml
  README.md
```

Filenames are `{case_id}_{doc_type}`, never `{case_id}_{layout_id}`, so a
model cannot infer the layout template before reading a pixel. The policy and
the hashed manifest ship **with the data**: the image hashes make scoring
against a wrong corpus vintage impossible rather than merely detectable
afterwards, and the prompt travels alongside because prompt and ground truth
are a matched pair. `transcript_sha256` is hashed for the same reason the
image is — re-emitting transcripts under a changed serialisation policy moves
no pixel, so an image-only check would pass while the labels underneath had
changed.

`family` and `severity` state what was done to the page: `clean`/`none` for
this export, and the intake channel and rung for a degraded one. They are on
every record so a corpus is self-describing — a degraded page that cannot say
what was done to it cannot be scored on identifying it — and they are what
`degraded/ground_truth.jsonl` pools across all seven corpora.

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

Parser runners live in
[bank-statement-error-analysis](https://github.com/tmnestor/bank-statement-error-analysis)
— `runners/run_docling.py`, `run_mineru.py`, `run_vlm.py`, each in its own
environment — together with the transcription analysis built on their output.
The interface between the two repositories is the **exported corpus directory**
above, not shared code: it consumes a `parsing_<stamp>/` export and verifies the
manifest's sha256 per image before scoring anything.

`extraction_<stamp>/ground_truth.{jsonl,csv}` currently has **no consumer**. It
is emitted so that an information-extraction benchmark does not have to
reimplement the converter from `ground_truth/*.yml`; nothing reads it yet.

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
