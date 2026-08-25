#!/usr/bin/env bash
# Step 1 of 3, run LOCALLY: build the degraded bank-statement corpora.
#
# Bank statements first, and only, because that is where the deployment case
# lives. The 31B's argument is 99.0% usable amounts and 1.0% misfiled on
# statements; on receipts every gemma checkpoint already misfiles zero, so
# degrading receipts would measure a document type with no headroom. Invoices
# are near-saturated too. If statements survive scanning, widen the set then.
#
# 55 pages x 6 tiers = 330 degraded pages, about 300 MB of JPEG.
#
# Runs in an environment with augraphy, NOT in docparse. On this machine the
# predecessor repo's `synthetic` env already carries the exact pins; otherwise
# create the one this repo declares:
#
#     conda env create -f environment-degrade.yml
#     pip uninstall -y opencv-python && pip install --no-deps augraphy==8.2.6

set -uo pipefail

ENV_NAME=${ENV_NAME:-synthetic}
CORPUS=${CORPUS:-parsing_20260820}
OUT=${OUT:-degraded}
TYPE=${TYPE:-bank_statements}

fail() { echo "!! $*" >&2; exit 1; }

[[ -d $CORPUS/images ]] || fail "corpus not found: $CORPUS (set CORPUS=)"

conda run -n "$ENV_NAME" python -c "import augraphy, cv2, numpy" 2>/dev/null ||
    fail "environment '$ENV_NAME' cannot import augraphy/cv2/numpy.
   Set ENV_NAME=, or create it:  conda env create -f environment-degrade.yml"

# The clean corpus must be the one the 31B was scored on, or the degraded set
# measures degradation plus a corpus change.
[[ -f $CORPUS/manifest.jsonl ]] || fail "$CORPUS has no manifest.jsonl; it is not an export"
pages=$(grep -c "\"doc_type\": \"$TYPE\"" "$CORPUS/manifest.jsonl")
echo "source: $CORPUS  ($pages $TYPE page(s))"
[[ $pages -gt 0 ]] || fail "no $TYPE pages in $CORPUS/manifest.jsonl"

mkdir -p "$OUT"
conda run -n "$ENV_NAME" python -m generators.degradation.cli \
    --corpus "$CORPUS" --out "$OUT" --type "$TYPE" || fail "degradation failed"

echo
echo "=== built ==="
du -sh "$OUT"/*/ 2>/dev/null

# Ship only what the run needs. The transcripts and manifest travel too, because
# `score` verifies the manifest before it will score anything -- and they are
# small next to the images.
tar czf degraded_${TYPE}.tgz -C "$OUT" .
echo
echo "archive: degraded_${TYPE}.tgz  ($(du -h degraded_${TYPE}.tgz | cut -f1))"
cat <<NOTE

Next, copy it to the sandbox and unpack beside the repo:

  scp degraded_${TYPE}.tgz <sandbox>:~/nfs_share/tod_2026/doc-parsing-corpus/
  ssh <sandbox> 'cd ~/nfs_share/tod_2026/doc-parsing-corpus && mkdir -p $OUT &&
                 tar xzf degraded_${TYPE}.tgz -C $OUT'

then run ./run_degraded_31b.sh there.
NOTE
