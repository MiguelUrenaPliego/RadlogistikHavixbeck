"""
pipeline.py
===========
Callable end-to-end pipeline.  Import and call run(config) directly, or pass
a path to a JSON config file on the command line:

    python pipeline.py config.json

All paths in the config are resolved relative to the current working directory
at call time (same behaviour as main.py).
"""
from __future__ import annotations

import json
import os
import sys
from typing import Dict, List

import geopandas as gpd
import igraph as ig
import numpy as np
import osmnx as ox
import pandas as pd
import shapely
import folium
from shapely.geometry import LineString, MultiLineString

from .data_utils import safe_parse_list, is_list_column, fix_coord, json_serializable, is_missing
from .connections import build_all_pairs_connections, route_connections, enrich_pois
from .delivery_loops import build_producer_loops, build_consumer_loops, build_custom_loops, DeliveryLoop
from .route_map import image_layer_map, route_map, poi_map, green_map, red_map, create_background_layers
import src.routing as routing
import src.osm as osm
import src.co2 as co2


def run(config: dict) -> None:
    """Run the full pipeline with the given config dict."""

    # ── Resolve config sections ──────────────────────────────────────────────
    paths      = config["paths"]
    loop_cfg   = config["loop"]
    car_cfg    = config["car"]
    ebike_cfg  = config["ebike"]
    score_cfg  = config["scoring"]
    bf_cfg     = config["bikefriendliness"]

    pois_path         = paths["pois"]
    goods_path        = paths["goods"]
    aoi_path          = paths["aoi"]
    streets_graph_path = os.path.normpath(paths["streets_graph"])
    osm_xml_file      = os.path.normpath(paths["osm_xml"])
    streets_path      = paths["streets"]
    custom_loops_path = paths["custom_loops"]
    loops_folder      = paths["loops_output"]
    map_folder        = paths["map_output"]
    raster_subfolder  = paths.get("raster_output", "raster_layers")

    RASTER_LAYER_DPI = config.get("raster_dpi", 1500)
    DEFAULT_LANGUAGE = config.get("default_language", "de")

    MAX_STOPS        = loop_cfg["max_stops"]
    MAX_RADIUS       = loop_cfg["max_radius_m"]
    MAX_ADDED_DISTANCE = loop_cfg["max_added_distance_m"]

    car_node_penalty        = car_cfg["node_penalty"]
    car_acceleration        = car_cfg["acceleration"]
    car_min_cruising_time   = car_cfg["min_cruising_time"]
    car_min_cruising_speed  = car_cfg["min_cruising_speed"]
    car_max_stop_and_go_speed = car_cfg["max_stop_and_go_speed"]
    car_stopping_time       = car_cfg["stopping_time"]
    car_maxspeeds           = car_cfg["maxspeeds"]

    ebike_node_penalty         = ebike_cfg["node_penalty"]
    ebike_acceleration         = ebike_cfg["acceleration"]
    ebike_min_cruising_time    = ebike_cfg["min_cruising_time"]
    ebike_min_cruising_speed   = ebike_cfg["min_cruising_speed"]
    ebike_max_stop_and_go_speed = ebike_cfg["max_stop_and_go_speed"]
    bike_stopping_time         = ebike_cfg["stopping_time"]
    _BIKE_AVOID_FACTOR         = ebike_cfg.get("bike_avoid_factor", 50)
    ebike_maxspeeds            = ebike_cfg["maxspeeds"]

    min_bikefriendliness      = score_cfg["min_bikefriendliness"]
    max_travel_time_reduction = score_cfg["max_travel_time_reduction"]
    max_bike_extra_time       = score_cfg["max_bike_extra_time"]
    friendliness_weight       = score_cfg["friendliness_weight"]
    time_weight               = score_cfg["time_weight"]
    product_weight            = score_cfg["product_weight"]

    edge_selection = [
        'highway', 'lanes', 'bike_separation', 'pavement', 'access_restrictions',
        'width', 'junction', 'tunnel', 'area',
        'car_maxspeed', 'car_travel_time', 'car_avg_speed',
        'car_co2', 'ebike_maxspeed',
        'ebike_percieved_travel_time', 'ebike_avg_speed', 'ebike_travel_time',
        'bikefriendliness',
    ]

    # ── Load AOI ─────────────────────────────────────────────────────────────
    aoi = gpd.read_file(aoi_path)
    aoi = aoi.to_crs(aoi.estimate_utm_crs())

    # ── Build / load street graph ─────────────────────────────────────────────
    if os.path.isfile(streets_graph_path):
        G = ox.load_graphml(streets_graph_path)
    else:
        G = ox.graph_from_xml(osm_xml_file)
        G = ox.project_graph(G, to_crs=aoi.crs)
        G = ox.truncate.largest_component(G, strongly=True)
        nodes, edges = ox.graph_to_gdfs(G)

        network_gdf = ox.features_from_xml(filepath=osm_xml_file)

        edges["bike_separation"]    = osm.bike_separation(edges, network_gdf)
        edges["access_restrictions"] = osm.access_restrictions(edges, network_gdf)
        edges["pavement"]           = osm.pavement(edges, network_gdf)
        edges["all_highways"]       = edges["highway"].copy()
        edges["highway"]            = edges["highway"].map(routing.normalize_route_type)
        G = ox.graph_from_gdfs(nodes, edges)
        os.makedirs(streets_path, exist_ok=True)
        ox.save_graphml(G, streets_graph_path)

        aoi_copy = aoi.copy()
        aoi_copy.geometry = aoi_copy.geometry.centroid
        aoi["osmid"] = routing.nearest_nodes(aoi_copy, G)
        node_geom = nodes.geometry.loc[aoi["osmid"]].values
        aoi["distance_to_node"] = aoi.geometry.centroid.distance(
            gpd.GeoSeries(node_geom, index=aoi.index, crs=aoi.crs)
        )
        aoi.to_file(aoi_path)

    nodes, edges = ox.graph_to_gdfs(G)

    # ── Load goods ────────────────────────────────────────────────────────────
    goods = pd.read_csv(goods_path, sep=",")
    list_cols = [col for col in goods.columns if is_list_column(goods[col])]
    for col in list_cols:
        goods[col] = goods[col].apply(safe_parse_list)
    for col in ["Lat", "Lon"]:
        if col in goods.columns:
            goods[col] = goods[col].apply(fix_coord)

    # ── Load POIs ─────────────────────────────────────────────────────────────
    if pois_path.lower().endswith(('.geojson', '.gpkg', '.shp', '.json')):
        pois = gpd.read_file(pois_path)
        pois = pois.to_crs(epsg=4326) if pois.crs else pois.set_crs("EPSG:4326")
    else:
        try:
            pois = pd.read_csv(pois_path, sep=";")
        except Exception:
            pois = pd.read_csv(pois_path, sep=",")

        if "geometry" in pois.columns:
            from shapely import wkt
            pois["geometry"] = pois["geometry"].apply(lambda x: wkt.loads(x) if isinstance(x, str) else x)
            pois = gpd.GeoDataFrame(pois, geometry="geometry", crs=4326)
        elif "Lat" in pois.columns and "Lon" in pois.columns:
            pois["Lat"] = pd.to_numeric(pois["Lat"], errors='coerce')
            pois["Lon"] = pd.to_numeric(pois["Lon"], errors='coerce')
            pois = gpd.GeoDataFrame(pois, geometry=gpd.points_from_xy(pois["Lon"], pois["Lat"]), crs=4326)
        elif "lat" in pois.columns and "lon" in pois.columns:
            pois["lat"] = pd.to_numeric(pois["lat"], errors='coerce')
            pois["lon"] = pd.to_numeric(pois["lon"], errors='coerce')
            pois = gpd.GeoDataFrame(pois, geometry=gpd.points_from_xy(pois["lon"], pois["lat"]), crs=4326)
        elif "latitude" in pois.columns and "longitude" in pois.columns:
            pois["latitude"] = pd.to_numeric(pois["latitude"], errors='coerce')
            pois["longitude"] = pd.to_numeric(pois["longitude"], errors='coerce')
            pois = gpd.GeoDataFrame(pois, geometry=gpd.points_from_xy(pois["longitude"], pois["latitude"]), crs=4326)
        else:
            try:
                pois = gpd.read_file(pois_path)
                pois = pois.to_crs(epsg=4326) if pois.crs else pois.set_crs("EPSG:4326")
            except Exception as e:
                raise ValueError(f"Could not load POIs from {pois_path}: {e}")

    list_cols = [col for col in pois.columns if is_list_column(pois[col])]
    for col in ["ConsumedGoods", "ProducedGoods"]:
        if col in pois.columns and col not in list_cols:
            list_cols.append(col)
    for col in list_cols:
        pois[col] = pois[col].apply(safe_parse_list)

    # Convert good_id ints → product names
    _good_id_to_name = {int(r["good_id"]): str(r["Product"]) for _, r in goods.iterrows()}
    for col in ["ConsumedGoods", "ProducedGoods"]:
        if col in pois.columns:
            pois[col] = pois[col].apply(
                lambda ids: [
                    _good_id_to_name[int(x)]
                    for x in (ids if isinstance(ids, list) else [])
                    if str(x).lstrip("-").isdigit() and int(x) in _good_id_to_name
                ]
            )

    for col in ["Lat", "Lon", "lat", "lon", "latitude", "longitude"]:
        if col in pois.columns:
            pois[col] = pois[col].apply(fix_coord)

    pois = pois.to_crs(aoi.crs)
    pois["osmid"] = routing.nearest_nodes(pois, G)
    node_geom = nodes.geometry.loc[pois["osmid"]].values
    pois["distance_to_node"] = pois.geometry.centroid.distance(
        gpd.GeoSeries(node_geom, index=pois.index, crs=pois.crs)
    )

    too_far = pois["distance_to_node"] > 400
    if too_far.any():
        dropped = pois.loc[too_far, ["poi_id", "Company", "distance_to_node"]].to_string() if "poi_id" in pois.columns else str(pois.loc[too_far, "distance_to_node"])
        print(f"Dropping {too_far.sum()} POI(s) with distance_to_node > 400 m:\n{dropped}")
        pois = pois.loc[~too_far].copy()

    def _to_bool(x):
        if isinstance(x, bool):
            return x
        return str(x).strip().lower() in ("true", "1", "yes", "wahr")

    pois["Mandatory"] = pois["Mandatory"].apply(_to_bool) if "Mandatory" in pois.columns else False

    if "poi_id" not in pois.columns:
        pois["poi_id"] = pois.index
    pois["poi_id"] = pois["poi_id"].astype(int)
    pois = pois.set_index("poi_id", drop=False)

    # ── Validation ────────────────────────────────────────────────────────────
    pois_all  = set(pois["ProducedGoods"].explode().dropna()) | set(pois["ConsumedGoods"].explode().dropna())
    goods_set = set(goods["Product"].dropna())
    if goods_set - pois_all:
        print("WARNING: Products in goods not found in POIs:", goods_set - pois_all)
    if pois_all - goods_set:
        print("WARNING: Products in POIs not found in goods:", pois_all - goods_set)

    # ── 1. All-pairs connections ───────────────────────────────────────────────
    print("Building all-pairs connections …")
    connections_df = build_all_pairs_connections(
        pois=pois, goods=goods,
        filter_b2b=True, filter_radlogistik=True, filter_potenzial_gt0=True,
    )
    if connections_df.empty:
        raise ValueError("No connections found between POIs. Check ConsumedGoods/ProducedGoods columns and goods data.")
    print(f"  → {len(connections_df)} rows "
          f"({connections_df['origin'].nunique()} origins × "
          f"{connections_df['destination'].nunique()} destinations × "
          f"{connections_df['Product'].nunique()} products)")

    os.makedirs(streets_path, exist_ok=True)

    # ── 2. Edge attributes ────────────────────────────────────────────────────
    edges["car_maxspeed"] = routing.infer_maxspeed(edges, car_maxspeeds, enforce=False)
    edges.loc[(edges["pavement"] == "cobblestone") & (edges["car_maxspeed"] > 30), "car_maxspeed"] = 30
    edges.loc[(edges["pavement"] == "unpaved")     & (edges["car_maxspeed"] > 30), "car_maxspeed"] = 20
    edges.loc[(edges["pavement"] == "landuse_unsuitable_for_cars"), "car_maxspeed"] = 0
    edges["car_travel_time"], edges["car_avg_speed"] = routing.travel_time(
        edges=edges,
        acceleration=car_acceleration,
        min_cruising_speed=car_min_cruising_speed,
        min_cruising_time=car_min_cruising_time,
        max_stop_and_go_speed=car_max_stop_and_go_speed,
        node_penalty=car_node_penalty,
        maxspeed_col="car_maxspeed",
        return_speed=True,
    )
    edges["car_co2"] = co2.route_hbefa(
        edges, avg_speed_col="car_avg_speed", vehicle_type="gasoline_pc",
        maxspeed_col="car_maxspeed", return_total=False,
    )
    edges["ebike_maxspeed"] = routing.infer_maxspeed(edges, ebike_maxspeeds, enforce=True)
    edges.loc[(edges["pavement"] == "cobblestone") & (edges["ebike_maxspeed"] > 10), "ebike_maxspeed"] = 10
    edges.loc[(edges["pavement"] == "unpaved")     & (edges["ebike_maxspeed"] > 15), "ebike_maxspeed"] = 15
    edges.loc[(edges["pavement"] == "landuse_unsuitable_for_cars"), "ebike_maxspeed"] = 5
    edges["ebike_travel_time"], edges["ebike_avg_speed"] = routing.travel_time(
        edges=edges,
        acceleration=ebike_acceleration,
        min_cruising_speed=ebike_min_cruising_speed,
        min_cruising_time=ebike_min_cruising_time,
        max_stop_and_go_speed=ebike_max_stop_and_go_speed,
        node_penalty=ebike_node_penalty,
        maxspeed_col="ebike_maxspeed",
        return_speed=True,
    )
    edges["ebike_percieved_travel_time"] = edges["ebike_travel_time"].copy()
    edges["bikefriendliness"] = edges.apply(
        lambda row: routing.compute_bikefriendliness(row, bf_cfg, min_bikefriendliness), axis=1
    )
    r = max_travel_time_reduction
    worst = 1 / (1 - r)
    edges["ebike_percieved_travel_time"] = edges["ebike_travel_time"] * (
        worst - (edges["bikefriendliness"] - 1) * (worst - 1) / 9
    )
    edges.loc[edges["bikefriendliness"] == 0, "ebike_percieved_travel_time"] = (
        edges.loc[edges["bikefriendliness"] == 0, "ebike_travel_time"] * _BIKE_AVOID_FACTOR
    )

    # ── 3. igraph ─────────────────────────────────────────────────────────────
    print("Building igraph …")
    edges_reset = edges.reset_index().copy()
    if "distance" not in edges_reset.columns:
        edges_reset["distance"] = edges_reset.geometry.length
    g = ig.Graph.TupleList(
        edges_reset[["u", "v"]].itertuples(index=False, name=None), directed=True
    )
    node_to_idx = {name: idx for idx, name in enumerate(g.vs["name"])}
    idx_to_node = {idx: name for name, idx in node_to_idx.items()}
    for col in edges_reset.columns:
        if col not in ["u", "v"]:
            g.es[col] = edges_reset[col].tolist()

    # ── 4. Route all pairs ────────────────────────────────────────────────────
    print("Routing all pairs …")
    osmid_to_poi_map: Dict[int, List[int]] = {}
    for idx, r in pois.iterrows():
        osmid_to_poi_map.setdefault(int(r["osmid"]), []).append(int(idx))
    node_coords = {osmid: (pt.x, pt.y) for osmid, pt in nodes.geometry.items()}
    connections_df, all_pairs = route_connections(
        connections_df, g, node_to_idx, idx_to_node,
        return_all_pairs=True, crs=aoi.crs,
        all_poi_osmids=pois["osmid"].tolist(),
        osmid_to_poi_map=osmid_to_poi_map,
        node_coords=node_coords,
    )

    # ── 5. Derived scores ─────────────────────────────────────────────────────
    connections_df["travel_time_difference"] = (
        connections_df["ebike_percieved_travel_time"] - connections_df["car_travel_time"]
    )
    x = (
        connections_df["travel_time_difference"] + bike_stopping_time - car_stopping_time
    ) / (connections_df["car_travel_time"] + car_stopping_time)
    max_t = max_bike_extra_time

    def _time_score(v):
        if v > max_t:  return 0
        if v == max_t: return 1
        if v <= 0:     return 10
        return 10 - 9 * (v / max_t)

    connections_df["ebike_travel_timescore"] = x.apply(_time_score)
    x2 = connections_df["ebike_friendliness_route"]
    connections_df["ebike_route_score"] = np.where(
        x2 <= min_bikefriendliness, 0,
        np.where(x2 >= 10, 10, 1 + 9 * (x2 - min_bikefriendliness) / (10 - min_bikefriendliness))
    )
    connections_df["ebike_product_score"] = connections_df["Potential"]
    connections_df["ebike_score"] = (
        connections_df["ebike_product_score"] * product_weight
        + connections_df["ebike_route_score"] * friendliness_weight
        + connections_df["ebike_travel_timescore"] * time_weight
    ) / 10
    connections_df.loc[
        (connections_df["ebike_product_score"] == 0)
        | (connections_df["ebike_route_score"] == 0)
        | (connections_df["ebike_travel_timescore"] == 0),
        "ebike_score",
    ] = 0

    # ── 5b. Raster background layers + POI map (before routing) ──────────────
    os.makedirs(map_folder, exist_ok=True)
    road_edges = json_serializable(
        edges[edge_selection + ["geometry"]].reset_index(drop=True).copy(), allow_lists=False
    )
    raster_data = {col: road_edges[[col, "geometry"]] for col in edge_selection}
    raster_path = os.path.join(map_folder, raster_subfolder)
    image_dict = create_background_layers(raster_data, raster_path, overwrite=False, dpi=RASTER_LAYER_DPI)

    print("Rendering POI map …")
    m_poi = poi_map(pois=pois, goods=goods, image_dict=image_dict, default_lang=DEFAULT_LANGUAGE)
    poi_map_path = os.path.join(map_folder, "poi_map.html")
    m_poi.save(poi_map_path)
    print(f"  → saved poi_map.html to: {os.path.abspath(poi_map_path)}")

    # ── 6. Delivery loops ─────────────────────────────────────────────────────
    print("Computing producer delivery loops …")
    producer_loops = build_producer_loops(
        connections_df=connections_df, pois=pois, goods=goods,
        max_stops=MAX_STOPS, max_radius_m=MAX_RADIUS,
        max_added_distance_m=MAX_ADDED_DISTANCE, all_pairs=all_pairs,
    )
    print(f"  → {len(producer_loops)} producer loops")

    print("Computing consumer delivery loops …")
    consumer_loops = build_consumer_loops(
        connections_df=connections_df, pois=pois, goods=goods,
        max_stops=MAX_STOPS, max_added_distance_m=MAX_ADDED_DISTANCE,
        all_pairs=all_pairs,
    )
    print(f"  → {len(consumer_loops)} consumer loops")

    # ── 6b. GeoJSON loop export ───────────────────────────────────────────────
    def _loops_to_geodataframe(loops: List[DeliveryLoop], all_pairs: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        if not loops or all_pairs is None or all_pairs.empty:
            crs = all_pairs.crs if all_pairs is not None and not all_pairs.empty else "EPSG:4326"
            return gpd.GeoDataFrame(columns=["geometry"], crs=crs)
        pair_car_geom: Dict = {}
        for _, row in all_pairs.iterrows():
            a, b = int(row["origin_poi_id"]), int(row["destination_poi_id"])
            pair_car_geom[(a, b)] = row.get("car_geometry")
        good_id_to_name = {int(r["good_id"]): str(r["Product"]) for _, r in goods.iterrows()}
        poi_name = {int(idx): str(row.get("Company", f"POI {idx}")) for idx, row in pois.iterrows()}
        records = []
        for i, loop in enumerate(loops):
            if not loop.is_valid:
                continue
            legs = loop.car_legs or []
            parts = []
            for j in range(len(legs) - 1):
                a, b = legs[j]["poi_id"], legs[j + 1]["poi_id"]
                geom = pair_car_geom.get((a, b)) or pair_car_geom.get((b, a))
                if geom is not None and not (hasattr(geom, "is_empty") and geom.is_empty):
                    parts.append(geom)
            geom = MultiLineString([
                list(p.coords) if isinstance(p, LineString) else list(p.geoms[0].coords)
                for p in parts
            ]) if parts else None
            records.append({
                "loop_index": i, "mode": loop.mode,
                "home_poi_id": loop.home_poi_id,
                "home_poi": poi_name.get(loop.home_poi_id, str(loop.home_poi_id)),
                "products": ", ".join(loop.products_covered),
                "car_time_min": round(loop.car_time, 2),
                "ebike_time_min": round(loop.ebike_time, 2),
                "car_dist_km": round(loop.car_distance, 3),
                "ebike_dist_km": round(loop.ebike_distance, 3),
                "stops": len(loop.stop_poi_ids),
                "geometry": geom,
            })
        if not records:
            return gpd.GeoDataFrame(columns=["geometry"], crs=all_pairs.crs)
        return gpd.GeoDataFrame(records, geometry="geometry", crs=all_pairs.crs)

    def _save_loops_geojson(loops: List[DeliveryLoop], path: str) -> None:
        gdf = _loops_to_geodataframe(loops, all_pairs)
        if gdf.empty:
            return
        if gdf.crs and gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs(4326)
        dir_part = os.path.dirname(path)
        if dir_part:
            os.makedirs(dir_part, exist_ok=True)
        gdf.to_file(path, driver="GeoJSON")
        print(f"  → saved {path} ({len(gdf)} loops)")

    # ── 7. Custom loops ───────────────────────────────────────────────────────
    custom_loops_dict: Dict[str, List[DeliveryLoop]] = {}
    if os.path.exists(custom_loops_path):
        try:
            with open(custom_loops_path, "r", encoding="utf-8") as f:
                layers_data = json.load(f)
            for entry in layers_data:
                layer_name = entry["layer_name"]
                custom_loops_dict[layer_name] = build_custom_loops(
                    custom_loops_data=entry["loops"],
                    connections_df=connections_df, pois=pois, goods=goods,
                    all_pairs=all_pairs,
                )
        except Exception as e:
            print(f"Error loading custom loops from {custom_loops_path}: {e}")
    # ── 7b. Restaurant supplier loops (optional, project-specific) ────────────
    restaurant_suppliers_path = paths.get("restaurant_suppliers", "")
    if restaurant_suppliers_path and os.path.exists(restaurant_suppliers_path):
        from .restaurant_loops import build_restaurant_supplier_loops
        print(f"Building restaurant supplier loops from {restaurant_suppliers_path} …")
        generated = build_restaurant_supplier_loops(
            suppliers_csv_path=restaurant_suppliers_path,
            pois=pois, goods=goods,
            connections_df=connections_df, all_pairs=all_pairs,
            max_stops=MAX_STOPS,
        )
        for layer_name, raw_loops in generated.items():
            built = build_custom_loops(
                custom_loops_data=raw_loops,
                connections_df=connections_df, pois=pois, goods=goods,
                all_pairs=all_pairs,
            )
            custom_loops_dict.setdefault(layer_name, [])
            custom_loops_dict[layer_name].extend(built)
            print(f"  → {layer_name}: {len(built)} generated loops")

    total_custom = sum(len(v) for v in custom_loops_dict.values())
    print(f"  → {total_custom} custom loops across {len(custom_loops_dict)} layers")

    # ── 8. Save loops GeoJSON ─────────────────────────────────────────────────
    print("Saving loop GeoJSONs …")
    _save_loops_geojson(producer_loops, os.path.join(loops_folder, "producer_loops.geojson"))
    _save_loops_geojson(consumer_loops, os.path.join(loops_folder, "consumer_loops.geojson"))
    for layer_name, custom_loops in custom_loops_dict.items():
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in layer_name)
        _save_loops_geojson(custom_loops, os.path.join(loops_folder, f"{safe_name}_loops.geojson"))

    # ── 9. Render loop map ────────────────────────────────────────────────────
    print("Rendering loop map …")
    m_routes = image_layer_map(image_dict, add_layer_control=False, opacity=0.6)
    m_routes = route_map(
        pois=pois, goods=goods, all_pairs=all_pairs,
        producer_loops=producer_loops, consumer_loops=consumer_loops,
        custom_loops=custom_loops_dict, m=m_routes,
        default_lang=DEFAULT_LANGUAGE,
    )
    loop_map_path = os.path.join(map_folder, "loop_map.html")
    m_routes.save(loop_map_path)
    print(f"  → saved loop_map.html to: {os.path.abspath(loop_map_path)}")
    print("Done ✓")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python pipeline.py <config.json>")
        sys.exit(1)
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        cfg = json.load(f)
    run(cfg)
