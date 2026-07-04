(function () {
  // Bespoke.js (Marp's slide engine) hides inactive slides by setting
  // content-visibility: hidden on each slide's wrapping
  // <svg class="bespoke-marp-slide"> — an ANCESTOR of every <section>, so
  // this can't be expressed as a Marp theme/markdown <style> rule (Marp
  // scopes every such selector as a descendant of section, and a <style>
  // placed outside slide content is still scoped the same way). It has to
  // be forced via JS on the actual DOM nodes instead.
  //
  // On https (GitHub Pages) the PDF viewer inside iframe.pdf-embed runs as
  // an out-of-process iframe, and Chromium fails to repaint that OOPIF's
  // compositor surface when content-visibility flips from hidden back to
  // visible — so any pdf-embed slide that wasn't the very first one shown
  // renders only its background, never the PDF page, until reloaded from
  // scratch. On file:// there is no site isolation for the PDF plugin, so
  // the bug never appears locally.
  //
  // Forcing content-visibility: visible (as an !important inline style,
  // so it outranks the stylesheet's !important rule) avoids the broken
  // repaint path entirely. Slides still hide the same way via the
  // opacity/pointer-events/z-index bespoke.js sets alongside
  // content-visibility, so this has no visible effect other than fixing
  // the bug.
  document.querySelectorAll("svg.bespoke-marp-slide").forEach(function (svg) {
    svg.style.setProperty("content-visibility", "visible", "important");
  });
})();
