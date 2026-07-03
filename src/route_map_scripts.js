/* route_map_scripts.js
 * ============================================================
 * Single rendering pipeline for producer / consumer / custom loops.
 * Every loop is a list of "legs": {poi_id, load_products, unload_products,
 * mandatory}. Excluding a product just drops the non-mandatory stops that
 * only carry that product and re-stitches the loop using the direct
 * POI->POI connection already present in MAP_DATA.PAIRS (no solver here —
 * Python already pre-computed literally every POI x POI pair).
 * ============================================================ */
(function () {
  "use strict";

  var D = window.MAP_DATA || {};
  var GOODS = D.GOODS || {};
  var POIS = D.POIS || {};
  var PAIRS = D.PAIRS || {};
  var LAYERS = D.LAYERS || {};
  var LAYER_NAMES = D.LAYER_NAMES || {};
  var LAYER_ORDER = D.LAYER_ORDER || [];
  // POI-only maps (poi_map(), no loops) group POIs into named layers instead
  // (e.g. "all" vs "selection") — LAYER_ORDER stays empty for these.
  var POI_LAYERS = D.POI_LAYERS || {};
  var POI_LAYER_NAMES = D.POI_LAYER_NAMES || {};
  var POI_LAYER_ORDER = D.POI_LAYER_ORDER || [];
  var PRODUCT_IDS = D.PRODUCT_IDS || [];
  var TRANSLATIONS = D.TRANSLATIONS || {};
  var CUSTOM_LAYER_KEYS = new Set(D.CUSTOM_LAYER_KEYS || []);

  // ── mutable state ────────────────────────────────────────────────────
  var state = {
    lang: D.DEFAULT_LANG || "de",
    layerKey: LAYER_ORDER[0] || "producer",
    poiLayerKey: POI_LAYER_ORDER[0] || null,
    vehicle: "ebike",          // "ebike" | "car" — last one toggled wins when both checked
    showEbike: true,
    showCar: false,
    activeProducts: new Set(PRODUCT_IDS.map(String)),
    // highlight-1 (click isolation): null, or {loopIds:Set, poiIds:Set}
    isolation: null,
    // currently hovered (highlight-2) leg/poi info, used to know what to clear
    hoveredKey: null,
  };

  /** POI ids belonging to the currently active POI layer (poi_map mode
   * only) — falls back to every POI when no POI_LAYERS were exported
   * (regular loop maps). */
  function poisForActivePoiLayer() {
    if (POI_LAYER_ORDER.length > 0) {
      return POI_LAYERS[state.poiLayerKey] || [];
    }
    return Object.keys(POIS);
  }

  function t(key) {
    var dict = TRANSLATIONS[state.lang] || TRANSLATIONS.de || {};
    return dict[key] != null ? dict[key] : key;
  }

  // ── leaflet map handle ───────────────────────────────────────────────
  function getLeafletMap() {
    var found = null;
    Object.keys(window).forEach(function (k) {
      if (k.indexOf("map_") === 0 && window[k] && typeof window[k].addLayer === "function") {
        found = window[k];
      }
    });
    return found;
  }

  function pairKey(a, b) {
    return a + "_" + b;
  }

  function pairMetrics(a, b, vehicle) {
    var fwd = PAIRS[pairKey(a, b)];
    if (fwd && fwd[vehicle]) return fwd[vehicle];
    var rev = PAIRS[pairKey(b, a)];
    if (rev && rev[vehicle]) return rev[vehicle];
    return null;
  }

  function pairExists(a, b, vehicle) {
    var fwd = PAIRS[pairKey(a, b)];
    if (fwd && fwd[vehicle] && fwd[vehicle].c && fwd[vehicle].c.length >= 2) return true;
    var rev = PAIRS[pairKey(b, a)];
    if (rev && rev[vehicle] && rev[vehicle].c && rev[vehicle].c.length >= 2) return true;
    return false;
  }

  // ════════════════════════════════════════════════════════════════════
  // LEG RESOLUTION — drop excluded stops, re-stitch via direct pair lookup
  // ════════════════════════════════════════════════════════════════════
  function legIsDroppable(leg) {
    if (leg.mandatory) return false;
    var goods = (leg.load_products || []).concat(leg.unload_products || []);
    if (goods.length === 0) return false; // nothing to filter on -> never drop
    return goods.every(function (gid) { return !state.activeProducts.has(String(gid)); });
  }

  /** A leg counts as "active" for edge-trimming purposes if it's
   * user-marked mandatory, OR it has at least one load/unload product that
   * is currently active. A leg with literally no products at all (e.g. a
   * closing "nothing happens here" return leg) is NOT active and CAN be
   * trimmed from the edges once nothing keeps it anchored. */
  function legIsActiveForTrim(leg) {
    if (leg.mandatory) return true;
    var goods = (leg.load_products || []).concat(leg.unload_products || []);
    return goods.some(function (gid) { return state.activeProducts.has(String(gid)); });
  }

  /** Trim non-mandatory, inactive stops off the FRONT and BACK of a leg
   * list, keeping whatever contiguous run remains in between (interior
   * drop-and-restitch still applies to that remaining run separately).
   * User-marked mandatory stops are hard anchors and are never trimmed
   * even if nothing active flows through them. */
  function trimLoopLegs(legs) {
    var start = 0;
    var end = legs.length - 1;
    while (start <= end && !legIsActiveForTrim(legs[start])) start++;
    while (end >= start && !legIsActiveForTrim(legs[end])) end--;
    if (start > end) return [];
    return legs.slice(start, end + 1);
  }

  /**
   * Resolve a loop's legs (for one vehicle) into the EFFECTIVE list of stops
   * after (1) trimming inactive stops off both edges, (2) dropping excluded
   * interior stops and re-stitching the path through direct connections
   * looked up in PAIRS. Returns:
   *   { stops: [poi_id...], segments: [{from, to, dropped:[poi_ids between]}],
   *     valid: bool }
   * `valid` requires the loop to actually carry at least one active
   * product somewhere — a loop with nothing active flowing through it is
   * hidden entirely, regardless of any mandatory flags.
   */
  function resolveLoopLegs(loop, vehicle) {
    var rawLegs = vehicle === "car" ? loop.car_legs : loop.ebike_legs;
    if (!rawLegs || rawLegs.length < 2) return { stops: [], segments: [], valid: false };

    var legs = trimLoopLegs(rawLegs);
    if (legs.length < 2) return { stops: [], segments: [], valid: false };

    var kept = [];
    var droppedSinceLastKept = [];
    var segments = [];

    for (var i = 0; i < legs.length; i++) {
      var leg = legs[i];
      if (legIsDroppable(leg)) {
        droppedSinceLastKept.push(leg.poi_id);
        continue;
      }
      kept.push(leg);
      if (kept.length > 1) {
        segments.push({
          from: kept[kept.length - 2].poi_id,
          to: leg.poi_id,
          dropped: droppedSinceLastKept.slice(),
        });
      }
      droppedSinceLastKept = [];
    }

    // Second pass: also drop non-mandatory stops with no route in PAIRS
    // (e.g. farms outside the street-graph AOI). Iterate until stable so
    // that dropping one stop can expose the next as un-routable too.
    // Mandatory stops failing still kill the whole loop (handled by allRouteable below).
    var moreDrops = true;
    while (moreDrops) {
      moreDrops = false;
      var newKept = [kept[0]];
      var newSegs = [];
      var dropBuf = [];
      for (var k = 1; k < kept.length; k++) {
        var kl = kept[k];
        var prev = newKept[newKept.length - 1];
        if (!kl.mandatory && !pairExists(prev.poi_id, kl.poi_id, vehicle)) {
          dropBuf.push(kl.poi_id);
          moreDrops = true;
        } else {
          newKept.push(kl);
          if (newKept.length > 1) {
            newSegs.push({ from: prev.poi_id, to: kl.poi_id, dropped: dropBuf.slice() });
          }
          dropBuf = [];
        }
      }
      kept = newKept;
      segments = newSegs;
    }

    var hasActiveProduct = kept.some(function (l) {
      var goods = (l.load_products || []).concat(l.unload_products || []);
      return goods.some(function (gid) { return state.activeProducts.has(String(gid)); });
    });
    // A loop is only valid if every resolved segment has a drawable route in
    // PAIRS. Missing geometry means the pair was never routed or the route
    // failed — the loop can't be traveled and must not highlight those POIs.
    var allRouteable = segments.every(function (seg) {
      return pairExists(seg.from, seg.to, vehicle);
    });
    var valid = kept.length >= 2 && hasActiveProduct && allRouteable;
    return { stops: kept.map(function (l) { return l.poi_id; }), segments: segments, legs: kept, valid: valid };
  }

  /** Accumulate the products actually flowing on the EFFECTIVE (post-filter)
   * loop, per segment, so a dropped stop's goods correctly disappear and a
   * re-stitched segment still shows whatever passes through it. */
  function effectiveLegGoods(loop, vehicle, resolved) {
    // Walk the original legs to know, at each KEPT stop, what's actually
    // on the vehicle (accumulated loads minus unloads) restricted to active
    // products. This keeps "accumulate along the loop" semantics correct
    // even after stops are dropped. Uses the same edge-trimmed leg list as
    // resolveLoopLegs so segment indices line up correctly.
    var rawLegs = vehicle === "car" ? loop.car_legs : loop.ebike_legs;
    var legs = trimLoopLegs(rawLegs || []);
    var carried = [];
    var perKeptIndex = []; // goods actively carried on the segment LEAVING each kept stop
    for (var i = 0; i < legs.length; i++) {
      var leg = legs[i];
      var isKept = !legIsDroppable(leg);
      (leg.load_products || []).forEach(function (gid) {
        if (state.activeProducts.has(String(gid)) && carried.indexOf(gid) === -1) carried.push(gid);
      });
      (leg.unload_products || []).forEach(function (gid) {
        var idx = carried.indexOf(gid);
        if (idx !== -1) carried.splice(idx, 1);
      });
      if (isKept) {
        perKeptIndex.push(carried.slice());
      }
    }
    return perKeptIndex; // perKeptIndex[k] = goods carried on segment leaving kept-stop k
  }

  // ════════════════════════════════════════════════════════════════════
  // LOOP HELPERS
  // ════════════════════════════════════════════════════════════════════
  function loopsForActiveLayer() {
    return LAYERS[state.layerKey] || [];
  }

  function loopById(loopId) {
    for (var key in LAYERS) {
      var arr = LAYERS[key];
      for (var i = 0; i < arr.length; i++) {
        if (arr[i].id === loopId) return arr[i];
      }
    }
    return null;
  }

  // ════════════════════════════════════════════════════════════════════
  // STARS
  // ════════════════════════════════════════════════════════════════════
  var _starIdCounter = 0;
  var STAR_PATH_D = "M7 0.5 L9.05 4.66 L13.65 5.33 L10.33 8.57 L11.11 13.15 L7 11 L2.89 13.15 L3.67 8.57 L0.35 5.33 L4.95 4.66 Z";

  function starsHtml(score0to10) {
    var score5 = Math.max(0, Math.min(5, (score0to10 || 0) / 2));
    var html = '<span class="stars-wrap">';
    for (var i = 0; i < 5; i++) {
      var fillFrac = Math.max(0, Math.min(1, score5 - i));
      var fillWidth = (fillFrac * 14).toFixed(2);
      var clipId = "starclip" + (_starIdCounter++);
      html +=
        '<span class="star-slot">' +
        '<svg viewBox="0 0 14 14"><path class="star-bg" d="' + STAR_PATH_D + '"/></svg>' +
        '<svg viewBox="0 0 14 14">' +
        '<clipPath id="' + clipId + '"><rect x="0" y="0" width="' + fillWidth + '" height="14"/></clipPath>' +
        '<path class="star-fg" d="' + STAR_PATH_D + '" clip-path="url(#' + clipId + ')"/>' +
        "</svg></span>";
    }
    html += "</span>";
    return html;
  }

  // ════════════════════════════════════════════════════════════════════
  // GEOMETRY / LEAFLET HELPERS — polylines are drawn lazily, on demand,
  // straight from the coordinates shipped in MAP_DATA.PAIRS[key][vehicle].c
  // (NOT pre-rendered by folium — that produced one DOM element per pair
  // per vehicle per fill/border, which bloated the saved HTML to ~80MB).
  // ════════════════════════════════════════════════════════════════════
  var _pairLayers = {}; // "a_b_vehicle" -> {fill: L.Polyline, border: L.Polyline, latlngs: L.LatLng[]}
  var _legLayerGroup = null;

  function ensureLegLayerGroup() {
    var lmap = getLeafletMap();
    if (!lmap) return null;
    if (!_legLayerGroup) _legLayerGroup = L.layerGroup().addTo(lmap);
    return _legLayerGroup;
  }

  function getPairLayers(a, b, vehicle) {
    var fwdKey = a + "_" + b + "_" + vehicle;
    if (_pairLayers[fwdKey]) return { layers: _pairLayers[fwdKey], reversed: false };
    var revKey = b + "_" + a + "_" + vehicle;
    if (_pairLayers[revKey]) return { layers: _pairLayers[revKey], reversed: true };

    // Not drawn yet -- look up coordinates and draw now.
    var group = ensureLegLayerGroup();
    if (!group) return null;
    var fwdData = PAIRS[pairKey(a, b)];
    var reversed = false;
    var coordData = fwdData && fwdData[vehicle];
    if (!coordData || !coordData.c || coordData.c.length < 2) {
      var revData = PAIRS[pairKey(b, a)];
      coordData = revData && revData[vehicle];
      reversed = true;
    }
    if (!coordData || !coordData.c || coordData.c.length < 2) return null;

    var latlngs = coordData.c.map(function (p) { return L.latLng(p[0], p[1]); });
    var fillColor = vehicle === "ebike" ? "#3a9d3a" : "#c0392b";
    var border = L.polyline(latlngs, {
      color: "#000000", weight: 0, opacity: 0, className: "leg-border leg-" + vehicle,
      interactive: true, pane: "overlayPane",
    }).addTo(group);
    var fill = L.polyline(latlngs, {
      color: fillColor, weight: 0, opacity: 0, className: "leg-fill leg-" + vehicle,
      interactive: true, pane: "overlayPane",
    }).addTo(group);

    var entry = { fill: fill, border: border, latlngs: latlngs };
    var key = reversed ? (b + "_" + a + "_" + vehicle) : fwdKey;
    _pairLayers[key] = entry;

    [fill, border].forEach(function (layer) {
      layer.on("add", function () {
        var el = layer.getElement();
        if (el) {
          el.setAttribute("data-pair-from", reversed ? b : a);
          el.setAttribute("data-pair-to", reversed ? a : b);
          el.setAttribute("data-vehicle", vehicle);
        }
      });
      // Trigger 'add' handler immediately since the layer is already added.
      var el0 = layer.getElement();
      if (el0) {
        el0.setAttribute("data-pair-from", reversed ? b : a);
        el0.setAttribute("data-pair-to", reversed ? a : b);
        el0.setAttribute("data-vehicle", vehicle);
      }
    });

    return { layers: entry, reversed: reversed };
  }

  function flattenLatLngs(latlngs) {
    var flat = [];
    if (!Array.isArray(latlngs)) return flat;
    for (var i = 0; i < latlngs.length; i++) {
      var item = latlngs[i];
      if (Array.isArray(item)) flat = flat.concat(flattenLatLngs(item));
      else if (item && item.lat != null) flat.push(item);
    }
    return flat;
  }

  function polylineLength(pts) {
    var d = 0;
    for (var i = 0; i < pts.length - 1; i++) d += pts[i].distanceTo(pts[i + 1]);
    return d;
  }

  function pointAtDistance(pts, target) {
    if (pts.length === 0) return null;
    if (pts.length === 1) return { latlng: pts[0], angle: 0 };
    var accum = 0;
    for (var i = 0; i < pts.length - 1; i++) {
      var p1 = pts[i], p2 = pts[i + 1];
      var d = p1.distanceTo(p2);
      if (accum + d >= target) {
        var ratio = d === 0 ? 0 : (target - accum) / d;
        var lat = p1.lat + ratio * (p2.lat - p1.lat);
        var lng = p1.lng + ratio * (p2.lng - p1.lng);
        var dy = p2.lat - p1.lat, dx = p2.lng - p1.lng;
        var angle = (Math.atan2(dx, dy) * 180 / Math.PI + 360) % 360;
        return { latlng: L.latLng(lat, lng), angle: angle };
      }
      accum += d;
    }
    var lastA = pts[pts.length - 2], lastB = pts[pts.length - 1];
    var dy2 = lastB.lat - lastA.lat, dx2 = lastB.lng - lastA.lng;
    return { latlng: lastB, angle: (Math.atan2(dx2, dy2) * 180 / Math.PI + 360) % 360 };
  }

  // ════════════════════════════════════════════════════════════════════
  // POI MARKER ICON COMPOSITION
  // For standard layers: consumed-icons → poi-icon → produced-icons
  // For custom layers:   icons inferred from load/unload products in loop legs.
  // When consumed set == produced set: poi-icon ↔ product-icons (no duplication).
  // ════════════════════════════════════════════════════════════════════
  function _poiGoodsFromLegs() {
    // Returns {pid: {loaded: Set, unloaded: Set}} for the current active layer's loops.
    var result = {};
    var loops = loopsForActiveLayer();
    loops.forEach(function (loop) {
      var legs = (state.vehicle === "car" ? loop.car_legs : loop.ebike_legs) || loop.car_legs || [];
      legs.forEach(function (leg) {
        var pid = String(leg.poi_id);
        if (!result[pid]) result[pid] = { loaded: new Set(), unloaded: new Set() };
        (leg.load_products || []).forEach(function (g) { result[pid].loaded.add(String(g)); });
        (leg.unload_products || []).forEach(function (g) { result[pid].unloaded.add(String(g)); });
      });
    });
    return result;
  }

  function renderPoiMarkers() {
    // Derive displayed goods from actual loop legs so only products genuinely
    // loaded/unloaded at each POI in the current layer are shown.
    // In poi_map mode (no loops) fall back to the POI's static columns.
    var legGoods = LAYER_ORDER.length > 0 ? _poiGoodsFromLegs() : null;

    Object.keys(POIS).forEach(function (pid) {
      var el = document.querySelector('.poi[data-poi-id="' + pid + '"]');
      if (!el) return;
      var poi = POIS[pid];
      var inner = el.querySelector(".poi-inner");
      if (!inner) return;

      var activeProduced, activeConsumed;
      if (legGoods && legGoods[pid]) {
        // loaded at POI = this POI supplies/produces for the loop
        // unloaded at POI = this POI receives/consumes from the loop
        activeProduced = Array.from(legGoods[pid].loaded)
          .filter(function (gid) { return state.activeProducts.has(gid); });
        activeConsumed = Array.from(legGoods[pid].unloaded)
          .filter(function (gid) { return state.activeProducts.has(gid); });
      } else if (legGoods) {
        // POI is visible (mandatory/etc.) but has no leg in the active layer
        activeProduced = [];
        activeConsumed = [];
      } else {
        // poi_map mode: no loops, use static produced/consumed columns
        activeProduced = (poi.produced || []).map(String)
          .filter(function (gid) { return state.activeProducts.has(gid); });
        activeConsumed = (poi.consumed || []).map(String)
          .filter(function (gid) { return state.activeProducts.has(gid); });
      }

      var baseIcon = '<span class="poi-base-icon">' + (poi.icon || "🏠") + "</span>";

      // Supermarkets (basket_both): both received and dispatched goods are
      // frequent and varied enough that they always collapse to a single
      // 🧺 with a double arrow, regardless of whether the sets match.
      if (poi.basket_both && (activeProduced.length > 0 || activeConsumed.length > 0)) {
        inner.innerHTML = baseIcon +
          '<span class="poi-arrow poi-arrow-bi">↔</span>' +
          '<span class="poi-prod-group poi-both">🧺</span>';
        return;
      }

      // Check if produced and consumed are the exact same set → ↔ mode
      var producedSet = new Set(activeProduced);
      var consumedSet = new Set(activeConsumed);
      var allGoods = Array.from(new Set(activeProduced.concat(activeConsumed)));
      var sameGoods = allGoods.length > 0 &&
        allGoods.every(function (g) { return producedSet.has(g) && consumedSet.has(g); });

      if (sameGoods) {
        var goodIcons = allGoods.map(function (gid) { return (GOODS[gid] || {}).icon || ""; }).join("");
        inner.innerHTML = baseIcon +
          '<span class="poi-arrow poi-arrow-bi">↔</span>' +
          '<span class="poi-prod-group poi-both">' + goodIcons + "</span>";
        return;
      }

      // Standard layout: consumed → poi → produced
      // Basket POIs (e.g. Gastronomie) collapse all consumed/produced goods
      // to a single basket icon for legibility.
      var consumedIcons = (poi.basket && activeConsumed.length > 0)
        ? "🧺"
        : activeConsumed.map(function (gid) { return (GOODS[gid] || {}).icon || ""; }).join("");
      var producedIcons = (poi.basket && activeProduced.length > 0)
        ? "🧺"
        : activeProduced.map(function (gid) { return (GOODS[gid] || {}).icon || ""; }).join("");

      var parts = [];
      if (consumedIcons) parts.push('<span class="poi-prod-group poi-consumed">' + consumedIcons + "</span>");
      parts.push(baseIcon);
      if (producedIcons) parts.push('<span class="poi-prod-group poi-produced">' + producedIcons + "</span>");
      inner.innerHTML = parts.join('<span class="poi-arrow">→</span>');
    });
  }

  // ════════════════════════════════════════════════════════════════════
  // TOOLTIP (hover box) — follows the mouse, shows loop/leg/poi summary.
  // ════════════════════════════════════════════════════════════════════
  var tooltipEl = null;
  function ensureTooltipEl() {
    if (!tooltipEl) tooltipEl = document.getElementById("routeTip");
    return tooltipEl;
  }

  function fmtMetricsHtml(agg) {
    var html = "";
    html += '<div class="tip-row"><span>' + t("bike_time") + "</span><b>" +
      (agg.ebike.dist > 0 ? agg.ebike.time.toFixed(1) + " " + t("min_unit") : t("no_data")) + "</b></div>";
    html += '<div class="tip-row"><span>' + t("bike_quality") + "</span><b>" + starsHtml(agg.ebike.friendliness) + "</b></div>";
    html += '<div class="tip-row"><span>' + t("bike_distance") + "</span><b>" +
      (agg.ebike.dist > 0 ? agg.ebike.dist.toFixed(2) + " " + t("km_unit") : t("no_data")) + "</b></div>";
    html += '<div class="tip-row"><span>' + t("car_time") + "</span><b>" +
      (agg.car.dist > 0 ? agg.car.time.toFixed(1) + " " + t("min_unit") : t("no_data")) + "</b></div>";
    html += '<div class="tip-row"><span>' + t("car_distance") + "</span><b>" +
      (agg.car.dist > 0 ? agg.car.dist.toFixed(2) + " " + t("km_unit") : t("no_data")) + "</b></div>";
    html += '<div class="tip-row"><span>' + t("car_co2") + "</span><b>" +
      (agg.car.dist > 0 ? agg.car.co2.toFixed(2) + " " + t("kg_unit") : t("no_data")) + "</b></div>";
    return html;
  }

  function showLoopTooltip(loop) {
    var el = ensureTooltipEl();
    if (!el) return;
    var m = loopMetrics(loop);
    el.innerHTML = "<b>" + t("popup_loop_title") + "</b>" + fmtMetricsHtml(m);
    el.style.display = "block";
  }

  function showLegTooltip(fromPid, toPid, vehicle) {
    var el = ensureTooltipEl();
    if (!el) return;
    var carM = pairMetrics(fromPid, toPid, "car");
    var ebikeM = pairMetrics(fromPid, toPid, "ebike");
    var agg = {
      ebike: { time: ebikeM ? ebikeM.t || 0 : 0, dist: ebikeM ? ebikeM.d || 0 : 0, friendliness: ebikeM ? ebikeM.f || 0 : 0 },
      car: { time: carM ? carM.t || 0 : 0, dist: carM ? carM.d || 0 : 0, co2: carM ? carM.co2 || 0 : 0 },
    };
    var fromName = (POIS[fromPid] || {}).name || fromPid;
    var toName = (POIS[toPid] || {}).name || toPid;
    el.innerHTML = "<b>" + fromName + " → " + toName + "</b>" + fmtMetricsHtml(agg);
    el.style.display = "block";
  }

  function showPoiTooltip(pid, touchingLoops) {
    var el = ensureTooltipEl();
    if (!el) return;
    var poi = POIS[pid] || {};
    var agg = aggregateMetrics(touchingLoops);
    el.innerHTML = "<b>" + (poi.name || pid) + "</b>" + fmtMetricsHtml(agg);
    el.style.display = "block";
  }

  function hideTooltip() {
    var el = ensureTooltipEl();
    if (el) el.style.display = "none";
  }

  function positionTooltip(evt) {
    var el = ensureTooltipEl();
    if (!el || el.style.display === "none") return;
    var x = evt.clientX + 16;
    var y = evt.clientY + 16;
    var maxX = window.innerWidth - 340;
    var maxY = window.innerHeight - 160;
    el.style.left = Math.min(x, maxX) + "px";
    el.style.top = Math.min(y, maxY) + "px";
  }

  // ════════════════════════════════════════════════════════════════════
  // POPUPS — loop popup (metrics + clickable product list) and POI popup
  // (name, produced/consumed clickable product lists, summary of all
  // loops through this POI). Both reuse a single Leaflet popup bound to
  // the mouse click position.
  // ════════════════════════════════════════════════════════════════════
  function productListHtml(goodIds) {
    if (!goodIds || goodIds.length === 0) return "";
    return goodIds.map(function (gid) {
      var g = GOODS[gid] || {};
      return '<span class="prod-chip" data-good-id="' + gid + '" onclick="window.__showGoodsDetail(' + gid + ')">' +
        (g.icon || "📦") + " " + (g.name || gid) + "</span>";
    }).join(" ");
  }

  function loopPopupHtml(loop) {
    var vehicle = state.showCar && !state.showEbike ? "car" : "ebike";
    var m = loopMetrics(loop);
    var resolved = resolveLoopLegs(loop, vehicle);
    var allGoods = new Set();
    (resolved.legs || []).forEach(function (leg) {
      (leg.load_products || []).forEach(function (g) { if (state.activeProducts.has(String(g))) allGoods.add(g); });
      (leg.unload_products || []).forEach(function (g) { if (state.activeProducts.has(String(g))) allGoods.add(g); });
    });
    return (
      '<div class="popup-box"><b>' + t("popup_loop_title") + "</b>" +
      fmtMetricsHtml(m) +
      '<div class="popup-sep"></div>' +
      '<div class="popup-products">' + productListHtml(Array.from(allGoods)) + "</div>" +
      "</div>"
    );
  }

  function poiPopupHtml(pid) {
    var poi = POIS[pid] || {};
    var loops = loopsTouchingPoi(pid, loopsForActiveLayer(), state.showCar && !state.showEbike ? "car" : "ebike");
    var agg = aggregateMetrics(loops);

    // Use leg-based goods (same logic as marker icons) so the popup reflects
    // what actually flows at this POI in the current layer, and filter by
    // the active product checkboxes.
    var produced, consumed;
    if (LAYER_ORDER.length > 0) {
      var legGoods = _poiGoodsFromLegs();
      var entry = legGoods[String(pid)];
      if (entry) {
        produced = Array.from(entry.loaded).filter(function (g) { return state.activeProducts.has(g); });
        consumed = Array.from(entry.unloaded).filter(function (g) { return state.activeProducts.has(g); });
      } else {
        produced = (poi.produced || []).map(String).filter(function (g) { return state.activeProducts.has(g); });
        consumed = (poi.consumed || []).map(String).filter(function (g) { return state.activeProducts.has(g); });
      }
    } else {
      produced = (poi.produced || []).map(String).filter(function (g) { return state.activeProducts.has(g); });
      consumed = (poi.consumed || []).map(String).filter(function (g) { return state.activeProducts.has(g); });
    }

    return (
      '<div class="popup-box"><b>' + (poi.name || pid) + "</b>" +
      '<div class="popup-sub">' + (poi.address || "") + "</div>" +
      (poi.sector ? '<div class="popup-sub popup-sector">' + poi.sector + "</div>" : "") +
      '<div class="popup-sep"></div>' +
      '<div class="popup-label">' + t("popup_poi_produces") + "</div>" +
      '<div class="popup-products">' + (productListHtml(produced) || t("no_data")) + "</div>" +
      '<div class="popup-label">' + t("popup_poi_consumes") + "</div>" +
      '<div class="popup-products">' + (productListHtml(consumed) || t("no_data")) + "</div>" +
      '<div class="popup-sep"></div>' +
      fmtMetricsHtml(agg) +
      "</div>"
    );
  }

  // Level→visual mapping for size and weight (both use the same 5-step scale).
  var _LEVEL_ICONS = {
    "none":   { bars: 0, emoji: "○○○○○" },
    "tiny":   { bars: 1, emoji: "●○○○○" },
    "small":  { bars: 2, emoji: "●●○○○" },
    "medium": { bars: 3, emoji: "●●●○○" },
    "big":    { bars: 4, emoji: "●●●●○" },
    "huge":   { bars: 5, emoji: "●●●●●" },
  };
  var _WEIGHT_EMOJI = { none:"○○○○○", tiny:"🪶○○○○", small:"🪶🪶○○○", medium:"⚖️⚖️⚖️○○", big:"💪💪💪💪○", huge:"🏋️🏋️🏋️🏋️🏋️" };
  var _SIZE_EMOJI   = { none:"○○○○○", tiny:"🔹○○○○", small:"🔹🔹○○○", medium:"🔵🔵🔵○○", big:"🔴🔴🔴🔴○", huge:"🔴🔴🔴🔴🔴" };

  function levelHtml(value, emojiMap) {
    if (!value) return '<span class="gdb-level-none">—</span>';
    var icons = (emojiMap || {})[value] || value;
    return '<span class="gdb-level" title="' + value + '">' + icons + '</span>';
  }

  window.__showGoodsDetail = function (gid) {
    var g = GOODS[gid];
    var box = document.getElementById("goodsDetailBox");
    if (!box || !g) return;
    box.innerHTML =
      '<div class="gdb-close" onclick="document.getElementById(\'goodsDetailBox\').style.display=\'none\'">✕</div>' +
      '<div class="gdb-title">' + (g.icon || "") + " " + g.name + "</div>" +
      '<div class="gdb-row"><span class="gdb-label">' + t("goods_potential") + "</span>" + starsHtml(g.potential) + "</div>" +
      '<div class="gdb-row"><span class="gdb-label">' + t("goods_weight") + "</span>" + levelHtml(g.weight, _WEIGHT_EMOJI) + "</div>" +
      '<div class="gdb-row"><span class="gdb-label">' + t("goods_size") + "</span>" + levelHtml(g.size, _SIZE_EMOJI) + "</div>" +
      '<div class="gdb-row"><span class="gdb-label">' + t("goods_features") + "</span><span>" +
      ((g.special_features || []).join(", ") || t("no_data")) + "</span></div>";
    box.style.display = "block";
  };

  var _clickPopup = null;
  function openPopupAt(latlng, html) {
    var lmap = getLeafletMap();
    if (!lmap) return;
    if (_clickPopup) lmap.closePopup(_clickPopup);
    _clickPopup = L.popup({ maxWidth: 320, className: "custom-popup" }).setLatLng(latlng).setContent(html).openOn(lmap);
  }

  // ════════════════════════════════════════════════════════════════════
  // EVENT WIRING
  // ════════════════════════════════════════════════════════════════════
  var hoveredKind = null; // "poi" | "leg" | null
  var hoveredId = null;

  function activeVehicleForHover() {
    if (state.showEbike) return "ebike";
    return "car";
  }

  function findPoiEl(target) {
    return target.closest ? target.closest(".poi") : null;
  }

  function findLegEl(target) {
    return target.closest ? target.closest(".leg-fill, .leg-border, .dyn-arrow, .dyn-product") : null;
  }

  function legElPairInfo(el) {
    var from = el.getAttribute("data-pair-from");
    var to = el.getAttribute("data-pair-to");
    var vehicle = el.getAttribute("data-vehicle");
    if (from == null || to == null || !vehicle) return null;
    return { from: from, to: to, vehicle: vehicle };
  }

  function bindHoverHandlers() {
    document.addEventListener("mouseover", function (e) {
      var poiEl = findPoiEl(e.target);
      if (poiEl) {
        var pid = poiEl.getAttribute("data-poi-id");
        if (hoveredKind === "poi" && hoveredId === pid) return;
        hoveredKind = "poi"; hoveredId = pid;
        hoverPoi(pid);
        return;
      }
      var legEl = findLegEl(e.target);
      if (legEl) {
        var info = legElPairInfo(legEl);
        if (!info) return;
        var key = info.from + "_" + info.to + "_" + info.vehicle;
        if (hoveredKind === "leg" && hoveredId === key) return;
        hoveredKind = "leg"; hoveredId = key;
        hoverLeg(info.from, info.to);
      }
    });

    document.addEventListener("mouseout", function (e) {
      var related = e.relatedTarget;
      var stillOnPoi = related && findPoiEl(related);
      var stillOnLeg = related && findLegEl(related);
      if (hoveredKind === "poi" && !stillOnPoi) { hoveredKind = null; hoveredId = null; endHover(); }
      if (hoveredKind === "leg" && !stillOnLeg) { hoveredKind = null; hoveredId = null; endHover(); }
    });

    document.addEventListener("mousemove", positionTooltip);
  }

  // Click: distinguish a genuine click from a drag-to-pan.
  var _downPos = null;
  function bindClickHandlers() {
    document.addEventListener("mousedown", function (e) { _downPos = { x: e.clientX, y: e.clientY }; });

    document.addEventListener("click", function (e) {
      var moved = _downPos && (Math.abs(e.clientX - _downPos.x) > 6 || Math.abs(e.clientY - _downPos.y) > 6);
      if (moved) return; // was a drag, not a click

      // Clicks inside UI chrome (popups, filter panel, dropdowns, summary
      // box, language switcher, goods detail box, raster background-layer
      // dropdown + legend) must never be treated as a "background click"
      // that exits isolation -- only a genuine click on empty map area,
      // another POI, or another loop should do that.
      if (e.target.closest && e.target.closest(
        "#ctrlPanel, #infoBox, #topBar, #langSwitcher, #goodsDetailBox, " +
        "#rasterSelectContainer, .maplegend, " +
        ".leaflet-popup, .leaflet-control"
      )) {
        return;
      }

      var poiEl = findPoiEl(e.target);
      if (poiEl) {
        var pid = poiEl.getAttribute("data-poi-id");
        if (state.isolation && state.isolation.originKey === "poi:" + pid) {
          clearIsolation();
        } else {
          isolatePoi(pid);
          var poi = POIS[pid];
          if (poi) openPopupAt(L.latLng(poi.lat, poi.lng), poiPopupHtml(pid));
        }
        e.stopPropagation();
        return;
      }

      var legEl = findLegEl(e.target);
      if (legEl) {
        var info = legElPairInfo(legEl);
        if (info) {
          var loop = loopContainingSegment(info.from, info.to, info.vehicle);
          if (loop && state.isolation && state.isolation.originKey === "loop:" + loop.id) {
            clearIsolation();
          } else if (loop) {
            isolateLoop(loop);
            var midLatLng = midpointOfSegment(info.from, info.to, info.vehicle);
            if (midLatLng) openPopupAt(midLatLng, loopPopupHtml(loop));
          }
        }
        e.stopPropagation();
        return;
      }

      // background click on empty map area -> exit isolation
      if (state.isolation) clearIsolation();
    });
  }

  function loopContainingSegment(fromPid, toPid, vehicle) {
    var loops = state.isolation ? loopsForIsolationOnly() : loopsForActiveLayer();
    for (var i = 0; i < loops.length; i++) {
      var segs = segmentsOfLoop(loops[i], vehicle);
      for (var j = 0; j < segs.length; j++) {
        if (String(segs[j].from) === String(fromPid) && String(segs[j].to) === String(toPid)) return loops[i];
        if (String(segs[j].from) === String(toPid) && String(segs[j].to) === String(fromPid)) return loops[i];
      }
    }
    return null;
  }

  function midpointOfSegment(fromPid, toPid, vehicle) {
    var found = getPairLayers(fromPid, toPid, vehicle);
    if (!found || !found.layers.fill || !found.layers.fill.getLatLngs) return null;
    var pts = flattenLatLngs(found.layers.fill.getLatLngs());
    var info = pointAtDistance(pts, polylineLength(pts) / 2);
    return info ? info.latlng : null;
  }

  // ════════════════════════════════════════════════════════════════════
  // CONTROL PANEL: vehicle + product checkboxes, layer dropdown
  // ════════════════════════════════════════════════════════════════════
  function populateLayerDropdown() {
    var dd = document.getElementById("layerDropdown");
    if (!dd) return;
    var isPoiMode = POI_LAYER_ORDER.length > 0;
    var order = isPoiMode ? POI_LAYER_ORDER : LAYER_ORDER;
    dd.innerHTML = "";
    order.forEach(function (key) {
      var opt = document.createElement("option");
      opt.value = key;
      var label = isPoiMode ? (POI_LAYER_NAMES[key] || key) : LAYER_NAMES[key];
      if (!isPoiMode) {
        if (key === "producer") label = t("layer_producer");
        if (key === "consumer") label = t("layer_consumer");
      }
      opt.textContent = label;
      dd.appendChild(opt);
    });
    dd.value = isPoiMode ? state.poiLayerKey : state.layerKey;
    dd.addEventListener("change", function (e) {
      if (isPoiMode) state.poiLayerKey = e.target.value;
      else state.layerKey = e.target.value;
      if (state.isolation) clearIsolation();
      populateProductChecks();
      renderActiveLayer();
    });
  }

  /** Every good_id referenced by ANY leg of ANY loop in the currently
   * active layer — used to narrow the product checkbox list so it only
   * shows products that can actually appear, instead of every product
   * across the whole map. In poi_map mode (no loops), the equivalent is
   * every good produced/consumed by a POI in the active POI layer. */
  function productIdsForActiveLayer() {
    var ids = new Set();
    if (LAYER_ORDER.length === 0) {
      poisForActivePoiLayer().forEach(function (pid) {
        var poi = POIS[pid] || {};
        (poi.produced || []).forEach(function (g) { ids.add(g); });
        (poi.consumed || []).forEach(function (g) { ids.add(g); });
      });
      return Array.from(ids).sort(function (a, b) { return a - b; });
    }
    loopsForActiveLayer().forEach(function (loop) {
      (loop.ebike_legs || []).concat(loop.car_legs || []).forEach(function (leg) {
        (leg.load_products || []).forEach(function (g) { ids.add(g); });
        (leg.unload_products || []).forEach(function (g) { ids.add(g); });
      });
    });
    return Array.from(ids).sort(function (a, b) { return a - b; });
  }

  var _prodChecksListenerBound = false;

  function populateProductChecks() {
    var box = document.getElementById("prodChecks");
    if (!box) return;
    box.innerHTML = "";
    var relevantIds = productIdsForActiveLayer();
    relevantIds.forEach(function (gid) {
      var g = GOODS[gid] || {};
      var checked = state.activeProducts.has(String(gid));
      var label = document.createElement("label");
      label.innerHTML = '<input type="checkbox" value="' + gid + '"' + (checked ? " checked" : "") + '> ' + (g.icon || "") + " " + (g.name || gid);
      box.appendChild(label);
    });
    if (!_prodChecksListenerBound) {
      _prodChecksListenerBound = true;
      box.addEventListener("change", function (e) {
        if (e.target.type !== "checkbox") return;
        var gid = e.target.value;
        if (e.target.checked) state.activeProducts.add(gid);
        else state.activeProducts.delete(gid);
        reRenderPreservingIsolation();
      });
    }
  }

  function bindVehicleChecks() {
    var ebikeBox = document.getElementById("modeEbike");
    var carBox = document.getElementById("modeCar");
    if (ebikeBox) {
      ebikeBox.addEventListener("change", function (e) {
        state.showEbike = e.target.checked;
        reRenderPreservingIsolation();
      });
    }
    if (carBox) {
      carBox.addEventListener("change", function (e) {
        state.showCar = e.target.checked;
        reRenderPreservingIsolation();
      });
    }
  }

  // ════════════════════════════════════════════════════════════════════
  // LANGUAGE SWITCHING
  // ════════════════════════════════════════════════════════════════════
  function applyTranslations() {
    document.querySelectorAll("[data-i18n]").forEach(function (el) {
      el.textContent = t(el.getAttribute("data-i18n"));
    });
    var dd = document.getElementById("layerDropdown");
    if (dd) {
      Array.prototype.forEach.call(dd.options, function (opt) {
        if (opt.value === "producer") opt.textContent = t("layer_producer");
        if (opt.value === "consumer") opt.textContent = t("layer_consumer");
      });
    }
    var label = document.getElementById("currentLangLabel");
    if (label) label.textContent = state.lang.toUpperCase();
  }

  window.toggleLanguage = function () {
    state.lang = state.lang === "de" ? "en" : "de";
    applyTranslations();
    renderActiveLayer();
  };

  // ════════════════════════════════════════════════════════════════════
  // INSTRUCTIONS MODAL
  // ════════════════════════════════════════════════════════════════════
  window.openInstructions = function () {
    var el = document.getElementById("instructionsOverlay");
    if (el) el.classList.add("open");
  };
  window.closeInstructions = function () {
    var el = document.getElementById("instructionsOverlay");
    if (el) el.classList.remove("open");
  };

  // ════════════════════════════════════════════════════════════════════
  // EMBEDDING HOOK (postMessage) — lets a page that embeds this map in an
  // <iframe> (e.g. the presentation) pick an initial layer / dismiss the
  // instructions modal, without changing the map's normal standalone
  // behaviour (opened directly, no message ever arrives).
  // ════════════════════════════════════════════════════════════════════
  window.addEventListener("message", function (e) {
    var data = e.data || {};
    if (data.type === "setLayer" && LAYER_ORDER.indexOf(data.layer) !== -1) {
      state.layerKey = data.layer;
      var dd = document.getElementById("layerDropdown");
      if (dd) dd.value = data.layer;
      if (state.isolation) clearIsolation();
      populateProductChecks();
      renderActiveLayer();
    }
    if (data.type === "closeInstructions") {
      window.closeInstructions();
    }
  });

  // ════════════════════════════════════════════════════════════════════
  // INIT
  // ════════════════════════════════════════════════════════════════════
  var _initDone = false;
  function init() {
    var lmap = getLeafletMap();
    if (!lmap) { setTimeout(init, 100); return; }
    if (_initDone) return;
    _initDone = true;

    populateLayerDropdown();
    populateProductChecks();
    bindVehicleChecks();
    bindHoverHandlers();
    bindClickHandlers();
    applyTranslations();
    renderActiveLayer();
    window.openInstructions();

    if (typeof L.control.scale === "function") {
      L.control.scale({ position: "topleft", imperial: false }).addTo(lmap);
    }

    lmap.on("zoomend moveend", function () {
      // polyline DOM elements are stable across pan/zoom in Leaflet/SVG
      // renderer, so no re-index needed; dynamic markers stay anchored.
    });
  }


  function updateSummaryBox(loops) {
    var agg = aggregateMetrics(loops);
    var set = function (id, html) {
      var el = document.getElementById(id);
      if (el) el.innerHTML = html;
    };
    set("ibBikeTime", agg.ebike.dist > 0 ? agg.ebike.time.toFixed(1) + " " + t("min_unit") : t("no_data"));
    set("ibBikeStars", starsHtml(agg.ebike.friendliness));
    set("ibBikeDist", agg.ebike.dist > 0 ? agg.ebike.dist.toFixed(2) + " " + t("km_unit") : t("no_data"));
    set("ibCarTime", agg.car.dist > 0 ? agg.car.time.toFixed(1) + " " + t("min_unit") : t("no_data"));
    set("ibCarDist", agg.car.dist > 0 ? agg.car.dist.toFixed(2) + " " + t("km_unit") : t("no_data"));
    set("ibCarCo2", agg.car.dist > 0 ? agg.car.co2.toFixed(2) + " " + t("kg_unit") : t("no_data"));
  }

  function loopsTouchingPoi(pid, loops, vehicle) {
    pid = String(pid);
    return loops.filter(function (loop) {
      var resolved = resolveLoopLegs(loop, vehicle);
      return resolved.valid && resolved.stops.some(function (s) { return String(s) === pid; });
    });
  }

  function segmentsOfLoop(loop, vehicle) {
    var resolved = resolveLoopLegs(loop, vehicle);
    return resolved.valid ? resolved.segments : [];
  }

  // ════════════════════════════════════════════════════════════════════
  // HIGHLIGHT-2 (HOVER): style change only, nothing hidden.
  // While highlight-1 is active, hover scope is restricted to ONE leg +
  // its 2 endpoint POIs (per-leg), not the whole loop.
  // ════════════════════════════════════════════════════════════════════
  function clearHighlight2() {
    document.body.classList.remove("hl2-active");
    document.querySelectorAll(".leg-fill.hl2, .leg-border.hl2").forEach(function (el) {
      el.classList.remove("hl2");
    });
    document.querySelectorAll(".poi.hl2").forEach(function (el) { el.classList.remove("hl2"); });
    // Only strip hl2-origin from POIs that are NOT the current isolation's
    // origin POI -- isolation's pink "you clicked this" marker must persist
    // through hover changes, it's not a hover-only effect.
    var isolationOriginPid = null;
    if (state.isolation && state.isolation.originKey && state.isolation.originKey.indexOf("poi:") === 0) {
      isolationOriginPid = state.isolation.originKey.slice("poi:".length);
    }
    document.querySelectorAll(".poi.hl2-origin").forEach(function (el) {
      var pid = el.getAttribute("data-poi-id");
      if (isolationOriginPid != null && String(pid) === String(isolationOriginPid)) return;
      el.classList.remove("hl2-origin");
    });
    if (_hoverMarkers) _hoverMarkers.clearLayers();
  }

  /**
   * @param segments    leg/loop segments to highlight (blue, larger) — vehicle-agnostic
   *                    {from, to} pairs; applied to every active vehicle's geometry.
   * @param poiIds      every POI connected through the highlighted segments (blue border)
   * @param originPoiId if this highlight-2 was triggered by hovering/clicking
   *                    directly on a POI, that POI's id (pink border, frontmost)
   */
  function applyHighlight2Segments(segments, poiIds, originPoiId) {
    document.body.classList.add("hl2-active");
    vehiclesToRender().forEach(function (vehicle) {
      segments.forEach(function (seg) {
        var found = getPairLayers(seg.from, seg.to, vehicle);
        if (!found) return;
        // Move border THEN fill to the end of their parent, in that order,
        // so fill paints last/topmost (matching the original creation order:
        // border underneath, colored fill on top) while both still end up
        // above every other (non-highlighted) leg in the SVG.
        ["border", "fill"].forEach(function (k) {
          var layer = found.layers[k];
          var el = layer && layer.getElement ? layer.getElement() : null;
          if (el) {
            el.classList.add("hl2");
            // z-index has no effect on SVG <path> elements -- paint order is
            // purely DOM order, so bring the highlighted path to the end of
            // its parent <g>/<svg> to render it above all other loop legs.
            if (el.parentNode) el.parentNode.appendChild(el);
          }
        });
      });
    });
    poiIds.forEach(function (pid) {
      if (originPoiId != null && String(pid) === String(originPoiId)) return; // styled separately below
      var el = document.querySelector('.poi[data-poi-id="' + pid + '"]');
      if (el) el.classList.add("hl2");
    });
    if (originPoiId != null) {
      var originEl = document.querySelector('.poi[data-poi-id="' + originPoiId + '"]');
      if (originEl) originEl.classList.add("hl2-origin");
    }
    // Larger blue arrows/icons for the highlighted segments, on their own
    // layer so the base (normal-style) markers are never touched.
    var loops = state.isolation ? loopsForIsolationOnly() : loopsForActiveLayer();
    renderHoverDynamicMarkers(segments, loops);
  }

  function hoverLoop(loop) {
    clearHighlight2();
    // Use the first active vehicle to derive segments — car/ebike share the same stop order.
    var refVehicle = vehiclesToRender()[0] || "ebike";
    var segments = segmentsOfLoop(loop, refVehicle);
    var resolved = resolveLoopLegs(loop, refVehicle);
    applyHighlight2Segments(segments, resolved.stops.map(String), null);
    showLoopTooltip(loop);
  }

  function hoverLeg(fromPid, toPid) {
    clearHighlight2();
    var seg = { from: Number(fromPid), to: Number(toPid) };
    applyHighlight2Segments([seg], [String(fromPid), String(toPid)], null);
    showLegTooltip(fromPid, toPid, vehiclesToRender()[0] || "ebike");
  }

  function hoverPoi(pid) {
    clearHighlight2();
    var refVehicle = vehiclesToRender()[0] || "ebike";
    var loops = state.isolation ? loopsForIsolationOnly() : loopsForActiveLayer();
    var touching = loopsTouchingPoi(pid, loops, refVehicle);
    var allSegments = [];
    var allPois = new Set([String(pid)]);
    touching.forEach(function (loop) {
      segmentsOfLoop(loop, refVehicle).forEach(function (seg) {
        allSegments.push(seg);
        allPois.add(String(seg.from));
        allPois.add(String(seg.to));
      });
    });
    applyHighlight2Segments(allSegments, Array.from(allPois), pid);
    showPoiTooltip(pid, touching);
  }

  function loopsForIsolationOnly() {
    if (!state.isolation) return loopsForActiveLayer();
    return loopsForActiveLayer().filter(function (loop) { return state.isolation.loopIds.has(loop.id); });
  }

  function endHover() {
    clearHighlight2();
    hideTooltip();
  }

  // ════════════════════════════════════════════════════════════════════
  // HIGHLIGHT-1 (CLICK ISOLATION): hide everything not connected.
  // ════════════════════════════════════════════════════════════════════
  function applyIsolation(loopIds, poiIds, originKey, originPoiId) {
    state.isolation = { loopIds: loopIds, poiIds: poiIds, originKey: originKey };
    document.body.classList.add("isolation-active");
    document.querySelectorAll(".leg-fill, .leg-border").forEach(function (el) {
      el.classList.remove("iso-keep");
    });
    document.querySelectorAll(".poi").forEach(function (el) {
      el.classList.remove("iso-keep", "hl2-origin");
    });

    var loops = loopsForActiveLayer().filter(function (l) { return loopIds.has(l.id); });
    var isolatedSegmentsByVehicle = { ebike: [], car: [] };
    vehiclesToRender().forEach(function (vehicle) {
      loops.forEach(function (loop) {
        segmentsOfLoop(loop, vehicle).forEach(function (seg) {
          isolatedSegmentsByVehicle[vehicle].push(seg);
          var found = getPairLayers(seg.from, seg.to, vehicle);
          if (!found) return;
          ["fill", "border"].forEach(function (k) {
            var layer = found.layers[k];
            var el = layer && layer.getElement ? layer.getElement() : null;
            if (el) el.classList.add("iso-keep");
          });
        });
      });
    });
    poiIds.forEach(function (pid) {
      var el = document.querySelector('.poi[data-poi-id="' + pid + '"]');
      if (el) el.classList.add("iso-keep");
    });
    if (originPoiId != null) {
      var originEl = document.querySelector('.poi[data-poi-id="' + originPoiId + '"]');
      if (originEl) originEl.classList.add("hl2-origin");
    }
    // Re-draw the base (normal-style) arrows/icons restricted to just the
    // isolated loop(s) — under isolation only these should be visible.
    renderBaseDynamicMarkers(isolatedSegmentsByVehicle, loops);
    updateSummaryBox(loops);
  }

  function clearIsolation() {
    state.isolation = null;
    document.body.classList.remove("isolation-active");
    document.querySelectorAll(".iso-keep").forEach(function (el) { el.classList.remove("iso-keep"); });
    document.querySelectorAll(".hl2-origin").forEach(function (el) { el.classList.remove("hl2-origin"); });
    renderActiveLayer();
  }

  /** Re-render after a filter change (product/vehicle checkbox) without
   * losing an active highlight-1 isolation. Re-derives the isolated POI
   * set fresh (a stop's visibility can change when filters change) but
   * keeps the same loop(s)/origin selected. */
  function reRenderPreservingIsolation() {
    if (!state.isolation) {
      renderActiveLayer();
      return;
    }
    var loopIds = state.isolation.loopIds;
    var originKey = state.isolation.originKey;
    var originPoiId = null;
    if (originKey && originKey.indexOf("poi:") === 0) originPoiId = originKey.slice("poi:".length);

    renderActiveLayer();
    var poiIds = new Set();
    var loops = loopsForActiveLayer().filter(function (l) { return loopIds.has(l.id); });
    vehiclesToRender().forEach(function (vehicle) {
      loops.forEach(function (loop) {
        resolveLoopLegs(loop, vehicle).stops.forEach(function (s) { poiIds.add(String(s)); });
      });
    });
    if (originPoiId != null) poiIds.add(String(originPoiId));
    applyIsolation(loopIds, poiIds, originKey, originPoiId);
  }

  function isolateLoop(loop) {
    var poiIds = new Set();
    ["ebike", "car"].forEach(function (vehicle) {
      resolveLoopLegs(loop, vehicle).stops.forEach(function (s) { poiIds.add(String(s)); });
    });
    applyIsolation(new Set([loop.id]), poiIds, "loop:" + loop.id);
  }

  function isolatePoi(pid) {
    var loops = loopsForActiveLayer();
    var loopIds = new Set();
    var poiIds = new Set([String(pid)]);
    ["ebike", "car"].forEach(function (vehicle) {
      loopsTouchingPoi(pid, loops, vehicle).forEach(function (loop) {
        loopIds.add(loop.id);
        resolveLoopLegs(loop, vehicle).stops.forEach(function (s) { poiIds.add(String(s)); });
      });
    });
    applyIsolation(loopIds, poiIds, "poi:" + pid, pid);
  }

  var _dynamicMarkers = null;
  var _hoverMarkers = null;

  function clearAllLegClasses() {
    document.querySelectorAll(".leg-fill, .leg-border").forEach(function (el) {
      el.classList.remove("lv", "hl2", "hl2-from-isolation");
    });
  }

  function clearAllPoiClasses() {
    document.querySelectorAll(".poi").forEach(function (el) {
      el.classList.remove("pv", "hl2", "isolated-out", "isolated-in");
    });
  }

  function vehiclesToRender() {
    var list = [];
    if (state.showEbike) list.push("ebike");
    if (state.showCar) list.push("car");
    return list;
  }

  function setSegmentClass(seg, vehicle, add) {
    var found = getPairLayers(seg.from, seg.to, vehicle);
    if (!found) return;
    ["fill", "border"].forEach(function (k) {
      var layer = found.layers[k];
      if (!layer || !layer.getElement) return;
      var el = layer.getElement();
      if (!el) return;
      if (add) el.classList.add("lv");
      else el.classList.remove("lv");
    });
  }

  function poiHasActiveGoods(pid) {
    var poi = POIS[pid];
    if (!poi) return false;
    var all = (poi.produced || []).concat(poi.consumed || []);
    return all.some(function (gid) { return state.activeProducts.has(String(gid)); });
  }

  function renderActiveLayer() {
    clearAllLegClasses();
    clearAllPoiClasses();

    var loops = loopsForActiveLayer();
    var visiblePois = new Set();
    var visibleSegmentsByVehicle = { ebike: [], car: [] };

    // In poi_map mode (no loop layers): show every POI in the active POI
    // layer that either carries no goods at all, or has at least one
    // produced/consumed good among the checked product filters.
    if (LAYER_ORDER.length === 0) {
      poisForActivePoiLayer().forEach(function (pid) {
        var poi = POIS[pid] || {};
        var goods = (poi.produced || []).concat(poi.consumed || []);
        if (goods.length === 0 || goods.some(function (gid) { return state.activeProducts.has(String(gid)); })) {
          visiblePois.add(String(pid));
        }
      });
    }

    // Base visibility: every POI flagged Mandatory=true is always shown.
    Object.keys(POIS).forEach(function (pid) {
      if (POIS[pid].mandatory) visiblePois.add(pid);
    });

    // Everything else is visible only if it's a stop on at least one loop
    // in the active layer for the active product selection. This must NOT
    // depend on which vehicle checkboxes are checked -- those only control
    // which route lines get drawn (below), not which POIs show up.
    loops.forEach(function (loop) {
      ["ebike", "car"].forEach(function (vehicle) {
        var resolved = resolveLoopLegs(loop, vehicle);
        if (!resolved.valid) return;
        resolved.stops.forEach(function (pid) { visiblePois.add(String(pid)); });
      });
      vehiclesToRender().forEach(function (vehicle) {
        var resolved = resolveLoopLegs(loop, vehicle);
        if (!resolved.valid) return;
        resolved.segments.forEach(function (seg) {
          setSegmentClass(seg, vehicle, true);
          visibleSegmentsByVehicle[vehicle].push(seg);
        });
      });
    });

    visiblePois.forEach(function (pid) {
      var el = document.querySelector('.poi[data-poi-id="' + pid + '"]');
      if (el) el.classList.add("pv");
    });

    renderPoiMarkers();
    // Normal mode never shows arrows/product icons -- they only appear
    // under highlight-1 (isolated loop, see applyIsolation) or highlight-2
    // (hover, see renderHoverDynamicMarkers).
    if (_dynamicMarkers) _dynamicMarkers.clearLayers();
    updateSummaryBox(loops);
  }

  var _renderToken = 0; // bumped on every base render call; stale batches abort early

  /** BASE layer: normal-style (small, black) arrows + product icons for
   * every currently visible segment. Rebuilt only on a full re-render
   * (layer switch, vehicle/product checkbox change, isolation change) —
   * NEVER touched by hover, so hovering can never duplicate or leak
   * markers into this layer. */
  function renderBaseDynamicMarkers(visibleSegmentsByVehicle, loopsInPlay) {
    var lmap = getLeafletMap();
    if (!lmap) return;
    if (!_dynamicMarkers) _dynamicMarkers = L.featureGroup().addTo(lmap);
    else _dynamicMarkers.clearLayers();

    var loops = loopsInPlay || loopsForActiveLayer();
    var allTasks = [];
    ["ebike", "car"].forEach(function (vehicle) {
      (visibleSegmentsByVehicle[vehicle] || []).forEach(function (seg) {
        allTasks.push({ seg: seg, vehicle: vehicle });
      });
    });

    var myToken = ++_renderToken;
    var BATCH_SIZE = 25;
    var i = 0;

    function processBatch() {
      if (myToken !== _renderToken) return; // a newer render superseded this one
      var end = Math.min(i + BATCH_SIZE, allTasks.length);
      for (; i < end; i++) {
        var task = allTasks[i];
        var goods = segmentGoods(task.seg, task.vehicle, loops);
        drawArrowsAndIcons(_dynamicMarkers, task.seg, task.vehicle, { goods: goods, hl2: false });
      }
      if (i < allTasks.length) {
        (window.requestAnimationFrame || setTimeout)(processBatch, 0);
      }
    }
    processBatch();
  }

  /** HOVER layer: blue/larger hl2-style arrows + icons for whatever's
   * currently hovered. Completely separate feature group from the base
   * layer, cleared on every hover change — can never accumulate stale
   * markers or duplicate the base layer's markers. */
  function renderHoverDynamicMarkers(segments, loops) {
    var lmap = getLeafletMap();
    if (!lmap) return;
    if (!_hoverMarkers) _hoverMarkers = L.featureGroup().addTo(lmap);
    else _hoverMarkers.clearLayers();
    // Draw arrows/icons for every active vehicle so both routes are highlighted.
    vehiclesToRender().forEach(function (vehicle) {
      segments.forEach(function (seg) {
        var goods = segmentGoods(seg, vehicle, loops);
        drawArrowsAndIcons(_hoverMarkers, seg, vehicle, { goods: goods, hl2: true });
      });
    });
  }

  function segmentGoods(seg, vehicle, loopsForGoods) {
    // Recompute which goods flow on this exact segment, across whichever
    // loop(s) produced it (usually just one, but be safe and union them).
    var goods = new Set();
    (loopsForGoods || []).forEach(function (loop) {
      var resolved = resolveLoopLegs(loop, vehicle);
      var carried = effectiveLegGoods(loop, vehicle, resolved);
      for (var i = 0; i < resolved.segments.length; i++) {
        var s = resolved.segments[i];
        if (s.from === seg.from && s.to === seg.to) {
          (carried[i] || []).forEach(function (g) { goods.add(g); });
        }
      }
    });
    return Array.from(goods);
  }

  var MAX_ARROWS_PER_SEGMENT = 4;
  var MAX_ICONS_PER_SEGMENT = 3;

  /** A stable, deterministic 0..1 offset derived from the segment's
   * endpoints, used to stagger where the FIRST marker sits along the
   * route. Without this, a loop with only 2 stops draws the "there" and
   * "back" legs as the SAME physical road (just reversed), and since
   * both use the same even-spacing formula, every marker pair lands on
   * top of each other. */
  function segmentOffsetSeed(seg, vehicle) {
    var s = String(seg.from) + "_" + String(seg.to) + "_" + vehicle;
    var hash = 0;
    for (var i = 0; i < s.length; i++) {
      hash = (hash * 31 + s.charCodeAt(i)) >>> 0;
    }
    return (hash % 1000) / 1000; // deterministic, but spreads reversed pairs apart
  }

  /** Compute {latlng, angle} for several target distances along pts in ONE
   * linear pass (instead of re-walking the whole polyline per marker, which
   * made long routes (multi-km) freeze the page on initial render). */
  function pointsAtDistances(pts, targets) {
    var results = new Array(targets.length).fill(null);
    if (pts.length === 0 || targets.length === 0) return results;
    if (pts.length === 1) {
      for (var t = 0; t < targets.length; t++) results[t] = { latlng: pts[0], angle: 0 };
      return results;
    }
    var order = targets.map(function (d, idx) { return idx; }).sort(function (a, b) { return targets[a] - targets[b]; });
    var oi = 0;
    var accum = 0;
    for (var i = 0; i < pts.length - 1 && oi < order.length; i++) {
      var p1 = pts[i], p2 = pts[i + 1];
      var segLen = p1.distanceTo(p2);
      while (oi < order.length && targets[order[oi]] <= accum + segLen) {
        var target = targets[order[oi]];
        var ratio = segLen === 0 ? 0 : (target - accum) / segLen;
        ratio = Math.max(0, Math.min(1, ratio));
        var lat = p1.lat + ratio * (p2.lat - p1.lat);
        var lng = p1.lng + ratio * (p2.lng - p1.lng);
        var dy = p2.lat - p1.lat, dx = p2.lng - p1.lng;
        var angle = (Math.atan2(dx, dy) * 180 / Math.PI + 360) % 360;
        results[order[oi]] = { latlng: L.latLng(lat, lng), angle: angle };
        oi++;
      }
      accum += segLen;
    }
    // Any remaining targets beyond the last point fall back to the endpoint.
    var last = pts[pts.length - 1];
    var prev = pts[pts.length - 2];
    var dy2 = last.lat - prev.lat, dx2 = last.lng - prev.lng;
    var endAngle = (Math.atan2(dx2, dy2) * 180 / Math.PI + 360) % 360;
    while (oi < order.length) {
      results[order[oi]] = { latlng: last, angle: endAngle };
      oi++;
    }
    return results;
  }

  function drawArrowsAndIcons(targetLayer, seg, vehicle, opts) {
    opts = opts || {};
    var found = getPairLayers(seg.from, seg.to, vehicle);
    if (!found || !found.layers.fill || !found.layers.fill.getLatLngs) return;
    var pts = flattenLatLngs(found.layers.fill.getLatLngs());
    if (found.reversed) pts = pts.slice().reverse();
    if (pts.length < 2) return;
    var totalDist = polylineLength(pts);
    if (totalDist < 30) return;

    var isHl2 = !!opts.hl2;
    var arrowSize = isHl2 ? 22 : 14;
    var iconSize = isHl2 ? 26 : 15;

    // Stagger the starting position per segment so a 2-stop loop's "there"
    // and "back" legs (same physical road, reversed) don't stack their
    // markers exactly on top of each other.
    var offsetSeed = segmentOffsetSeed(seg, vehicle);

    // Arrows: evenly spaced, capped at MAX_ARROWS_PER_SEGMENT regardless of
    // route length (a multi-km route must not produce hundreds of markers).
    var arrowCount = Math.min(MAX_ARROWS_PER_SEGMENT, Math.max(1, Math.floor(totalDist / 150)));
    var arrowSpacing = totalDist / arrowCount;
    var arrowStart = offsetSeed * arrowSpacing;
    var arrowDistances = [];
    for (var ai = 0; ai < arrowCount; ai++) {
      arrowDistances.push((arrowStart + arrowSpacing * ai) % totalDist);
    }
    var arrowPoints = pointsAtDistances(pts, arrowDistances);
    arrowPoints.forEach(function (info) {
      if (!info) return;
      var html =
        '<div class="dyn-arrow' + (isHl2 ? " dyn-hl2" : "") + '" data-pair-from="' + seg.from + '" data-pair-to="' + seg.to + '" data-vehicle="' + vehicle + '">' +
        '<svg viewBox="0 0 10 10" style="width:' + arrowSize + "px;height:" + arrowSize + "px;" +
        "transform:rotate(" + info.angle + 'deg);">' +
        '<path d="M5 0 L10 10 L5 7.5 L0 10 Z"/></svg></div>';
      L.marker(info.latlng, {
        icon: L.divIcon({ html: html, className: "", iconSize: [arrowSize, arrowSize], iconAnchor: [arrowSize / 2, arrowSize / 2] }),
        interactive: true,
        bubblingMouseEvents: true,
      }).addTo(targetLayer);
    });

    // Product icons: same capped spacing approach, with a DIFFERENT offset
    // (golden-ratio shifted) so arrows and icons don't line up identically
    // either. Added AFTER arrows so they sit above them (z-order = draw
    // order within the same Leaflet layer group).
    var goodsIds = (opts.goods || []).filter(function (g) { return state.activeProducts.has(String(g)); });
    if (goodsIds.length === 0) return;
    var iconsHtml = goodsIds.map(function (g) { return (GOODS[g] || {}).icon || "📦"; }).join("");
    var iconCount = Math.min(MAX_ICONS_PER_SEGMENT, Math.max(1, Math.floor(totalDist / 320)));
    var iconSpacing = totalDist / iconCount;
    var iconOffsetSeed = (offsetSeed + 0.618) % 1; // golden-ratio shift vs arrow offset
    var iconStart = iconOffsetSeed * iconSpacing;
    var prodDistances = [];
    for (var pi = 0; pi < iconCount; pi++) {
      prodDistances.push((iconStart + iconSpacing * pi) % totalDist);
    }
    var prodPoints = pointsAtDistances(pts, prodDistances);
    prodPoints.forEach(function (info) {
      if (!info) return;
      var html =
        '<div class="dyn-product' + (isHl2 ? " dyn-hl2" : "") + '" data-pair-from="' + seg.from + '" data-pair-to="' + seg.to + '" data-vehicle="' + vehicle + '" style="font-size:' + iconSize + 'px;">' +
        iconsHtml + "</div>";
      L.marker(info.latlng, {
        icon: L.divIcon({ html: html, className: "", iconSize: [iconSize, iconSize], iconAnchor: [iconSize / 2, iconSize / 2] }),
        interactive: true,
        bubblingMouseEvents: true,
      }).addTo(targetLayer);
    });
  }

  function loopMetrics(loop) {
    var out = {
      ebike: { time: 0, dist: 0, friendSum: 0, friendDistSum: 0, valid: false },
      car: { time: 0, dist: 0, co2: 0, valid: false },
    };
    ["ebike", "car"].forEach(function (vehicle) {
      var resolved = resolveLoopLegs(loop, vehicle);
      if (!resolved.valid) return;
      var ok = true;
      resolved.segments.forEach(function (seg) {
        var m = pairMetrics(seg.from, seg.to, vehicle);
        if (!m || m.t == null) { ok = false; return; }
        out[vehicle].time += m.t;
        out[vehicle].dist += m.d || 0;
        if (vehicle === "ebike") {
          out.ebike.friendSum += (m.f || 0) * (m.d || 0);
          out.ebike.friendDistSum += (m.d || 0);
        } else {
          out.car.co2 += m.co2 || 0;
        }
      });
      out[vehicle].valid = ok && resolved.segments.length > 0;
    });
    out.ebike.friendliness = out.ebike.friendDistSum > 0
      ? out.ebike.friendSum / out.ebike.friendDistSum : 0;
    return out;
  }

  /** Sum metrics across every (valid, visible) loop in a set. */
  function aggregateMetrics(loops) {
    var agg = {
      ebike: { time: 0, dist: 0, friendSum: 0, friendDistSum: 0 },
      car: { time: 0, dist: 0, co2: 0 },
    };
    loops.forEach(function (loop) {
      var m = loopMetrics(loop);
      if (m.ebike.valid) {
        agg.ebike.time += m.ebike.time;
        agg.ebike.dist += m.ebike.dist;
        agg.ebike.friendSum += m.ebike.friendSum;
        agg.ebike.friendDistSum += m.ebike.friendDistSum;
      }
      if (m.car.valid) {
        agg.car.time += m.car.time;
        agg.car.dist += m.car.dist;
        agg.car.co2 += m.car.co2;
      }
    });
    agg.ebike.friendliness = agg.ebike.friendDistSum > 0
      ? agg.ebike.friendSum / agg.ebike.friendDistSum : 0;
    return agg;
  }

  if (document.readyState === "complete" || document.readyState === "interactive") init();
  else document.addEventListener("DOMContentLoaded", init);
})();