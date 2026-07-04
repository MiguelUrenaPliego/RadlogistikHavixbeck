# Previewing the presentation locally

Do not open `presentation.html` via a `file://` URL to test PDF-embed slides —
Chromium's PDF viewer behaves differently under `file://` than it does on
GitHub Pages (`https://`), so bugs that only show up over https (e.g. PDF
pages not repainting when navigating between slides) won't reproduce
locally that way.

Instead, serve the repo over local HTTP:

```bash
cd /home/miguel/Documents/UNI/Master/2/Radlogistik
python3 -m http.server 8791
```

Then open, in a private/incognito window (to avoid stale cache):

```
http://localhost:8791/marp/presentation.html
```

Navigate through several slides (not just the one you land on) to check
that every PDF-embed slide renders its content, not just the background.

Stop the server with Ctrl+C when done.
