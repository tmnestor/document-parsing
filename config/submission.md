# Submitting a run for scoring

This corpus is scored by a separate tool. You do **not** need its code, its
language, or its environment to be scored by it — the interface is this
directory and the directory of files you produce from it. Any stack that can
write text files can submit.

Read `README.md` beside this file first: it describes the corpus, the
transcription conventions in `serialisation.yml`, and the hashes to verify
before you begin.

## What you submit

One directory. Its immediate subdirectories are systems, named however you want
the system to appear in results — that name is what a leaderboard prints, so
make it identify the model and the deployment, not the run:

```
my_submission/
└── acme-vlm-9B-fp8-2xL4/
    ├── CASE001_bank_statements.md
    ├── CASE001_invoices.md
    ├── CASE001_receipts.md
    ├── ...                        one .md per transcript stem, no exceptions
    ├── _timing.json               REQUIRED
    ├── _prompt_provenance.json    recommended
    └── _unproduced.json           only if some page cannot be produced
```

A directory of loose `.md` files with no system subdirectory is an error, not an
anonymous system. Files beginning with `_` are sidecars and are never mistaken
for predictions.

## 1. The predictions

One Markdown file per page, named for the transcript stem it answers — take the
stems from `transcripts/`, not from your own numbering. `CASE001_invoices.md`
here answers `transcripts/CASE001_invoices.md`.

The file holds your system's transcription of that page and nothing else: no
preamble, no fenced wrapper around the whole document, no commentary.

**Cover every stem.** A missing file stops scoring, because silence is
indistinguishable from a run that died half-way. If your system genuinely cannot
produce a page, declare it (below) — it is then scored as a total failure rather
than quietly excused, so every system stays averaged over the same pages and the
numbers remain comparable.

## 2. `_timing.json` — required

Throughput is half of what is being compared, and it cannot be recovered from
the predictions afterwards. Write it yourself:

```json
{
  "system": "acme-vlm-9B-fp8-2xL4",
  "cards": 2,
  "shard": 0,
  "shards": 1,
  "includes_model_load": false,
  "images": 189,
  "inference_seconds": 2143.7,
  "images_per_minute": 5.29
}
```

| Key | Meaning |
|---|---|
| `system` | Must equal the directory name. |
| `cards` | GPUs (or accelerators) this process occupied. Use `0` for CPU-only. |
| `shard`, `shards` | See "Running in parallel" below. Use `0` and `1` if you ran one process. |
| `includes_model_load` | See below. Get this right. |
| `images` | Pages this process attempted. |
| `inference_seconds` | Seconds spent generating them. |
| `images_per_minute` | `60 * images / inference_seconds`. |

**`includes_model_load` decides whether your number is comparable.** Loading
weights is paid once per process, not per page, and is not what a serving
cluster is sized on — so if you can measure generation separately, exclude the
load and set this `false`. If you cannot (your tool loads the model on every
invocation, so all you have is wall clock), set it **`true`**. Your rate is then
published as a floor, marked `≥`, and read as understating your system. Setting
it `false` when the seconds include load does not flatter you — it puts a
load-inclusive number in a column of load-exclusive ones and makes your system
look slower than it is.

### Running in parallel

If several processes shared the pages, each writes its **own**
`_timing.shard0.json`, `_timing.shard1.json`, … with its own `images`,
`inference_seconds` and `cards`, and its own `shard` index with `shards` set to
the total.

Do not merge them yourself. They are combined correctly on the scoring side:
images are **summed**, seconds are **maxed** — shards run concurrently, so the
box's clock is the slowest of them, not the total. Summing seconds, or taking
the slowest image count, misreports throughput by roughly the number of shards.

## 3. `_prompt_provenance.json` — recommended

**Your prompt is your choice.** Nothing here constrains it, and you are not
required to use the `prompt.md` that ships with this corpus.

But prompt wording moves results as much as model choice does — in one measured
case here, a change to how tables were requested moved a score from 0.4296 to
0.4718 on *identical images* — so a result whose prompt is unrecorded cannot be
interpreted or reproduced later, including by you.

```json
{
  "prompt": "prompts/acme_transcribe_v4.md",
  "sha256": "<hex sha256 of the exact prompt text you sent>",
  "words": 412
}
```

Hash the prompt **as sent**, after any templating. If you send different prompts
to different pages, this file does not describe your run — say so when you
submit, rather than recording one of them.

Omitting the file is allowed. The run is then reported with no prompt recorded,
which is treated as its own distinct value: it will never be silently pooled
with a run whose prompt is known.

## 4. `_unproduced.json` — only when needed

Pages your system cannot produce at all, declared explicitly:

```json
{"unproduced": ["CASE014_receipts", "CASE031_receipts"]}
```

A declared page is scored as a total failure. A page may be declared **or**
produced, never both — a stale declaration beside a real prediction is an error.

## Before you hand it over

- Every transcript stem in `transcripts/` has a `.md`, or is declared unproduced.
- No extra `.md` files for stems that are not in this corpus.
- `_timing.json` exists, its `system` matches the directory name, and
  `includes_model_load` is honest.
- You verified this corpus's `pixels_sha256` values before running, so your
  predictions answer the pages you think they do.

Submit the directory, and say which corpus (`parsing_YYYYMMDD`) it answers.
