# Previewing the presentation locally

Serve the repo over local HTTP rather than opening `presentation.html` via
a `file://` URL — some browser behavior (e.g. iframe handling) differs
between `file://` and the `https://` GitHub Pages serves in production, so
testing over HTTP is closer to what visitors actually see:

```bash
cd /home/miguel/Documents/UNI/Master/2/Radlogistik
python3 -m http.server 8791
```

Then open, in a private/incognito window (to avoid stale cache):

```
http://localhost:8791/marp/presentation.html
```

Navigate through several slides (not just the one you land on) and check
on both desktop and mobile if possible. Stop the server with Ctrl+C when
done.

# Regenerating PDF-page images

Slides that show a page "directly from the PDF" use a pre-rendered JPG
(`pdf_pages/page-N.jpg`), not a live embed of `presentation_base.pdf` —
mobile browsers (iOS Safari, Android Chrome) have no built-in PDF viewer
plugin for iframes, so a live embed just showed a fallback "open
presentation_base.pdf" link there instead of the page.

This means the images go stale whenever `presentation_base.pdf` changes.
After editing the PDF (or adding/removing/reordering which pages appear —
see the config comment at the top of `presentation.md`), regenerate them:

```bash
python3 marp/render_pdf_pages.py
```

It re-renders `pdf_pages/page-N.jpg` for every page of
`presentation_base.pdf` (not just the ones currently used in
`presentation.md`), so `pdf_pages/page-N.jpg` is always PDF page N.
Requires `pdftoppm`/`pdfinfo` (poppler-utils): `apt install poppler-utils`
/ `brew install poppler`.

Then rebuild `presentation.html` from `presentation.md` (via VS Code's
Marp "Export as HTML", or `npx @marp-team/marp-cli presentation.md -o
presentation.html --allow-local-files --html --theme-set theme.css`).

# Converting new cover images to JPG

`assets/*.png` covers with no transparency are kept as JPG instead (JPG
compresses photo-like images much better than PNG; flat/text screenshots
can go the other way, so check before committing). After adding a new PNG
to `assets/`:

```bash
python3 marp/convert_assets_to_jpg.py
```

It writes a `.jpg` next to any PNG with no alpha channel and prints the
size delta. Only keep the JPG (and update references, then delete the
PNG) if it actually saved space.
