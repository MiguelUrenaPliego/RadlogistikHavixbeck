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
