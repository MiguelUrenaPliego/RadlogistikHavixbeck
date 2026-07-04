"""
main.py
=======
Configure and run the end-to-end pipeline.

Edit the variables below, then run:

    python main.py

All heavy logic lives in pipeline.py — import and call pipeline.run(config)
directly if you want to drive the pipeline from your own code.
"""

from src import pipeline

# ── Paths ─────────────────────────────────────────────────────────────────────
pois_path          = "data/points_of_interest.csv"
goods_path         = "data/goods.csv"
aoi_path           = "aoi/aoi.gpkg"
streets_graph_path = "streets/streets.graphml"
osm_xml_file       = "streets/streets.osm"
streets_path       = "streets"
custom_loops_path  = "data/loops.json"
restaurant_suppliers_path = "data/restaurant_suppliers.csv"
loops_folder       = "loops"
map_folder         = "."
raster_subfolder   = "raster_layers"

# ── Rendering ─────────────────────────────────────────────────────────────────
RASTER_LAYER_DPI = 1500

# ── Delivery-loop construction ────────────────────────────────────────────────
MAX_STOPS          = 12
MAX_RADIUS         = 5000    # meters — producer loops only
MAX_ADDED_DISTANCE = 250     # meters — max detour before splitting a loop

# ── Car routing ───────────────────────────────────────────────────────────────
car_node_penalty          = 2
car_acceleration          = 1.5
car_min_cruising_time     = 2
car_min_cruising_speed    = 10
car_max_stop_and_go_speed = 20
car_stopping_time         = 3   # minutes

car_maxspeeds = {
    "living_street":  15,
    "motorway":       100,
    "motorway_link":  60,
    "primary":        60,
    "primary_link":   60,
    "residential":    15,
    "secondary":      55,
    "secondary_link": 55,
    "service":        30,
    "tertiary":       50,
    "tertiary_link":  50,
    "trunk":          80,
    "trunk_link":     60,
    "track":          10,
    "pedestrian":     1,
    "footway":        1,
    "cicleway":       1,
    "unclassified":   30,
}

# ── E-bike routing ────────────────────────────────────────────────────────────
ebike_node_penalty           = 0.5
ebike_acceleration           = 2
ebike_min_cruising_time      = 1
ebike_min_cruising_speed     = 5
ebike_max_stop_and_go_speed  = 0
bike_stopping_time           = 0  # minutes
_BIKE_AVOID_FACTOR           = 50

ebike_maxspeeds = {
    "living_street":  15,
    "motorway":        1,
    "motorway_link":   1,
    "primary":        20,
    "primary_link":   20,
    "residential":    15,
    "secondary":      18,
    "secondary_link": 18,
    "service":        15,
    "tertiary":       15,
    "tertiary_link":  15,
    "trunk":          20,
    "trunk_link":     20,
    "unclassified":   15,
}

# ── Bike scoring ──────────────────────────────────────────────────────────────
min_bike_score              = 5
bike_travel_time_reduction  = 0.4   # 40% perceived time reduction at best
max_bike_extra_time         = 0.1   # 10% extra over car is still acceptable
friendliness_weight         = 3
time_weight                 = 4
product_weight              = 3

# ── Car scoring ───────────────────────────────────────────────────────────────
# Scales car_perceived_travel_time to prefer bigger/faster roads for route
# selection (route_map only ever shows car time/distance/CO2, never a car
# "score" -- unlike bike_score this purely steers routing, it's not a
# rating). car_travel_time_reduction plays the same role as
# bike_travel_time_reduction above: the fraction by which perceived time on
# the best-scoring road is reduced relative to the worst-scoring one.
car_travel_time_reduction = 0.2

car_score_config = {
    "highway": {
        "column": "highway",
        "weight": 10,
        "default": 1,
        "mode": "categorical",
        "list_behaviour": "max",
        "values": {
            "living_street":  1,
            "motorway":       10,
            "motorway_link":  10,
            "primary":        10,
            "primary_link":   10,
            "residential":    1,
            "secondary":      10,
            "secondary_link": 10,
            "service":        1,
            "tertiary":       8,
            "tertiary_link":  8,
            "trunk":          10,
            "trunk_link":     10,
            "unclassified":   1,
        },
    },
}

