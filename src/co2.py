"""
co2.py
======
Estimates hot operational CO2 emissions using the HBEFA 4.2 Application Guidelines.
Provides speed-based Level of Service (LOS) mapping.
"""

from typing import Literal, Optional, Dict, List, Union, Tuple
import numpy as np

# --- TYPE DEFINITIONS BASED ON HBEFA 4.2 SCHEME ---
LOSClass = Literal["A", "B", "C", "D", "E", "F"]

VehicleType = Literal[
    "gasoline_pc",
    "diesel_pc",
    "diesel_hgv",
    "gasoline_mc",
    "ev_pc",
]

# Route types from OSM highway
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

RouteType = str


def get_los(
    maxspeed_kmh: float,
    avg_speed_kmh: float,
    format: Literal["hbefa", "LOS"] = "LOS",
) -> Union[int, str]:
    """Determine the HBEFA Level of Service (LOS) from speed data."""
    los_thresholds: Dict[int, List[float]] = {
        30: [28.0, 20.2, 12.4, 7.6],
        40: [34.0, 25.3, 15.5, 8.7],
        50: [41.0, 29.0, 17.1, 9.2],
        60: [54.0, 37.9, 19.8, 9.2],
        70: [62.0, 45.1, 23.0, 10.1],
        80: [72.0, 52.2, 25.5, 10.5],
        90: [83.0, 60.0, 26.0, 11.0],
        100: [92.0, 66.3, 26.0, 11.0],
        110: [106.0, 77.1, 26.0, 11.0],
        120: [117.0, 87.1, 26.0, 11.0],
        130: [127.0, 95.3, 26.0, 11.0],
        140: [135.0, 108.6, 26.0, 11.0],
    }

    # Snap speed limit to nearest available threshold set.
    valid_maxspeed = min(
        los_thresholds.keys(),
        key=lambda x: abs(x - maxspeed_kmh),
    )

    t1, t2, t3, t4 = los_thresholds[valid_maxspeed]

    if avg_speed_kmh >= t1:
        hbefa_id = 1
    elif avg_speed_kmh >= t2:
        hbefa_id = 2
    elif avg_speed_kmh >= t3:
        hbefa_id = 3
    elif avg_speed_kmh >= t4:
        hbefa_id = 4
    else:
        hbefa_id = 5

    if format == "hbefa":
        return hbefa_id

    letter_mapping = {
        1: "B",
        2: "D",
        3: "E",
        4: "F",
        5: "F",
    }

    return letter_mapping[hbefa_id]


