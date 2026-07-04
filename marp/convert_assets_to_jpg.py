#!/usr/bin/env python3
"""Convert assets/*.png to .jpg where the image has no transparency.

JPG is meaningfully smaller than PNG for the flat cover/background photos
in assets/, and none of them use an alpha channel, so nothing is lost.
Run whenever a new PNG is added to assets/:

    python3 marp/convert_assets_to_jpg.py

Writes assets/<name>.jpg next to each convertible PNG (source PNGs are
left in place — update references and delete the PNGs by hand once
you've checked the JPGs look right).
"""
import sys
from pathlib import Path

from PIL import Image

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
JPEG_QUALITY = 90


def has_transparency(im: Image.Image) -> bool:
    if im.mode not in ("RGBA", "LA", "P"):
        return False
    im = im.convert("RGBA")
    return im.getextrema()[-1][0] < 255


def main():
    pngs = sorted(ASSETS_DIR.glob("*.png"))
    if not pngs:
        sys.exit(f"No PNGs found in {ASSETS_DIR}")
    for png in pngs:
        im = Image.open(png)
        if has_transparency(im):
            print(f"skip  {png.name} (uses transparency)")
            continue
        jpg = png.with_suffix(".jpg")
        im.convert("RGB").save(jpg, "JPEG", quality=JPEG_QUALITY)
        saved = png.stat().st_size - jpg.stat().st_size
        print(f"write {jpg.name} ({saved / 1024:+.0f} KiB vs PNG)")


if __name__ == "__main__":
    main()
