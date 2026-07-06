---
marp: true
theme: FUAS
paginate: true
html: true
footer: '07.06.2026 - Frankfurt University of Applied Sciences - Radlogistik'
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
  pdf_pages/page-N.jpg (it renders every page of the PDF, not just the
  ones used below).
- To choose which pages appear, just add/remove/reorder the slides below —
  each one is a single <img class="pdf-embed" ...data-pdf-page="N"> line,
  plus the slide-class comment above it. The data-pdf-page attribute must
  match the real page number of the PDF, which is also the filename
  (pdf_pages/page-N.jpg is always PDF page N).
- presentation_base.pdf currently has 31 pages. The title slide always
  shows page 3 (title/authors/logos). Any slide that does NOT come
  directly from the PDF (e.g. the map slide) must use class "custom-slide"
  and show the PDF's LAST page (currently 31) as its background — update
  that page number here if pages are added/removed from the PDF.
- The footer text in the front matter above (and the page number next to
  it, bottom-left on every slide) is set once per presentation — edit the
  `footer:` value before presenting. Both are suppressed on the title
  slide via local "_footer" and "_paginate" directive comments — always
  use the underscore-prefixed local form there, since a non-prefixed
  directive changes the value for every following slide too, not just
  that one.
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
<!-- _footer: "" -->
<!-- _paginate: false -->
<img class="pdf-embed" src="pdf_pages/page-3.jpg" data-pdf-page="3" alt="">

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

<!-- _class: custom-slide map-slide -->
<img class="pdf-embed" src="pdf_pages/page-16.jpg" data-pdf-page="31" alt="">

<div class="map-frame">
  <iframe class="map-embed" data-layer="custom_2" src="../poi_map.html"></iframe>
</div>

---

<!-- _class: pdf-page -->
<img class="pdf-embed" src="pdf_pages/page-17.jpg" data-pdf-page="17" alt="">

---

<!-- _class: pdf-page -->
<img class="pdf-embed" src="pdf_pages/page-18.jpg" data-pdf-page="18" alt="">

---

<!-- _class: pdf-page -->
<img class="pdf-embed" src="pdf_pages/page-19.jpg" data-pdf-page="19" alt="">

---

<!-- _class: pdf-page -->
<img class="pdf-embed" src="pdf_pages/page-20.jpg" data-pdf-page="20" alt="">

---

<!-- _class: pdf-page -->
<img class="pdf-embed" src="pdf_pages/page-21.jpg" data-pdf-page="21" alt="">

---

<!-- _class: custom-slide map-slide -->
<img class="pdf-embed" src="pdf_pages/page-22.jpg" data-pdf-page="31" alt="">

<div class="map-frame">
  <iframe class="map-embed" data-layer="custom_2" src="../loop_map.html"></iframe>
</div>

---


<!-- _class: pdf-page -->
<img class="pdf-embed" src="pdf_pages/page-23.jpg" data-pdf-page="23" alt="">

---

<!-- _class: pdf-page -->
<img class="pdf-embed" src="pdf_pages/page-24.jpg" data-pdf-page="24" alt="">

---

<!-- _class: pdf-page -->
<img class="pdf-embed" src="pdf_pages/page-25.jpg" data-pdf-page="25" alt="">

---

<!-- _class: pdf-page -->
<img class="pdf-embed" src="pdf_pages/page-26.jpg" data-pdf-page="26" alt="">

---

<!-- _class: pdf-page -->
<img class="pdf-embed" src="pdf_pages/page-27.jpg" data-pdf-page="27" alt="">

---

<!-- _class: pdf-page -->
<img class="pdf-embed" src="pdf_pages/page-28.jpg" data-pdf-page="28" alt="">

---

<!-- _class: pdf-page -->
<img class="pdf-embed" src="pdf_pages/page-29.jpg" data-pdf-page="29" alt="">

---

<!-- _class: pdf-page -->
<img class="pdf-embed" src="pdf_pages/page-30.jpg" data-pdf-page="30" alt="">

<script src="map_embed.js"></script>
