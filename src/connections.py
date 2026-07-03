"""
connections.py
==============
Builds the **all-pairs** connections_df: every POI → every other POI for
every Produkt that appears in *either* POI's Produzierte-Güter OR
Konsumierte-Güter list.

The rule is:
  origin_produkts  = (Produzierte Güter of origin)  ∪ (Konsumierte Güter of origin)
  dest_produkts    = (Produzierte Güter of dest)     ∪ (Konsumierte Güter of dest)
  common_produkts  = origin_produkts ∩ dest_produkts
  → one row per (origin, dest, produkt) for each produkt in common_produkts,
    if both POIs pass the B2B? & Radlogistik? filters and goods.Potenzial > 0.

After pair generation, igraph routes EVERY unique (origin_osmid, dest_osmid)
pair (car + ebike) and merges the results back in one go.

All columns from the old connections_df are preserved so downstream code
(route_map, delivery_loops) works without changes.

UPDATED: all_pairs is now returned as a GeoDataFrame (not a dict) with one row
per POI pair, containing all routing metrics and geometries. This allows
selective visibility control of individual pairs in the map rendering.
"""
from __future__ import annotations

import os
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import geopandas as gpd
import igraph as ig
import numpy as np
import pandas as pd
from shapely.geometry import LineString
from tqdm import tqdm

from .data_utils import safe_parse_list, is_list_column, fix_coord, json_serializable, is_missing

# Sectors that are considered the same group for the same-sector filter.
# A supermarket and a winery are both in the food/beverage supply chain and
# should not be connected for products the destination already produces.
_FOOD_SECTOR_GROUP = frozenset({
    "Lebensmittelhandel",
    "Landwirtschaft",
    # legacy English names (osm_data not yet regenerated)
    "supermarket", "bakery", "food_market", "drink_shop",
    "winery", "brewery", "beekeeper",
})


def _sector_group(sector: str) -> str:
    """Return a normalised group key for same-sector comparison."""
    s = (sector or "").strip()
    return "food" if s in _FOOD_SECTOR_GROUP else s


# ─────────────────────────────────────────────────────────────────────────────
# ALL-PAIRS CONNECTION BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_all_pairs_connections(
    pois: gpd.GeoDataFrame,
    goods: pd.DataFrame,
    filter_b2b: bool = True,
    filter_radlogistik: bool = True,
    filter_potenzial_gt0: bool = True,
) -> pd.DataFrame:
    """Return a DataFrame with one row per (origin POI, dest POI, Produkt) where:
    - origin and dest share at least one Produkt in their combined product lists
      (produced OR consumed)
    - both POIs pass B2B?, Radlogistik? filters
    - goods.Potenzial > 0 for that Produkt

    Self-loops (origin == dest) are excluded.
    """
    goods_lookup: Dict[str, dict] = goods.set_index("Product").to_dict("index")
    goods_set = set(goods_lookup.keys())

    has_b2b = "B2B" in pois.columns
    has_cargo = "CargoBikeFriendly" in pois.columns

    rows = []
    for origin_idx, origin_row in pois.iterrows():
        # Origin must PRODUCE the product — it is the supplier/deliverer
        origin_produced = set(origin_row.get("ProducedGoods") or []) & goods_set

        for dest_idx, dest_row in pois.iterrows():
            if origin_idx == dest_idx:
                continue

            # Destination must CONSUME the product — it is the receiver
            dest_consumed = set(dest_row.get("ConsumedGoods") or []) & goods_set

            # Only create a connection for products that origin produces AND dest consumes
            common = origin_produced & dest_consumed
            if not common:
                continue

            # B2B / Radlogistik filter at pair level (skip if column absent)
            if filter_b2b and has_b2b and not (origin_row.get("B2B") and dest_row.get("B2B")):
                continue
            if filter_radlogistik and has_cargo and not (origin_row.get("CargoBikeFriendly") and dest_row.get("CargoBikeFriendly")):
                continue

            # Same-sector filter: if origin and destination are in the same sector,
            # only allow importing a product that the destination does NOT itself produce.
            # (A POI that both produces and consumes a product self-supplies within its sector.)
            og = _sector_group(origin_row.get("Sector") or "")
            dg = _sector_group(dest_row.get("Sector") or "")
            same_sector = bool(og and og == dg)
            dest_produced = set(dest_row.get("ProducedGoods") or []) & goods_set if same_sector else set()

            for produkt in sorted(common):
                # Skip same-sector deliveries of products the destination already produces
                if same_sector and produkt in dest_produced:
                    continue

                g = goods_lookup[produkt]
                if filter_potenzial_gt0 and (g.get("Potential") or 0) <= 0:
                    continue

                origin_name = (
                    ("Hamlet " + str(origin_row["Hamlet"]) + " - ")
                    if not is_missing(origin_row.get("Hamlet"))
                    else ""
                ) + str(origin_row.get("Company", ""))

                dest_name = (
                    ("Hamlet " + str(dest_row["Hamlet"]) + " - ")
                    if not is_missing(dest_row.get("Hamlet"))
                    else ""
                ) + str(dest_row.get("Company", ""))

                rows.append({
                    "origin": origin_idx,
                    "destination": dest_idx,
                    "origin_osmid": pois.at[origin_idx, "osmid"],
                    "destination_osmid": pois.at[dest_idx, "osmid"],
                    "Start": origin_name,
                    "DestinationName": dest_name,
                    "Product": produkt,
                    "Potential": g.get("Potential"),
                    "Weight": g.get("Weight"),
                    "Size": g.get("Size"),
                    "SpecialFeatures": g.get("SpecialFeatures"),
                    "Icon": g.get("Icon"),
                    "B2B": origin_row.get("B2B") and dest_row.get("B2B"),
                    "CargoBikeFriendly": origin_row.get("CargoBikeFriendly") and dest_row.get("CargoBikeFriendly"),
                })

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.reset_index(drop=True)
    df["route_id"] = df.index
    return df


