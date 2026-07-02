"""
download_osm_pois.py
====================
Configure and run the OSM POI downloader.

Edit the variables below, then run:

    python download_osm_pois.py

All download logic lives in osm_download.py — import and call
osm_download.run(config) directly if you want to drive it from your own code.
"""

from src import osm_download

# ── Which topics to download ──────────────────────────────────────────────────
# Each topic maps to a list of POI types in osm_poi_config.json.
osm_poi_list = ["food", "drinks", "healthcare"]

# ── Folder containing osm_poi_config.json ────────────────────────────────────
osm_pois_path = "osm_downloader"

# ── AOI source ────────────────────────────────────────────────────────────────
# Path to a GeoPackage/Shapefile that defines the bounding area.
# Set to None to fall back to aoi_place_name (Nominatim geocoding).
aoi_path = "aoi/aoi.gpkg"

# Nominatim place name fallback (used only if aoi_path is None or missing).
aoi_place_name = "Havixbeck, Germany"

# ── Output paths ──────────────────────────────────────────────────────────────
output_pois_path  = "osm_data/points_of_interest.csv"
output_goods_path = "osm_data/goods.csv"

# ─────────────────────────────────────────────────────────────────────────────

config = {
    "topics":          osm_poi_list,
    "config_dir":      osm_pois_path,
    "aoi_path":        aoi_path,
    "aoi_place_name":  aoi_place_name,
    "output_pois":     output_pois_path,
    "output_goods":    output_goods_path,
}

osm_download.run(config)
