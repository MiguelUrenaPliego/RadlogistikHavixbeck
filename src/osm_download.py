"""
osm_download.py
===============
Callable OSM POI downloader.  Import and call run(config) directly, or pass
a path to a JSON config file on the command line:

    python osm_download.py my_config.json

Config dict keys
----------------
topics          : list[str]   — e.g. ["food", "drinks", "healthcare"]
config_dir      : str         — folder containing osm_poi_config.json
aoi_path        : str | None  — GeoPackage/Shapefile bounding area (or None)
aoi_place_name  : str         — Nominatim fallback if aoi_path is missing
output_pois     : str         — output CSV path for POIs
output_goods    : str         — output CSV path for goods
"""
from __future__ import annotations

import ast
import json
import os
import sys

import geopandas as gpd
import osmnx as ox
import pandas as pd
import requests
from shapely.geometry import Point


# ── Sector mapping: OSM poi_type_key → German sector name ────────────────────

_POI_TYPE_TO_SECTOR: dict[str, str] = {
    "gastronomy": "Gastronomie",
    "bar": "Gastronomie",
    "supermarket": "Lebensmittelhandel",
    "bakery": "Lebensmittelhandel",
    "food_market": "Lebensmittelhandel",
    "drink_shop": "Lebensmittelhandel",
    "winery": "Landwirtschaft",
    "brewery": "Landwirtschaft",
    "beekeeper": "Landwirtschaft",
    "pharmacy": "Medizin",
    "healthcare_provider": "Medizin",
}

# ── Overpass helpers ──────────────────────────────────────────────────────────

_OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]


def _overpass_query(query: str, timeout: int = 120) -> list[dict]:
    for i, url in enumerate(_OVERPASS_URLS, 1):
        try:
            resp = requests.get(
                url,
                params={"data": query},
                timeout=timeout,
                headers={"User-Agent": "osm_download.py"},
            )
            if resp.status_code != 200:
                print(f"  server {i} HTTP {resp.status_code} — trying next")
                continue
            data = resp.json()
            if "elements" not in data:
                print(f"  server {i} missing 'elements' — trying next")
                continue
            return data["elements"]
        except Exception as e:
            print(f"  server {i} error: {e} — trying next")
    raise RuntimeError("All Overpass servers failed.")


def _fetch_poi_type(osm_tags_list: list[dict], bbox_str: str) -> list[dict]:
    parts = []
    for tags in osm_tags_list:
        tag_filter = "".join(f'["{k}"="{v}"]' for k, v in tags.items())
        for elem in ("node", "way", "relation"):
            parts.append(f'{elem}{tag_filter}({bbox_str});')
    query = f'[out:json][timeout:120];\n(\n  ' + "\n  ".join(parts) + f'\n);\nout center;'
    return _overpass_query(query)


def _element_to_point(el: dict) -> tuple[float | None, float | None]:
    if el.get("type") == "node":
        lat, lon = el.get("lat"), el.get("lon")
        if lat is not None and lon is not None:
            return lat, lon
    center = el.get("center")
    if center:
        lat, lon = center.get("lat"), center.get("lon")
        if lat is not None and lon is not None:
            return lat, lon
    geometry = el.get("geometry")
    if geometry:
        lats = [n["lat"] for n in geometry if "lat" in n]
        lons = [n["lon"] for n in geometry if "lon" in n]
        if lats and lons:
            return sum(lats) / len(lats), sum(lons) / len(lons)
    return None, None


def _element_address(tags: dict) -> str:
    parts = []
    street = tags.get("addr:street", "")
    housenumber = tags.get("addr:housenumber", "")
    if street:
        parts.append(f"{street} {housenumber}".strip())
    city = tags.get("addr:city", "") or tags.get("addr:suburb", "")
    postcode = tags.get("addr:postcode", "")
    if postcode or city:
        parts.append(f"{postcode} {city}".strip())
    return ", ".join(p for p in parts if p)


def _element_name(tags: dict, label: str) -> str:
    return tags.get("name") or tags.get("brand") or tags.get("operator") or label


def _good_ids_for(names: list[str], name_to_id: dict[str, int]) -> list[int]:
    return [name_to_id[n] for n in names if n in name_to_id]


def _goods_with_producers(rows: list[dict]) -> set[int]:
    produced: set[int] = set()
    for row in rows:
        for gid in row.get("_produced_ids", []):
            produced.add(gid)
    return produced


def _write_csv(rows: list[dict], path: str, columns: list[str]) -> None:
    dir_part = os.path.dirname(path)
    if dir_part:
        os.makedirs(dir_part, exist_ok=True)
    lines = [";".join(f'"{c}"' for c in columns)]
    for row in rows:
        lines.append(";".join(f'"{row.get(c, "")}"' for c in columns))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  → {path}  ({len(rows)} rows)")


# ── Public API ────────────────────────────────────────────────────────────────

