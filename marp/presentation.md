---
marp: true
theme: FUAS
paginate: true
html: true
---

<!--
Configuration
=============
- Every slide taken "directly from the PDF" shows a given page of
  presentation_base.pdf as a pre-rendered JPG via
  <img class="pdf-embed" src="pdf_pages/page-N.jpg" data-pdf-page="N">.
  This used to be a live <iframe> straight into the PDF, but mobile
  browsers (iOS Safari, Android Chrome) have no built-in PDF viewer plugin
  for iframes — they just showed a fallback "open presentation_base.pdf"
  link there instead of the page. Pre-rendered images work everywhere.
  This DOES mean the images go stale when presentation_base.pdf changes —
  run `python3 marp/render_pdf_pages.py` afterwards to regenerate every
  pdf_pages/page-N.jpg referenced by a data-pdf-page="N" attribute below.
- To choose which pages appear, just add/remove/reorder the slides below —
  each one is a single <img class="pdf-embed" ...data-pdf-page="N"> line,
  plus the slide-class comment above it. After adding/removing a page
  number, rerun render_pdf_pages.py so its JPG exists.
- presentation_base.pdf currently has 17 pages. The title slide always
  shows page 2 (title/authors/logos). Any slide that does NOT come
  directly from the PDF (e.g. the map slide) must use class "custom-slide"
  and show the PDF's LAST page (currently 17) as its background — update
  that page number here if pages are added/removed from the PDF.
- Map slides: give the <iframe class="map-embed"> a data-layer attribute
  to control which loop layer it opens on. This never touches loop_map.html
  itself (opened normally/directly it still starts on its own default
  layer) — the wiring script at the bottom of this file only reaches into
  the embedded iframe copy on this specific slide, via postMessage (works
  across the file:// origin boundary, unlike direct DOM access). Valid
  values (see LAYER_NAMES in loop_map.html):
    producer  -> Produzentschleifen
    consumer  -> Verbraucherschleifen
    custom_0  -> Landwirtschaft
    custom_1  -> Stift Tilbeck
    custom_2  -> Restaurants
    custom_3  -> Lebensmittelhandel
  Add more map slides the same way, just with a different data-layer.
-->

<!-- _class: title -->
<img class="pdf-embed" src="pdf_pages/page-2.jpg" data-pdf-page="2" alt="">

---

<!-- _class: pdf-page -->
<img class="pdf-embed" src="pdf_pages/page-3.jpg" data-pdf-page="3" alt="">

---

<!-- _class: custom-slide map-slide -->
<img class="pdf-embed" src="pdf_pages/page-17.jpg" data-pdf-page="17" alt="">

<div class="map-frame">
  <iframe class="map-embed" data-layer="custom_2" src="../loop_map.html"></iframe>
</div>

---

<!-- _class: pdf-page -->
<img class="pdf-embed" src="pdf_pages/page-4.jpg" data-pdf-page="4" alt="">

---

<!-- _class: pdf-page -->
<img class="pdf-embed" src="pdf_pages/page-5.jpg" data-pdf-page="5" alt="">

---

<!-- _class: pdf-page -->
<img class="pdf-embed" src="pdf_pages/page-6.jpg" data-pdf-page="6" alt="">

---

<!-- _class: pdf-page -->
<img class="pdf-embed" src="pdf_pages/page-7.jpg" data-pdf-page="7" alt="">

---

<!-- _class: pdf-page -->
<img class="pdf-embed" src="pdf_pages/page-8.jpg" data-pdf-page="8" alt="">

---

<!-- _class: pdf-page -->
<img class="pdf-embed" src="pdf_pages/page-9.jpg" data-pdf-page="9" alt="">

---

<!-- _class: pdf-page -->
<img class="pdf-embed" src="pdf_pages/page-10.jpg" data-pdf-page="10" alt="">

---

<!-- _class: pdf-page -->
<img class="pdf-embed" src="pdf_pages/page-11.jpg" data-pdf-page="11" alt="">

---

<!-- _class: pdf-page -->
<img class="pdf-embed" src="pdf_pages/page-12.jpg" data-pdf-page="12" alt="">

---

<!-- _class: pdf-page -->
<img class="pdf-embed" src="pdf_pages/page-13.jpg" data-pdf-page="13" alt="">

---

<!-- _class: pdf-page -->
<img class="pdf-embed" src="pdf_pages/page-14.jpg" data-pdf-page="14" alt="">

---

<!-- _class: pdf-page -->
<img class="pdf-embed" src="pdf_pages/page-15.jpg" data-pdf-page="15" alt="">

---

<!-- _class: pdf-page -->
<img class="pdf-embed" src="pdf_pages/page-16.jpg" data-pdf-page="16" alt="">

<script src="map_embed.js"></script>
