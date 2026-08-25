#!/usr/bin/env bash
# Regenerate the bank-statement corpus and its degradations, from a clean clone.
#
# Everything below is derived from files in this repository: the authored ground
# truth in `ground_truth/bank_statements.yml`, the layouts in
# `config/layouts/bank_statements.yml`, the data pools, the fonts, and the
# degradation ladder in `config/degradation.yml`. No image is stored in git and
# none needs to be -- every step is seeded, so this reproduces the corpus BYTE
# FOR BYTE rather than producing an equivalent one.
#
# Verified on 2026-08-22 from a fresh clone: 55 images identical, 55 transcripts
# identical, and the degraded JPEGs identical too.
#
#   validate    ground truth: ABN checksums, GST as one eleventh, date and
#               amount formats, layout references, fit budgets
#   generate    page images + the transcript events captured at draw time
#   serialise   events + serialisation.yml -> Markdown transcripts
#   export      the shippable corpus: images, transcripts, hashed manifest,
#               prompt, policy
#   degrade     six corpora, two intake channels x three severities  [optional]
#
# TWO ENVIRONMENTS. The first four steps need `docparse`, which is five
# pure-Python packages. Degradation needs `docparse-degrade`, which adds numpy,
# opencv and augraphy. They are separate because `docparse` must stay importable
# without them; see environment-degrade.yml.

set -uo pipefail

ENV_NAME=${ENV_NAME:-docparse}
# The predecessor repo's `synthetic` env carries exactly the right pins and
# already exists on the PROD host, so it is preferred over creating a new one.
# environment-degrade.yml declares the same versions but installing it plainly
# leaves BOTH opencv builds present and the wrong one wins -- see the note there
# and the guard in generators/degradation/geometry.py.
DEGRADE_ENV=${DEGRADE_ENV:-synthetic}
DATE_STAMP=${DATE_STAMP:-$(date +%Y%m%d)}
DEGRADE=${DEGRADE:-yes}

# WHERE THE CORPORA LAND. Deliberately outside the repository: they are data,
# not source, and the repo tracks no images at all. One dated directory holds
# the clean corpus and every degraded variant, so a run is a single self-
# describing artefact that can be moved, archived or deleted as one thing.
#
# `../evaluation_data`, beside the repository rather than inside it, which is
# where it sits on every host: ~/nfs_share/tod_2026/evaluation_data on the
# sandbox and in PROD, ~/Desktop/evaluation_data locally. Derived from the
# SCRIPT's location, not the working directory, so it resolves the same however
# the script is invoked.
REPO_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
EVAL_ROOT=${EVAL_ROOT:-$(cd -- "$REPO_DIR/.." && pwd)/evaluation_data}
RUN_NAME=${RUN_NAME:-bank_statements_$DATE_STAMP}
TARGET="$EVAL_ROOT/$RUN_NAME"

fail() { echo "!! $*" >&2; exit 1; }
step() { echo; echo "=== $* ==="; }

command -v conda >/dev/null || fail "conda is not on PATH"

