# What shipped, 2026-08-31 to 2026-09-01

43 commits, `2cdf8dd..53ae125`.

## Degradation became a scored benchmark dimension (today)

- Every manifest record now carries `family` and `severity` — all seven corpora
  self-describing, clean baseline included
- `degraded/ground_truth.jsonl`: 1,323 rows pooling all seven corpora, each
  naming its transcript
- Scoring reads the label from the manifest instead of guessing it from a
  directory name; raises instead of silently returning `clean/none`
- `--metadata-only` decouples carried metadata from rendering — a README fix
  costs seconds, not 1,134 renders

## Line-item extraction ground truth for bank statements (yesterday)

- Parallel field groups explode into one row per transaction, emitted beside
  the document-level file
- Running balance authored in ground truth rather than computed backwards, with
  the balance chain enforced at validation
- Found and closed four `field_types` groups that validation was silently
  ignoring

## CBA statements now match the real layout (yesterday)

- Long-form Australian date band (`Fri 28 Aug 2026`) spanning the table, rows
  underneath staying `DD/MM/YYYY`
- Date column dropped from date-grouped statements, matching the actual CBA page
- Band charged one cell rather than one per column; grid width reset per table

## Scoring pipeline unblocked (yesterday)

- New HTML table parser — scoring had only ever parsed pipe tables while the
  corpus emits HTML
- Prompt leak guard extended to see `<td colspan=…>` cells it had been blind to

## Correctness and hand-off hygiene

- `export --target` made required — the flag that once put 712 files in the
  data root
- Docs corrected: scoring lives in this repo, and the consuming repo is now
  named
- Two corpus vintages regenerated (08-31, 09-01)
