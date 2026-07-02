"""
loop_format.py
==============
Shared "legs" format used by every loop in the map — producer, consumer,
and custom (loops.json) loops all end up as a list of legs:

    [
      {"poi_id": int, "load_products": [good_id, ...],
                       "unload_products": [good_id, ...], "mandatory": bool},
      ...
    ]

The first and last leg dict in the list are always the same poi_id (the
loop's home / start+end stop) and are always mandatory=True. Intermediate
stops are mandatory only when they can never be dropped by the product
checkboxes (set explicitly in loops.json, or — for producer/consumer loops
— never, since every intermediate stop there exists only to move one
particular product).

Products are referenced by **good_id (int)**, never by product name, so the
map's product checkboxes (which operate on good_id) can filter consistently
across every layer.

A loop is only valid (kept on the map) if it has at least two mandatory
stops (normally: the home stop counted at both the start and the end of the
list, i.e. index 0 and index -1). This guarantees a loop can never collapse
to nothing when every non-mandatory stop is excluded by product filters.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import pandas as pd


def build_good_id_lookup(goods: pd.DataFrame) -> Dict[str, int]:
    """Product name -> good_id, from goods.csv (good_id is the row's own id)."""
    lookup: Dict[str, int] = {}
    for _, row in goods.iterrows():
        name = row.get("Product")
        gid = row.get("good_id")
        if name is None or gid is None:
            continue
        lookup[str(name)] = int(gid)
    return lookup


def names_to_good_ids(names: Optional[Sequence[str]], name_to_id: Dict[str, int]) -> List[int]:
    if not names:
        return []
    out = []
    for n in names:
        if n is None:
            continue
        gid = name_to_id.get(str(n))
        if gid is not None:
            out.append(int(gid))
    return out


def make_leg(poi_id: int, load: Optional[Sequence[int]] = None,
             unload: Optional[Sequence[int]] = None, mandatory: bool = False) -> dict:
    return {
        "poi_id": int(poi_id),
        "load_products": [int(g) for g in (load or [])],
        "unload_products": [int(g) for g in (unload or [])],
        "mandatory": bool(mandatory),
    }


def legs_for_producer_loop(home_poi_id: int, ordered_stops: List[int], good_id: int) -> List[dict]:
    """home (load good) -> consumer, consumer, ... -> home (nothing).

    `ordered_stops` is the full visiting order EXCLUDING the closing return to
    home, e.g. [home, c1, c2, ..., cn] (it must start with home_poi_id).

    Home legs (first and last) are mandatory=True so the closing return is
    never trimmed by the client-side trimLoopLegs, guaranteeing the loop
    always renders as a closed round-trip on the map. Intermediate stops
    remain non-mandatory so product checkboxes can still drop them.
    If the product gets fully unchecked, hasActiveProduct goes False and
    the whole loop hides (trimLoopLegs alone would leave a valid 2-leg
    skeleton, which we don't want — that is handled by resolveLoopLegs
    returning valid=False).
    """
    legs: List[dict] = []
    for i, poi_id in enumerate(ordered_stops):
        if i == 0:
            legs.append(make_leg(poi_id, load=[good_id], unload=[], mandatory=True))
        else:
            legs.append(make_leg(poi_id, load=[], unload=[good_id], mandatory=False))
    legs.append(make_leg(home_poi_id, load=[], unload=[], mandatory=True))
    return legs


def legs_for_consumer_loop(home_poi_id: int, ordered_stops: List[int],
                           stop_good_ids: Dict[int, List[int]]) -> List[dict]:
    """home (nothing) -> producer (loads its products), ... -> home (unloads
    everything accumulated along the loop).

    `ordered_stops` is the full visiting order EXCLUDING the closing return to
    home, e.g. [home, p1, p2, ..., pn] (it must start with home_poi_id).
    `stop_good_ids[poi_id]` gives the good_ids loaded at that producer stop.

    Home legs (first/last) are mandatory=True so the loop always renders as a
    closed round-trip — see legs_for_producer_loop's docstring for details.
    """
    legs: List[dict] = []
    accumulated: List[int] = []
    for i, poi_id in enumerate(ordered_stops):
        if i == 0:
            legs.append(make_leg(poi_id, load=[], unload=[], mandatory=True))
        else:
            goods_here = stop_good_ids.get(poi_id, [])
            accumulated.extend(g for g in goods_here if g not in accumulated)
            legs.append(make_leg(poi_id, load=goods_here, unload=[], mandatory=False))
    legs.append(make_leg(home_poi_id, load=[], unload=list(accumulated), mandatory=True))
    return legs


def count_mandatory(legs: List[dict]) -> int:
    return sum(1 for leg in legs if leg.get("mandatory"))


def is_valid_loop(legs: List[dict]) -> bool:
    """Structural validity only: a real loop needs at least 2 legs (so it
    has somewhere to go and back). Whether it's actually SHOWN on the map
    depends on live product-checkbox state — a loop with zero active
    products anywhere gets fully hidden, and partially-active loops get
    trimmed/restitched — both handled client-side in route_map_scripts.js
    (trimLoopLegs / resolveLoopLegs), not here. This keeps every loop
    available to export regardless of which products happen to be active
    when main.py runs."""
    return len(legs) >= 2


def leg_pair_ids(legs: List[dict]) -> List[str]:
    """Consecutive 'a_b' pair_id strings walking the legs in order."""
    return [f"{legs[i]['poi_id']}_{legs[i + 1]['poi_id']}" for i in range(len(legs) - 1)]


def all_good_ids_in_legs(legs: List[dict]) -> List[int]:
    out = set()
    for leg in legs:
        out.update(leg.get("load_products") or [])
        out.update(leg.get("unload_products") or [])
    return sorted(out)