# ── Bike-score config ─────────────────────────────────────────────────────────
bike_score_config = {
    "access_restrictions": {
        "column": "access_restrictions",
        "weight": 10,
        "default": 5,
        "mode": "categorical",
        "list_behaviour": "max",
        "values": {
            "pedestrian+bikes": 10,
            "private": 1,
            "pedestrian": 1,
            "permit": 10,
            "residents": 10,
        },
        "ignore": {
            "pedestrian+bikes": ["bike_separation", "highway", "lanes", "car_maxspeed"],
            "private":          ["bike_separation", "highway", "lanes", "car_maxspeed"],
            "pedestrian":       ["bike_separation", "highway", "lanes", "car_maxspeed"],
        },
    },
    "bike_separation": {
        "column": "bike_separation",
        "weight": 10,
        "default": 1,
        "mode": "categorical",
        "list_behaviour": "max",
        "values": {
            "none":       1,
            "mixed":      1,
            "complete":  10,
            "soft":       5,
            "prohibited": 1,
        },
        "ignore": {
            "prohibited": ["access_restrictions", "highway", "lanes", "car_maxspeed"],
            "complete": ["access_restrictions", "highway", "lanes", "car_maxspeed"],
            "soft": ["access_restrictions", "highway"]
        },
    },
    "pavement": {
        "column": "pavement",
        "weight": 5,
        "default": 10,
        "mode": "categorical",
        "list_behaviour": "max",
        "values": {
            "asphalt":     10,
            "concrete":    10,
            "cobblestone":  5,
            "unpaved":      1,
        },
    },
    "highway": {
        "column": "highway",
        "weight": 10,
        "default": 1,
        "mode": "categorical",
        "list_behaviour": "max",
        "values": {
            "trunk":          1,
            "motorway_link":  1,
            "trunk_link":     1,
            "secondary":      2,
            "path":          10,
            "unclassified":   1,
            "tertiary":       3,
            "service":        8,
            "track":         10,
            "residential":   10,
            "living_street": 10,
            "footway":        7,
            "cycleway":      10,
            "primary":        1,
            "motorway":       1,
            "tertiary_link":  3,
            "pedestrian":     7,
            "secondary_link": 2,
            "bridleway":      8,
        },
        "ignore": {
            "living_street": ["bike_separation", "car_maxspeed"],
            "residential":   ["bike_separation", "car_maxspeed"],
            "footway":       ["bike_separation", "car_maxspeed", "lanes"],
            "cycleway":      ["bike_separation", "car_maxspeed", "lanes"],
            "pedestrian":    ["bike_separation", "car_maxspeed", "lanes"],
            "bridleway":     ["bike_separation", "car_maxspeed", "lanes"],
            "path":          ["bike_separation", "car_maxspeed", "lanes"],
            "track":         ["bike_separation", "car_maxspeed", "lanes"],
        },
    },
    "lanes": {
        "column": "lanes",
        "weight": 5,
        "default": 5,
        "mode": "numeric",
        "list_behaviour": "max",
        "values": {1: 10, 2: 5, 3: 1},
    },
    "car_maxspeed": {
        "column": "car_maxspeed",
        "weight": 10,
        "default": 5,
        "mode": "numeric",
        "list_behaviour": "max",
        "values": {0: 10, 5: 10, 20: 8, 30: 5, 50: 1},
        "ignore": {0: ["bike_separation"]},
    },
}

# ── Language ──────────────────────────────────────────────────────────────────
DEFAULT_LANGUAGE = "de"

# ─────────────────────────────────────────────────────────────────────────────

config = {
    "paths": {
        "pois":          pois_path,
        "goods":         goods_path,
        "aoi":           aoi_path,
        "streets_graph": streets_graph_path,
        "osm_xml":       osm_xml_file,
        "streets":       streets_path,
        "custom_loops":  custom_loops_path,
        "restaurant_suppliers": restaurant_suppliers_path,
        "loops_output":  loops_folder,
        "map_output":    map_folder,
        "raster_output": raster_subfolder,
    },
    "raster_dpi": RASTER_LAYER_DPI,
    "loop": {
        "max_stops":          MAX_STOPS,
        "max_radius_m":       MAX_RADIUS,
        "max_added_distance_m": MAX_ADDED_DISTANCE,
    },
    "car": {
        "node_penalty":          car_node_penalty,
        "acceleration":          car_acceleration,
        "min_cruising_time":     car_min_cruising_time,
        "min_cruising_speed":    car_min_cruising_speed,
        "max_stop_and_go_speed": car_max_stop_and_go_speed,
        "stopping_time":         car_stopping_time,
        "maxspeeds":             car_maxspeeds,
    },
    "ebike": {
        "node_penalty":          ebike_node_penalty,
        "acceleration":          ebike_acceleration,
        "min_cruising_time":     ebike_min_cruising_time,
        "min_cruising_speed":    ebike_min_cruising_speed,
        "max_stop_and_go_speed": ebike_max_stop_and_go_speed,
        "stopping_time":         bike_stopping_time,
        "bike_avoid_factor":     _BIKE_AVOID_FACTOR,
        "maxspeeds":             ebike_maxspeeds,
    },
    "scoring": {
        "min_bike_score":             min_bike_score,
        "bike_travel_time_reduction": bike_travel_time_reduction,
        "max_bike_extra_time":        max_bike_extra_time,
        "friendliness_weight":        friendliness_weight,
        "time_weight":                time_weight,
        "product_weight":             product_weight,
    },
    "car_score": {
        "config":                     car_score_config,
        "travel_time_reduction":      car_travel_time_reduction,
    },
    "bike_score": bike_score_config,
    "default_language": DEFAULT_LANGUAGE,
}

pipeline.run(config)
