# Degradation Matrix and Text Scoring — Follow-Ups

**Date:** 2026-08-26
**Source:** the twelve per-task reviews, the whole-branch review, and the fix-wave
re-review carried out while building
`docs/superpowers/specs/2026-08-25-degradation-matrix-and-scoring-design.md`.
**Merged as:** `2eed28a..3ee37a6` (20 commits).

Everything below was found by a review, triaged, and deliberately **not** fixed —
either because it was Minor, or because the fix would have cost more than the
defect. Nothing here blocks anything shipped. Line references are as-reviewed and
may have shifted slightly under the fix wave.

Two Critical and five Important findings from the same reviews **were** fixed
before merge; they are in the git history (`28b328f`, `569caf7`, `220213d`,
`e455b8c`) and are not repeated here.

---

## 1. Worth a ticket

**F1 — the matrix guard fires after all the work, not at startup.**
`generators/degradation/matrix.py:98`. `degrade` renders every tier image and
*then* validates that each named corpus exists beside the matrix. With a
non-default `--out` that does not contain the clean corpus, a run spends 30–60
minutes of Augraphy, leaves the tier corpora on disk, and raises `MatrixError`
with no matrix written and no standalone command to build one. The default path
(`--out` unset → `exports_dir`, where the clean corpus already lives) is
unaffected. Conflicts with the repo's own "validate configuration at startup,
before any work begins" rule. **Fix:** a pre-check in `degrade` before the tier
loop. Also `MatrixError` is not caught in `cli.py`, so it surfaces as a traceback
rather than through `_fail` — pre-existing for the "no clean row" branch.

**F2 — `degrade` treats `prompt.md` as optional; `scoring` requires it.**
`generators/degradation/cli.py:199-202` copies each carried file only
`if source.exists()`, while `scoring/corpus.py:87-96` refuses any corpus lacking
`prompt.md`. A clean corpus missing the prompt therefore produces six tiers that
all pass `degrade` and all fail `score` — again after the full Augraphy run.
`export` always writes it today, so exposure is low, but `prompt.md` is
load-bearing for the prompt-hash guard and deserves a fail-fast in `degrade`
rather than a silent skip. `README.md` and `serialisation.yml` are fine as
best-effort copies.

**F3 — the shipped README can contradict itself on case folding.**
`generators/export.py:177` now generates the normalisation clause from
`config/scoring.yml`, but `:182` still hardcodes *"**Do not fold case**: reading
account names and identifiers with correct case is legitimately part of
transcription."* Flip `fold_case: true` and the shipped README says "…and fold
case" in one paragraph and "**Do not fold case**" two paragraphs later — exactly
the drift generating the clause was meant to make impossible. **Fix:** delete the
redundant sentence, or make its rationale conditional.

**F4 — rows carry no fingerprint of the policy they were scored under.**
`report --rows a.jsonl --rows b.jsonl` merges freely, and `--rows` is explicitly
repeatable, so two runs made either side of a `config/scoring.yml` edit aggregate
into one table with nothing flagging the incomparability. `report` loads the
policy but reads only `reporting.percentiles`. **Fix:** record a `policy_sha256`
per row and assert agreement in `aggregate`. Low likelihood — the config is
version-controlled and argues against changing itself — but the consequence is a
silently meaningless comparison, which is the failure class this project's
vintage hashing exists to prevent.

---

## 2. Ergonomics and error quality

**F5 — `--out` in a non-existent directory fails after the work.**
`scoring/score.py:222` calls `write_text` with no `mkdir(parents=True,
exist_ok=True)`, and sits outside the `try`, so `score --out results/rows.jsonl`
with no `results/` gives a bare traceback after scoring completes.
`scoring/report.py:192` has the same shape.

**F6 — `entry["manifest_sha256"]` is unguarded.** `scoring/score.py:231`. A
hand-edited or pre-branch `matrix.jsonl` lacking the key raises a bare `KeyError`
past `except ScoringError`. Every matrix `write_matrix` produces has it, so
exposure is narrow — and `entry["corpus"]`/`["family"]` already had the same
shape.