# Find an environment that can actually run the pipeline, rather than demanding
# one by name. The render steps need only Pillow, PyYAML, typer, rich and Faker,
# and more than one environment here has them: the sandbox carries `synthetic`
# and no `docparse`, and refusing to start there was pure friction.
#
# Membership is tested by importing the CLI, not by listing packages, because
# the import is the thing that has to work.
#
# Verified 2026-08-22 that docparse (Pillow 12.3.0) and synthetic (12.2.0)
# render all 55 statements BYTE-IDENTICALLY, so the choice does not change the
# corpus. If a future environment diverges, the manifest hashes will say so.
usable_env() {
    local candidate
    for candidate in "$@"; do
        [[ -n $candidate ]] || continue
        conda env list | grep -qE "^${candidate}\s" || continue
        if conda run -n "$candidate" python -c             'import evaluation.cli' >/dev/null 2>&1; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

if [[ -n ${ENV_NAME_EXPLICIT:-} ]]; then
    :
elif found=$(usable_env "$ENV_NAME" synthetic docparse "${CONDA_DEFAULT_ENV:-}"); then
    [[ $found == "$ENV_NAME" ]] || echo "note: using '$found' — '$ENV_NAME' is not available here"
    ENV_NAME=$found
else
    fail "no environment here can import evaluation.cli.
   Tried: $ENV_NAME, synthetic, docparse, ${CONDA_DEFAULT_ENV:-none}
   It needs Pillow, PyYAML, typer, rich and Faker.
   Create one:  conda env create -f environment.yml"
fi

[[ -f ground_truth/bank_statements.yml ]] ||
    fail "run this from the repository root — ground_truth/bank_statements.yml is not here"

# The file is a mapping keyed by case id, not a list of entries carrying one.
entries=$(grep -cE '^CASE[0-9]+:' ground_truth/bank_statements.yml || true)
echo "ground truth: $entries authored bank statements"
echo "environments: $ENV_NAME (render) / $DEGRADE_ENV (degrade)"
echo "date stamp:   $DATE_STAMP"
echo "destination:  $TARGET"

# Refuse to overwrite a previous run. A corpus is identified by the hashes in
# its manifest, and quietly writing over one would leave any predictions already
# scored against it pointing at images that no longer exist.
if [[ -e $TARGET ]]; then
    fail "$TARGET already exists.
   A regenerated corpus is byte-identical to the one already there, so there is
   nothing to gain by overwriting it — and predictions scored against the old
   images would silently point at new ones.
   Either use it as it stands, or pass RUN_NAME= / DATE_STAMP= for a new
   directory, or remove it deliberately."
fi
mkdir -p "$TARGET" || fail "cannot create $TARGET"

step "validate — ground truth, layouts, budgets"
conda run -n "$ENV_NAME" python -m evaluation.cli validate ||
    fail "validation failed; fix the ground truth or layouts before rendering"

step "generate — page images and draw-time transcript events"
conda run -n "$ENV_NAME" python -m evaluation.cli generate --type bank_statements ||
    fail "generation failed"

step "serialise — events to Markdown"
conda run -n "$ENV_NAME" python -m evaluation.cli serialise || fail "serialisation failed"

step "export — the shippable corpus"
conda run -n "$ENV_NAME" python -m evaluation.cli export \
    --date "$DATE_STAMP" --target "$TARGET" || fail "export failed"

corpus="$TARGET/parsing_${DATE_STAMP}"
pages=$(find "$corpus/images" -name '*.png' | wc -l | tr -d ' ')
echo "  $corpus: $pages page(s)"

if [[ $DEGRADE != yes ]]; then
    echo
    echo "Skipping degradation (DEGRADE=$DEGRADE). The clean corpus is in $corpus/."
    exit 0
fi

if ! conda env list | grep -qE "^${DEGRADE_ENV}\s"; then
    cat >&2 <<NOTE

!! environment '$DEGRADE_ENV' does not exist, so the degradation step is skipped.
   The clean corpus in $corpus/ is complete and usable.

   If the predecessor repo's `synthetic` env exists here, use it:
     DEGRADE_ENV=synthetic ./regenerate_bank_statements.sh

   Otherwise create one. The uninstall is NOT optional: augraphy declares the
   GUI opencv-python as a hard requirement, so a plain install leaves both
   builds present, cv2 resolves to the wrong one, and 2 of 9 degraded images
   come out different. generators/degradation/geometry.py refuses to run in
   that state rather than writing a corpus that will not reproduce.

     conda env create -f environment-degrade.yml
     conda activate docparse-degrade
     pip uninstall -y opencv-python
     pip install --no-deps augraphy==8.2.6
     pip list | grep -i opencv     # must show ONLY opencv-python-headless
NOTE
    exit 1
fi

step "degrade — six corpora, two intake channels"
conda run -n "$DEGRADE_ENV" python -m generators.degradation.cli \
    --corpus "$corpus" --out "$TARGET" --type bank_statements || fail "degradation failed"

step "eval-export — the layout LMM_POC's extractor reads"
# A second projection of the SAME render, not a second generation, so the two
# consumers cannot drift. Flat images plus ground_truth.{jsonl,csv}, mirroring
# evaluation_data/degraded_20260812; verified byte-identical against LMM_POC's
# own records for all 55 cases.
conda run -n "$ENV_NAME" python -m evaluation.eval_export_cli \
    --corpus "$corpus" --degraded "$TARGET" --out "$EVAL_ROOT" \
    --date "$DATE_STAMP" || fail "eval export failed"

echo
echo "=== done: $TARGET ==="
find "$TARGET" -maxdepth 1 -mindepth 1 -type d | sort | while read -r d; do
    printf "  %-46s %3s page(s)\n" "$(basename "$d")" \
        "$(find "$d/images" -type f | wc -l | tr -d ' ')"
done
cat <<'NOTE'

Every corpus carries its own manifest.jsonl of image hashes, and `score` refuses
to score predictions against a corpus whose hashes do not match. So a corpus
regenerated here can be checked against one generated elsewhere by comparing
manifests -- if the hashes agree, the two are the same corpus and predictions
transfer between them.
NOTE