def run(config: dict) -> None:
    """Download OSM POIs and write CSV output files.

    Parameters
    ----------
    config:
        topics          : list[str]
        config_dir      : str
        aoi_path        : str | None
        aoi_place_name  : str
        output_pois     : str
        output_goods    : str
    """
    topics        = config["topics"]
    config_dir    = config["config_dir"]
    aoi_path      = config.get("aoi_path")
    aoi_place_name = config.get("aoi_place_name", "")
    output_pois   = config["output_pois"]
    output_goods  = config["output_goods"]

    poi_config_path = os.path.join(config_dir, "osm_poi_config.json")
    print(f"Loading POI config from {poi_config_path}")
    with open(poi_config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # Load AOI
    if aoi_path and os.path.exists(aoi_path):
        print(f"Loading AOI from {aoi_path}")
        aoi = gpd.read_file(aoi_path)
    else:
        print(f"AOI file not found — geocoding '{aoi_place_name}' via Nominatim")
        aoi = ox.geocode_to_gdf(aoi_place_name)
    aoi = aoi.to_crs(epsg=4326)

    bbox = aoi.total_bounds  # (minx, miny, maxx, maxy)
    bbox_str = f"{bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]}"
    aoi_geom = aoi.union_all()

    name_to_id = {g["name"]: g["good_id"] for g in cfg["goods"]}

    requested_types: set[str] = set()
    for topic in topics:
        for pt in cfg["topic_to_poi_types"].get(topic, []):
            requested_types.add(pt)

    all_rows: list[dict] = []
    seen_osm_ids: set[str] = set()
    seen_coords: set[tuple] = set()

    for poi_type_key in cfg["poi_types"]:
        if poi_type_key not in requested_types:
            continue

        poi_cfg = cfg["poi_types"][poi_type_key]
        print(f"Fetching {poi_type_key} ({poi_cfg['label']}) …")

        try:
            elements = _fetch_poi_type(poi_cfg["osm_tags"], bbox_str)
        except RuntimeError as e:
            print(f"  SKIPPED: {e}")
            continue

        print(f"  {len(elements)} raw elements")

        produced_ids = _good_ids_for(poi_cfg["produces"], name_to_id)
        consumed_ids = _good_ids_for(poi_cfg["consumes"], name_to_id)

        for el in elements:
            osm_id = f"{el.get('type','?')}/{el.get('id','?')}"
            if osm_id in seen_osm_ids:
                continue
            seen_osm_ids.add(osm_id)

            lat, lon = _element_to_point(el)
            if lat is None or lon is None:
                continue
            if not aoi_geom.contains(Point(lon, lat)):
                continue

            coord_key = (round(lat, 5), round(lon, 5))
            if coord_key in seen_coords:
                continue
            seen_coords.add(coord_key)

            tags = el.get("tags", {})
            name = _element_name(tags, poi_cfg["label"])
            address = _element_address(tags)

            all_rows.append({
                "_poi_type": poi_type_key,
                "_produced_ids": produced_ids,
                "_consumed_ids": consumed_ids,
                "Address": address,
                "Lat": str(lat),
                "Lon": str(lon),
                "Sector": poi_type_key,
                "Company": name,
                "Weight": poi_cfg["weight"],
                "MinSize": poi_cfg["min_size"],
                "MaxSize": poi_cfg["max_size"],
                "Size": poi_cfg["size"],
            })

    print(f"\nTotal POIs before producer-filter: {len(all_rows)}")

    available_goods = _goods_with_producers(all_rows)
    print(f"Goods with local producers: {sorted(available_goods)}")

    poi_rows = []
    for i, row in enumerate(all_rows):
        consumed = [gid for gid in row["_consumed_ids"] if gid in available_goods]
        produced = list(row["_produced_ids"])
        poi_rows.append({
            "poi_id": str(i),
            "Address": row["Address"],
            "Lat": row["Lat"],
            "Lon": row["Lon"],
            "Sector": row["Sector"],
            "Company": row["Company"],
            "ConsumedGoods": str(consumed),
            "ProducedGoods": str(produced),
            "Weight": row["Weight"],
            "MinSize": row["MinSize"],
            "MaxSize": row["MaxSize"],
            "Size": row["Size"],
        })

    goods_rows = [
        {
            "good_id": str(g["good_id"]),
            "Product": g["name"],
            "Potential": str(g["potential"]),
            "Weight": g["weight"],
            "Size": g["size"],
            "SpecialFeatures": str(g["special_features"]),
            "Icon": g["icon"],
        }
        for g in cfg["goods"]
    ]

    poi_columns = cfg["poi_csv_columns"]
    goods_columns = ["good_id", "Product", "Potential", "Weight", "Size", "SpecialFeatures", "Icon"]

    print("\nWriting output files …")
    _write_csv(poi_rows, output_pois, poi_columns)
    _write_csv(goods_rows, output_goods, goods_columns)
    print("Done.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python osm_download.py <config.json>")
        sys.exit(1)
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        cfg = json.load(f)
    run(cfg)
