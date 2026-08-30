# A Self-Contained Corpus Repository — Design

**Date:** 2026-08-27
**Status:** Approved, not yet implemented

---

## 1. Purpose

Make this repository something a third party can clone and run, producing every
artifact their own document-parsing or information-extraction application needs:

- **invoices, receipts and bank statements** as pristine pages with canonical
  transcripts,
- **their degradations** — scan and photo intake, three severities each,
- **IE ground truth** — flat images beside `ground_truth.{jsonl,csv}`.

One command, one environment, one dated run directory.

The generator already produces all three document types, and degradation already
covers all three (`config/degradation.yml` declares
`document_types: [bank_statements, receipts, invoices]`). What is missing is a
working front door, an IE projection, and an install a stranger can complete.

## 2. Decisions taken

| Decision | Chosen | Rejected, and why |
|---|---|---|
| IE support | **Emit IE ground truth; run no extractor.** | Full IE scoring would make this a benchmark harness, and scoring lives in a separate repository by deliberate design. Leaving IE out entirely makes every consumer write the same converter from `ground_truth/*.yml`. |
| Environments | **One.** | Two environments, one needing manual `pip` surgery that silently corrupts 2 of 9 degraded images when skipped, is not something to hand a stranger. See §4. |
| Front door | **`build_corpus.sh`.** | Documenting five commands leaves every consumer to reinvent the ordering and the degradation step. |
| `regenerate_bank_statements.sh` | **Delete.** | It drives the dead `evaluation.*` package, renders only bank statements while exporting all 189 pages, and is superseded. |
| `make_degraded_statements.sh` | **Keep.** | Already calls the current module; a useful narrow entry for re-degrading without regenerating. |

> **Correction (2026-08-31).** The IE row's reason contains a clause that was
> already false when this was written: "scoring lives in a separate repository
> by deliberate design". Scoring had moved *into* this repository two days
> earlier — `6df9759` and `ebedfcb`, both 2026-08-25 — under the reversal
> recorded in `2026-08-25-degradation-matrix-and-scoring-design.md` §3. Text
> scoring lives here, in `scoring/`, with its own `docparse-score` environment.
>
> The decision itself stands unchanged: emit IE ground truth, run no extractor.
> Only its stated reason was wrong, and the narrower true reason still supports
> it — this repository generates a corpus and scores transcription, so a full IE
> benchmark harness remains out of scope.
>
> The "Environments: **One.**" row is **not** affected: it concerns collapsing
> `docparse` and `docparse-degrade` into one *generator* environment (see §4),
> which `2329c2c` then did. `docparse-score` is a separate concern and no part
> of that row's claim.

## 3. What a run produces

```
evaluation_data/corpus_20260827/
  parsing_20260827/      images, transcripts, layout, tables, manifest, prompt, policy
  degraded/              scan x 3 severities, photo x 3, all three document types
  extraction_20260827/   flat images, ground_truth.jsonl, ground_truth.csv
```

Three consumers, three entry points, **one authored truth**. A document-parsing
user reads `parsing_*/`; a robustness user reads `degraded/`; an IE user reads
`extraction_*/`. Every one of these is a projection of the same draw-time events
and the same `ground_truth/*.yml` — nothing is re-derived, and no two of them
can disagree about a page.

## 4. One environment

### 4.1 The test being replaced tested the wrong thing

`tests/test_env.py::test_no_forbidden_dependencies` greps `environment.yml` for
the strings `numpy`, `opencv` and `augraphy`. It asserts nothing about the code:
`generators/` could import numpy throughout and the test would still pass,
because it only reads a YAML file's wording.

Its justification does not apply here either. `environment-degrade.yml` explains
the split as *"the runner tests import `runners/` in an environment where no
parser is installed"* — and **there is no `runners/` package in this
repository**. That is inherited language from the predecessor repo, the same way
`regenerate_bank_statements.sh` still calls a dead `evaluation.cli`.

Meanwhile the property anyone actually cares about **already holds**: no module
under `generators/` outside `generators/degradation/` imports numpy, cv2 or
augraphy, and `degradation/__init__.py` keeps its numpy import inside a function
so `config/degradation.yml` stays loadable — and validatable — without it.

### 4.2 The replacement

```python
def test_the_core_generator_imports_nothing_heavy():
    """`generators/` must stay importable from a checkout with nothing heavy
    installed — the property the environment-file grep only approximated.

    Tests the code rather than a file's wording: the old guard passed while
    numpy was imported anywhere under generators/, because it read only
    environment.yml. Parsed with `ast` rather than grepped, so a re-exported
    or aliased import cannot slip past.
    """


def test_degradation_defers_its_heavy_imports():
    """`config/degradation.yml` must stay loadable, and validatable, from a
    checkout with no numpy — which is why these imports sit inside functions
    rather than at module level."""
```

The first catches a regression the old test could not see. The second pins the
deferred-import trick the config loader depends on. Both are strictly stronger
than a grep over `environment.yml`.

### 4.3 The one real wrinkle, not papered over