# ─────────────────────────────────────────────────────────────────────────────
# IGRAPH ROUTING  (all unique osmid pairs)
# ─────────────────────────────────────────────────────────────────────────────

def route_connections(
    connections_df: pd.DataFrame,
    g: ig.Graph,
    node_to_idx: Dict,
    idx_to_node: Dict,
    return_all_pairs: bool = False,
    crs=None,
    all_poi_osmids: Optional[List[int]] = None,
    osmid_to_poi_map: Optional[Dict[int, List[int]]] = None,
    node_coords: Optional[Dict[int, Tuple[float, float]]] = None,
):
    """Run igraph shortest-paths for every unique (origin_osmid, dest_osmid)
    pair and merge results back into connections_df.
    """
    node_coords = node_coords or {}

    if connections_df.empty:
        empty_gdf = gpd.GeoDataFrame(columns=[
            "origin_poi_id", "destination_poi_id", "pair_id",
            "car_travel_time", "car_distance", "car_speed", "car_co2", "car_geometry",
            "ebike_travel_time_orig", "ebike_percieved_travel_time", "ebike_distance",
            "ebike_speed", "ebike_friendliness_route", "ebike_geometry", "geometry",
            "Product", "Potential", "Weight", "Size", "SpecialFeatures"
        ], crs=crs)
        return (connections_df, empty_gdf) if return_all_pairs else connections_df

    if return_all_pairs:
        if all_poi_osmids is not None:
            all_osmids = list(set(all_poi_osmids))
        else:
            all_osmids = list(set(
                connections_df["origin_osmid"].unique().tolist() +
                connections_df["destination_osmid"].unique().tolist()
            ))
        sources_osmid = [s for s in all_osmids if s in node_to_idx]
        all_targets_osmid = sources_osmid
    else:
        sources_osmid = [s for s in connections_df["origin_osmid"].unique() if s in node_to_idx]
        all_targets_osmid = [t for t in connections_df["destination_osmid"].unique() if t in node_to_idx]

    sources = sources_osmid
    all_targets = all_targets_osmid
    target_idx_list = [node_to_idx[t] for t in all_targets]

    def build_linestring(path_edges, start_idx):
        """Concatenate edge geometries in actual traversal order.

        osmnx stores a single (non-reversed) geometry for both directions of
        a two-way street. If we naively concatenate `geom.coords` in path
        order, edges traversed "backward" relative to how their geometry
        was digitized get appended start-to-end instead of end-to-start,
        which makes the resulting LineString jump to the far end of that
        segment and immediately back -- visually this looks like a spurious
        loop/detour. To avoid this we check, for each edge, which endpoint
        we are actually entering it from and reverse the coordinates if
        needed.
        """
        coords = []
        cur_idx = start_idx
        for e in path_edges:
            edge = g.es[e]
            seg = list(edge["geometry"].coords)

            # Determine the node we are leaving from / arriving at for this
            # hop based on the directed edge's actual source/target.
            if edge.source == cur_idx:
                next_idx = edge.target
            else:
                next_idx = edge.source

            cur_osmid = idx_to_node[cur_idx]
            next_osmid = idx_to_node[next_idx]
            cur_xy = node_coords.get(cur_osmid)
            next_xy = node_coords.get(next_osmid)

            if cur_xy is not None and next_xy is not None:
                d_start_cur = (seg[0][0] - cur_xy[0]) ** 2 + (seg[0][1] - cur_xy[1]) ** 2
                d_end_cur = (seg[-1][0] - cur_xy[0]) ** 2 + (seg[-1][1] - cur_xy[1]) ** 2
                if d_end_cur < d_start_cur:
                    seg = seg[::-1]

            # Avoid duplicating the shared vertex between consecutive segments.
            if coords and seg and coords[-1] == seg[0]:
                seg = seg[1:]

            coords.extend(seg)
            cur_idx = next_idx

        return LineString(coords) if coords else None

    results: Dict[Tuple, dict] = {}

    for s_osmid in tqdm(sources, desc="Routing"):
        s_idx = node_to_idx[s_osmid]

        car_paths = g.get_shortest_paths(
            v=s_idx, to=target_idx_list,
            weights="car_perceived_travel_time", output="epath"
        )
        ebike_paths = g.get_shortest_paths(
            v=s_idx, to=target_idx_list,
            weights="ebike_percieved_travel_time", output="epath"
        )

        for i, t_osmid in enumerate(all_targets):
            key = (s_osmid, t_osmid)

            # CAR
            c_path = car_paths[i]
            if c_path:
                c_time = c_dist = c_co2 = 0.0
                for e in c_path:
                    edge = g.es[e]
                    c_time += edge["car_travel_time"]
                    c_dist += edge["distance"]
                    c_co2 += edge["car_co2"]
                c_geom = build_linestring(c_path, s_idx)
            else:
                c_time = c_dist = c_co2 = None
                c_geom = None

            # EBIKE
            e_path = ebike_paths[i]
            if e_path:
                e_time_orig = e_dist = e_perc = e_friend = 0.0
                for e in e_path:
                    edge = g.es[e]
                    dist = edge["distance"]
                    e_time_orig += edge["ebike_travel_time"]
                    e_perc += edge["ebike_percieved_travel_time"]
                    e_dist += dist
                    e_friend += edge["bike_score"] * dist
                e_geom = build_linestring(e_path, s_idx)
                e_friend = e_friend / e_dist if e_dist else None
            else:
                e_time_orig = e_dist = e_perc = e_friend = None
                e_geom = None

            def _safe(op, *vals, check_inf=False):
                if any(v is None for v in vals):
                    return None
                r = op(*vals)
                if check_inf and r is not None and r > 1e6:
                    return None
                return r

            results[key] = {
                "car_travel_time": _safe(lambda x: x / 60, c_time, check_inf=True),
                "car_distance": _safe(lambda x: x / 1000, c_dist),
                "car_speed": _safe(
                    lambda d, t: (d / 1000) / (t / 3600), c_dist, c_time, check_inf=True
                ),
                "car_co2": c_co2,
                "car_geometry": c_geom,
                "ebike_travel_time_orig": _safe(lambda x: x / 60, e_time_orig, check_inf=True),
                "ebike_percieved_travel_time": _safe(lambda x: x / 60, e_perc, check_inf=True),
                "ebike_distance": _safe(lambda x: x / 1000, e_dist),
                "ebike_speed": _safe(
                    lambda d, t: (d / 1000) / (t / 3600), e_dist, e_perc, check_inf=True
                ),
                "ebike_friendliness_route": e_friend,
                "ebike_geometry": e_geom,
            }

    def enrich(row):
        key = (row["origin_osmid"], row["destination_osmid"])
        for k, v in results.get(key, {}).items():
            row[k] = v
        return row

    connections_df = connections_df.apply(enrich, axis=1)

    if not return_all_pairs:
        return connections_df

    # Map each osmid to the FULL list of POI ids that snap to it. Several
    # POIs can share the same nearest street node (e.g. neighbouring shops);
    # collapsing that to a single poi_id per osmid (as a plain dict would)
    # silently drops/misattributes all_pairs rows for every POI but the
    # last one seen at that node. A multimap keeps every real POI in the
    # output, reusing the shared node's travel time for each of them.
    osmid_to_pois: Dict[int, List[int]] = {}
    if osmid_to_poi_map is not None:
        for osmid, poi_val in osmid_to_poi_map.items():
            if isinstance(poi_val, (list, tuple, set)):
                osmid_to_pois.setdefault(osmid, []).extend(int(p) for p in poi_val)
            else:
                osmid_to_pois.setdefault(osmid, []).append(int(poi_val))
    else:
        for _, row in connections_df.iterrows():
            osmid_to_pois.setdefault(row["origin_osmid"], [])
            if int(row["origin"]) not in osmid_to_pois[row["origin_osmid"]]:
                osmid_to_pois[row["origin_osmid"]].append(int(row["origin"]))
            osmid_to_pois.setdefault(row["destination_osmid"], [])
            if int(row["destination"]) not in osmid_to_pois[row["destination_osmid"]]:
                osmid_to_pois[row["destination_osmid"]].append(int(row["destination"]))

    all_pairs_rows = []
    for (s_osmid, t_osmid), metrics in results.items():
        s_pois = osmid_to_pois.get(s_osmid, [])
        t_pois = osmid_to_pois.get(t_osmid, [])
        for s_poi in s_pois:
            for t_poi in t_pois:
                if s_poi == t_poi:
                    continue
                geom = metrics.get("car_geometry") or metrics.get("ebike_geometry")

                matching_conns = connections_df[(connections_df["origin"] == s_poi) & (connections_df["destination"] == t_poi)]
                if not matching_conns.empty:
                    prod_vals = []
                    pot_vals = []
                    gew_vals = []
                    groe_vals = []
                    bes_vals = []
                    for _, conn_row in matching_conns.iterrows():
                        p_val = str(conn_row.get("Product", ""))
                        i_val = str(conn_row.get("Icon", "")) if not is_missing(conn_row.get("Icon")) else ""
                        prod_vals.append(f"{p_val} {i_val}".strip())

                        if not is_missing(conn_row.get("Potential")):
                            pot_vals.append(str(conn_row.get("Potential")))
                        if not is_missing(conn_row.get("Weight")):
                            gew_vals.append(str(conn_row.get("Weight")))
                        if not is_missing(conn_row.get("Size")):
                            groe_vals.append(str(conn_row.get("Size")))
                        if not is_missing(conn_row.get("SpecialFeatures")):
                            bes_vals.append(str(conn_row.get("SpecialFeatures")))

                    produkt_str = ", ".join(prod_vals)
                    potenzial_str = ", ".join(pot_vals)
                    gewicht_str = ", ".join(gew_vals)
                    groesse_str = ", ".join(groe_vals)
                    besonderheiten_str = ", ".join(bes_vals)
                else:
                    produkt_str = ""
                    potenzial_str = ""
                    gewicht_str = ""
                    groesse_str = ""
                    besonderheiten_str = ""

                all_pairs_rows.append({
                    "origin_poi_id": s_poi,
                    "destination_poi_id": t_poi,
                    "origin_osmid": s_osmid,
                    "destination_osmid": t_osmid,
                    "pair_id": f"{s_poi}_{t_poi}",
                    "car_travel_time": metrics.get("car_travel_time"),
                    "car_distance": metrics.get("car_distance"),
                    "car_speed": metrics.get("car_speed"),
                    "car_co2": metrics.get("car_co2"),
                    "car_geometry": metrics.get("car_geometry"),
                    "ebike_travel_time_orig": metrics.get("ebike_travel_time_orig"),
                    "ebike_percieved_travel_time": metrics.get("ebike_percieved_travel_time"),
                    "ebike_distance": metrics.get("ebike_distance"),
                    "ebike_speed": metrics.get("ebike_speed"),
                    "ebike_friendliness_route": metrics.get("ebike_friendliness_route"),
                    "ebike_geometry": metrics.get("ebike_geometry"),
                    "geometry": geom,
                    "Product": produkt_str,
                    "Potential": potenzial_str,
                    "Weight": gewicht_str,
                    "Size": groesse_str,
                    "SpecialFeatures": besonderheiten_str,
                })
    
    all_pairs_gdf = gpd.GeoDataFrame(all_pairs_rows, crs=crs) if all_pairs_rows else gpd.GeoDataFrame(
        columns=[
            "origin_poi_id", "destination_poi_id", "pair_id",
            "car_travel_time", "car_distance", "car_speed", "car_co2", "car_geometry",
            "ebike_travel_time_orig", "ebike_percieved_travel_time", "ebike_distance",
            "ebike_speed", "ebike_friendliness_route", "ebike_geometry", "geometry",
            "Product", "Potential", "Weight", "Size", "SpecialFeatures"
        ],
        crs=crs
    )

    return connections_df, all_pairs_gdf


