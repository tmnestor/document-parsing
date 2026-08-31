# Degradation as scored ground truth

**Date:** 2026-09-01
**Status:** design, awaiting review

## 1. What this changes

Degradation already works. `generators/degradation/` renders six tiers — `scan`
and `photo`, three severities each — from an exported clean corpus, and
`matrix.jsonl` indexes the seven resulting corpora including the clean baseline.

What it is not is **scored**. `config/generation_config.yml` records the corpus
as "pristine-only", and CLAUDE.md places degradation outside the modelling
target: degraded pages exist "only as a secondary product of an already-exported
clean corpus". Nothing in the corpus states, per image, what was done to it.

This makes degradation a first-class benchmark dimension. Competing models,
outside this repository, are scored on two things at once:

1. **Identify the degradation** — which intake family and severity is this page?
2. **Transcribe the degraded page** — the existing transcription task, on damaged input.

## 2. Decisions taken

| Decision | Chosen | Rejected, and why |
|---|---|---|
| The label | **Family + severity, seven classes**: `clean`, and `scan`/`photo` × `light`/`moderate`/`heavy`. | Multi-label artefact detection (`skew`, `roller_streaks`, `cast_shadow`…) is closer to what a model perceives, but `config/degradation.yml` bundles artefacts into fixed tiers, so the set is largely a function of the tier — a model could infer the tier and emit the bundle without seeing anything. Sampled parameter values were rejected as a different task (regression) that no consumer asked for. |
| Path obfuscation | **None.** Tier corpora keep their `<corpus>_<family>-<severity>` names. | The corpus elsewhere refuses to leak an answer in a filename (`{case_id}_{doc_type}`, never the layout id). That rule does not bind here: the VLM under test reads the image, not the path. Pooling images under opaque variant names would duplicate 1,134 images to defend against an exposure the consumer does not have. |
| Where the label lives | **Both**: `family` and `severity` on every manifest record, so each corpus is self-describing; **and** one pooled `ground_truth.jsonl` across all seven, so the identification benchmark is a single artifact. | Manifest-only leaves a consumer to walk seven manifests to assemble the benchmark. Pooled-only leaves a degraded corpus unable to say what was done to it without a sibling file. |
| Transcription ground truth | **Unchanged — the existing transcript.** | Nothing to decide: `generators/degradation/cli.py:220` copies transcripts with `shutil.copyfile`. Degradation moves pixels, not content. |
| Format | **JSONL.** | Consistent with the 2026-08-31 line-item ruling: rows need not share a fixed header, and this project's config artifacts favour JSON over CSV. |

## 3. The label, and one property of it

A row's label is the pair the matrix already uses:

```
family:   clean | scan | photo
severity: none  | light | moderate | heavy
```

Seven valid combinations: `clean/none`, and each family × each severity.

### 3.1 A known weakness, recorded rather than fixed

The severity rungs were not tuned to be visually separable. `config/degradation.yml`
says so directly: both `heavy` tiers "were first written harsher than they are
now and pulled back after looking at the output: they should be at the edge of
legibility for a human, not past it." The ladder optimises for *finding where a
system breaks*, which is a different objective from *being distinguishable*.

A seven-class score may therefore behave closer to a three-class one — clean,
scan, photo — with noisy sub-rungs. That is a property of the benchmark, not a
defect to correct by re-tuning ladders that were deliberately set where they are.
Consumers should expect family accuracy to exceed severity accuracy, and a
scoring repository may reasonably report the two separately.

## 4. Where the ground truth lives

### 4.1 Per-image, in every manifest

`manifest_record` (`generators/export.py`) currently returns
`{image, transcript, doc_type, sha256, transcript_sha256}`. It gains `family` and
`severity`.

**This changes the clean export too**, and deliberately. `degrade` imports
`manifest_record` from `generators.export` — one writer serves both — so all
seven corpora describe themselves the same way, the clean one carrying
`family: clean`, `severity: none`. The alternative leaves the baseline as the
only corpus that cannot say what it is.

It is a change to an artifact consumers already read. It is additive: no existing
field moves or changes meaning.

### 4.2 Pooled, beside the matrix

`degraded/ground_truth.jsonl`, one row per image across all seven corpora:

```json
{"corpus": "parsing_20260901_scan-moderate",
 "image": "images/CASE001_bank_statements.png",
 "transcript": "transcripts/CASE001_bank_statements.md",
 "case_id": "CASE001", "doc_type": "bank_statements",
 "family": "scan", "severity": "moderate"}
```

It sits beside `matrix.jsonl` because it is the same kind of thing: an index
spanning corpora rather than describing one. Unlike `extraction_<stamp>/`, it
owns no images — they live in the tier corpora — so it is a file at the
degradation root, not a directory of its own.

`build_corpus.sh` already symlinks the clean corpus into `degraded/` so that
`write_matrix`'s "every corpus is a directory beside the matrix" rule holds
without duplicating 189 images. The pooled file inherits that arrangement: every
`corpus` value it names resolves beside it.

**Each row serves both tasks.** Carrying `transcript` costs nothing and means a
consumer scoring identification and transcription reads one file rather than
joining three.

## 5. Scale

| | corpora | pages each | rows |
|---|---|---|---|
| clean | 1 | 189 | 189 |
| scan (light, moderate, heavy) | 3 | 189 | 567 |
| photo (light, moderate, heavy) | 3 | 189 | 567 |
| **total** | **7** | | **1,323** |

## 6. Testing

- Every manifest record in all seven corpora carries `family` and `severity`,
  and the clean one carries `clean` / `none`.
- The pooled file has exactly 1,323 rows, 189 per corpus.
- Every `corpus` named in the pooled file resolves to a directory beside it,
  and every `image` and `transcript` path resolves inside that directory.
- The label pair in each pooled row matches the manifest record it came from —
  the two representations cannot drift.
- Every `(family, severity)` pair is one of the seven valid combinations.
- A degraded transcript is byte-identical to its clean counterpart, which is what
  makes the transcription label reusable.
- The existing clean corpus tests still pass with the widened manifest.

## 7. Out of scope

- **Re-tuning the severity ladders** so the rungs separate better. They are where
  they are for a stated reason; §3.1 records the consequence instead.
- **Artefact-level labels.** Rejected in §2; revisiting is its own spec.
- **Scoring.** This repository emits ground truth and runs no scorer. How
  identification accuracy is computed — and whether family and severity are
  reported separately — belongs to the consuming repository.
- **Degrading anything but an exported clean corpus.** The pipeline order stands:
  generate, serialise, export, then degrade.
- **`extraction_*`.** Line-item and document-level extraction ground truth are
  unaffected; degradation changes pixels, not authored values.

## 8. References

- `config/degradation.yml` — the two ladders, their phases, and the reasoning for
  each tier's settings, including the legibility tuning quoted in §3.1.
- `generators/degradation/cli.py:220` — transcripts copied with `shutil.copyfile`.
- `generators/degradation/cli.py:194` — tier directory naming,
  `<corpus>_<family>-<severity>`.
- `generators/degradation/matrix.py` — `matrix_row` and the rule that every
  indexed corpus is a directory beside the matrix.
- `build_corpus.sh` — the symlink that satisfies that rule for the clean baseline.
- `generators/export.py` — `manifest_record`, shared by export and degrade.