**F7 — an empty `matrix.jsonl` misattributes its error.** `scoring/score.py:261`.
With zero entries the refusal blames `--predictions-root` and prints
`wanted_corpora=[]`. It still exits 1 with a diagnostic rather than writing zero
rows, so the outcome is right and only the cause is wrong. `_matrix_rows` has no
non-empty guard.

**F8 — `--family`/`--severity` are silently ignored in matrix mode.** Passing
`--matrix … --family scan` is accepted and does nothing. The repo's fail-fast
style argues for a diagnostic.

**F9 — `--corpus` mode still has no empty-rows guard.** The fix wave added one for
matrix mode only. A corpus with an empty `manifest.jsonl` loads with zero pages
(`scoring/corpus.py:98-113`) and writes a zero-byte `rows.jsonl` under a green
"Scored 0 page(s)". Same asymmetry the matrix-mode fix removed.

**F10 — malformed machine-written files raise raw exceptions.** A malformed line
in `matrix.jsonl` (`generators/degradation/matrix.py`, `scoring/score.py`) or in
`manifest.jsonl` (`scoring/corpus.py:102-112`) surfaces as `KeyError` /
`JSONDecodeError` rather than a four-element diagnostic. Both files are
machine-written and hand-editing is not a supported workflow, so this is a
consistency gap rather than a live risk. Worth one change wrapping both.

**F11 — `export`'s `config/scoring.yml` guard fires inside `export_corpus`**
(`generators/export.py:180`) rather than at command start, so it reports after
images have been copied. Cheap and idempotent there, so low impact.

**F12 — the vintage-mismatch `recover` line doesn't explain the danger.**
`scoring/predictions.py`. The prompt-mismatch guard four lines above does explain
its stakes; this one gives the what and a remediation but not the why.

**F13 — `policy.py`'s file-missing `recover` hardcodes `"config/scoring.yml"`**
instead of the passed-in `path`, inconsistent with the same module's seven other
`recover` lines, which all interpolate. One-line fix.

---

## 3. Consistency and polish

**F14 — the tier-directory expression is duplicated.**
`generators/degradation/cli.py:160` and `:212` both build
`plan.out / f"{corpus.name}_{tier.family}-{tier.name}"`. Text-identical today; a
future edit to one only would silently break the matrix-to-directory mapping this
subsystem exists to guarantee. A `_tier_dir(corpus_name, tier)` helper is three
lines.

**F15 — the policy-path default is written twice.** `scoring/score.py:25` defines
`_DEFAULT_POLICY = Path("config/scoring.yml")`; `scoring/report.py:141-143`
re-literals the same string inline. Both should share one constant, plausibly on
`scoring.policy`, which already owns everything else about that file.

**F16 — `matrix_row` reads the manifest twice** (`read_text` then `read_bytes`).
`read_bytes` is required for a byte-exact hash and `read_text` cannot substitute
without an encode round-trip, so this is inherent rather than wasteful. One extra
read of a 165-line file, once per run.

**F17 — `_note(corpus, tier, pages)` leaves `tier` unannotated.**
`generators/degradation/cli.py:220`. `Tier` is importable from the same package.
The only unannotated parameter in the branch.

**F18 — `_SCORING_POLICY_PATH` is cwd-relative** (`generators/export.py`),
matching the dominant `generators/` pattern (`pipeline.py`, `schema.py`,
`field_providers.py`) but not the more robust `__file__`-anchored form used by
`content_engine.py` and `payment_block.py`. A pre-existing split in the codebase
worth standardising someday.

**F19 — `case_id = stem.split("_")[0]`** (`scoring/corpus.py`) assumes no
underscore inside a case id. Verified against the shipped manifest: all 165 stems
are `CASE###_<doc_type>`. Holds for everything the generator produces.

**F20 — `render_markdown` doesn't escape a literal `|`** in a field value
(`scoring/report.py`). Cell values are model ids, doc types, family, severity and
formatted floats; a pipe in any of those is implausible.

