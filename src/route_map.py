"""
route_map.py
============
Renders the interactive folium map.

Design (rewrite): every loop — producer, consumer, or custom (loops.json) —
is exported in exactly the same shape: a list of "legs", each
{poi_id, load_products (good_id list), unload_products (good_id list),
mandatory}. The browser side (route_map_scripts.js) only ever has to deal
with this one shape.

For the "exclude a stop" product-checkbox feature we don't run any solver in
the browser: we draw a hidden polyline for literally EVERY POI×POI pair
(both vehicles) from `all_pairs`, and ship a JS-side lookup table
(PAIRS[a_b] -> {car: {...}, ebike: {...}}) with travel time/distance/CO2/
friendliness for every pair. When a non-mandatory stop is excluded, the
browser simply skips that stop in the leg list and connects its neighbours
directly — the geometry/metrics for that direct connection already exist
in `all_pairs` and just need to be looked up.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

import folium
import geopandas as gpd
import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors

from .data_utils import is_missing
from .delivery_loops import DeliveryLoop

DIR_NAME = os.path.dirname(__file__)


# ─────────────────────────────────────────────────────────────────────────────
# COLOUR HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def green_map(score):
    try:
        score = max(0.0, min(10.0, float(score)))
    except Exception:
        return "#4caf50"
    t = score / 10.0
    light, dark = (170, 225, 140), (40, 155, 45)
    return "rgb({},{},{})".format(
        int(light[0] + (dark[0] - light[0]) * t),
        int(light[1] + (dark[1] - light[1]) * t),
        int(light[2] + (dark[2] - light[2]) * t),
    )


def red_map(score):
    try:
        score = max(0.0, min(10.0, float(score)))
    except Exception:
        return "#c62828"
    t = 1.0 - score / 10.0
    light, dark = (242, 167, 167), (198, 40, 40)
    return "rgb({},{},{})".format(
        int(light[0] + (dark[0] - light[0]) * t),
        int(light[1] + (dark[1] - light[1]) * t),
        int(light[2] + (dark[2] - light[2]) * t),
    )


# ─────────────────────────────────────────────────────────────────────────────
# BACKGROUND RASTER LAYERS  (separate dropdown, untouched logic — only the
# layer/legend HTML id namespace was cleaned up so it can't collide with the
# main layer-control dropdown)
# ─────────────────────────────────────────────────────────────────────────────
def render_gdf(gdf, filename, dpi=1500):
    gdf = gdf.copy()
    gdf = gdf[gdf.geometry.notna()]
    gdf = gdf[gdf.is_valid]
    gdf = gdf.to_crs(4326)
    minx, miny, maxx, maxy = gdf.total_bounds
    fig = plt.figure(figsize=(10, 10), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    gdf["geometry"] = gdf["geometry"].simplify(5)
    gdf.plot(ax=ax, color=gdf["color"], linewidth=0.6)
    ax.set_xlim(minx, maxx)
    ax.set_ylim(miny, maxy)
    ax.set_aspect("equal")
    ax.set_axis_off()
    fig.savefig(filename, dpi=dpi, bbox_inches="tight", pad_inches=0, transparent=True)
    plt.close(fig)
    return filename, (minx, miny, maxx, maxy)


def create_background_layers(data, path, overwrite=False, dpi=1500):
    os.makedirs(path, exist_ok=True)
    image_dict = {}
    for col in data.keys():
        print(f"  → background layer: {col} …")
        col_gdf = data[col][[col, "geometry"]].dropna().copy()
        col_gdf = col_gdf[col_gdf[col] != "[]"]
        col_gdf = col_gdf[col_gdf[col] != ""]
        col_gdf = col_gdf[col_gdf.geometry.is_valid]
        try:
            col_gdf[col] = pd.to_numeric(col_gdf[col], errors="raise")
        except (ValueError, TypeError):
            col_gdf[col] = col_gdf[col].astype(str)
        if is_numeric_dtype(col_gdf[col]):
            col_gdf["bin"] = pd.cut(col_gdf[col], bins=10, labels=False, include_lowest=True)
            max_value = col_gdf[col].max()
            col_gdf = (
                col_gdf.dropna()
                .dissolve(by="bin", aggfunc={col: "min"})
                .reset_index()
            )
            col_gdf.loc[col_gdf["bin"] == 9, col] = max_value
            n = len(col_gdf)
            cmap = plt.cm.Blues
            colors = [mcolors.to_hex(cmap(x)) for x in np.linspace(0.3, 1.0, n)]
            col_gdf["color"] = colors
            filename = os.path.abspath(path + f"/{col}.png")
            if (not overwrite) and os.path.isfile(filename):
                gdf = col_gdf.copy()
                gdf = gdf[gdf.geometry.notna()].to_crs(4326)
                bounds = gdf.total_bounds
            else:
                filename, bounds = render_gdf(col_gdf, filename, dpi=dpi)
            image_dict[col] = {"path": filename, "bounds": bounds, "colors": col_gdf[[col, "color"]]}
        else:
            col_gdf = col_gdf.dropna().dissolve(by=col).reset_index()
            base_colors = [
                "#0b3c5d", "#f4a261", "#6c5ce7", "#17becf", "#ff66c4",
                "#1f4e79", "#ffb703", "#8e44ad", "#00a8ff", "#d291bc",
                "#2f6fb0", "#f7c46c", "#5f27cd", "#2ec4b6", "#b565c2",
                "#4f81bd", "#f6b26b", "#3b3b98", "#74a9cf", "#e056fd",
            ]
            col_gdf = col_gdf.copy()
            if col == "highway":
                # "_link" variants (e.g. primary_link) share their parent
                # type's color instead of getting a distinct one.
                color_keys = col_gdf[col].str.replace(r"_link$", "", regex=True)
                # Grouped by traffic character rather than alphabetically:
                # residential/slow streets cluster in similar blues (easy to
                # read as "one calm group"), non-motorized ways cluster in
                # similar greens, while the busier/faster road classes each
                # get a clearly distinct, more saturated hue since telling
                # those apart matters more for route planning.
                key_to_color = {
                    # fast/major roads -- maximally distinct from each other
                    "motorway":       "#8b0000",
                    "trunk":          "#d7263d",
                    "primary":        "#e08a1e",
                    "secondary":      "#c9a227",
                    "tertiary":       "#6a3d9a",
                    # residential / slow streets -- similar blue family
                    "residential":    "#1f4e79",
                    "living_street":  "#3b78b3",
                    "unclassified":   "#6ea8d8",
                    "service":        "#a9cce3",
                    # non-motorized ways -- similar green family
                    "track":          "#1b5e20",
                    "bridleway":      "#33691e",
                    "path":           "#4caf50",
                    "footway":        "#81c784",
                    "pedestrian":     "#a5d6a7",
                    "cycleway":       "#00897b",
                }
                unmapped = sorted(set(color_keys.unique()) - set(key_to_color))
                for i, k in enumerate(unmapped):
                    key_to_color[k] = base_colors[i % len(base_colors)]
            else:
                color_keys = col_gdf[col]
                unique_keys = sorted(color_keys.unique())
                key_to_color = {
                    k: base_colors[i % len(base_colors)] for i, k in enumerate(unique_keys)
                }
            col_gdf["color"] = color_keys.map(key_to_color)
            filename = os.path.abspath(path + f"/{col}.png")
            if (not overwrite) and os.path.isfile(filename):
                gdf = col_gdf.copy()
                gdf = gdf[gdf.geometry.notna()].to_crs(4326)
                bounds = gdf.total_bounds
            else:
                filename, bounds = render_gdf(col_gdf, filename, dpi=dpi)
            image_dict[col] = {"path": filename, "bounds": bounds, "colors": col_gdf[[col, "color"]]}
    return image_dict


def image_layer_map(image_dict, m=None, add_layer_control=True, opacity=1.0):
    if not image_dict:
        if m is None:
            m = folium.Map(location=[51.9757, 7.4120], zoom_start=14, tiles="CartoDB positron")
        return m
    first = next(iter(image_dict.values()))
    bounds = first["bounds"]
    center = [(bounds[1] + bounds[3]) / 2, (bounds[0] + bounds[2]) / 2]
    if m is None:
        m = folium.Map(location=center, zoom_start=14, tiles="CartoDB positron")
    layer_names = list(image_dict.keys())
    layer_names_js = json.dumps(layer_names)
    for name, info in image_dict.items():
        fg = folium.FeatureGroup(name=f"raster::{name}", overlay=True, show=False)
        fg.add_to(m)
        # Reference the PNG by relative URL instead of folium's default
        # behaviour (base64-embedding the file contents), which otherwise
        # bloats the saved HTML by the full size of every raster layer.
        img_bounds = [[info["bounds"][1], info["bounds"][0]],
                      [info["bounds"][3], info["bounds"][2]]]
        # window.onload runs after every other inline <script> on the page,
        # so the feature-group variable is guaranteed to exist by then
        # regardless of where folium happens to place this element.
        m.get_root().html.add_child(folium.Element(f"""
        <script>
        window.addEventListener("load", function() {{
            {fg.get_name()}.addLayer(
                L.imageOverlay({json.dumps(f"raster_layers/{name}.png")}, {json.dumps(img_bounds)}, {{opacity: {opacity}}})
            );
        }});
        </script>
        """))

    dropdown = """
    <div id="rasterSelectContainer">
      <select id="rasterSelect">
        <option value="None" data-i18n-opt="raster_none">—</option>
        """ + "".join(f'<option value="{x}">{x}</option>' for x in layer_names) + """
      </select>
    </div>
    <script>
    (function() {
        var imageLayerNames = new Set(""" + layer_names_js + """);

        function getLeafletMapForRaster() {
            for (var k in window) {
                if (k.indexOf("map_") === 0 && window[k] && typeof window[k].addLayer === "function") return window[k];
            }
            return null;
        }

        function findOverlaysRegistry() {
            // folium emits a global "layer_control_<id>_layers" object with an
            // ".overlays" map of {layerName: L.Layer}. This is far more robust
            // than parsing/clicking the (hidden) control's DOM checkboxes.
            for (var k in window) {
                if (k.indexOf("layer_control_") === 0 && k.indexOf("_layers") === k.length - 7) {
                    var candidate = window[k];
                    if (candidate && candidate.overlays) return candidate.overlays;
                }
            }
            return null;
        }

        function applySelection(selected) {
            var lmap = getLeafletMapForRaster();
            var overlays = findOverlaysRegistry();
            if (!lmap || !overlays) return;
            Object.keys(overlays).forEach(function (name) {
                var bareName = name.indexOf("raster::") === 0 ? name.slice("raster::".length) : name;
                if (!imageLayerNames.has(bareName)) return;
                var layer = overlays[name];
                var shouldShow = (selected !== "None" && bareName === selected);
                var isShown = lmap.hasLayer(layer);
                if (shouldShow && !isShown) lmap.addLayer(layer);
                if (!shouldShow && isShown) lmap.removeLayer(layer);
            });
        }

        document.getElementById("rasterSelect").addEventListener("change", function(e) {
            var selected = e.target.value;
            applySelection(selected);
            document.querySelectorAll(".maplegend").forEach(function(x) { x.style.display = "none"; });
            if (selected !== "None") {
                var legend = document.getElementById("legend_" + selected);
                if (legend) legend.style.display = "block";
            }
        });
    })();
    </script>
    """
    m.get_root().html.add_child(folium.Element(dropdown))

    legend_html = ""
    for name, info in image_dict.items():
        color_df = info["colors"]
        value_col = [c for c in color_df.columns if c != "color"][0]
        if pd.api.types.is_numeric_dtype(color_df[value_col]):
            df = color_df.sort_values(value_col)
            colors = df["color"].tolist()
            n = max(len(colors) - 1, 1)
            gradient = ",".join(f"{c} {100*i/n:.1f}%" for i, c in enumerate(colors))
            legend_html += (
                f'<div id="legend_{name}" class="maplegend" style="display:none;">'
                f'<b>{name}</b>'
                f'<div class="maplegend-gradient" style="background:linear-gradient(to right,{gradient});"></div>'
                f'<div class="maplegend-minmax">'
                f'<span>{df[value_col].min():.2f}</span><span>{df[value_col].max():.2f}</span>'
                f'</div></div>'
            )
        else:
            items = ""
            for _, row in color_df.iterrows():
                items += (
                    f'<div class="maplegend-item">'
                    f'<div class="maplegend-swatch" style="background:{row["color"]};"></div>'
                    f'<span>{row[value_col]}</span></div>'
                )
            legend_html += (
                f'<div id="legend_{name}" class="maplegend" style="display:none;">'
                f'<b>{name}</b>{items}</div>'
            )
    m.get_root().html.add_child(folium.Element(legend_html))
    return m


# ─────────────────────────────────────────────────────────────────────────────
# DATA EXPORT HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _f(v, dec=4):
    """Float-or-None, JSON-safe."""
    if v is None:
        return None
    try:
        f = float(v)
        if f != f or abs(f) > 1e9:  # NaN / inf guard
            return None
        return round(f, dec)
    except Exception:
        return None


def _parse_list(v):
    if isinstance(v, list):
        return v
    if v is None:
        return []
    if isinstance(v, str):
        s = v.strip()
        if not s or s in ("[]", "nan"):
            return []
        try:
            parsed = json.loads(s.replace("'", '"'))
            return parsed if isinstance(parsed, list) else [parsed]
        except Exception:
            return []
    return []


def _export_goods(goods: pd.DataFrame) -> Dict[str, dict]:
    out = {}
    for _, row in goods.iterrows():
        gid = row.get("good_id")
        if gid is None or (isinstance(gid, float) and np.isnan(gid)):
            continue
        out[str(int(gid))] = {
            "name": str(row.get("Product", "")),
            "icon": str(row.get("Icon", "") or ""),
            "potential": _f(row.get("Potential"), 1),
            "weight": str(row.get("Weight", "") or ""),
            "size": str(row.get("Size", "") or ""),
            "special_features": _parse_list(row.get("SpecialFeatures")),
        }
    return out


def _good_name_to_id(goods: pd.DataFrame) -> Dict[str, int]:
    return {str(r["Product"]): int(r["good_id"]) for _, r in goods.iterrows() if r.get("Product")}


def _to_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    s = str(v).strip().lower()
    return s in ("true", "1", "yes", "wahr")


# Project-specific (main.py only): collapse the consumed/produced icon list to
# a single 🧺 for legibility, but only once a POI actually deals in enough
# Lebensmittel-related goods to make listing them individually unreadable.
# A POI gets the basket only if it produces or consumes MORE THAN
# _BASKET_THRESHOLD distinct Lebensmittel-related goods (counting produced +
# consumed together); otherwise its real product icons are shown as usual.
# double-arrow ↔ form applies when it both produces and consumes some of
# them. Set _BASKET_ICON_ENABLED = False (e.g. from a notebook) to disable
# entirely.
_LEBENSMITTEL_RELATED_NAMES = {
    "Lebensmittel", "Getränke", "Brot", "Wein", "beer", "Honig",
    "Landwirtschaftserzeugnisse", "Spargel", "Erdbeeren",
}
_BASKET_THRESHOLD = 3
_BASKET_ICON_ENABLED = True


def _export_pois(pois: gpd.GeoDataFrame, good_name_to_id: Dict[str, int]) -> Dict[str, dict]:
    out = {}
    pois4326 = pois.to_crs(4326) if pois.crs and pois.crs.to_epsg() != 4326 else pois
    lebensmittel_related_ids = {
        good_name_to_id[n] for n in _LEBENSMITTEL_RELATED_NAMES if n in good_name_to_id
    }
    for idx, row in pois4326.iterrows():
        pid = row.get("poi_id", idx)
        produced_names = _parse_list(row.get("ProducedGoods"))
        consumed_names = _parse_list(row.get("ConsumedGoods"))
        produced_ids = sorted({good_name_to_id[n] for n in produced_names if n in good_name_to_id})
        consumed_ids = sorted({good_name_to_id[n] for n in consumed_names if n in good_name_to_id})
        geom = row.geometry
        sector = str(row.get("Sector", "") or "")
        related_produced = {g for g in produced_ids if g in lebensmittel_related_ids}
        related_consumed = {g for g in consumed_ids if g in lebensmittel_related_ids}
        related_count = len(related_produced | related_consumed)
        show_basket = _BASKET_ICON_ENABLED and related_count > _BASKET_THRESHOLD
        out[str(int(pid))] = {
            "name": str(row.get("Company", "") or f"POI {pid}"),
            "address": str(row.get("Address", "") or ""),
            "sector": sector,
            "hamlet": "" if is_missing(row.get("Hamlet")) else str(row.get("Hamlet")),
            "lat": geom.y if geom is not None else None,
            "lng": geom.x if geom is not None else None,
            "icon": str(row.get("Icon", "") or "🏠"),
            "produced": produced_ids,
            "consumed": consumed_ids,
            "mandatory": _to_bool(row.get("Mandatory")),
            "basket": show_basket,
            "basket_both": show_basket and bool(related_produced) and bool(related_consumed),
        }
    return out


def _pair_key(a, b) -> str:
    return f"{int(a)}_{int(b)}"


def _simplify_coords(geom, tolerance_deg: float = 0.00003) -> List[List[float]]:
    """[[lat,lng],...] coordinate list for a LineString, simplified to keep
    the exported payload small (avoids shipping every OSM vertex)."""
    if geom is None or geom.is_empty:
        return []
    try:
        simplified = geom.simplify(tolerance_deg, preserve_topology=False)
    except Exception:
        simplified = geom
    return [[round(lat, 6), round(lng, 6)] for lng, lat in simplified.coords]


def _export_all_pairs(all_pairs: gpd.GeoDataFrame, only_keys: Optional[set] = None) -> Dict[str, dict]:
    """Every POI x POI pair -> compact car/ebike metrics AND geometry, used
    by the browser to both look up a direct connection when a stop gets
    excluded, and to draw the polyline (raw Leaflet calls — far more compact
    in the saved HTML than one folium.PolyLine per pair).

    `only_keys`, if given, is a set of (a, b) int tuples (either direction)
    to include — every other pair is skipped. This keeps the exported
    payload to just the pairs that can ever actually appear on the map
    (loop legs + their direct-substitute connections) instead of literally
    every POI x POI combination, which scales quadratically and dominates
    file size for no benefit.
    """
    out = {}
    if all_pairs is None or all_pairs.empty:
        return out

    src_crs = all_pairs.crs
    needs_reproject = bool(src_crs) and src_crs.to_epsg() != 4326

    # IMPORTANT: GeoDataFrame.to_crs() only reprojects the ACTIVE geometry
    # column. all_pairs has TWO geometry-typed columns (car_geometry and
    # ebike_geometry) but only one can be "active" at a time, so naively
    # calling all_pairs.to_crs(4326) silently leaves the other column in
    # its original projected CRS (e.g. UTM meters) — which is exactly what
    # caused every drawn polyline to collapse to a degenerate point. Both
    # columns must be reprojected explicitly as their own GeoSeries.
    car_geom_4326 = all_pairs["car_geometry"]
    ebike_geom_4326 = all_pairs["ebike_geometry"]
    if needs_reproject:
        car_geom_4326 = gpd.GeoSeries(car_geom_4326, crs=src_crs).to_crs(4326)
        ebike_geom_4326 = gpd.GeoSeries(ebike_geom_4326, crs=src_crs).to_crs(4326)

    for idx in all_pairs.index:
        row = all_pairs.loc[idx]
        a = row.get("origin_poi_id")
        b = row.get("destination_poi_id")
        if a is None or b is None:
            continue
        a, b = int(a), int(b)
        if only_keys is not None and (a, b) not in only_keys and (b, a) not in only_keys:
            continue
        car_coords = _simplify_coords(car_geom_4326.loc[idx])
        ebike_coords = _simplify_coords(ebike_geom_4326.loc[idx])
        if not car_coords and not ebike_coords:
            continue  # no routable path — omit so JS won't highlight as connected
        key = _pair_key(a, b)
        out[key] = {
            "car": {
                "t": _f(row.get("car_travel_time"), 3),
                "d": _f(row.get("car_distance"), 4),
                "co2": _f(row.get("car_co2"), 2),
                "c": car_coords,
            },
            "ebike": {
                "t": _f(row.get("ebike_travel_time_orig"), 3),
                "d": _f(row.get("ebike_distance"), 4),
                "f": _f(row.get("ebike_friendliness_route"), 3),
                "c": ebike_coords,
            },
        }
    return out


def _reachable_pairs_for_legs(legs: List[dict]) -> set:
    """Every (poi_a, poi_b) pair that could end up directly connected once
    SOME subset of non-mandatory stops between them gets dropped by the
    product checkboxes. Since drops never reorder stops, a pair (i, j) with
    i < j is reachable iff every stop strictly between i and j is
    droppable-in-principle (non-mandatory) — it doesn't matter whether it's
    currently dropped, just whether dropping is *possible* for some product
    selection, so we only need "not mandatory" here, not the live active-
    products check.
    """
    pairs = set()
    n = len(legs)
    for i in range(n):
        for j in range(i + 1, n):
            if all(not legs[k]["mandatory"] for k in range(i + 1, j)):
                pairs.add((legs[i]["poi_id"], legs[j]["poi_id"]))
            else:
                break  # a mandatory stop in between blocks any longer reach from i
    return pairs


def _reachable_pairs_for_loops(loops_json: List[dict]) -> set:
    pairs = set()
    for loop in loops_json:
        pairs |= _reachable_pairs_for_legs(loop["ebike_legs"])
        pairs |= _reachable_pairs_for_legs(loop["car_legs"])
    return pairs


def _loop_to_json(loop: DeliveryLoop, loop_id: str) -> dict:
    return {
        "id": loop_id,
        "mode": loop.mode,
        "home": int(loop.home_poi_id),
        "ebike_legs": loop.ebike_legs or [],
        "car_legs": loop.car_legs or [],
    }


def _export_layer(loops: List[DeliveryLoop], layer_key: str) -> List[dict]:
    return [_loop_to_json(loop, f"{layer_key}_{i}") for i, loop in enumerate(loops) if loop.is_valid]


# ─────────────────────────────────────────────────────────────────────────────
# POI MARKERS
# ─────────────────────────────────────────────────────────────────────────────
# Shared with poi_selection_map() below, which shows exactly these two groups.
RESTAURANT_POIS = {42, 43, 44, 45, 47, 48, 49, 50}
TILBECK_POIS = {34}


def _add_poi_markers(m: folium.Map, pois: gpd.GeoDataFrame, goods_export: Dict[str, dict]) -> None:
    folium.map.CustomPane("poiPane", z_index=650).add_to(m)
    fg = folium.FeatureGroup(name="__pois__", overlay=True, show=True, control=False)
    fg.add_to(m)

    pois4326 = pois.to_crs(4326) if pois.crs and pois.crs.to_epsg() != 4326 else pois

    for idx, row in pois4326.iterrows():
        pid = int(row.get("poi_id", idx))
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        base_icon = str(row.get("Icon", "") or "🏠")

        if pid in RESTAURANT_POIS:
            extra_class = " poi-restaurant"
        elif pid in TILBECK_POIS:
            extra_class = " poi-tilbeck"
        else:
            extra_class = ""

        folium.Marker(
            [geom.y, geom.x],
            pane="poiPane",
            icon=folium.DivIcon(
                html=f'<div class="poi{extra_class}" data-poi-id="{pid}"><div class="poi-inner">{base_icon}</div></div>',
                icon_size=(0, 0),
            ),
        ).add_to(fg)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ROUTE MAP
# ─────────────────────────────────────────────────────────────────────────────
def route_map(
    pois: gpd.GeoDataFrame,
    goods: pd.DataFrame,
    all_pairs: gpd.GeoDataFrame,
    producer_loops: List[DeliveryLoop],
    consumer_loops: List[DeliveryLoop],
    custom_loops: Dict[str, List[DeliveryLoop]],
    m: Optional[folium.Map] = None,
    default_lang: str = "de",
):
    """Build the full interactive map.

    Parameters
    ----------
    pois, goods : the source tables (with poi_id / good_id columns).
    all_pairs : GeoDataFrame, every POI->POI pair (both directions), with
        car_geometry/ebike_geometry/car_travel_time/.../ebike_friendliness_route
        columns — this is the single source of truth for ALL route geometry
        and metrics drawn on the map (loops just reference pairs of it).
    producer_loops, consumer_loops : List[DeliveryLoop] from delivery_loops.py.
    custom_loops : {layer_name: List[DeliveryLoop]} from loops.json.
    """
    pois = pois.copy()
    if "poi_id" not in pois.columns:
        pois["poi_id"] = pois.index
    crs = all_pairs.crs if all_pairs is not None and not all_pairs.empty else (pois.crs or 4326)

    center = (
        pois.to_crs(4326).geometry.union_all().centroid.y,
        pois.to_crs(4326).geometry.union_all().centroid.x,
    )
    if m is None:
        m = folium.Map(location=center, zoom_start=14, tiles="CartoDB positron")

    good_name_to_id = _good_name_to_id(goods)

    # ── layers / loops ──────────────────────────────────────────────────────
    layers: Dict[str, List[dict]] = {
        "producer": _export_layer(producer_loops, "producer"),
        "consumer": _export_layer(consumer_loops, "consumer"),
    }
    layer_names: Dict[str, str] = {"producer": "producer_loop", "consumer": "consumer_loop"}
    layer_order: List[str] = ["producer", "consumer"]

    for i, (layer_name, loops_list) in enumerate(custom_loops.items()):
        key = f"custom_{i}"
        layers[key] = _export_layer(loops_list, key)
        layer_names[key] = layer_name
        layer_order.append(key)

    # ── product checkboxes: every good_id referenced by ANY loop's legs ────
    referenced_good_ids = set()
    for loops_list in layers.values():
        for loop in loops_list:
            for leg in loop["ebike_legs"] + loop["car_legs"]:
                referenced_good_ids.update(leg["load_products"])
                referenced_good_ids.update(leg["unload_products"])

    # ── geometry export restricted to pairs that can ever actually appear:
    # every loop leg, plus every direct-substitute connection that could
    # arise from dropping some subset of non-mandatory stops between two
    # other stops. This keeps the exported payload from scaling with
    # len(pois)^2 (which dominated file size when every POI pair was sent).
    reachable_pairs: set = set()
    for loops_list in layers.values():
        reachable_pairs |= _reachable_pairs_for_loops(loops_list)

    custom_layer_keys = [f"custom_{i}" for i in range(len(custom_loops))]

    map_data = {
        "GOODS": _export_goods(goods),
        "POIS": _export_pois(pois, good_name_to_id),
        "PAIRS": _export_all_pairs(all_pairs, only_keys=reachable_pairs),
        "LAYERS": layers,
        "LAYER_NAMES": layer_names,
        "LAYER_ORDER": layer_order,
        "PRODUCT_IDS": sorted(referenced_good_ids),
        "DEFAULT_LANG": default_lang,
        "CUSTOM_LAYER_KEYS": custom_layer_keys,
    }

    # ── translations / column translations ──────────────────────────────────
    translations = {}
    lang_path = os.path.join(DIR_NAME, "languages.json")
    if os.path.exists(lang_path):
        with open(lang_path, "r", encoding="utf-8") as f:
            translations = json.load(f)
    map_data["TRANSLATIONS"] = translations

    # ── inject CSS ───────────────────────────────────────────────────────────
    with open(os.path.join(DIR_NAME, "route_map_styles.css"), "r", encoding="utf-8") as f:
        css_content = f.read()
    m.get_root().html.add_child(folium.Element(f"<style>{css_content}</style>"))

    # ── POI layer (leg polylines are now drawn client-side from MAP_DATA.PAIRS) ──
    _add_poi_markers(m, pois, map_data["GOODS"])

    # ── inject window.MAP_DATA + JS ─────────────────────────────────────────
    m.get_root().html.add_child(folium.Element(
        f"<script>window.MAP_DATA = {json.dumps(map_data)};</script>"
    ))
    with open(os.path.join(DIR_NAME, "route_map_scripts.js"), "r", encoding="utf-8") as f:
        js_content = f.read()
    m.get_root().html.add_child(folium.Element(f"<script>{js_content}</script>"))

    # ── static HTML widgets ──────────────────────────────────────────────────
    m.get_root().html.add_child(folium.Element("""
