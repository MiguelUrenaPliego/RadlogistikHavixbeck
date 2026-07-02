"""
delivery_loops.py (REFACTORED)
==============================
Given the precomputed all-pairs connections_df (every POI→POI × Produkt with
car and ebike travel times already filled in by igraph routing), compute the
optimal delivery loops for:

  - Producer mode: for each (producer, Produkt) pair, find the best round-trip
    loop visiting ALL eligible consumers of that product within MAX_RADIUS.
    NO SPLITTING — one optimal TSP solution per (producer, product) pair.
    
  - Consumer mode: for each consumer, find the best round-trip loop visiting
    enough producers to cover every product the consumer needs. NO RADIUS LIMIT.
    NO SPLITTING — one optimal set-cover TSP solution per consumer.

Optimality = minimum total travel time (separately for ebike and car).

Key changes from original:
  1. REMOVED _farthest_pair_split() and _build_split_loops() — these were
     splitting optimal TSP solutions arbitrarily, leading to suboptimal final
     routes and confusing multi-loop setups for single delivery needs.
  2. REMOVED MAX_ADDED_DISTANCE optimization heuristic — no longer deciding to
     split loops based on incremental distance.
  3. Producer loops: visit ALL eligible consumers within MAX_RADIUS in ONE loop.
  4. Consumer loops: visit enough producers to cover ALL needed products in ONE
     loop, with NO radius limit (can include far-away producers if optimal).
  5. Ebike always reuses the same stops/order as car version (as before).

Returns a list of Loop objects, each carrying loop orders and routing metrics.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
import geopandas as gpd

from .loop_solver import solve_producer_loop, solve_consumer_loop, LoopSolverError
from .loop_format import (
    legs_for_producer_loop,
    legs_for_consumer_loop,
    is_valid_loop,
    leg_pair_ids,
    build_good_id_lookup,
)


@dataclass
class DeliveryLoop:
    mode: str                          # "producer" | "consumer" | "custom"
    home_poi_id: int
    stop_poi_ids: List[int]            # candidate stops considered (unordered)
    produkt: Optional[str]             # producer mode: which product (name, legacy/debug only)
    products_covered: List[str]        # consumer mode: which products covered (names, legacy/debug only)
    ebike_poi_ids: List[int]           # ordered loop incl. return: [home, ..., home]
    car_poi_ids: List[int]             # ordered loop incl. return: [home, ..., home]
    ebike_pair_ids: List[str]          # consecutive "origin_destination" pair_ids
    car_pair_ids: List[str]            # consecutive "origin_destination" pair_ids
    ebike_time: float                  # minutes, total loop
    car_time: float
    ebike_distance: float              # km
    car_distance: float
    # Unified leg format (the single source of truth for rendering): each is
    # a list of {poi_id, load_products (good_id list), unload_products
    # (good_id list), mandatory} dicts, ordered start..end (closing back to
    # home). May differ between vehicles since car/ebike orderings can differ.
    ebike_legs: Optional[List[dict]] = None
    car_legs: Optional[List[dict]] = None
    stops_metadata: Optional[List[dict]] = None

    @property
    def is_valid(self) -> bool:
        """A loop must be structurally valid (>=2 legs) for AT LEAST one
        vehicle's leg list to be worth keeping on the map."""
        e_ok = self.ebike_legs is not None and is_valid_loop(self.ebike_legs)
        c_ok = self.car_legs is not None and is_valid_loop(self.car_legs)
        return e_ok or c_ok


def _build_time_matrix(
    poi_ids: List[int],          # [home_id, stop1_id, stop2_id, ...]
    pair_times: Dict[Tuple[int, int], float],
    fill: float = float("inf"),
) -> np.ndarray:
    n = len(poi_ids)
    mat = np.full((n, n), fill)
    for i, a in enumerate(poi_ids):
        for j, b in enumerate(poi_ids):
            if i != j:
                mat[i][j] = pair_times.get((a, b), fill)
    return mat


