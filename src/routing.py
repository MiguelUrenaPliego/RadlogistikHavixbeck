"""
routing.py
==========
OSM and network routing functions: snapped node detection, travel time metrics,
and isochrone computation.
"""

from __future__ import annotations
from typing import Optional, Union, Iterable, Tuple, List, Dict

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
import re
import math 

MAXSPEEDS = {
    "living_street": 30,
    "motorway": 100,
    "motorway_link": 60,
    "primary": 50,
    "primary_link": 50,
    "residential": 30,
    "secondary": 40,
    "secondary_link": 40,
    "service": 20,
    "tertiary": 40,
    "tertiary_link": 40,
    "trunk": 80,
    "trunk_link": 60,
    "unclassified": 40,
}

ROUTE_TYPE_PRIORITY = [
    "motorway",
    "motorway_link",
    "trunk",
    "trunk_link",
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
    "service",
    "living_street",
    "residential",
    "unclassified",
]


def normalize_route_type(route_type, route_type_priority=ROUTE_TYPE_PRIORITY):
    if isinstance(route_type, (list, tuple)):
        return next(
            (rt for rt in route_type_priority if rt in route_type),
            None
        )
    return route_type  # already a single value


def normalize_maxspeed(x) -> float:
    """Normalize OSM maxspeed values into numeric km/h values."""
    if isinstance(x, list):
        vals = [normalize_maxspeed(v) for v in x]
        vals = [v for v in vals if np.isfinite(v)]
        return float(min(vals)) if vals else np.nan

    if x is None or (isinstance(x, float) and np.isnan(x)):
        return np.nan

    if isinstance(x, str):
        x = x.lower().strip()
        x = x.replace(" ", "")
        x = re.sub(r"km/?h|kmh|kph", "", x)
        x = re.sub(r"mph", "", x)

        if ";" in x:
            vals = [normalize_maxspeed(v) for v in x.split(";")]
            vals = [v for v in vals if np.isfinite(v)]
            return float(min(vals)) if vals else np.nan

        match = re.search(r"(\d+(\.\d+)?)", x)
        if match:
            return float(match.group(1))

        return np.nan

    if isinstance(x, (int, float, np.number)):
        return float(x)

    return np.nan


def infer_maxspeed_row(
    highway,
    maxspeeds: dict = MAXSPEEDS,
) -> float:
    """Infer default maxspeed from OSM highway classification."""
    def get_speed(h):
        if pd.isna(h):
            return maxspeeds["unclassified"]
        return maxspeeds.get(h, maxspeeds["unclassified"])

    if isinstance(highway, list):
        vals = [get_speed(h) for h in highway]
        vals = [v for v in vals if not pd.isna(v)]
        if len(vals) == 0:
            return maxspeeds["unclassified"]
        return float(max(vals))

    return float(get_speed(highway))


def infer_maxspeed(
    edges: gpd.GeoDataFrame,
    maxspeeds: Union[Dict[str, float], float] = MAXSPEEDS,
    enforce: bool = False,
    maxspeed_col: str = "maxspeed",
) -> List[float]:
    """Infer maxspeed values from OSM edges and return as a list of floats."""
    if maxspeed_col not in edges.columns:
        values = np.full(len(edges), np.nan, dtype=float)
    else:
        values = edges[maxspeed_col].to_numpy(dtype=object)

    if enforce:
        values = np.array([
            infer_maxspeed_row(h, maxspeeds=maxspeeds)
            for h in edges["highway"]
        ], dtype=float)
    else:
        values = np.array([
            normalize_maxspeed(v)
            for v in values
        ], dtype=float)

        mask = ~np.isfinite(values)
        if mask.any():
            values[mask] = np.array([
                infer_maxspeed_row(h, maxspeeds=maxspeeds)
                for h in edges.loc[mask, "highway"]
            ], dtype=float)

    if not np.all(np.isfinite(values)):
        bad = values[~np.isfinite(values)]
        raise ValueError(f"Invalid maxspeed values detected: {bad[:10].tolist()}")

    return values.tolist()


