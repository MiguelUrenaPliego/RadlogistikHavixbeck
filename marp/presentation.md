---
marp: true
theme: FUAS
paginate: true
html: true
---

<!--
Configuration
=============
- Every slide taken "directly from the PDF" embeds presentation_base.pdf
  live at a given page via <iframe class="pdf-embed" src="presentation_base.pdf#page=N...">
  — there is no image conversion step, so editing presentation_base.pdf is
  immediately reflected the next time this presentation is opened. Nothing
  in this file needs to change when the PDF's content changes, only when
  the PAGE COUNT or the set of pages you want to show changes.
- To choose which pages appear, just add/remove/reorder the slides below —
  each one is a single <iframe class="pdf-embed" ...#page=N> line, plus the
  slide-class comment above it.
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
<iframe class="pdf-embed" src="presentation_base.pdf#page=2&toolbar=0&navpanes=0&scrollbar=0&view=Fit"></iframe>

---

<!-- _class: pdf-page -->
<iframe class="pdf-embed" src="presentation_base.pdf#page=3&toolbar=0&navpanes=0&scrollbar=0&view=Fit"></iframe>

---

<!-- _class: custom-slide map-slide -->
<iframe class="pdf-embed" src="presentation_base.pdf#page=17&toolbar=0&navpanes=0&scrollbar=0&view=Fit"></iframe>

<div class="map-frame">
  <iframe class="map-embed" data-layer="custom_2" src="../loop_map.html"></iframe>
</div>

---

<!-- _class: pdf-page -->
<iframe class="pdf-embed" src="presentation_base.pdf#page=4&toolbar=0&navpanes=0&scrollbar=0&view=Fit"></iframe>

---

<!-- _class: pdf-page -->
<iframe class="pdf-embed" src="presentation_base.pdf#page=5&toolbar=0&navpanes=0&scrollbar=0&view=Fit"></iframe>

---

<!-- _class: pdf-page -->
<iframe class="pdf-embed" src="presentation_base.pdf#page=6&toolbar=0&navpanes=0&scrollbar=0&view=Fit"></iframe>

---

<!-- _class: pdf-page -->
<iframe class="pdf-embed" src="presentation_base.pdf#page=7&toolbar=0&navpanes=0&scrollbar=0&view=Fit"></iframe>

---

<!-- _class: pdf-page -->
<iframe class="pdf-embed" src="presentation_base.pdf#page=8&toolbar=0&navpanes=0&scrollbar=0&view=Fit"></iframe>

---

<!-- _class: pdf-page -->
<iframe class="pdf-embed" src="presentation_base.pdf#page=9&toolbar=0&navpanes=0&scrollbar=0&view=Fit"></iframe>

---

<!-- _class: pdf-page -->
<iframe class="pdf-embed" src="presentation_base.pdf#page=10&toolbar=0&navpanes=0&scrollbar=0&view=Fit"></iframe>

---

<!-- _class: pdf-page -->
<iframe class="pdf-embed" src="presentation_base.pdf#page=11&toolbar=0&navpanes=0&scrollbar=0&view=Fit"></iframe>

---

<!-- _class: pdf-page -->
<iframe class="pdf-embed" src="presentation_base.pdf#page=12&toolbar=0&navpanes=0&scrollbar=0&view=Fit"></iframe>

---

<!-- _class: pdf-page -->
<iframe class="pdf-embed" src="presentation_base.pdf#page=13&toolbar=0&navpanes=0&scrollbar=0&view=Fit"></iframe>

---

<!-- _class: pdf-page -->
<iframe class="pdf-embed" src="presentation_base.pdf#page=14&toolbar=0&navpanes=0&scrollbar=0&view=Fit"></iframe>

---

<!-- _class: pdf-page -->
<iframe class="pdf-embed" src="presentation_base.pdf#page=15&toolbar=0&navpanes=0&scrollbar=0&view=Fit"></iframe>

---

<!-- _class: pdf-page -->
<iframe class="pdf-embed" src="presentation_base.pdf#page=16&toolbar=0&navpanes=0&scrollbar=0&view=Fit"></iframe>

<script>
(function () {
  // The map lives at a different file:// path, so it has an opaque "null"
  // origin — reaching into its contentDocument is blocked by the browser.
  // postMessage still works across that boundary, and loop_map.html only
  // reacts to it when embedded like this; opened directly it behaves
  // exactly as before (see the "message" listener in route_map_scripts.js).
  function wireMapEmbed(iframe) {
    function send() {
      var layer = iframe.dataset.layer;
      if (layer) iframe.contentWindow.postMessage({ type: "setLayer", layer: layer }, "*");
      iframe.contentWindow.postMessage({ type: "closeInstructions" }, "*");
    }
    // Repeat for a couple of seconds: the map needs time to initialise
    // (Leaflet + dropdown population) after the iframe's load event fires.
    iframe.addEventListener("load", function () {
      var tries = 0;
      var timer = setInterval(function () {
        send();
        tries += 1;
        if (tries > 10) clearInterval(timer);
      }, 300);
    });
  }
  document.querySelectorAll("iframe.map-embed").forEach(wireMapEmbed);
})();
</script>
