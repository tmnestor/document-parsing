"""Locate cross-machine non-determinism: is it the pixels, or only the container?

`compare_vintages.py` hashes what the manifest hashes -- FILE BYTES. That is the
right thing for scoring (a corpus is its files) but the wrong thing for
diagnosis, because two machines can render *identical pixels* and still write
different bytes: PNG is lossless, so a different zlib produces a different
compressed stream from the same image, and JPEG encoding varies with libjpeg.

So this hashes PIXELS as well, and reports both. The pair tells you which
problem you have:

  pixels EQUAL, bytes DIFFER   -> rendering is deterministic; only the encoder
                                  differs. Fixable without touching the
                                  renderer, and no page actually looks
                                  different.
  pixels DIFFER                -> rendering itself is not portable. The synthetic
                                  probes below then localise it: text-only
                                  differing implicates FreeType/Raqm glyph
                                  rasterisation, shapes-only implicates drawing.

Run on each machine and compare the printed lines. Nothing is transferred.

    python probe_determinism.py [CORPUS_ROOT]
"""

import hashlib
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, features

REPO = Path(__file__).resolve().parent
FONT = REPO / "fonts" / "LiberationSans-Regular.ttf"


def _pixels(image: Image.Image) -> str:
    """Hash the decoded pixels, independent of how the file was encoded."""
    return hashlib.sha256(image.convert("RGB").tobytes()).hexdigest()[:16]


def _environment() -> None:
    import PIL

    print(f"Pillow {PIL.__version__}")
    for name in ("freetype2", "raqm", "harfbuzz", "fribidi", "libjpeg_turbo", "zlib"):
        try:
            print(f"  {name:16} present={features.check(name)!s:5} version={features.version(name)}")
        except Exception as exc:  # a feature Pillow does not know about
            print(f"  {name:16} unknown ({exc})")


def _synthetic() -> None:
    """Render text and shapes separately, so the two can be blamed separately."""
    # Shapes only: no font involved at all.
    shapes = Image.new("RGB", (400, 200), "white")
    draw = ImageDraw.Draw(shapes)
    draw.rectangle((10, 10, 380, 60), outline="black", width=2)
    draw.line((10, 100, 380, 180), fill="black", width=3)
    draw.ellipse((40, 110, 200, 190), outline="black", width=1)
    print(f"  shapes-only          pixels={_pixels(shapes)}")

    # Text only, with the vendored face the corpus actually uses.
    for size in (9, 11, 14):
        canvas = Image.new("RGB", (700, 90), "white")
        pen = ImageDraw.Draw(canvas)
        font = ImageFont.truetype(str(FONT), size)
        pen.text((5, 5), "Commonwealth Bank 01/09/2023 $4,224.77 EFTPOS", font=font, fill="black")
        pen.text((5, 45), "WgAVAWjy fi fl ffi 0123456789 ,.-$%", font=font, fill="black")
        engine = getattr(font, "layout_engine", "?")
        print(f"  text size={size:<3} engine={engine!s:24} pixels={_pixels(canvas)}")


def _corpus(root: Path) -> None:
    """Digest a corpus twice over: by decoded pixels, and by file bytes."""
    for images in sorted(root.rglob("images")):
        if not images.is_dir():
            continue
        files = sorted(p for p in images.iterdir() if p.suffix in {".png", ".jpg"})
        if not files:
            continue
        pixels = hashlib.sha256()
        raw = hashlib.sha256()
        for path in files:
            with Image.open(path) as handle:
                pixels.update(handle.convert("RGB").tobytes())
            raw.update(path.read_bytes())
        name = images.parent.resolve().name
        print(
            f"  {name:34} n={len(files):<5} pixels={pixels.hexdigest()[:16]} bytes={raw.hexdigest()[:16]}"
        )


def main() -> int:
    print("=== environment ===")
    _environment()
    print("\n=== synthetic renders (no corpus needed) ===")
    _synthetic()
    if len(sys.argv) > 1:
        print("\n=== corpus, pixels vs file bytes ===")
        _corpus(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