def travel_time_row(
    L: float,
    vmax_kmh: float,
    a: float = 1,
    min_speed_kmh: float = 10,
    min_time: float = 5,
    stop_speed: float = 50,
    v0: Optional[float] = None,
) -> float:
    """Estimate edge travel time using asymmetric kinematics."""
    vmax = vmax_kmh / 3.6
    vmin = min_speed_kmh / 3.6
    v_stop = stop_speed / 3.6

    if v0 is None:
        v0 = v_stop if vmax_kmh > stop_speed else 0.0
    else:
        v0 = v0 / 3.6

    if vmax_kmh <= stop_speed:
        v1 = 0.0
    else:
        v1 = v_stop

    if vmax <= max(v0, v1):
        if vmax == 0: 
            return 10**20 
        return L / vmax

    d_acc = (vmax**2 - v0**2) / (2 * a)
    d_dec = (vmax**2 - v1**2) / (2 * a)
    d_total = d_acc + d_dec

    if d_total >= L:
        v_peak = np.sqrt(
            (2 * a * L + v0**2 + v1**2) / 2
        )
        return (v_peak - v0) / a + (v_peak - v1) / a

    d_cruise = L - d_total
    t_cruise = d_cruise / vmax

    t_acc = (vmax - v0) / a
    t_dec = (vmax - v1) / a

    if vmax >= vmin and t_cruise < min_time:
        A = 1 / a
        B = min_time
        C = -(L + (v0**2 + v1**2) / (2 * a))
        v_allowed = (-B + np.sqrt(B**2 - 4 * A * C)) / (2 * A)
        vmax_eff = min(v_allowed, vmax)

        d_acc = (vmax_eff**2 - v0**2) / (2 * a)
        d_dec = (vmax_eff**2 - v1**2) / (2 * a)

        if d_acc + d_dec >= L:
            v_peak = np.sqrt((2 * a * L + v0**2 + v1**2) / 2)
            return (v_peak - v0) / a + (v_peak - v1) / a

        d_cruise = L - d_acc - d_dec
        t_acc = (vmax_eff - v0) / a
        t_dec = (vmax_eff - v1) / a
        t_cruise = d_cruise / vmax_eff

        return t_acc + t_cruise + t_dec

    return t_acc + t_cruise + t_dec


def travel_time(
    edges: gpd.GeoDataFrame,
    acceleration: float = 1,
    min_cruising_speed: float = 10,
    min_cruising_time: float = 5,
    max_stop_and_go_speed: float = 50,
    node_penalty: float = 5,
    maxspeed_col: str = "maxspeed",
    length_col: str = "length",
    return_speed: bool = False,
) -> Union[List[float], Tuple[List[float], List[float]]]:
    """Compute travel time and optionally average speed for graph edges."""
    edges = edges.copy()
    edges[maxspeed_col] = edges[maxspeed_col].astype(float)
    edges[length_col] = edges[length_col].astype(float)

    edges["travel_time"] = edges.apply(
        lambda row: travel_time_row(
            row[length_col],
            row[maxspeed_col],
            acceleration,
            min_cruising_speed,
            min_cruising_time,
            max_stop_and_go_speed,
        ) + node_penalty,
        axis=1,
    )

    if return_speed:
        edges["avg_speed"] = (
            (edges[length_col] / 1000)
            / (edges["travel_time"] / 3600)
        )
        return (
            edges["travel_time"].tolist(),
            edges["avg_speed"].tolist(),
        )

    return edges["travel_time"].tolist()


def nearest_nodes(
    geometries: Union[gpd.GeoDataFrame, gpd.GeoSeries],
    G: nx.MultiDiGraph,
    max_dist: Optional[float] = None,
) -> list:
    """Find nearest graph nodes to geometries."""
    nodes = ox.graph_to_gdfs(G, edges=False)
    geom = geometries.to_crs(nodes.crs).copy()
    geom["node_id"] = None

    idx_geom, idx_nodes = nodes.sindex.nearest(
        geom.geometry,
        max_distance=max_dist,
        return_all=False,
    )

    geom.iloc[
        idx_geom,
        geom.columns.get_loc("node_id"),
    ] = list(nodes.index[idx_nodes])

    return list(geom["node_id"])


