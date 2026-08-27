#!/usr/bin/env bash
# Build the whole corpus: clean pages, degradations, and IE ground truth.
#
# Everything is derived from files in this repository -- the authored ground
# truth, the layouts, the data pools, the fonts and the degradation ladder. No
# image is stored in git and none needs to be: every step is seeded, so this
# reproduces a corpus BYTE FOR BYTE rather than producing an equivalent one.
#
#   ./build_corpus.sh              everything
#   DEGRADE=no ./build_corpus.sh   clean corpus and extraction only (much faster)
#
set -uo pipefail

ENV_NAME=${ENV_NAME:-docparse}
DATE_STAMP=${DATE_STAMP:-$(date +%Y%m%d)}
DEGRADE=${DEGRADE:-yes}
AUGRAPHY_VERSION=8.2.6

# Derived from the SCRIPT's location, not the working directory, so it resolves
# the same however the script is invoked.
REPO_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
EVAL_ROOT=${EVAL_ROOT:-$(cd -- "$REPO_DIR/.." && pwd)/evaluation_data}
TARGET="$EVAL_ROOT/corpus_$DATE_STAMP"

fail() { echo "!! $*" >&2; exit 1; }
step() { echo; echo "=== $* ==="; }

cd "$REPO_DIR" || fail "cannot enter $REPO_DIR"
command -v conda >/dev/null || fail "conda is not on PATH"

# Refuse to overwrite. A corpus is identified by the hashes in its manifest,
# and quietly writing over one would leave any predictions already scored
# against it pointing at images that no longer exist.
[[ -e $TARGET ]] && fail "$TARGET already exists.
   Use it as it stands, pass DATE_STAMP= for a new directory, or remove it
   deliberately."

step "environment"
if ! conda env list | grep -qE "^${ENV_NAME}\s"; then
    echo "creating '$ENV_NAME'"
    conda env create -f environment.yml || fail "could not create '$ENV_NAME'"
fi

# augraphy declares the GUI opencv-python as a hard requirement, which would
# displace the headless build. Both provide cv2, and having both installed is a
# coin toss over which one wins -- 2 of 9 degraded images come out different,
# and generators/degradation/geometry.py refuses to run in that state rather
# than writing a corpus that will not reproduce. So: install it without its
# dependencies, then VERIFY, rather than telling a human to check.
if ! conda run -n "$ENV_NAME" python -c 'import augraphy' >/dev/null 2>&1; then
    echo "installing augraphy==$AUGRAPHY_VERSION --no-deps"
    conda run -n "$ENV_NAME" pip install --no-deps "augraphy==$AUGRAPHY_VERSION" ||
        fail "could not install augraphy"
fi
gui=$(conda run -n "$ENV_NAME" pip list 2>/dev/null | grep -ci '^opencv-python ' || true)
[[ $gui -eq 0 ]] || fail "both opencv builds are installed in '$ENV_NAME'.
   cv2 would resolve unpredictably and degraded images would not reproduce.
   Fix:  conda run -n $ENV_NAME pip uninstall -y opencv-python
         conda run -n $ENV_NAME pip install --no-deps augraphy==$AUGRAPHY_VERSION"
conda run -n "$ENV_NAME" python -c 'import cv2, augraphy, numpy' ||
    fail "'$ENV_NAME' cannot import the degradation stack"

mkdir -p "$TARGET" || fail "cannot create $TARGET"
echo "destination: $TARGET"

step "validate — ground truth, layouts, fit budgets"
conda run -n "$ENV_NAME" python -m generators.pipeline validate ||
    fail "validation failed; fix the ground truth or layouts before rendering"

step "generate — page images and draw-time transcript events"
conda run -n "$ENV_NAME" python -m generators.pipeline generate || fail "generation failed"

step "serialise — events to Markdown"
conda run -n "$ENV_NAME" python -m generators.pipeline serialise || fail "serialisation failed"

step "export — the clean corpus"
conda run -n "$ENV_NAME" python -m generators.pipeline export \
    --date "$DATE_STAMP" --target "$TARGET" || fail "export failed"

corpus="$TARGET/parsing_${DATE_STAMP}"
pages=$(find "$corpus/images" -name '*.png' | wc -l | tr -d ' ')
echo "  $corpus: $pages page(s)"

step "extract — flat images and ground_truth.{jsonl,csv}"
conda run -n "$ENV_NAME" python -m generators.pipeline extract \
    --corpus "$corpus" --target "$TARGET" --date "$DATE_STAMP" || fail "extraction export failed"

if [[ $DEGRADE != yes ]]; then
    echo
    echo "Skipping degradation (DEGRADE=$DEGRADE)."
else
    step "degrade — scan and photo intake, three severities each"
    # write_matrix() requires every row's corpus -- including the clean
    # baseline -- to be a directory beside matrix.jsonl (generators/degradation
    # /matrix.py), because a matrix is meant to sit beside every corpus it
    # indexes. The clean corpus lives one level up, in $TARGET, not in
    # $TARGET/degraded, so link it in rather than duplicating 189 images.
    mkdir -p "$TARGET/degraded" || fail "cannot create $TARGET/degraded"
    ln -s "../$(basename "$corpus")" "$TARGET/degraded/$(basename "$corpus")" ||
        fail "cannot link the clean corpus into $TARGET/degraded"
    conda run -n "$ENV_NAME" python -m generators.degradation.cli \
        --corpus "$corpus" --out "$TARGET/degraded" || fail "degradation failed"
fi

echo
echo "=== done: $TARGET ==="
find "$TARGET" -maxdepth 1 -mindepth 1 -type d | sort | while read -r d; do
    printf "  %-30s %4s image(s)\n" "$(basename "$d")" \
        "$(find "$d" -name '*.png' -o -name '*.jpg' | wc -l | tr -d ' ')"
done
