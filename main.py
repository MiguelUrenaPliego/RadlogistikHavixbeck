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
car_node_penalty          = 5
car_acceleration          = 1.5
car_min_cruising_time     = 5
car_min_cruising_speed    = 10
car_max_stop_and_go_speed = 50
car_stopping_time         = 1   # minutes

car_maxspeeds = {
    "living_street":  30,
    "motorway":      100,
    "motorway_link":  60,
    "primary":        50,
    "primary_link":   50,
    "residential":    30,
    "secondary":      40,
    "secondary_link": 40,
    "service":        20,
    "tertiary":       40,
    "tertiary_link":  40,
    "trunk":          80,
    "trunk_link":     60,
    "unclassified":   40,
}

# ── E-bike routing ────────────────────────────────────────────────────────────
ebike_node_penalty           = 1
ebike_acceleration           = 2
ebike_min_cruising_time      = 5
ebike_min_cruising_speed     = 5
ebike_max_stop_and_go_speed  = 0
bike_stopping_time           = 0.1  # minutes
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

# ── Scoring ───────────────────────────────────────────────────────────────────
min_bikefriendliness      = 5
max_travel_time_reduction = 0.4   # 40% perceived time reduction at best
max_bike_extra_time       = 0.1   # 10% extra over car is still acceptable
friendliness_weight       = 3
time_weight               = 4
product_weight            = 3

# ── Bike-friendliness config ──────────────────────────────────────────────────
bikefriendliness_config = {
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
            "secondary":      3,
            "path":          10,
            "unclassified":   1,
            "tertiary":       5,
            "service":        8,
            "track":         10,
            "residential":   10,
            "living_street": 10,
            "footway":        7,
            "cycleway":      10,
            "primary":        1,
            "motorway":       1,
            "tertiary_link":  5,
            "pedestrian":     7,
            "secondary_link": 3,
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
        "weight": 10,
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
        "min_bikefriendliness":      min_bikefriendliness,
        "max_travel_time_reduction": max_travel_time_reduction,
        "max_bike_extra_time":       max_bike_extra_time,
        "friendliness_weight":       friendliness_weight,
        "time_weight":               time_weight,
        "product_weight":            product_weight,
    },
    "bikefriendliness": bikefriendliness_config,
    "default_language": DEFAULT_LANGUAGE,
}

pipeline.run(config)