def nearest_edges(
    geometries: Union[gpd.GeoDataFrame, gpd.GeoSeries],
    G: nx.MultiDiGraph,
    max_dist: Optional[float] = None,
) -> list:
    """Find nearest graph edges to geometries."""
    edges = ox.graph_to_gdfs(G, nodes=False)
    geom = geometries.to_crs(edges.crs).copy()
    geom["edge_id"] = None

    idx_geom, idx_edges = edges.sindex.nearest(
        geom.geometry,
        max_distance=max_dist,
        return_all=False,
    )

    geom.iloc[
        idx_geom,
        geom.columns.get_loc("edge_id"),
    ] = list(edges.index[idx_edges])

    return list(geom["edge_id"])


def geometries_to_nodes(
    G: Union[
        nx.MultiDiGraph,
        gpd.GeoDataFrame,
        Tuple[gpd.GeoDataFrame, gpd.GeoDataFrame],
        Tuple[nx.MultiDiGraph, gpd.GeoDataFrame, gpd.GeoDataFrame],
    ],
    geoms: Union[
        tuple[float, float],
        Point,
        BaseGeometry,
        gpd.GeoSeries,
        gpd.GeoDataFrame,
        list,
        dict,
        int,
    ],
    max_dist: Optional[float] = None,
) -> List[Optional[int]]:
    """Convert heterogeneous geometry inputs into nearest graph node IDs."""
    if isinstance(G, tuple):
        if len(G) == 3:
            G, nodes, edges = G
        elif len(G) == 2:
            nodes, edges = G
            G = ox.graph_from_gdfs(nodes, edges)
    elif isinstance(G, gpd.GeoDataFrame):
        nodes = G
        G = None
    else:
        nodes = ox.graph_to_gdfs(G, edges=False)

    if nodes.crs is None:
        raise ValueError("Nodes GeoDataFrame must have CRS")

    points: List = []

    if isinstance(geoms, dict):
        lat = geoms.get("lat")
        lon = geoms.get("lon")
        if isinstance(lat, (list, tuple)) and isinstance(lon, (list, tuple)):
            points = [Point(xy) for xy in zip(lon, lat)]
        else:
            points = [Point((lon, lat))]
    elif isinstance(geoms, gpd.GeoSeries):
        points = [
            geom if isinstance(geom, Point) else geom.centroid
            for geom in geoms
        ]
    elif isinstance(geoms, gpd.GeoDataFrame):
        geom_col = geoms.geometry
        points = [
            geom if isinstance(geom, Point) else geom.centroid
            for geom in geom_col
        ]
    elif isinstance(geoms, list):
        if any(isinstance(x, (gpd.GeoSeries, gpd.GeoDataFrame)) for x in geoms):
            merged = []
            for x in geoms:
                if isinstance(x, (gpd.GeoSeries, gpd.GeoDataFrame)):
                    merged.append(x)
            if len(merged) > 0:
                unioned = merged[0]
                for m in merged[1:]:
                    unioned = unioned.append(m)
                points = [unioned.union_all().centroid]
        else:
            for g in geoms:
                if isinstance(g, tuple):
                    points.append(Point(g))
                elif isinstance(g, dict):
                    points.append(Point((g["lon"], g["lat"])))
                elif isinstance(g, BaseGeometry):
                    points.append(g if isinstance(g, Point) else g.centroid)
                elif isinstance(g, int):
                    points.append(g)
                else:
                    raise TypeError(f"Unsupported list element: {type(g)}")
    elif isinstance(geoms, tuple):
        if len(geoms) == 2:
            points = [Point(geoms)]
        else:
            raise TypeError("Tuple input must be a single (lon, lat) pair")
    else:
        if isinstance(geoms, int):
            points = [geoms]
        elif isinstance(geoms, BaseGeometry):
            points = [geoms if isinstance(geoms, Point) else geoms.centroid]
        else:
            raise TypeError(f"Unsupported input type: {type(geoms)}")

    s = pd.Series(points)
    is_node = s.apply(lambda x: isinstance(x, (int, np.integer)) and not isinstance(x, bool))

    result = pd.Series(index=s.index, dtype="object")
    result[is_node] = s[is_node]

    def normalize(x):
        if x is None:
            raise TypeError("Geometry is None")
        if isinstance(x, float) and math.isnan(x):
            raise TypeError("Geometry is NaN")
        if isinstance(x, BaseGeometry):
            return x if isinstance(x, Point) else x.centroid
        raise TypeError(f"Unsupported geometry type after parsing: {type(x)}")

    snap_mask = ~is_node
    snap_points = s[snap_mask].apply(normalize)

    if len(snap_points) > 0:
        gdf = gpd.GeoDataFrame(
            geometry=snap_points.tolist(),
            crs="EPSG:4326",
        )
        snapped = nearest_nodes(
            gdf,
            G if G is not None else nodes,
            max_dist=max_dist,
        )
        result.loc[snap_mask] = snapped

    return result.tolist()


