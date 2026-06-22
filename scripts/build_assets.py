"""Convert assets/*.svg → PNG and generate favicon variants.

Usage:
    uv run poe svg-to-png      # SVG → PNG only
    uv run poe favicon         # logo.png → favicon.ico + PNG sizes
    uv run poe build-assets    # both in sequence

Requires: cairosvg, Pillow (both in dev extras).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

ASSETS = Path(__file__).parent.parent / "assets"

# (source, dest, output_width, output_height) — None keeps natural SVG size
SVG_OUTPUTS: list[tuple[str, str, int | None, int | None]] = [
    ("logo.svg", "logo.png", 512, 512),
    ("hero.svg", "hero.png", None, None),
    ("social-preview.svg", "social-preview.png", None, None),
]

FAVICON_SIZES = [(16, 16), (32, 32), (48, 48), (64, 64)]
RESAMPLE = Image.Resampling.LANCZOS


def svg_to_png() -> None:
    import cairosvg  # noqa: PLC0415  # lazy: needs DYLD_LIBRARY_PATH set before import

    for svg_name, png_name, w, h in SVG_OUTPUTS:
        src = ASSETS / svg_name
        dst = ASSETS / png_name
        if w and h:
            cairosvg.svg2png(
                url=str(src), write_to=str(dst), output_width=w, output_height=h
            )
        else:
            cairosvg.svg2png(url=str(src), write_to=str(dst))
        print(f"  {svg_name} → {png_name}")
    print("✓ SVG → PNG")


def favicon() -> None:
    src = ASSETS / "logo.png"
    if not src.exists():
        raise FileNotFoundError(f"{src} not found — run `poe svg-to-png` first")

    img = Image.open(src).convert("RGBA")

    ico_path = ASSETS / "favicon.ico"
    img.save(ico_path, format="ICO", sizes=FAVICON_SIZES)
    print(f"  favicon.ico ({', '.join(f'{w}×{h}' for w, h in FAVICON_SIZES)})")

    for size_label, px in [("16x16", 16), ("32x32", 32)]:
        out = ASSETS / f"favicon-{size_label}.png"
        img.resize((px, px), RESAMPLE).save(out, format="PNG")
        print(f"  favicon-{size_label}.png")

    apple = ASSETS / "apple-touch-icon.png"
    img.resize((180, 180), RESAMPLE).save(apple, format="PNG")
    print("  apple-touch-icon.png")

    print("✓ favicon.ico + PNG variants")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build PNG and favicon assets from SVG sources"
    )
    parser.add_argument("cmd", choices=["svg-to-png", "favicon", "all"])
    args = parser.parse_args()

    if args.cmd in ("svg-to-png", "all"):
        svg_to_png()
    if args.cmd in ("favicon", "all"):
        favicon()


if __name__ == "__main__":
    main()
