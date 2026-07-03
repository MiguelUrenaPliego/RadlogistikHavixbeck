"""
restaurant_loops.py
====================
Project-specific (main.py only): builds the "Restaurants" and
"Lebensmittelhandel" custom layers automatically from a wide-format supplier
table (`data/restaurant_suppliers.csv`), instead of the loops being
hand-written into `data/loops.json`.

Input CSV format — one row per restaurant, up to 3 supplier POI ids per
category (blank if unused):

    Restaurant_id, Restaurant_name,
    Bakery_1, Bakery_2, Bakery_3,
    Supermarket_1, Supermarket_2, Supermarket_3,
    Wine_1, Wine_2, Wine_3,
    Beverage_1, Beverage_2, Beverage_3,
    Wochenmarkt_1, Wochenmarkt_2, Wochenmarkt_3,
    Landwirt_1, Landwirt_2, Landwirt_3

Each category maps to a fixed set of goods (by German product name, matching
`data/goods.csv`) — this keeps a supplier's contribution scoped to its role
(e.g. a "Beverage" supplier only delivers Getränke, even if that same POI's
full ProducedGoods column also lists Wein/beer for other purposes).

Stop order within each generated loop is TSP-optimized using the same solver
as the producer/consumer loops (`solve_producer_loop`), using real routed
car travel times from `connections_df`/`all_pairs`.

Output: {"Restaurants": [...], "Lebensmittelhandel": [...]}, each value a
list of raw leg-lists in the same shape as `loops.json` — ready to pass to
`delivery_loops.build_custom_loops`.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Union

import pandas as pd
import geopandas as gpd

from .delivery_loops import _pair_time_dict, _solve_tsp_path

CATEGORY_COLUMNS = ["Bakery", "Supermarket", "Wine", "Beverage", "Wochenmarkt", "Landwirt"]
SLOTS_PER_CATEGORY = 3

CATEGORY_TO_PRODUCT_NAMES = {
    "Bakery": ["Brot"],
    "Supermarket": ["Lebensmittel"],
    "Wine": ["Wein"],
    "Beverage": ["Getränke"],
    "Wochenmarkt": ["Landwirtschaftserzeugnisse", "Spargel", "Erdbeeren"],
    "Landwirt": ["Landwirtschaftserzeugnisse", "Spargel", "Erdbeeren"],
}


def _read_suppliers(suppliers_csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(suppliers_csv_path)
    df["Restaurant_id"] = df["Restaurant_id"].astype(int)
    return df


def _slot_columns(category: str) -> List[str]:
    return [f"{category}_{i}" for i in range(1, SLOTS_PER_CATEGORY + 1)]


def build_restaurant_supplier_loops(
    suppliers_csv_path: str,
    pois: pd.DataFrame,
    goods: pd.DataFrame,
    connections_df: pd.DataFrame,
    all_pairs: Optional[Union[Dict, gpd.GeoDataFrame]] = None,
    max_stops: int = 12,
) -> Dict[str, List[List[dict]]]:
    df = _read_suppliers(suppliers_csv_path)
    name_to_id = {str(r["Product"]): int(r["good_id"]) for _, r in goods.iterrows()}
    car_times = _pair_time_dict(connections_df, "car_travel_time", all_pairs)

    # (restaurant_id, category, supplier_id) rows, deduplicated per restaurant/category
    links: List[tuple] = []
    for _, row in df.iterrows():
        rid = int(row["Restaurant_id"])
        for category in CATEGORY_COLUMNS:
            seen = set()
            for col in _slot_columns(category):
                val = row.get(col)
                if pd.isna(val) or str(val).strip() == "":
                    continue
                sid = int(float(val))
                if sid in seen:
                    continue
                seen.add(sid)
                links.append((rid, category, sid))

    def _goods_for(category: str) -> List[int]:
        return sorted({name_to_id[n] for n in CATEGORY_TO_PRODUCT_NAMES[category] if n in name_to_id})

    # ── Restaurants layer: one loop per restaurant, home = restaurant ──────────
    restaurants_loops: List[List[dict]] = []
    restaurant_ids = sorted({rid for rid, _, _ in links})
    for rid in restaurant_ids:
        rlinks = [(cat, sid) for (r, cat, sid) in links if r == rid]
        supplier_products: Dict[int, set] = {}
        for cat, sid in rlinks:
            supplier_products.setdefault(sid, set()).update(_goods_for(cat))
        stop_ids = sorted(supplier_products.keys())
        if not stop_ids:
            continue
        ordered, _ = _solve_tsp_path(rid, stop_ids, car_times, max_stops)
        all_goods: set = set()
        legs = [{"poi_id": rid, "load_products": [], "unload_products": [], "mandatory": True}]
        for sid in ordered[1:-1]:
            gids = sorted(supplier_products[sid])
            all_goods.update(gids)
            legs.append({"poi_id": sid, "load_products": gids, "unload_products": [], "mandatory": False})
        legs.append({
            "poi_id": rid, "load_products": [], "unload_products": sorted(all_goods), "mandatory": True,
        })
        restaurants_loops.append(legs)

    # ── Lebensmittelhandel layer: one loop per (category, supplier) ────────────
    lebensmittel_loops: List[List[dict]] = []
    supplier_groups: Dict[tuple, List[int]] = {}
    for rid, cat, sid in links:
        supplier_groups.setdefault((cat, sid), []).append(rid)

    for (cat, sid), rids in supplier_groups.items():
        gids = _goods_for(cat)
        stop_ids = sorted(set(rids))
        if not stop_ids:
            continue
        ordered, _ = _solve_tsp_path(sid, stop_ids, car_times, max_stops)
        legs = [{"poi_id": sid, "load_products": gids, "unload_products": [], "mandatory": True}]
        for rid in ordered[1:-1]:
            legs.append({"poi_id": rid, "load_products": [], "unload_products": gids, "mandatory": False})
        legs.append({"poi_id": sid, "load_products": [], "unload_products": [], "mandatory": True})
        lebensmittel_loops.append(legs)

    return {
        "Restaurants": restaurants_loops,
        "Lebensmittelhandel": lebensmittel_loops,
    }