**F21 — `_DASHES` omits U+2010 and U+2011** (`scoring/normalise.py`). NFKC does
*not* fold U+2010 to ASCII hyphen, so the gap is real — but a model emitting a
Unicode hyphen where the corpus has ASCII is rare, and costs one edit when it
happens.

**F22 — spec §9.1 writes `cli degrade`, a subcommand that does not exist.** The
app registers a single `@app.command()`, so Typer omits the subcommand name;
`--help` confirms `python -m generators.degradation.cli [OPTIONS]`. Code,
docstrings and every `recover` string use the correct form — only the spec's prose
is stale.

**F23 — under `--type`/`--limit`, clean and tier matrix rows report different
page counts.** The clean row describes the full source corpus, tier rows the
filtered subset. The scorer is immune — it pairs by page stem and scores each
corpus against its own manifest — and the clean row is accurate *about the clean
directory*. What diverges is the claim that the matrix describes one comparable
set. A warning when either filter is active would close it.

---

## 4. Test-suite gaps

**F24 — both WER tests use equal word counts.** Neither would catch
`word_error_rate` dividing by `len(pred_words)` instead of `len(ref_words)`. The
code divides correctly (`scoring/metrics.py:58`); the test cannot catch a
regression. One unequal-length case closes it. Note `tests/` is gitignored, so
this cannot block a commit.

**F25 — document `strip_markdown`'s lossiness in `config/scoring.yml`.** Content-free
separator rows collapse entirely. That file's comments are already the best
documentation in the repo; three lines noting it would finish the thought.

---

## 5. Parked findings — real, with a revisit condition

Both were raised by review, adjudicated against corpus evidence, and independently
re-verified by the whole-branch review, which agreed. Neither is a defect **for
this corpus**; both become real if the stated condition changes.

**P1 — `_SEPARATOR_ROW` over-matches any line of whitespace/colon/hyphen/pipe.**
Checked against all 165 shipped transcripts: 248 lines match the pattern, **zero
contain an alphanumeric character**. Every over-match is a content-free blank row
that `config/serialisation.yml`'s `headerless_table: empty_header_row` deliberately
emits. No line carrying text can match. Tightening would not change blank-row
handling (pipe-stripping plus whitespace collapse already erases them) and *would*
start penalising a model that renders empty cells as dashes — a cosmetic choice
NORMALISED exists to forgive.
**Revisit if:** a layout ever emits a data row built only from punctuation. A model
dropping such a row would then be invisible to NORMALISED. STRICT still catches it.

**P2 — `_EMPHASIS` strips unpaired underscores.** **Zero of the 165 transcripts
contain an underscore.** With no underscore in any reference, a stray `_` from a
model is noise, and the current permissive strip forgives it; paired-delimiter
matching would preserve it and manufacture a mismatch.
**Revisit if:** an identifier ever gains an underscore — a new field, or a new
`config/data_pools.yml` value. A model dropping it would then be invisible to
NORMALISED.

---

## 6. Deferred subsystems

Named in the spec's §2 as out of scope for that increment, and still open.

**A — ground-truth emission.** Geometry on events, block IDs, an explicit order
index, table HTML. This is what would let the corpus score layout detection,
reading order and table structure (TEDS) rather than text alone. The renderer
already knows every element's box, class and draw order at capture time — those
labels are *computed and discarded*, not absent. Emitting them would give
**authored** rather than annotated ground truth for those tasks, which no existing
benchmark has.

**B — structural realism.** Side-by-side vendor/payer invoice blocks,
`colspan`/`rowspan`, spanning headers. Invoices currently contain **no genuine
side-by-side content at all**: the only two `split` blocks in
`config/layouts/invoices.yml` have an empty left column and exist to right-align
totals. Vendor-left/payer-right is the construct the design doc names as the one
convention competent models genuinely disagree on, and it occurs in real
Australian tax invoices — making it the highest-value structural addition.

**Trigger for B:** spec §9 success criterion 5 — if no degradation tier separates
at least two models on at least one document type, the corpus cannot discriminate
them and B stops being optional.