def _pair_time_dict(
    connections_df: pd.DataFrame,
    time_col: str,
    all_pairs: Optional[Union[Dict, gpd.GeoDataFrame]] = None,
) -> Dict[Tuple[int, int], float]:
    """Build (origin_poi_id, destination_poi_id) -> time mapping."""
    d: Dict[Tuple[int, int], float] = {}
    
    if all_pairs is not None:
        if isinstance(all_pairs, gpd.GeoDataFrame):
            if not all_pairs.empty:
                for _, row in all_pairs.iterrows():
                    a_id = int(row["origin_poi_id"])
                    b_id = int(row["destination_poi_id"])
                    t = row.get(time_col)
                    if t is not None and not (isinstance(t, float) and (np.isnan(t) or np.isinf(t))):
                        d[(a_id, b_id)] = float(t)
        else:
            for key, metrics in all_pairs.items():
                t = metrics.get(time_col)
                if t is not None and not (isinstance(t, float) and (np.isnan(t) or np.isinf(t))):
                    d[key] = float(t)
    
    for _, row in connections_df.iterrows():
        key = (int(row["origin"]), int(row["destination"]))
        t = row.get(time_col)
        if t is None or (isinstance(t, float) and np.isnan(t)):
            continue
        if key not in d or t < d[key]:
            d[key] = float(t)
    return d


