"""Compare two builds of the same corpus, corpus by corpus, from their manifests.

Determinism is a contract this repository states but has only ever tested on one
machine: `test_the_same_input_renders_byte_identical_images` re-renders locally.
Rebuilding on another host tests the stronger claim, and the manifest is what
makes the answer measurable rather than a matter of opinion -- every record
carries `sha256` for the image and `transcript_sha256` for the text.

Only manifests are read, never images, so the remote side of a comparison is a
few hundred kilobytes to copy back rather than a gigabyte.

    python compare_vintages.py LOCAL_CORPUS_ROOT REMOTE_CORPUS_ROOT

where each root is a `corpus_<stamp>/` directory, or any directory holding
`manifest.jsonl` files one level down. Exits 1 if anything differs, so it can
gate a build.

Read the result per corpus, not as a single verdict: clean pages diverging means
the renderer is not portable, while only degraded pages diverging localises it
to the degradation stack -- OpenCV's warp and blur take different SIMD paths on
ARM and x86, which is the likeliest cause of a ±1 pixel difference that changes
every hash.
"""

import hashlib
import json
import sys
from pathlib import Path


def _manifests(root: Path) -> dict[str, Path]:
    """Every corpus under `root`, by directory name, that carries a manifest."""
    found: dict[str, Path] = {}
    for manifest in sorted(root.rglob("manifest.jsonl")):
        # Resolve so the clean corpus symlinked into degraded/ is not counted twice.
        corpus = manifest.parent
        found.setdefault(corpus.resolve().name, manifest)
    return found


def _rows(manifest: Path) -> dict[str, dict]:
    rows = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            rows[Path(record["image"]).stem] = record
    return rows


def _fingerprint(manifest: Path) -> tuple[int, str, str]:
    """Two digests over a corpus's hashes: one for images, one for transcripts.

    For comparing two machines that cannot exchange files. The sandbox this was
    written for has no rsync and no scp -- transfer is a browser upload -- so a
    corpus's identity has to fit in a line somebody can paste. Equal digests mean
    every image (or transcript) in that corpus is byte-identical; unequal means
    at least one differs, and only then is a file worth moving.

    **They are reported separately because the two answer different questions.**
    Images differing while transcripts match localises the cause to rendering --
    font rasterisation, or the degradation stack's SIMD paths. Transcripts
    differing means the page CONTENT differs, which is a far more serious result:
    the ground truth itself is not reproducible, and no amount of re-rendering
    fixes it. One combined digest cannot tell those apart.

    Sorted by stem so the digest describes the corpus rather than the order its
    manifest happens to be written in.
    """
    rows = _rows(manifest)
    stems = sorted(rows)
    images = "".join(f"{s}\0{rows[s]['sha256']}\n" for s in stems)
    texts = "".join(f"{s}\0{rows[s].get('transcript_sha256', '')}\n" for s in stems)
    return (
        len(rows),
        hashlib.sha256(images.encode("utf-8")).hexdigest(),
        hashlib.sha256(texts.encode("utf-8")).hexdigest(),
    )


def _print_fingerprints(root: Path) -> int:
    print(f"{'corpus':34} {'pages':>5}  {'images':<18} {'transcripts':<18}")
    for name, manifest in sorted(_manifests(root).items()):
        pages, images, texts = _fingerprint(manifest)
        print(f"{name:34} {pages:>5}  {images[:16]}   {texts[:16]}")
    return 0


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--fingerprint":
        return _print_fingerprints(Path(sys.argv[2]))
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    left_root, right_root = Path(sys.argv[1]), Path(sys.argv[2])
    left, right = _manifests(left_root), _manifests(right_root)

    only_left = sorted(set(left) - set(right))
    only_right = sorted(set(right) - set(left))
    for name in only_left:
        print(f"!! {name}: present in {left_root.name} only")
    for name in only_right:
        print(f"!! {name}: present in {right_root.name} only")

    print(f"\n{'corpus':34} {'pages':>6} {'images match':>14} {'transcripts match':>18}")
    differing = []
    for name in sorted(set(left) & set(right)):
        lrows, rrows = _rows(left[name]), _rows(right[name])
        shared = sorted(set(lrows) & set(rrows))
        images = sum(1 for s in shared if lrows[s]["sha256"] == rrows[s]["sha256"])
        # transcript_sha256 postdates some corpora; compare it only where both state it.
        pairs = [(s, lrows[s].get("transcript_sha256"), rrows[s].get("transcript_sha256")) for s in shared]
        comparable = [(s, a, b) for s, a, b in pairs if a and b]
        texts = sum(1 for _, a, b in comparable if a == b)

        text_col = f"{texts}/{len(comparable)}" if comparable else "n/a"
        flag = "" if images == len(shared) and texts == len(comparable) else "   <-- DIFFERS"
        print(f"{name:34} {len(shared):>6} {images:>9}/{len(shared):<4} {text_col:>18}{flag}")

        if images != len(shared):
            examples = [s for s in shared if lrows[s]["sha256"] != rrows[s]["sha256"]][:3]
            differing.append((name, examples))

    for name, examples in differing:
        print(f"\n{name}: first differing images")
        for stem in examples:
            print(f"  {stem}")

    ok = not differing and not only_left and not only_right
    print("\nIdentical." if ok else "\nBuilds differ.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