def route(
    G: Union[
        nx.MultiDiGraph,
        tuple[gpd.GeoDataFrame, gpd.GeoDataFrame],
        tuple[nx.MultiDiGraph, gpd.GeoDataFrame, gpd.GeoDataFrame],
    ],
    origin: Union[
        gpd.GeoDataFrame,
        gpd.GeoSeries,
        Point,
        tuple[float, float],
        int,
    ],
    destination: Union[
        gpd.GeoDataFrame,
        gpd.GeoSeries,
        Point,
        tuple[float, float],
        int,
    ],
    max_dist: float = 1000,
    travel_time_col: str = "travel_time",
    length_col: str = "length",
):
    """Compute shortest-path route statistics using an OSMnx graph."""
    if isinstance(G, tuple):
        if len(G) == 3:
            G, nodes, edges = G
        elif len(G) == 2:
            nodes, edges = G
            G = ox.graph_from_gdfs(nodes, edges)
        else:
            raise ValueError("Unsupported tuple format for G.")
    else:
        nodes, edges = ox.graph_to_gdfs(G)

    osmids = geometries_to_nodes(
        G,
        [origin, destination],
        max_dist=max_dist,
    )

    route_nodes = ox.shortest_path(
        G,
        osmids[0],
        osmids[1],
        weight=travel_time_col,
    )

    if route_nodes is None:
        raise ValueError("No route found between origin and destination.")

    pairs = pd.DataFrame({
        "u": route_nodes[:-1],
        "v": route_nodes[1:],
    })

    edges_df = edges.reset_index()
    best_edges = (
        edges_df
        .sort_values(travel_time_col)
        .drop_duplicates(subset=["u", "v"], keep="first")
    )

    route_edges = pairs.merge(
        best_edges,
        on=["u", "v"],
        how="left",
    )

    route_edges_gdf = gpd.GeoDataFrame(
        route_edges,
        geometry="geometry",
        crs=edges.crs,
    )

    route_nodes_gdf = nodes.loc[route_nodes].copy()

    total_length = route_edges[length_col].sum()
    total_travel_time = route_edges[travel_time_col].sum()
    avg_speed = (
        (total_length / 1000)
        / (total_travel_time / 3600)
    )

    return (
        total_length / 1000,      # km
        total_travel_time / 60,   # minutes
        avg_speed,                # km/h
        route_nodes_gdf,
        route_edges_gdf,
    )


def isochrone(
    G,
    n,
    cutoff: Optional[float] = None,
    undirected: bool = False,
    travel_time_col: str = "travel_time",
    target: Optional[Union[int, Point, tuple, BaseGeometry]] = None,
    output_col_name: str = "isochrone",
    save_paths: bool = False,
    max_dist: float = 1000,
):
    """Compute isochrone distances from one or multiple source nodes."""
    input_is_graph = isinstance(G, nx.MultiDiGraph)
    input_is_tuple_2 = isinstance(G, tuple) and len(G) == 2
    input_is_tuple_3 = isinstance(G, tuple) and len(G) == 3

    if input_is_tuple_3:
        G, nodes, edges = G
    elif input_is_tuple_2:
        nodes, edges = G
        G = ox.graph_from_gdfs(nodes, edges)

    if not isinstance(n, (list, tuple, set)):
        n = [n]

    source_nodes = geometries_to_nodes(G, n, max_dist=max_dist)

    if target is not None:
        target = geometries_to_nodes(G, [target], max_dist=max_dist)[0]

    G = G.copy()
    H = G.to_undirected() if undirected else G

    if target is None:
        distances, paths = nx.multi_source_dijkstra(
            H,
            source_nodes,
            cutoff=cutoff,
            weight=travel_time_col,
        )
        nx.set_node_attributes(G, distances, output_col_name)
    else:
        distance, path = nx.multi_source_dijkstra(
            H,
            source_nodes,
            target=target,
            cutoff=cutoff,
            weight=travel_time_col,
        )
        paths = {target: path}
        nx.set_node_attributes(
            G,
            {node: distance for node in path},
            output_col_name,
        )

    if save_paths:
        path_df = pd.DataFrame.from_dict(paths, orient="index").stack()
        path_df = path_df.reset_index()
        path_df.columns = ["root", "step", "node"]
        node_paths = (
            path_df.sort_values(["root", "step"])
            .groupby("root")["node"]
            .agg(list)
        )
        nx.set_node_attributes(
            G,
            node_paths.to_dict(),
            output_col_name + "_path",
        )

    if input_is_graph:
        return G

    nodes, edges = ox.graph_to_gdfs(G)
    if input_is_tuple_2:
        return nodes, edges
    if input_is_tuple_3:
        return G, nodes, edges
    return G