<div id="langSwitcher" onclick="window.toggleLanguage()" title="Sprache wechseln / Switch Language">
  🌐 <span id="currentLangLabel">DE</span>
</div>
"""))
    m.get_root().html.add_child(folium.Element("""
<div id="topBar">
  <select id="layerDropdown" class="layer-dropdown"></select>
</div>
"""))
    m.get_root().html.add_child(folium.Element("""
<div id="ctrlPanel">
  <h4 data-i18n="filters_title"></h4>
  <div class="sect">
    <div class="sect-title" data-i18n="vehicle"></div>
    <label><input type="checkbox" id="modeEbike" checked> <span data-i18n="ebike_routes"></span></label>
    <label><input type="checkbox" id="modeCar"> <span data-i18n="car_routes"></span></label>
  </div>
  <div class="sect">
    <div class="sect-title" data-i18n="products"></div>
    <div id="prodChecks"></div>
  </div>
</div>
"""))
    m.get_root().html.add_child(folium.Element('<div id="routeTip"></div>'))
    m.get_root().html.add_child(folium.Element("""
<div id="infoBox">
  <b data-i18n="summary_title"></b>
  <div class="ib-row"><span class="ib-label" data-i18n="bike_time"></span><span id="ibBikeTime">—</span></div>
  <div class="ib-row ib-row-stars"><span class="ib-label" data-i18n="bike_quality"></span><span id="ibBikeStars">—</span></div>
  <div class="ib-row"><span class="ib-label" data-i18n="bike_distance"></span><span id="ibBikeDist">—</span></div>
  <hr class="ib-sep">
  <div class="ib-row"><span class="ib-label" data-i18n="car_time"></span><span id="ibCarTime">—</span></div>
  <div class="ib-row"><span class="ib-label" data-i18n="car_distance"></span><span id="ibCarDist">—</span></div>
  <div class="ib-row"><span class="ib-label" data-i18n="car_co2"></span><span id="ibCarCo2">—</span></div>