def _pair_dist_dict(
    connections_df: pd.DataFrame,
    dist_col: str,
    all_pairs: Optional[Union[Dict, gpd.GeoDataFrame]] = None,
) -> Dict[Tuple[int, int], float]:
    """Build (origin_poi_id, destination_poi_id) -> distance mapping."""
    d: Dict[Tuple[int, int], float] = {}
    
    if all_pairs is not None:
        if isinstance(all_pairs, gpd.GeoDataFrame):
            if not all_pairs.empty:
                for _, row in all_pairs.iterrows():
                    a_id = int(row["origin_poi_id"])
                    b_id = int(row["destination_poi_id"])
                    v = row.get(dist_col)
                    if v is not None and not (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
                        d[(a_id, b_id)] = float(v)
        else:
            for key, metrics in all_pairs.items():
                v = metrics.get(dist_col)
                if v is not None and not (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
                    d[key] = float(v)
    
    for _, row in connections_df.iterrows():
        key = (int(row["origin"]), int(row["destination"]))
        v = row.get(dist_col)
        if v is None or (isinstance(v, float) and np.isnan(v)):
            continue
        if key not in d or v < d[key]:
            d[key] = float(v)
    return d


def _ensure_closed_legs(home: int, legs: List[dict]) -> List[dict]:
    """Guarantee a leg list starts AND ends at `home`, regardless of what the
    leg-building helpers (legs_for_producer_loop / legs_for_consumer_loop)
    produced. Loops that don't return to their starting POI aren't loops —
    this is the structural backstop so a bug or edge case upstream can never
    silently ship an open route to the map."""
    if not legs:
        return legs
    if legs[0]["poi_id"] != home:
        legs = [{"poi_id": home, "load_products": [], "unload_products": [], "mandatory": True}] + legs
    if legs[-1]["poi_id"] != home:
        legs = legs + [{"poi_id": home, "load_products": [], "unload_products": [], "mandatory": True}]
    return legs


def _order_to_poi_and_pair_ids(
    order: List[int],
    poi_ids: List[int],
) -> Tuple[List[int], List[str]]:
    ordered = [poi_ids[i] for i in order]
    pair_ids = [f"{ordered[i]}_{ordered[i + 1]}" for i in range(len(ordered) - 1)]
    return ordered, pair_ids


def _solve_tsp_path(home: int, stops: List[int], time_dict: Dict[Tuple[int, int], float],
                    max_stops: int) -> Tuple[List[int], float]:
    """Exact TSP path (home -> visit every stop -> home), minimizing total
    time. Returns (poi_id order incl. closing home, total_time)."""
    if not stops:
        return [home, home], 0.0
    poi_ids = [home] + stops
    mat = _build_time_matrix(poi_ids, time_dict)
    try:
        order, total_time = solve_producer_loop(mat, max_stops=max_stops)
    except LoopSolverError:
        order = []
    if not order:
        # Infeasible (e.g. no routed time in one direction) — fall back to
        # visiting every stop in a fixed order rather than silently
        # dropping them from the loop.
        order = list(range(len(poi_ids))) + [0]
        total_time = sum(
            time_dict.get((poi_ids[order[i]], poi_ids[order[i + 1]]), 1.0)
            for i in range(len(order) - 1)
        )
    poi_seq, _ = _order_to_poi_and_pair_ids(order, poi_ids)
    return poi_seq, total_time


def _path_distance(poi_seq: List[int], dist_dict: Dict[Tuple[int, int], float]) -> float:
    total = 0.0
    for i in range(len(poi_seq) - 1):
        total += dist_dict.get((poi_seq[i], poi_seq[i + 1]), 0.0)
    return total


def build_producer_loops(
    connections_df: pd.DataFrame,
    pois: pd.DataFrame,
    goods: pd.DataFrame,
    max_stops: int = 12,
    max_radius_m: float = 5000.0,
    max_added_distance_m: float = 250.0,  # DEPRECATED — no longer used
    all_pairs: Optional[Union[Dict, gpd.GeoDataFrame]] = None,
) -> List[DeliveryLoop]:
    """For each (producer POI, product), build the minimum-distance delivery
    loop visiting all its eligible consumers.

    Algorithm (distances always from the real routed car/ebike linestrings
    in all_pairs/connections_df, never straight-line):
      1. Candidate consumers = every consumer of that product within
         max_radius_m (car distance) of the producer.
      2. If a producer has zero candidates within radius, force-include its
         single closest consumer of that product regardless of distance —
         every producer must end up with at least one loop.
      3. Solve the exact TSP path producer -> ALL candidates -> producer
         (car distances decide the stop set/order). This is now a SINGLE
         optimal loop per (producer, product) — NO SPLITTING.
      4. The ebike version of each loop reuses EXACTLY the same stops/order
         as the car version that decided it — only the ebike time/distance
         metrics differ.

    NOTE: max_added_distance_m is deprecated and ignored. Loops are no longer
    split based on distance penalties.
    """
    ebike_times = _pair_time_dict(connections_df, "ebike_travel_time_orig", all_pairs)
    car_times   = _pair_time_dict(connections_df, "car_travel_time", all_pairs)
    ebike_dists = _pair_dist_dict(connections_df, "ebike_distance", all_pairs)
    car_dists   = _pair_dist_dict(connections_df, "car_distance", all_pairs)
    good_id_of  = build_good_id_lookup(goods)

    max_radius_km = max_radius_m / 1000.0

    loops: List[DeliveryLoop] = []

    def make_loop(home: int, stop_order_car: List[int], prod: str) -> Optional[DeliveryLoop]:
        """Build a single DeliveryLoop for this producer+product+stops combo."""
        car_seq, car_time = _solve_tsp_path(home, stop_order_car, car_times, max_stops)
        # ebike reuses the SAME stop set/order the car solve produced —
        # only recompute ebike metrics for that fixed sequence.
        ebike_seq = car_seq
        ebike_time = sum(
            ebike_times.get((ebike_seq[i], ebike_seq[i + 1]), 1.0)
            for i in range(len(ebike_seq) - 1)
        )
        car_dist = _path_distance(car_seq, car_dists)
        ebike_dist = _path_distance(ebike_seq, ebike_dists)

        car_poi_seq, car_pair_ids = car_seq, [f"{car_seq[i]}_{car_seq[i+1]}" for i in range(len(car_seq) - 1)]
        ebike_poi_seq, ebike_pair_ids = ebike_seq, [f"{ebike_seq[i]}_{ebike_seq[i+1]}" for i in range(len(ebike_seq) - 1)]

        good_id = good_id_of.get(prod)
        if good_id is None:
            return None
        car_legs = _ensure_closed_legs(home, legs_for_producer_loop(home, car_poi_seq[:-1], good_id))
        ebike_legs = _ensure_closed_legs(home, legs_for_producer_loop(home, ebike_poi_seq[:-1], good_id))

        return DeliveryLoop(
            mode="producer",
            home_poi_id=home,
            stop_poi_ids=stop_order_car,
            produkt=prod,
            products_covered=[prod],
            ebike_poi_ids=ebike_poi_seq,
            car_poi_ids=car_poi_seq,
            ebike_pair_ids=ebike_pair_ids,
            car_pair_ids=car_pair_ids,
            ebike_time=ebike_time,
            car_time=car_time,
            ebike_distance=ebike_dist,
            car_distance=car_dist,
            ebike_legs=ebike_legs,
            car_legs=car_legs,
        )

    # Build real consumed/produced sets from POI data (source of truth).
    real_consumed: Dict[int, set] = {}  # consumer_poi_id -> set of product names it actually consumes
    real_produced_by: Dict[str, List[int]] = {}  # product_name -> [producer_poi_ids]
    for poi_id, poi_row in pois.iterrows():
        poi_id = int(poi_id)
        consumed = poi_row.get("ConsumedGoods") or []
        real_consumed[poi_id] = {g for g in consumed if g and str(g) != "nan"}
        produced = poi_row.get("ProducedGoods") or []
        for prod_name in produced:
            if prod_name and str(prod_name) != "nan":
                real_produced_by.setdefault(str(prod_name), []).append(poi_id)

    # Group connections_df by (origin=producer, Product) to get, for each
    # producer+product, the exact set of eligible consumers (already
    # filtered for B2B/CargoBikeFriendly/Potential by connections.py).
    # Cross-check against real_consumed to ensure consumers actually need
    # this product per points_of_interest.csv — the loop must only stop at
    # POIs that genuinely consume what the producer is delivering.
    by_producer_product: Dict[Tuple[int, str], List[int]] = {}
    for _, row in connections_df.iterrows():
        producer = int(row["origin"])
        consumer = int(row["destination"])
        prod = str(row["Product"])
        if prod not in real_consumed.get(consumer, set()):
            continue  # skip: consumer doesn't actually list this product as consumed
        by_producer_product.setdefault((producer, prod), []).append(consumer)

    producers_handled: set = set()

    for (producer, prod), consumers in by_producer_product.items():
        consumers = sorted(set(consumers))
        if not consumers:
            continue

        # Filter to MAX_RADIUS
        within_radius = [
            c for c in consumers
            if car_dists.get((producer, c), float("inf")) <= max_radius_km
        ]
        candidates = within_radius
        if not candidates:
            # Force at least one loop: closest consumer regardless of radius.
            closest = min(consumers, key=lambda c: car_dists.get((producer, c), float("inf")), default=None)
            if closest is None:
                continue
            candidates = [closest]

        # ✓ SOLVE TSP ONCE with ALL candidates (no splitting)
        loop = make_loop(producer, candidates, prod)
        if loop is not None and loop.is_valid:
            loops.append(loop)
            producers_handled.add(producer)

    # ── COVERAGE GUARANTEE ────────────────────────────────────────────────
    # Every (consumer POI, product) pair from points_of_interest.csv must be
    # reachable via at least one producer loop.  After the main loop above,
    # some consumers may still be uncovered because they were outside every
    # producer's radius, or not in connections_df at all.  Find those gaps
    # and add a direct producer→consumer loop for each one.
    covered_consumer_product: set = set()  # (consumer_poi_id, product_name)
    for loop in loops:
        prod_name = loop.produkt
        if not prod_name:
            continue
        good_id = good_id_of.get(prod_name)
        if good_id is None:
            continue
        for leg in (loop.car_legs or []):
            if good_id in leg.get("unload_products", []):
                covered_consumer_product.add((leg["poi_id"], prod_name))

    for c_id, c_row in pois.iterrows():
        c_id = int(c_id)
        consumed = c_row.get("ConsumedGoods") or []
        for prod_name in consumed:
            if not prod_name or str(prod_name) == "nan":
                continue
            if (c_id, prod_name) in covered_consumer_product:
                continue
            # Find nearest producer of this product (no radius cap here)
            producer_ids = real_produced_by.get(prod_name, [])
            best_producer = min(
                (p for p in producer_ids if p != c_id),
                key=lambda p: car_dists.get((p, c_id), float("inf")),
                default=None,
            )
            if best_producer is None:
                continue
            loop = make_loop(best_producer, [c_id], prod_name)
            if loop is not None and loop.is_valid:
                loops.append(loop)
                producers_handled.add(best_producer)
                covered_consumer_product.add((c_id, prod_name))

    # Safety net: any producer with products but that ended up with zero
    # loops (e.g. no eligible consumers passed connections.py's B2B/
    # CargoBike/Potential filters at all) still gets forced a loop to its
    # single closest consumer of that product.
    for p_id, p_row in pois.iterrows():
        p_id = int(p_id)
        if p_id in producers_handled:
            continue
        p_goods = p_row.get("ProducedGoods") or []
        if not isinstance(p_goods, list) or not p_goods:
            continue
        prod = next((g for g in p_goods if g and str(g) != "nan"), None)
        if prod is None:
            continue
        # Closest POI that actually consumes this product
        consumers_of_prod = [
            int(idx) for idx in pois.index
            if int(idx) != p_id and prod in real_consumed.get(int(idx), set())
        ]
        closest_c = min(
            consumers_of_prod,
            key=lambda c: car_dists.get((p_id, c), float("inf")),
            default=None,
        )
        if closest_c is None:
            # No real consumer exists for this product — skip (no bogus loop)
            continue
        loop = make_loop(p_id, [closest_c], prod)
        if loop is not None and loop.is_valid:
            loops.append(loop)
            producers_handled.add(p_id)

    return loops


def _solve_consumer_set_cover(
    home: int, candidates: List[int], stop_product_names: Dict[int, List[str]],
    needed_products: set, good_id_of: Dict[str, int], time_dict: Dict[Tuple[int, int], float],
    max_stops: int,
) -> Tuple[List[int], float, set]:
    """Run the exact set-cover+TSP solver for `home` needing `needed_products`,
    choosing among `candidates` (each supplying stop_product_names[c]).
    Returns (poi_seq incl. closing home, total_time, products_actually_covered)."""
    if not candidates:
        return [home, home], 0.0, set()
    poi_ids = [home] + candidates
    stop_masks = []
    needed_list = sorted(needed_products)
    needed_index = {p: i for i, p in enumerate(needed_list)}
    needed_mask = (1 << len(needed_list)) - 1
    for c in candidates:
        mask = 0
        for p in stop_product_names.get(c, []):
            if p in needed_index:
                mask |= 1 << needed_index[p]
        stop_masks.append(mask)
    mat = _build_time_matrix(poi_ids, time_dict)
    try:
        order, total_time, covered_mask = solve_consumer_loop(
            mat, stop_masks, needed_mask, max_stops=max_stops
        )
    except LoopSolverError:
        order, total_time, covered_mask = [], float("inf"), 0
    if not order:
        # Fallback: just visit every candidate (no solver success).
        poi_seq = [home] + candidates + [home]
        total_time = sum(
            time_dict.get((poi_seq[i], poi_seq[i + 1]), 1.0) for i in range(len(poi_seq) - 1)
        )
        covered = set()
        for c in candidates:
            covered.update(stop_product_names.get(c, []))
        return poi_seq, total_time, covered & needed_products

    poi_seq, _ = _order_to_poi_and_pair_ids(order, poi_ids)
    covered_products = {needed_list[i] for i in range(len(needed_list)) if covered_mask & (1 << i)}
    return poi_seq, total_time, covered_products


def build_consumer_loops(
    connections_df: pd.DataFrame,
    pois: pd.DataFrame,
    goods: pd.DataFrame,
    max_stops: int = 12,
    max_added_distance_m: float = 250.0,  # DEPRECATED — no longer used
    all_pairs: Optional[Union[Dict, gpd.GeoDataFrame]] = None,
) -> List[DeliveryLoop]:
    """For each consumer POI, build the minimum-distance loop covering
    every product it needs.

    Algorithm (distances always from the real routed car/ebike linestrings
    in all_pairs/connections_df, never straight-line; NO radius cap here):
      1. Candidate producers = every producer of any product the consumer
         needs (from connections_df, already B2B/CargoBike/Potential
         filtered). NO RADIUS LIMIT — can consider far-away producers.
      2. Solve the exact set-cover+TSP path (consumer -> enough producers to
         cover every needed product -> consumer), minimizing distance. This is
         now a SINGLE optimal loop per consumer — NO SPLITTING.
      3. Every consumer must end up with all its needed products covered —
         if the exact solver can't reach full coverage within max_stops,
         top up with extra loops for whatever remains uncovered.
      4. The ebike version of each loop reuses EXACTLY the same stops/order
         as the car version that decided it.

    NOTE: max_added_distance_m is deprecated and ignored. Loops are no longer
    split based on distance penalties.
    """
    ebike_times = _pair_time_dict(connections_df, "ebike_travel_time_orig", all_pairs)
    car_times   = _pair_time_dict(connections_df, "car_travel_time", all_pairs)
    ebike_dists = _pair_dist_dict(connections_df, "ebike_distance", all_pairs)
    car_dists   = _pair_dist_dict(connections_df, "car_distance", all_pairs)
    good_id_of  = build_good_id_lookup(goods)

    loops: List[DeliveryLoop] = []

    # producer -> [product names it supplies], from connections_df (already
    # B2B/CargoBikeFriendly/Potential filtered) grouped by destination.
    producer_products_for: Dict[int, Dict[int, List[str]]] = {}  # consumer -> {producer: [products]}
    consumer_needed: Dict[int, set] = {}
    for _, row in connections_df.iterrows():
        consumer = int(row["destination"])
        producer = int(row["origin"])
        prod = str(row["Product"])
        producer_products_for.setdefault(consumer, {}).setdefault(producer, [])
        if prod not in producer_products_for[consumer][producer]:
            producer_products_for[consumer][producer].append(prod)
        consumer_needed.setdefault(consumer, set()).add(prod)

    def make_loop(home: int, stops: List[int], products_loaded: Dict[int, List[str]],
                  covered: set, valid_candidates: List[int], needed_products: set) -> Optional[DeliveryLoop]:
        """Build a single DeliveryLoop for this consumer+stops combo."""
        # Defensive sanitization: only ever build a loop from stops that are
        # real candidate producers for THIS consumer and actually carry a
        # product this loop claims to cover. This makes any upstream
        # indexing slip (e.g. a stale/mismatched stop list) structurally
        # harmless instead of silently producing a wrong loop. `valid_candidates`
        # and `needed_products` are passed explicitly (not read from outer
        # loop variables) to avoid a late-binding closure bug where they'd
        # otherwise resolve to whichever consumer was processed LAST.
        valid_candidate_set = set(valid_candidates)
        stops = [s for s in stops if s in valid_candidate_set]
        if not stops:
            return None
        products_loaded = {
            pid: [n for n in names if n in needed_products]
            for pid, names in products_loaded.items()
            if pid in stops
        }
        products_loaded = {pid: names for pid, names in products_loaded.items() if names}
        if not products_loaded:
            return None

        car_seq, car_time = _solve_tsp_path(home, stops, car_times, max_stops)
        ebike_seq = car_seq  # car decides stops; ebike reuses them exactly
        ebike_time = sum(
            ebike_times.get((ebike_seq[i], ebike_seq[i + 1]), 1.0)
            for i in range(len(ebike_seq) - 1)
        )
        car_dist = _path_distance(car_seq, car_dists)
        ebike_dist = _path_distance(ebike_seq, ebike_dists)

        car_poi_seq, car_pair_ids = car_seq, [f"{car_seq[i]}_{car_seq[i+1]}" for i in range(len(car_seq) - 1)]
        ebike_poi_seq, ebike_pair_ids = ebike_seq, [f"{ebike_seq[i]}_{ebike_seq[i+1]}" for i in range(len(ebike_seq) - 1)]

        stop_good_ids = {
            pid: [good_id_of[n] for n in names if n in good_id_of]
            for pid, names in products_loaded.items()
        }
        car_legs = _ensure_closed_legs(home, legs_for_consumer_loop(home, car_poi_seq[:-1], stop_good_ids))
        ebike_legs = _ensure_closed_legs(home, legs_for_consumer_loop(home, ebike_poi_seq[:-1], stop_good_ids))

        return DeliveryLoop(
            mode="consumer",
            home_poi_id=home,
            stop_poi_ids=stops,
            produkt=None,
            products_covered=sorted(covered),
            ebike_poi_ids=ebike_poi_seq,
            car_poi_ids=car_poi_seq,
            ebike_pair_ids=ebike_pair_ids,
            car_pair_ids=car_pair_ids,
            ebike_time=ebike_time,
            car_time=car_time,
            ebike_distance=ebike_dist,
            car_distance=car_dist,
            ebike_legs=ebike_legs,
            car_legs=car_legs,
        )

    consumers_handled: set = set()

    for consumer, needed in consumer_needed.items():
        if not needed:
            continue
        candidates = sorted(producer_products_for.get(consumer, {}).keys())
        if not candidates:
            continue

        stop_products_all = {
            p: list(producer_products_for[consumer][p]) for p in candidates
        }
        remaining = set(needed)

        # ✓ SOLVE SET-COVER TSP ONCE with ALL candidates (no splitting)
        one_seq, _, one_covered = _solve_consumer_set_cover(
            consumer, candidates, stop_products_all, remaining, good_id_of, car_times, max_stops
        )
        one_stops = one_seq[1:-1]

        if one_covered == remaining and one_covered:
            # Full coverage achieved in a single loop
            loop = make_loop(consumer, one_stops, stop_products_all, one_covered, candidates, needed)
            if loop is not None and loop.is_valid:
                loops.append(loop)
                consumers_handled.add(consumer)
                remaining = set()
        elif one_covered:
            # Partial coverage from the set-cover attempt is still useful
            # -- keep it, then top up whatever's left below.
            loop = make_loop(consumer, one_stops, stop_products_all, one_covered, candidates, needed)
            if loop is not None and loop.is_valid:
                loops.append(loop)
                consumers_handled.add(consumer)
                remaining -= one_covered

        # If anything is still uncovered (e.g. the exact solver hit max_stops),
        # top up with simple direct loops to whichever single producer covers
        # each remaining product, so every consumer ends up with ALL its needed
        # products covered.
        for prod in sorted(remaining):
            best_producer = None
            best_dist = float("inf")
            for p in candidates:
                if prod in producer_products_for[consumer].get(p, []):
                    dist = car_dists.get((consumer, p), float("inf"))
                    if dist < best_dist:
                        best_dist = dist
                        best_producer = p
            if best_producer is None:
                continue
            loop = make_loop(consumer, [best_producer], {best_producer: [prod]}, {prod}, candidates, needed)
            if loop is not None and loop.is_valid:
                loops.append(loop)
                consumers_handled.add(consumer)

    # Safety net: any consumer with needs but zero loops at all (e.g. no
    # candidates passed connections.py's filters) gets forced a loop to its
    # closest other POI, same as the producer side's fallback.
    for c_id, c_row in pois.iterrows():
        c_id = int(c_id)
        if c_id in consumers_handled:
            continue
        consumed = c_row.get("ConsumedGoods") or []
        needed_products = {g for g in consumed if g and str(g) != "nan"}
        if not needed_products:
            continue
        closest_p = min(
            (int(idx) for idx in pois.index if int(idx) != c_id),
            key=lambda p: car_dists.get((c_id, p), float("inf")),
            default=None,
        )
        if closest_p is None:
            continue
        prod = next(iter(needed_products))
        loop = make_loop(c_id, [closest_p], {closest_p: [prod]}, {prod}, [closest_p], needed_products)
        if loop is not None and loop.is_valid:
            loops.append(loop)
            consumers_handled.add(c_id)

    # ─────────────────────────────────────────────────────────────────────
    # FINAL HARD VALIDATION: drop any leg that loads a product a POI does
    # not actually produce (per points_of_interest.csv), independent of
    # whatever upstream logic built the loop. This guarantees correctness
    # of the rendered map regardless of any bug in the optimizer above.
    # ─────────────────────────────────────────────────────────────────────
    real_produced: Dict[int, set] = {}
    for p_id, p_row in pois.iterrows():
        p_goods = p_row.get("ProducedGoods") or []
        real_produced[int(p_id)] = {g for g in p_goods if g and str(g) != "nan"}

    good_name_of_id = {gid: name for name, gid in good_id_of.items()}

    def _sanitize_loop_legs(home: int, legs: List[dict]) -> Optional[List[dict]]:
        kept_stops = []
        for leg in legs:
            pid = leg["poi_id"]
            load_ok = [g for g in leg.get("load_products", []) if good_name_of_id.get(g) in real_produced.get(pid, set())]
            # unload is fine to keep as-is (it's just "this consumer received
            # X here", which is always true for the home/return leg)
            new_leg = dict(leg)
            new_leg["load_products"] = load_ok
            if pid == home or load_ok or leg.get("unload_products"):
                kept_stops.append(new_leg)
        if len(kept_stops) < 2:
            return None
        return kept_stops

    validated_loops: List[DeliveryLoop] = []
    recovered_products: Dict[int, set] = {}
    for loop in loops:
        new_ebike = _sanitize_loop_legs(loop.home_poi_id, loop.ebike_legs or [])
        new_car = _sanitize_loop_legs(loop.home_poi_id, loop.car_legs or [])
        if new_ebike is None or new_car is None:
            continue
        loop.ebike_legs = new_ebike
        loop.car_legs = new_car
        actually_covered = {
            good_name_of_id.get(g) for leg in new_ebike for g in leg.get("load_products", [])
        } - {None}
        loop.products_covered = sorted(actually_covered) if actually_covered else loop.products_covered
        recovered_products.setdefault(loop.home_poi_id, set()).update(actually_covered)
        if loop.is_valid:
            validated_loops.append(loop)
    loops = validated_loops

    # Re-check full coverage after sanitization and top up anything that
    # the cleanup removed, using ONLY real producer relationships.
    for c_id, c_row in pois.iterrows():
        c_id = int(c_id)
        c_consumed = c_row.get("ConsumedGoods") or []
        needed_products = {g for g in c_consumed if g and str(g) != "nan"}
        if not needed_products:
            continue
        have = recovered_products.get(c_id, set())
        still_missing = needed_products - have
        for prod in sorted(still_missing):
            best_producer = None
            best_dist = float("inf")
            for p_id, prods in real_produced.items():
                if p_id == c_id or prod not in prods:
                    continue
                dist = car_dists.get((c_id, p_id), float("inf"))
                if dist < best_dist:
                    best_dist = dist
                    best_producer = p_id
            if best_producer is None:
                continue
            loop = make_loop(c_id, [best_producer], {best_producer: [prod]}, {prod}, [best_producer], {prod})
            if loop is not None and loop.is_valid:
                loops.append(loop)

    return loops


def _ensure_poi_product_consistency(
    pois: pd.DataFrame,
    legs: List[dict],
    good_name_of: Dict[int, str],
) -> None:
    """Custom loops (loops.json) are hand-authored, so a leg might say a POI
    unloads (= consumes) or loads (= produces) a product that isn't actually
    listed in that POI's ConsumedGoods/ProducedGoods in
    points_of_interest.csv. Rather than silently render an inconsistent map
    (e.g. a beverage delivery to a place that doesn't list itself as
    consuming beverages), add the missing product to the POI's
    Consumed/ProducedGoods in place so the map (POI markers, popups, good
    icons) and the source data agree with what the loop actually does.
    """
    if "poi_id" not in pois.columns:
        return
    for leg in legs:
        pid = leg["poi_id"]
        if pid not in pois.index:
            continue
        for good_id in leg.get("unload_products") or []:
            name = good_name_of.get(int(good_id))
            if not name:
                continue
            consumed = pois.at[pid, "ConsumedGoods"]
            if not isinstance(consumed, list):
                consumed = []
            if name not in consumed:
                consumed = consumed + [name]
                pois.at[pid, "ConsumedGoods"] = consumed
        for good_id in leg.get("load_products") or []:
            name = good_name_of.get(int(good_id))
            if not name:
                continue
            produced = pois.at[pid, "ProducedGoods"]
            if not isinstance(produced, list):
                produced = []
            if name not in produced:
                produced = produced + [name]
                pois.at[pid, "ProducedGoods"] = produced


def build_custom_loops(
    custom_loops_data: List[List[dict]],
    connections_df: pd.DataFrame,
    pois: pd.DataFrame,
    goods: pd.DataFrame,
    all_pairs: Optional[Union[Dict, gpd.GeoDataFrame]] = None,
) -> List[DeliveryLoop]:
    """Build DeliveryLoop objects directly from loops.json.

    Each entry in `custom_loops_data` is already a list of leg dicts:
        {"poi_id": int, "load_products": [good_id, ...],
         "unload_products": [good_id, ...], "mandatory": bool}

    The same leg list is used for both car and ebike — custom loops define a
    fixed visiting order (unlike producer/consumer loops, which are TSP
    ordered separately per vehicle).

    NOTE: this mutates `pois` in place (ConsumedGoods/ProducedGoods) to stay
    consistent with what each loop's legs actually load/unload — see
    _ensure_poi_product_consistency.
    """
    ebike_times = _pair_time_dict(connections_df, "ebike_travel_time_orig", all_pairs)
    car_times   = _pair_time_dict(connections_df, "car_travel_time", all_pairs)
    ebike_dists = _pair_dist_dict(connections_df, "ebike_distance", all_pairs)
    car_dists   = _pair_dist_dict(connections_df, "car_distance", all_pairs)
    good_name_of = {int(r["good_id"]): str(r["Product"]) for _, r in goods.iterrows()}

    loops: List[DeliveryLoop] = []
    for raw_legs in custom_loops_data:
        if not isinstance(raw_legs, list) or len(raw_legs) < 2:
            continue

        legs = [
            {
                "poi_id": int(leg["poi_id"]),
                "load_products": [int(g) for g in (leg.get("load_products") or [])],
                "unload_products": [int(g) for g in (leg.get("unload_products") or [])],
                "mandatory": bool(leg.get("mandatory", False)),
            }
            for leg in raw_legs
        ]

        # Ensure the loop closes back to its start
        if legs[0]["poi_id"] != legs[-1]["poi_id"]:
            legs.append({
                "poi_id": legs[0]["poi_id"],
                "load_products": [],
                "unload_products": [],
                "mandatory": True,
            })

        if not is_valid_loop(legs):
            continue

        _ensure_poi_product_consistency(pois, legs, good_name_of)

        seq_full = [leg["poi_id"] for leg in legs]
        home_poi_id = seq_full[0]
        stop_poi_ids = seq_full[1:-1]

        ebike_time = car_time = ebike_dist = car_dist = 0.0
        for i in range(len(seq_full) - 1):
            key = (seq_full[i], seq_full[i + 1])
            ebike_time += ebike_times.get(key, 5.0)
            car_time += car_times.get(key, 3.0)
            ebike_dist += ebike_dists.get(key, 1.0)
            car_dist += car_dists.get(key, 1.0)

        pair_ids = leg_pair_ids(legs)

        products_covered = sorted({
            good_name_of[gid]
            for leg in legs
            for gid in (leg["load_products"] + leg["unload_products"])
            if gid in good_name_of
        })

        loop = DeliveryLoop(
            mode="custom",
            home_poi_id=home_poi_id,
            stop_poi_ids=stop_poi_ids,
            produkt=None,
            products_covered=products_covered,
            ebike_poi_ids=seq_full,
            car_poi_ids=seq_full,
            ebike_pair_ids=pair_ids,
            car_pair_ids=pair_ids,
            ebike_time=ebike_time,
            car_time=car_time,
            ebike_distance=ebike_dist,
            car_distance=car_dist,
            ebike_legs=legs,
            car_legs=legs,
        )
        loops.append(loop)
    return loops