def is_missing(value):
    # None
    if value is None:
        return True

    # list-like
    if isinstance(value, (list, tuple, np.ndarray, pd.Series)):
        return len(value) == 0

    # scalar
    return pd.isna(value)


def compute_bikefriendliness(row, bikefriendliness_config, min_bikefriendliness=5):
    """Compute bike friendliness score based on multiple attribute criteria."""
    weighted_sum = 0
    total_weight = 0
    ignored = set()

    # Pass 1: find ignore rules
    for crit_name, crit in bikefriendliness_config.items():
        col = crit["column"]
        value = row[col] if col in row.index else None
        if is_missing(value):
            continue
        ignore_rules = crit.get("ignore", {})
        values_iter = value if isinstance(value, (list, tuple, np.ndarray)) else [value]
        for v in values_iter:
            if v in ignore_rules:
                ignored.update(ignore_rules[v])

    # Pass 2: compute scoring
    for crit_name, criterion in bikefriendliness_config.items():
        if crit_name in ignored:
            continue
        col = criterion["column"]
        if col not in row.index:
            continue
        value = row[col]
        score = score_value(value, criterion)
        if score == 0:
            return 0
        weighted_sum += score * criterion["weight"]
        total_weight += criterion["weight"]

    if total_weight == 0:
        return np.nan

    score = weighted_sum / total_weight
    if score < min_bikefriendliness:
        # Previously this returned a hard 0, which main.py then turned into
        # an effectively infinite travel time -- making the edge completely
        # impassable rather than merely discouraged. Since OSM tags like
        # bike_separation/lanes/access_restrictions are absent on most ways,
        # this ended up blocking nearly all primary/secondary/tertiary roads
        # outright, forcing huge detours even when a short stretch of such
        # a road would have been a perfectly reasonable shortcut.
        #
        # Instead, scale smoothly down toward (but never exactly) 0, so the
        # edge stays heavily penalized -- routers will strongly prefer
        # friendlier alternatives -- but Dijkstra can still fall back to it
        # when doing so avoids an even larger detour.
        return max(0.05, score / min_bikefriendliness)
    else:
        return 1 + (score - min_bikefriendliness) * 9 / (10 - min_bikefriendliness)


def score_value(value, criterion):
    values = criterion["values"]
    default = criterion["default"]
    mode = criterion.get("mode", "categorical")

    if isinstance(value, (list, tuple, set, np.ndarray)):
        if len(value) == 0:
            return default
        scores = []
        for v in value:
            if is_missing(v):
                continue
            if mode == "numeric":
                scores.append(numeric_lookup(v, values))
            else:
                scores.append(values.get(v, default))
        if not scores:
            return default
        list_behaviour = criterion.get("list_behaviour", "max")
        if list_behaviour == "max":
            return max(scores)
        elif list_behaviour == "min":
            return min(scores)
        else:
            return sum(scores) / len(scores)

    if is_missing(value):
        return default

    if mode == "numeric":
        return numeric_lookup(value, values)
    return values.get(value, default)


def numeric_lookup(value, mapping):
    if is_missing(value):
        return None
    try:
        v = float(value)
    except:
        return None
    keys = sorted(mapping.keys())
    valid_keys = [k for k in keys if isinstance(k, (int, float))]
    best = None
    for k in valid_keys:
        if v >= k:
            best = k
        else:
            break
    if best is None:
        return mapping[valid_keys[0]]
    return mapping[best]