# ─────────────────────────────────────────────────────────────────────────────
# POI RELATIONSHIP ENRICHMENT
# ─────────────────────────────────────────────────────────────────────────────

def enrich_pois(pois: gpd.GeoDataFrame, connections_df: pd.DataFrame) -> gpd.GeoDataFrame:
    """Add origins/destinations/route_ids/Icon columns to pois from connections_df."""
    pois = pois.copy()
    for col in ["destinations", "destinations_osmid", "origins", "origins_osmid",
                "Suppliers", "Customers", "route_ids"]:
        pois[col] = [[] for _ in range(len(pois))]

    # Load goods icons mapping from goods.csv
    import os
    import csv
    import json
    goods_icons = {}
    goods_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "goods.csv")
    if os.path.exists(goods_path):
        try:
            with open(goods_path, "r", encoding="utf-8") as gf:
                reader = csv.DictReader(gf)
                for r in reader:
                    prod = r.get("Product")
                    icon = r.get("Icon")
                    if prod and icon:
                        goods_icons[prod.strip()] = icon.strip()
        except Exception as e:
            print(f"Error loading goods.csv in enrich_pois: {e}")

    pois["Icon"] = "🏠"
    for i in pois.index:
        row = pois.loc[i]
        icon = None
        
        # 1. Try to find icon from ProducedGoods
        raw_p = row.get("ProducedGoods")
        if raw_p and str(raw_p) != "nan":
            try:
                p_list = json.loads(raw_p) if isinstance(raw_p, str) else raw_p
                if isinstance(p_list, list) and len(p_list) > 0:
                    for p in p_list:
                        if p.strip() in goods_icons:
                            icon = goods_icons[p.strip()]
                            break
            except Exception:
                pass
                
        # 2. Try to find icon from ConsumedGoods
        if not icon:
            raw_c = row.get("ConsumedGoods")
            if raw_c and str(raw_c) != "nan":
                try:
                    c_list = json.loads(raw_c) if isinstance(raw_c, str) else raw_c
                    if isinstance(c_list, list) and len(c_list) > 0:
                        for c in c_list:
                            if c.strip() in goods_icons:
                                icon = goods_icons[c.strip()]
                                break
                except Exception:
                    pass
        
        if icon:
            pois.at[i, "Icon"] = icon

    dest_idx_map = defaultdict(list)
    dest_osmid_map = defaultdict(list)
    dest_name_map = defaultdict(list)
    dest_route_map = defaultdict(list)
    dest_icon_map = defaultdict(list)
    orig_idx_map = defaultdict(list)
    orig_osmid_map = defaultdict(list)
    orig_name_map = defaultdict(list)
    orig_route_map = defaultdict(list)
    orig_icon_map = defaultdict(list)

    for _, row in connections_df.iterrows():
        o = row["origin"]
        d = row["destination"]
        r = row["route_id"]
        icon = row.get("Icon", None)

        dest_idx_map[o].append(d)
        dest_osmid_map[o].append(row["destination_osmid"])
        dest_name_map[o].append(row["DestinationName"])
        dest_route_map[o].append(r)
        if icon is not None:
            dest_icon_map[o].append(str(icon))

        orig_idx_map[d].append(o)
        orig_osmid_map[d].append(row["origin_osmid"])
        orig_name_map[d].append(row["Start"])
        orig_route_map[d].append(r)
        if icon is not None:
            orig_icon_map[d].append(str(icon))

    for i in pois.index:
        pois.at[i, "destinations"] = dest_idx_map[i]
        pois.at[i, "destinations_osmid"] = dest_osmid_map[i]
        pois.at[i, "origins"] = orig_idx_map[i]
        pois.at[i, "origins_osmid"] = orig_osmid_map[i]
        pois.at[i, "Suppliers"] = orig_name_map[i]
        pois.at[i, "Customers"] = dest_name_map[i]
        all_routes = orig_route_map[i] + dest_route_map[i]
        pois.at[i, "route_ids"] = list(set(all_routes))
        # Keep original Icon unchanged

    pois["poi_id"] = pois.index
    # Keep only POIs that appear in at least one connection
    pois = pois[pois["route_ids"].apply(lambda x: len(x) > 0)]
    return pois