augraphy declares the GUI `opencv-python` as a hard requirement. Installed
plainly, both cv2 builds are present, which one wins is a coin toss, and **2 of
9 degraded images come out different** — a corpus that will not reproduce.
`environment-degrade.yml` already lists augraphy's true transitive dependencies
(`numba`, `scikit-image`, `scikit-learn`) precisely so it can be installed with
`--no-deps`; that block moves into `environment.yml`.

Conda's `pip:` section applies flags to the whole invocation, so `--no-deps`
cannot be scoped to one package there. `build_corpus.sh` therefore performs that
single step and **verifies** it:

```bash
pip install --no-deps augraphy==8.2.6
python -c "import cv2, augraphy"          # both import
pip list | grep -ci '^opencv-python '     # must be 0: headless only
```

A README instruction a human can skip becomes a scripted step that fails loudly.
`generators/degradation/geometry.py` already refuses to run when both builds are
present, so this turns a corrupt corpus discovered six steps later into a setup
error discovered immediately.

**Pins stay exact.** Degraded images are corpus data whose hashes go in a
manifest; a version bump that moves one pixel invalidates every prediction made
against the old images.

## 5. The IE export

A new `generators/extraction_export.py`. It has a home waiting for it:
`config/field_definitions.yml`'s `all_columns` is documented as *"the union of
the three `document_fields` lists above, in the order the derived CSV writes
them."* That comment describes this artifact — the schema was kept when the
writer was lost.

One row per case:

| Column | Source |
|---|---|
| `case_id` | the ground-truth key |
| `doc_type` | the document type |
| `image` | flat filename, matching the copied image |
| *(then)* | every field in `all_columns` order, `NOT_FOUND` where absent |

`ground_truth.jsonl` for programs, `ground_truth.csv` for spreadsheets, and the
images copied flat — no per-type subdirectories, because an IE consumer indexes
by `case_id` and should not need to know this corpus's internal layout.

It reads `ground_truth/*.yml` and the clean export's manifest. It renders
nothing, derives nothing and runs no extractor: the scope line moves from
"information extraction is out of scope" to **"we emit IE ground truth; we run
no extractor"**, and `CLAUDE.md` says so where the next contributor will read it.

`layout_fields` entries (`REWARDS_*`, `LINE_ITEM_CATEGORIES`) are **excluded**,
matching `all_columns`, which already omits them: a column only one layout
carries would be `NOT_FOUND` on almost every row.

## 6. `build_corpus.sh`

Replaces `regenerate_bank_statements.sh`, fixing its three faults: it drives
`generators.pipeline` rather than the dead `evaluation.cli`; it generates **all
three document types** rather than only bank statements while exporting all of
them; and it creates the environment if absent instead of printing instructions.

It keeps the two things the old script got right:

- **It refuses to overwrite an existing run directory.** A corpus is identified
  by the hashes in its manifest, and writing over one leaves predictions already
  scored pointing at images that no longer exist.
- **It resolves paths from the script's own location**, not the working
  directory, so it behaves the same however it is invoked.

Steps: environment → `validate` → `generate` → `serialise` → `export` →
`degrade` → extraction-export. Degradation is skippable with `DEGRADE=no`, since
it is by far the slowest step and a parsing-only consumer does not need it.

## 7. README

Rewritten as orientation for someone who has just cloned: what the corpus is,
what the three outputs are for, the one command, and what the repo deliberately
does not do.

It currently claims **165 pages**; the corpus is 189. That number becomes
derived from the ground truth or dropped entirely rather than re-hardcoded — the
same defect as the shipped README's `14 of 165`, which sat wrong across a corpus
that had grown by twelve pages.

## 8. Testing

| Area | What is asserted |
|---|---|
| Import hygiene | §4.2's two tests, replacing the `environment.yml` grep. |
| Extraction export | One row per case across all three types; column order matches `all_columns`; `NOT_FOUND` for absent fields; `layout_fields` columns absent; the JSONL and CSV agree row for row. |
| Export/ground-truth agreement | Every `case_id` in the extraction export exists in the clean manifest, and every manifest case appears exactly once — so the two projections cannot drift in either direction. |
| `build_corpus.sh` | A smoke run with `--limit` into a scratch target: all three output directories appear, and a second run against the same target fails rather than overwriting. |
| Existing suite | Unchanged and green. No image moves; this adds no page and revises no transcript. |

## 9. Out of scope

- Running any extractor, parser or scorer. The interface to those stays the
  exported corpus, not shared code.
- pip/venv install. Conda is assumed, as it is throughout this project.
- Multi-page documents, new layouts, new document types.
- Publishing the corpus anywhere. The repo produces it; distribution is
  separate.

## 10. References

- `config/field_definitions.yml` — `all_columns` and its "derived CSV" comment
- `config/degradation.yml` — the corpus matrix, already all three types
- `environment-degrade.yml` — the augraphy/opencv hazard and the exact pins
- `docs/superpowers/specs/2026-08-27-merged-cells-and-spanning-headers-design.md`
  — the most recent corpus increment, which took the corpus to 189 pages