def hbefa_row(
    distance_m: float,
    avg_speed_kmh: float,
    intersection_dist_m: Optional[float] = None,
    gradient_pct: float = 0,
    route_type: Optional[
        Union[
            RouteType,
            list[RouteType],
            tuple[RouteType, ...],
        ]
    ] = None,
    vehicle_type: VehicleType = "gasoline_pc",
    los: Optional[Union[LOSClass, int]] = None,
    max_speed: Optional[float] = None,
) -> float:
    """Estimates hot operational CO2 emissions using HBEFA 4.2 Application Guidelines."""
    if intersection_dist_m is None:
        intersection_dist_m = distance_m 

    if isinstance(route_type, (list, tuple)):
        route_type = next(
            (rt for rt in ROUTE_TYPE_PRIORITY if rt in route_type),
            None,
        )

    if los is None:
        if max_speed is None:
            los = "C" 
        else:
            los = get_los(max_speed, avg_speed_kmh) 

    # Area inference
    is_urban = intersection_dist_m < 1500

    # Road mapping
    road_mapping = {
        "motorway": ("Motorway-Nat", "Motorway-City"),
        "motorway_link": ("Primary-Nat", "Primary-City"),
        "trunk": ("Primary-Nat", "Primary-City"), 
        "trunk_link": ("Primary-Nat", "Primary-City"),
        "primary": ("Primary-Nat", "Primary-City"), 
        "primary_link": ("Primary-Nat", "Primary-City"),
        "secondary": ("Distributor-Rural", "Distributor-Urban"),
        "secondary_link": ("Distributor-Rural", "Distributor-Urban"), 
        "tertiary": ("Local-Rural", "Local-Urban"),
        "tertiary_link": ("Local-Rural", "Local-Urban"),
        "residential": ("Access-Rural", "Access-Urban"),
        "living_street": ("Access-Rural", "Access-Urban"),
        "service": ("Access-Rural", "Access-Urban"),
    }

    if route_type in road_mapping:
        rural_type, urban_type = road_mapping[route_type]
        if (urban_type == "Primary-City") and (intersection_dist_m < 500):
            urban_type = "Distributor-Urban"
        if (urban_type == "Distributor-Urban") and (intersection_dist_m < 250):
            urban_type = "Local-Urban"
    else:
        if is_urban:
            if intersection_dist_m >= 500:
                if avg_speed_kmh > 70:
                    urban_type = "Motorway-City"
                else:
                    urban_type = "Primary-City"
            elif intersection_dist_m >= 250:
                urban_type = "Distributor-Urban"
            else:
                urban_type = "Local-Urban" 
        else: 
            if intersection_dist_m >= 5000:
                if avg_speed_kmh > 90:
                    rural_type = "Motorway-Nat"
                else:
                    rural_type = "Primary-Nat"
            elif intersection_dist_m >= 1500:
                rural_type = "Distributor-Rural"
            else:
                rural_type = "Local-Rural" 

    hbefa_road_type = urban_type if is_urban else rural_type

    # Road penalties
    ROAD_TYPE_PENALTIES = {
        "Motorway-Nat": 0.98,
        "Primary-Nat": 1.00,
        "Distributor-Rural": 1.20,
        "Local-Rural": 1.30,
        "Access-Rural": 1.40,
        "Motorway-City": 0.95,
        "Primary-City": 1.10,
        "Distributor-Urban": 1.38,
        "Local-Urban": 1.56,
        "Access-Urban": 1.68
    }
    road_penalty = ROAD_TYPE_PENALTIES.get(hbefa_road_type, 1.20)

    # Base emission factor
    v = avg_speed_kmh
    if vehicle_type == "gasoline_pc":
        base_ef_gkm = (215 - 2.6 * v + 0.019 * v**2)
    elif vehicle_type == "diesel_pc":
        base_ef_gkm = (190 - 2.2 * v + 0.017 * v**2)
    elif vehicle_type == "diesel_hgv":
        base_ef_gkm = (1200 * (v**-0.35))
    elif vehicle_type == "gasoline_mc":
        base_ef_gkm = (110 - 2.8 * v + 0.025 * v**2) 
    else:
        base_ef_gkm = 0.0

    # LOS multiplier
    if isinstance(los, int):
        los_map = {1: 1.0, 2: 1.05, 3: 1.1, 4: 2.0, 5: 3.0}
    else:
        los_map = {"A": 1.0, "B": 1.05, "C": 1.1, "D": 1.15, "E": 1.5, "F": 2.0}

    los_multiplier = los_map.get(los.upper() if isinstance(los, str) else los, 1.05)

    # Gradient adjustment
    grad_sensitivities = {
        "gasoline_pc": 0.15, "diesel_pc": 0.15, "ev_pc": 0.15,
        "diesel_hgv": 0.25,
        "gasoline_mc": 0.10
    }
    sensitivity = grad_sensitivities.get(vehicle_type, 0.15)
    gradient_multiplier = 1.0 + (max(0, gradient_pct) * sensitivity)

    if is_urban and vehicle_type == "diesel_hgv":
        road_penalty *= 1.10

    total_ef = base_ef_gkm * los_multiplier * gradient_multiplier * road_penalty
    total_co2_kg = (total_ef * (distance_m / 1000)) / 1000

    return round(total_co2_kg, 4)


def route_hbefa(
    route_edges,
    avg_speed_col: str = "avg_speed",
    gradient_col: Optional[str] = "gradient",
    route_type_col: Optional[str] = "highway",
    vehicle_type: VehicleType = "gasoline_pc",
    los_col: Optional[str] = None,
    maxspeed_col: Optional[str] = "maxspeed",
    length_col: str = "length",
    return_total: bool = True
) -> Union[float, np.ndarray]:
    """Estimate total route-level CO2 emissions using HBEFA 4.2 approximations."""
    import pandas as pd
    required_columns = [length_col, avg_speed_col]

    for col in required_columns:
        if col not in route_edges.columns:
            raise KeyError(f"Required column '{col}' not found in route_edges.")

    if gradient_col is not None and gradient_col not in route_edges.columns:
        gradient_col = None

    if route_type_col is not None and route_type_col not in route_edges.columns:
        route_type_col = None

    if los_col is not None and los_col not in route_edges.columns:
        los_col = None

    if maxspeed_col is not None and maxspeed_col not in route_edges.columns:
        maxspeed_col = None

    total_emissions = route_edges.apply(
        lambda row: hbefa_row(
            distance_m=row[length_col],
            avg_speed_kmh=row[avg_speed_col],
            intersection_dist_m=row[length_col],
            gradient_pct=(0.0 if gradient_col is None else row[gradient_col]),
            route_type=(None if route_type_col is None else row[route_type_col]),
            vehicle_type=vehicle_type,
            los=(None if los_col is None else row[los_col]),
            max_speed=(None if maxspeed_col is None else row[maxspeed_col]),
        ),
        axis=1,
    )
    if return_total:
        return round(float(total_emissions.sum()), 4)
    else:
        return total_emissions
