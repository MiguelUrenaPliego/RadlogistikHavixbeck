#!/usr/bin/env python3
"""Render every page of presentation_base.pdf to JPG images for use in presentation.md.

Mobile browsers (iOS Safari, Android Chrome) don't have a built-in PDF
viewer plugin for <iframe> embeds, so the live-PDF-in-an-iframe approach
used on desktop just shows a fallback "open presentation_base.pdf" link
there. Pre-rendering every page as a JPG and embedding those with <img>
instead works on every device.

Run this whenever presentation_base.pdf changes:

    python3 marp/render_pdf_pages.py

It (re)renders every page of presentation_base.pdf into
marp/pdf_pages/page-N.jpg, where N is the PDF's real page number (matching
the data-pdf-page="N" attribute used in presentation.md). Requires
poppler-utils (`pdftoppm` and `pdfinfo`) to be installed
(apt install poppler-utils / brew install poppler).
"""
import re
import subprocess
import sys
from pathlib import Path

MARP_DIR = Path(__file__).resolve().parent
PDF_PATH = MARP_DIR / "presentation_base.pdf"
OUT_DIR = MARP_DIR / "pdf_pages"
DPI = 150
JPEG_QUALITY = 85


def page_count() -> int:
    info = subprocess.run(
        ["pdfinfo", str(PDF_PATH)], check=True, capture_output=True, text=True
    ).stdout
    match = re.search(r"^Pages:\s*(\d+)", info, re.MULTILINE)
    if not match:
        sys.exit("Could not determine page count from pdfinfo output")
    return int(match.group(1))


def render_page(page: int) -> None:
    out_prefix = OUT_DIR / f"page-{page}"
    subprocess.run(
        [
            "pdftoppm",
            "-jpeg",
            "-jpegopt", f"quality={JPEG_QUALITY}",
            "-r", str(DPI),
            "-f", str(page),
            "-l", str(page),
            str(PDF_PATH),
            str(out_prefix),
        ],
        check=True,
    )
    # pdftoppm appends "-<page>" to the prefix, zero-padded to the digit
    # width of the source PDF's page count (e.g. "-02" for a 17-page PDF)
    # — glob for it instead of guessing the padding, then drop the suffix.
    [rendered] = OUT_DIR.glob(f"{out_prefix.name}-*.jpg")
    target = out_prefix.with_suffix(".jpg")
    rendered.rename(target)
    print(f"  page {page} -> {target.relative_to(MARP_DIR)}")


def main():
    if not PDF_PATH.exists():
        sys.exit(f"Not found: {PDF_PATH}")
    OUT_DIR.mkdir(exist_ok=True)
    total = page_count()
    print(f"Rendering all {total} page(s) from {PDF_PATH.name} at {DPI} DPI...")
    for page in range(1, total + 1):
        render_page(page)
    print("Done.")


if __name__ == "__main__":
    main()