</div>
"""))
    m.get_root().html.add_child(folium.Element('<div id="goodsDetailBox"></div>'))

    m.get_root().html.add_child(folium.Element("""
<div id="instructionsBtn" onclick="window.openInstructions()" title="Anleitung / Instructions">?</div>
<div id="instructionsOverlay">
  <div id="instructionsModal">
    <h3 data-i18n="instructions_title"></h3>
    <ul>
      <li data-i18n="instructions_item1"></li>
      <li data-i18n="instructions_item2"></li>
      <li data-i18n="instructions_item3"></li>
      <li data-i18n="instructions_item4"></li>
      <li data-i18n="instructions_item5"></li>
    </ul>
    <div class="actions">
      <button onclick="window.closeInstructions()" data-i18n="instructions_close"></button>
    </div>
  </div>
</div>
"""))

    # ── LayerControl (hidden chrome; raster dropdown drives it) ─────────────
    folium.LayerControl(collapsed=False).add_to(m)
    m.get_root().html.add_child(folium.Element(
        "<style>.leaflet-control-layers{display:none !important;}</style>"
    ))

    return m


def poi_map(
    pois: gpd.GeoDataFrame,
    goods: pd.DataFrame,
    image_dict: Optional[dict] = None,
    m: Optional[folium.Map] = None,
    default_lang: str = "de",
) -> folium.Map:
    """Build a POI-only map: raster background layers + POI markers + goods boxes.

    No routes or loops are rendered. A layer dropdown (reusing the same
    #layerDropdown widget the loop map uses) switches which POI group is
    shown:
      - "all": every POI that handles at least one viable good (has both a
        producer and a consumer somewhere in the dataset) — the map's
        original behaviour.
      - "selection": just the restaurant group (blue markers) and the Stift
        Tilbeck group (red markers), regardless of goods viability.
    Product checkboxes further narrow visibility within whichever group is
    active, same as the loop map's product filter but applied directly to
    POIs (no loops here) — see the `LAYER_ORDER.length === 0` branch of
    renderActiveLayer() in route_map_scripts.js.
    """
    pois = pois.copy()
    if "poi_id" not in pois.columns:
        pois["poi_id"] = pois.index

    center = (
        pois.to_crs(4326).geometry.union_all().centroid.y,
        pois.to_crs(4326).geometry.union_all().centroid.x,
    )

    if m is None:
        if image_dict:
            m = image_layer_map(image_dict, add_layer_control=False, opacity=0.6)
        else:
            m = folium.Map(location=center, zoom_start=14, tiles="CartoDB positron")

    good_name_to_id = _good_name_to_id(goods)
    goods_export = _export_goods(goods)

    # All POIs are exported (unfiltered) so either layer can look any of them
    # up client-side; POI_LAYERS below controls which subset is *visible*.
    pois_export_full = _export_pois(pois, good_name_to_id)

    # "all" layer: only expose POIs whose goods have at least one producer
    # AND one consumer in the dataset — orphaned goods (consumed but never
    # produced, or produced but never consumed) clutter the map and can
    # never form a real delivery.
    from collections import defaultdict as _dd
    _producers_of: dict = _dd(set)
    _consumers_of: dict = _dd(set)
    for _pid, _pd in pois_export_full.items():
        for _g in _pd.get("produced", []):
            _producers_of[_g].add(_pid)
        for _g in _pd.get("consumed", []):
            _consumers_of[_g].add(_pid)
    viable_good_ids = sorted(
        _g for _g in set(_producers_of) | set(_consumers_of)
        if _producers_of[_g] and _consumers_of[_g]
    )
    viable_good_id_set = set(viable_good_ids)
    all_layer_ids = [
        pid for pid, data in pois_export_full.items()
        if any(g in viable_good_id_set for g in data.get("produced", []) + data.get("consumed", []))
    ]

    # "selection" layer: restaurants + Stift Tilbeck, no viability filtering
    # (it's a handful of specific POIs, not meant to round-trip goods among
    # just themselves).
    selection_ids = RESTAURANT_POIS | TILBECK_POIS
    selection_layer_ids = [pid for pid in pois_export_full if int(pid) in selection_ids]

    poi_layers = {"all": all_layer_ids, "selection": selection_layer_ids}
    poi_layer_names = {"all": "Alle POIs", "selection": "Restaurants & Stift Tilbeck"}
    poi_layer_order = ["all", "selection"]

    map_data = {
        "GOODS": goods_export,
        "POIS": pois_export_full,
        "PAIRS": {},
        "LAYERS": {},
        "LAYER_NAMES": {},
        "LAYER_ORDER": [],
        "POI_LAYERS": poi_layers,
        "POI_LAYER_NAMES": poi_layer_names,
        "POI_LAYER_ORDER": poi_layer_order,
        "PRODUCT_IDS": viable_good_ids,
        "DEFAULT_LANG": default_lang,
        "CUSTOM_LAYER_KEYS": [],
    }

    translations = {}
    lang_path = os.path.join(DIR_NAME, "languages.json")
    if os.path.exists(lang_path):
        with open(lang_path, "r", encoding="utf-8") as f:
            translations = json.load(f)
    map_data["TRANSLATIONS"] = translations

    with open(os.path.join(DIR_NAME, "route_map_styles.css"), "r", encoding="utf-8") as f:
        css_content = f.read()
    m.get_root().html.add_child(folium.Element(f"<style>{css_content}</style>"))
    # Tilbeck's default marker colour (route_map_styles.css) is pink; make it
    # red so it contrasts with the blue restaurant group in the "selection"
    # layer (harmless in the "all" layer too).
    m.get_root().html.add_child(folium.Element(
        "<style>.poi.poi-tilbeck .poi-inner { background: #fecaca; }</style>"
    ))

    # All markers are always in the DOM (same pattern as the loop map);
    # visibility is toggled client-side via the "pv" class.
    _add_poi_markers(m, pois, goods_export)

    m.get_root().html.add_child(folium.Element(
        f"<script>window.MAP_DATA = {json.dumps(map_data)};</script>"
    ))
    with open(os.path.join(DIR_NAME, "route_map_scripts.js"), "r", encoding="utf-8") as f:
        js_content = f.read()
    m.get_root().html.add_child(folium.Element(f"<script>{js_content}</script>"))

    m.get_root().html.add_child(folium.Element("""
<div id="langSwitcher" onclick="window.toggleLanguage()" title="Sprache wechseln / Switch Language">
  🌐 <span id="currentLangLabel">DE</span>
</div>
"""))
    m.get_root().html.add_child(folium.Element("""
<div id="topBar">
  <select id="layerDropdown" class="layer-dropdown"></select>
</div>
"""))
    m.get_root().html.add_child(folium.Element("""
<div id="ctrlPanel">
  <h4 data-i18n="filters_title"></h4>
  <div class="sect">
    <div class="sect-title" data-i18n="products"></div>
    <div id="prodChecks"></div>
  </div>
</div>
"""))
    m.get_root().html.add_child(folium.Element('<div id="routeTip"></div>'))
    m.get_root().html.add_child(folium.Element('<div id="goodsDetailBox"></div>'))

    folium.LayerControl(collapsed=False).add_to(m)
    m.get_root().html.add_child(folium.Element(
        "<style>.leaflet-control-layers{display:none !important;}</style>"
    ))

    return m