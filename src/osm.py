"""
osm.py
======
OSM and network pipeline help: Geofabrik, Overpass, schools, pavement,
access, and bike separation.
"""

from __future__ import annotations
import os
import requests
import tempfile
import shapely
import geopandas as gpd
import pandas as pd
import osmnx as ox
from osm2geojson import json2geojson

from .geocode import sanitize_filename, get_folder


def _write_poly_file(aoi: gpd.GeoDataFrame | gpd.GeoSeries, poly_path: str):
    """
    Write AOI geometry to a .poly file in Osmosis format, correctly
    handling Polygons, MultiPolygons, and interior rings (holes).
    """
    geom = aoi.to_crs(4326).union_all()

    with open(poly_path, "w") as f:
        f.write("aoi\n")  # Polygon name

        def write_ring(coords, ring_id, is_hole=False):
            prefix = "!" if is_hole else ""
            f.write(f"{prefix}{ring_id}\n")
            for x, y in coords:
                f.write(f"  {x:.7f} {y:.7f}\n")
            f.write("END\n")

        ring_counter = 1
        polygons = []
        if geom.geom_type == "Polygon":
            polygons.append(geom)
        elif geom.geom_type == "MultiPolygon":
            polygons.extend(geom.geoms)
        else:
            raise ValueError(
                f"Unsupported geometry type for .poly file: {geom.geom_type}"
            )

        for poly in polygons:
            write_ring(poly.exterior.coords, ring_counter)
            ring_counter += 1
            for interior in poly.interiors:
                write_ring(interior.coords, ring_counter, is_hole=True)
                ring_counter += 1

        f.write("END\n")


def download_geofabrik(
    aoi: gpd.GeoDataFrame | gpd.GeoSeries, output_folder: str = None
):
    """Finds the smallest Geofabrik region that contains the AOI and downloads the PBF file."""
    aoi = aoi.to_crs(4326)
    aoi_geom = aoi.union_all()
    if not aoi_geom.is_valid:
        print("Validity problem:", shapely.validation.explain_validity(aoi_geom))

    url = "https://download.geofabrik.de/index-v1.json"
    print(f"Fetching Geofabrik index from {url}...")
    response = requests.get(url)
    response.raise_for_status()
    data = response.json()

    candidate_regions = []

    for feature in data["features"]:
        properties = feature.get("properties", {})
        if not properties.get("urls", {}).get("pbf") or not feature.get("geometry"):
            continue

        try:
            region_geom = shapely.geometry.shape(feature["geometry"])
        except Exception as e:
            print(
                f"Warning: Could not process geometry for {properties.get('name', 'N/A')}: {e}"
            )
            continue

        if region_geom.contains(aoi_geom):
            area = region_geom.area
            candidate_regions.append((area, properties))

    if not candidate_regions:
        raise ValueError(
            "No Geofabrik region was found to contain your AOI. Please ensure the AOI is correct and within a single region."
        )

    candidate_regions.sort(key=lambda x: x[0])
    best_region = candidate_regions[0][1]

    pbf_url = best_region.get("urls", {}).get("pbf")
    safe_name = sanitize_filename(best_region.get("name", "region"))
    filename = f"{safe_name}.osm.pbf"

    if output_folder is not None:
        os.makedirs(output_folder, exist_ok=True)
        output_file = os.path.join(output_folder, filename)
    else:
        output_file = filename

    if os.path.exists(output_file):
        print(f"File '{output_file}' already exists. Skipping download.")
        return output_file

    print(f"Downloading '{best_region['name']}' from {pbf_url} ...")
    pbf_response = requests.get(pbf_url, stream=True)
    pbf_response.raise_for_status()

    with open(output_file, "wb") as f:
        for chunk in pbf_response.iter_content(chunk_size=8192):
            f.write(chunk)

    print(f"Downloaded geofabrik to {output_file}")
    return output_file


def build_osmium_filter_args(tag_filters: dict[str, set[str] | None]) -> str:
    parts = []
    for k, vs in tag_filters.items():
        if vs is None or len(vs) == 0:
            parts.append(f"w/{k}")
        else:
            for v in vs:
                parts.append(f"w/{k}={v}")
    return " ".join(parts)


def osmium_network_filter(network_type: str) -> dict[str, set[str] | None]:
    walk_highways = {
        "footway",
        "pedestrian",
        "path",
        "living_street",
        "steps",
        "residential",
        "service",
        "unclassified",
        "track",
    }
    bike_highways = {
        "cycleway",
        "path",
        "residential",
        "living_street",
        "unclassified",
        "service",
        "track",
    }
    drive_highways = {
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
        "residential",
        "unclassified",
        "service",
        "living_street",
    }
    primary_highways = {
        "trunk",
        "trunk_link",
        "primary",
        "primary_link",
        "secondary",
        "secondary_link",
        "tertiary",
        "tertiary_link",
        "residential",
        "unclassified",
        "service",
        "living_street",
    }

    if network_type == "walk":
        tag_filters = {
            "highway": walk_highways,
            "foot": {"yes", "designated"},
        }
    elif network_type == "bike":
        tag_filters = {
            "highway": bike_highways,
            "bicycle": {"yes", "designated"},
        }
    elif network_type == "drive":
        tag_filters = {
            "highway": drive_highways,
        }
    elif network_type == "all":
        tag_filters = {
            "highway": None,
        }
    elif network_type == "walk+bike":
        combined = walk_highways.union(bike_highways)
        tag_filters = {
            "highway": combined,
            "foot": {"yes", "designated"},
            "bicycle": {"yes", "designated"},
        }
    elif network_type == "bike+car":
        combined = drive_highways.union(bike_highways)
        tag_filters = {
            "highway": combined,
            "bicycle": {"yes", "designated"},
        }
    elif network_type == "walk+bike+primary":
        combined = walk_highways.union(bike_highways)
        combined = combined.union(primary_highways)
        tag_filters = {
            "highway": combined,
            "foot": {"yes", "designated"},
            "bicycle": {"yes", "designated"},
        }
    else:
        raise ValueError(f"Unknown network_type: {network_type}")

    return build_osmium_filter_args(tag_filters)


def geofabrik_to_osm(
    output_file: str,
    input_file: str = "",
    aoi: gpd.GeoDataFrame | gpd.GeoSeries = None,
    osmium_filter_args: str = "",
    overwrite: bool = False,
):
    if os.path.isfile(output_file) and (not overwrite):
        print(f"File '{output_file}' already exists. Skipping conversion.")
        return output_file

    if not input_file or not os.path.isfile(input_file):
        input_path = get_folder(input_file)
        print(
            f"File {input_file} does not exist. Downloading best matching geofabrik file."
        )
        input_file = download_geofabrik(aoi, input_path)

    current_file = input_file
    tag_filter_temp_file_path = None

    try:
        if osmium_filter_args:
            with tempfile.NamedTemporaryFile(suffix=".pbf", delete=False) as f:
                tag_filter_temp_file_path = f.name

            print(f"Applying tag filter: {osmium_filter_args}")
            cmd_filter = f"osmium tags-filter --overwrite {current_file} {osmium_filter_args} -o {tag_filter_temp_file_path}"
            if os.system(cmd_filter) != 0:
                raise RuntimeError("Tag filtering failed")
            current_file = tag_filter_temp_file_path

        if aoi is not None:
            poly_temp_file_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".poly", delete=False) as f:
                    poly_temp_file_path = f.name

                print("Creating .poly file for AOI clipping...")
                _write_poly_file(aoi, poly_temp_file_path)

                print("Extracting by geometry...")
                cmd_extract = f"osmium extract --overwrite --polygon {poly_temp_file_path} {current_file} -o {output_file} -f osm"
                if os.system(cmd_extract) != 0:
                    raise RuntimeError("Geometry extraction failed")
            finally:
                if poly_temp_file_path and os.path.exists(poly_temp_file_path):
                    os.remove(poly_temp_file_path)
        else:
            print("No AOI provided, converting to .osm format...")
            cmd_convert = f"osmium cat {current_file} -o {output_file} -f osm"
            if os.system(cmd_convert) != 0:
                raise RuntimeError("OSM conversion failed")

    finally:
        if tag_filter_temp_file_path and os.path.exists(tag_filter_temp_file_path):
            os.remove(tag_filter_temp_file_path)

    print("Finished. Final output:", output_file)
    return output_file


def download_street_graph(
    bounds: gpd.GeoSeries | gpd.GeoDataFrame,
    network_type: str = "walk",
    custom_filter=None,
):
    if bounds.crs.is_projected:
        crs = bounds.crs
    else:
        crs = bounds.estimate_utm_crs()

    G = ox.graph.graph_from_polygon(
        bounds.to_crs(4326).union_all(),
        network_type=network_type,
        simplify=True,
        retain_all=True,
        truncate_by_edge=True,
        custom_filter=custom_filter,
    )
    G = ox.projection.project_graph(G, to_crs=crs)
    return G


def overpass_api_query(query: str, bounds: gpd.GeoDataFrame | gpd.GeoSeries, timeout: int = 120):

    bbox = bounds.to_crs(4326).total_bounds
    bbox_str = f"{bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]}"

    query = query.replace("{{bbox}}", bbox_str)
    query = query.replace("[out:xml]", "[out:json]")

    overpass_urls = [
        "https://overpass-api.de/api/interpreter",
        "https://lz4.overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
    ]

    response_json = None

    for i, url in enumerate(overpass_urls, start=1):
        try:
            response = requests.get(
                url,
                params={"data": query},
                timeout=timeout,
                headers={"User-Agent": "Python Overpass Client"},
            )

            if response.status_code != 200:
                print(f"Warning: server {i}/{len(overpass_urls)} failed ({url}) HTTP {response.status_code}")
                if i < len(overpass_urls):
                    print(f"Retrying with next Overpass server...")
                continue

            try:
                response_json = response.json()
            except Exception as e:
                print(f"Warning: server {i}/{len(overpass_urls)} failed JSON decode ({e})")
                if i < len(overpass_urls):
                    print("Retrying with next Overpass server...")
                continue

            if "elements" not in response_json:
                print(f"Warning: server {i}/{len(overpass_urls)} missing 'elements'")
                if i < len(overpass_urls):
                    print("Retrying with next Overpass server...")
                continue

            break  # success (even if empty)

        except Exception as e:
            print(f"Warning: server {i}/{len(overpass_urls)} request error ({e})")
            if i < len(overpass_urls):
                print("Retrying with next Overpass server...")
            time.sleep(1)
            continue

    if response_json is None:
        raise RuntimeError("All Overpass servers failed.")

    geojson_response = json2geojson(response_json)

    gdf = gpd.GeoDataFrame.from_features(
        geojson_response,
        crs="EPSG:4326",
    ).reset_index(drop=True)

    # Empty result (no retry)
    if len(gdf) == 0:
        print("Warning: No OSM features found for this query.")
        return gdf.to_crs(bounds.crs)

    # Expand tags safely
    if "tags" in gdf.columns:
        tags = gdf["tags"].apply(pd.Series)

        if "type" in tags.columns:
            tags = tags.rename(columns={"type": "geometry_type"})

        gdf = pd.concat(
            [gdf.drop(columns=["tags"]), tags],
            axis=1,
        )

    gdf = gdf.loc[:, ~gdf.columns.duplicated()]
    gdf = gdf.to_crs(bounds.crs)
    gdf = gdf[gdf.geometry.intersects(bounds.union_all())]

    return gdf


def green_areas(bounds, intersected_geom=None, min_area=200, min_width=10, buffer=5):
    query = """
        [out:json][timeout:25];
        (
        node[leisure = "garden"]({{bbox}});
        node[leisure = "park"]({{bbox}});
        node[landuse = "greenfield"]({{bbox}});
        node[landuse = "grass"]({{bbox}});
        node[landuse = "forest"]({{bbox}});
        way[leisure = "garden"]({{bbox}});
        way[leisure = "park"]({{bbox}});
        way[landuse = "greenfield"]({{bbox}});
        way[landuse = "grass"]({{bbox}});
        way[landuse = "forest"]({{bbox}});
        relation[leisure = "garden"]({{bbox}});
        relation[leisure = "park"]({{bbox}});
        relation[landuse = "greenfield"]({{bbox}});
        relation[landuse = "grass"]({{bbox}});
        relation[landuse = "forest"]({{bbox}});
        );
        out body;
        >;
        out skel qt;
    """
    green_areas_gdf = overpass_api_query(query, bounds)
    crs = green_areas_gdf.estimate_utm_crs()
    green_areas_gdf = green_areas_gdf.to_crs(crs)
    green_areas_gdf = green_areas_gdf[green_areas_gdf.geometry.area > min_area]
    green_areas_gdf = green_areas_gdf.geometry.union_all()
    green_areas_gdf = shapely.buffer(green_areas_gdf, -min_width, quad_segs=2)
    green_areas_gdf = shapely.buffer(green_areas_gdf, buffer + min_width, quad_segs=2)
    green_areas_gdf = shapely.buffer(green_areas_gdf, -buffer, quad_segs=2)
    green_areas_gdf = gpd.GeoDataFrame({}, geometry=shapely.get_parts(green_areas_gdf), crs=crs)

    if intersected_geom is not None:
        intersected_geom_union = (
            intersected_geom
            .to_crs(green_areas_gdf.crs)
            .union_all()
        )
        geoms = list(green_areas_gdf.geometry)
        shapely.prepare(geoms)
        green_areas_gdf = green_areas_gdf[shapely.intersects(geoms, intersected_geom_union)]

    return green_areas_gdf.to_crs(bounds.crs)


def bus_stops(bounds):
    query = """
        [out:json][timeout:25];
        (
        node["highway"="bus_stop"]({{bbox}});
        );
        out body;
        >;
        out skel qt;
    """
    stops = overpass_api_query(query, bounds)
    return stops.to_crs(bounds.crs)


def schools(bounds):
    query = """
        [out:xml] [timeout:25];
        (
            node["amenity"="school"]({{bbox}});
            way["amenity"="school"]({{bbox}});
            relation["amenity"="school"]({{bbox}});
        );
        (._;>;);
        out body;
    """
    pois = overpass_api_query(query, bounds)
    return pois.to_crs(bounds.crs)


def healthcare(bounds):
    query = """
        [out:xml] [timeout:25];
        (
            node["amenity"~"hospital|clinic|doctors|healthcare"]({{bbox}});
            way["amenity"~"hospital|clinic|doctors|healthcare"]({{bbox}});
            relation["amenity"~"hospital|clinic|doctors|healthcare"]({{bbox}});
            node["healthcare"]({{bbox}});
            way["healthcare"]({{bbox}});
            relation["healthcare"]({{bbox}});
        );
        (._;>;);
        out body;
    """
    pois = overpass_api_query(query, bounds)
    return pois.to_crs(bounds.crs)


def groceries(bounds):
    query = """[out:xml][timeout:25];
    (
        node["shop"~"supermarket|grocery|convenience"]({{bbox}});
        way["shop"~"supermarket|grocery|convenience"]({{bbox}});
        relation["shop"~"supermarket|grocery|convenience"]({{bbox}});
        node["amenity"="marketplace"]({{bbox}});
        way["amenity"="marketplace"]({{bbox}});
        relation["amenity"="marketplace"]({{bbox}});
    );
    (._;>;);
    out body;
    """ 
    pois = overpass_api_query(query, bounds)
    return pois.to_crs(bounds.crs)


def shops(bounds):
    query = """[out:xml][timeout:25];
        (
            node["shop"]({{bbox}});
            way["shop"]({{bbox}});
            relation["shop"]({{bbox}});
        );
        (._;>;);
        out body;
    """
    pois = overpass_api_query(query, bounds)
    return pois.to_crs(bounds.crs)


def restaurants(bounds):
    query = """[out:xml][timeout:25];
        (
            node["amenity"~"restaurant|bar|pub|cafe|fast_food"]({{bbox}});
            way["amenity"~"restaurant|bar|pub|cafe|fast_food"]({{bbox}});
            relation["amenity"~"restaurant|bar|pub|cafe|fast_food"]({{bbox}});
        );
        (._;>;);
        out body;
    """
    pois = overpass_api_query(query, bounds)
    return pois.to_crs(bounds.crs)


def libraries(bounds):
    query = """[out:xml][timeout:25];
        (
            node["amenity"="library"]({{bbox}});
            way["amenity"="library"]({{bbox}});
            relation["amenity"="library"]({{bbox}});
        );
        (._;>;);
        out body;
    """
    pois = overpass_api_query(query, bounds)
    return pois.to_crs(bounds.crs)


def pharmacies(bounds):
    query = """[out:xml][timeout:25];
        (
            node["amenity"="pharmacy"]({{bbox}});
            way["amenity"="pharmacy"]({{bbox}});
            relation["amenity"="pharmacy"]({{bbox}});
        );
        (._;>;);
        out body;
    """
    pois = overpass_api_query(query, bounds)
    return pois.to_crs(bounds.crs)


def pavement(edges_gdf, network_gdf):
    """Classify pavement type for OSMnx edges."""
    edges = edges_gdf.copy()

    ASPHALT = {"asphalt"}
    CONCRETE = {"concrete", "concrete:lanes", "concrete:plates"}
    COBBLESTONE = {"cobblestone", "unhewn_cobblestone", "sett", "paving_stones", "pebblestone"}
    UNPAVED = {
        "dirt", "gravel", "ground", "grass", "woodchips",
        "compacted", "fine_gravel", "unpaved", "turf",
        "bark_mulch", "grass_paver", "sand", "mud"
    }
    UNSUITABLE_SURFACE = {"steps", "stepping_stones", "rock", "bare_rock", "earth", "snow", "ice"}
    LANDUSE_UNSUITABLE = {"farmland", "meadow", "forest", "grass", "orchard", "plant_nursery", "farmyard"}

    def norm(x):
        if isinstance(x, (list, tuple, np.ndarray, pd.Series)):
            return x[0] if len(x) else None
        if pd.isna(x):
            return None
        return x

    def classify_way(row):
        surface = norm(row.get("surface"))
        landuse = norm(row.get("landuse"))
        if surface in UNSUITABLE_SURFACE:
            return "pavement_unsuitable_for_cars"
        if landuse in LANDUSE_UNSUITABLE:
            return "landuse_unsuitable_for_cars"
        if surface in ASPHALT:
            return "asphalt"
        if surface in CONCRETE:
            return "concrete"
        if surface in COBBLESTONE:
            return "cobblestone"
        if surface in UNPAVED:
            return "unpaved"
        return None

    net = network_gdf.reset_index()
    net = net[net["element"] == "way"].copy()
    net["pavement"] = net.apply(classify_way, axis=1)
    lookup = dict(zip(net["id"], net["pavement"]))

    priority = {
        "pavement_unsuitable_for_cars": 5,
        "landuse_unsuitable_for_cars": 4,
        "unpaved": 3,
        "cobblestone": 2,
        "concrete": 2,
        "asphalt": 2,
        None: 0,
    }

    def resolve(osmid):
        if isinstance(osmid, (list, tuple, np.ndarray, pd.Series)):
            ids = list(osmid)
        else:
            ids = [osmid]
        best = None
        best_score = -1
        for oid in ids:
            cls = lookup.get(oid, None)
            score = priority.get(cls, 0)
            if score > best_score:
                best = cls
                best_score = score
        return best

    edges["_pavement"] = edges["osmid"].apply(resolve)
    return list(edges["_pavement"])


def access_restrictions(edges_gdf, network_gdf):
    """Classify vehicle access restrictions on OSMnx edges."""
    edges = edges_gdf.copy()
    priority = {
        None: 0,
        "residents": 1,
        "low_emissions": 2,
        "permit": 3,
        "pedestrian+bikes": 4,
        "pedestrian": 5,
        "only_car": 6,
        "private": 7,
    }
    drive_highways = {
        "motorway", "motorway_link",
        "trunk", "trunk_link",
        "primary", "primary_link",
        "secondary", "secondary_link",
        "tertiary", "tertiary_link",
        "residential", "unclassified",
        "service", "living_street",
    }

    def norm(x):
        if x is None:
            return None
        if isinstance(x, (list, tuple, np.ndarray, pd.Series)):
            return x[0] if len(x) else None
        if isinstance(x, float) and pd.isna(x):
            return None
        if pd.isna(x):
            return None
        return x

    def is_no(val):
        return val in {"no", "dismount", "use_sidepath", "discouraged"}

    def classify_way(row):
        access = norm(row.get("access"))
        motorcar = norm(row.get("motorcar"))
        bicycle = norm(row.get("bicycle"))
        foot = norm(row.get("foot"))
        highway = row.get("highway")

        if isinstance(highway, str):
            highway_list = [highway]
        elif isinstance(highway, (list, tuple, np.ndarray, pd.Series)):
            highway_list = list(highway)
        else:
            highway_list = [highway]

        highway_list = [h for h in highway_list if h not in {None, "none", "unknown", "nan"}]

        if access == "private" or motorcar == "private":
            return "private"
        if "low_emission" in str(access) or "environmental" in str(access):
            return "low_emissions"
        if access in {"residential", "residents", "destination"}:
            return "residents"
        if access in {"permit", "customers", "delivery", "forestry", "agricultural"}:
            return "permit"

        pedestrian_way = any(h in {"footway", "pedestrian", "steps"} for h in highway_list)
        if pedestrian_way:
            if bicycle in {"no", "dismount", "discouraged"}:
                return "pedestrian"
            return "pedestrian+bikes"

        car_allowed_by_highway = any(h in drive_highways for h in highway_list)
        car_blocked = (motorcar == "no") or (access == "no")
        bike_blocked = is_no(bicycle)
        foot_blocked = is_no(foot)

        if car_allowed_by_highway and not car_blocked and foot_blocked and bike_blocked:
            return "only_car"
        if car_blocked:
            if bike_blocked:
                return "pedestrian"
            return "pedestrian+bikes"
        return None

    net = network_gdf.reset_index()
    net = net[net["element"] == "way"].copy()
    net["_access_restrictions"] = net.apply(classify_way, axis=1)
    lookup = dict(zip(net["id"], net["_access_restrictions"]))

    def resolve(osmid):
        if isinstance(osmid, (list, tuple, np.ndarray, pd.Series)):
            ids = list(osmid)
        else:
            ids = [osmid]
        best = None
        best_score = -1
        for oid in ids:
            cls = lookup.get(oid, None)
            score = priority.get(cls, 0)
            if score > best_score:
                best = cls
                best_score = score
        return best

    edges["_access_restrictions"] = edges["osmid"].apply(resolve)
    return list(edges["_access_restrictions"])


def bike_separation(edges_gdf, network_gdf):
    """Add bike_separation classification to OSMnx edges."""
    edges = edges_gdf.copy()
    priority = {
        None: 0,
        "none": 1,
        "soft": 2,
        "mixed": 3,
        "complete": 4,
        "prohibited": 5,
    }
    bike_cols = [
        "cycleway", "cycleway:left", "cycleway:right", "cycleway:lane",
        "cycleway:both:lane", "segregated", "is_sidepath", "bicycle_road",
    ]

    def norm(x):
        if isinstance(x, (list, tuple, np.ndarray, pd.Series)):
            return x[0] if len(x) else None
        if pd.isna(x):
            return None
        return x

    def classify_way(row) -> str:
        bicycle = norm(row.get("bicycle"))
        cycleway = norm(row.get("cycleway"))
        left = norm(row.get("cycleway:left"))
        right = norm(row.get("cycleway:right"))
        segregated = norm(row.get("segregated"))
        sidepath = norm(row.get("is_sidepath"))
        bike_road = norm(row.get("bicycle_road"))

        if bicycle in {"no", "private", "dismount"}:
            return "prohibited"
        if row.get("access") == "no" and bicycle in {None, "no"}:
            return "prohibited"
        if row.get("highway") in {"motorway"}:
            return "prohibited"
        if all(pd.isna(row.get(c)) for c in bike_cols) and bicycle is None:
            return "none"
        if row.get("cycleway") == "no":
            return "prohibited"

        if (
            cycleway in {"track", "separate"}
            or left == "track"
            or right == "track"
            or segregated == "yes"
            or sidepath == "yes"
            or bike_road == "yes"
        ):
            return "complete"

        if (
            "advisory" in str(row.get("cycleway:lane"))
            or "advisory" in str(row.get("cycleway:both:lane"))
            or "pictogram" in str(row.get("cycleway:both:lane"))
            or cycleway == "lane"
            or left == "lane"
            or right == "lane"
        ):
            return "soft"

        if bicycle in {"yes", "permissive", "designated", "use_sidepath", None}:
            return "mixed"
        return "mixed"

    net = network_gdf.reset_index()
    net = net[net["element"] == "way"].copy()
    net["bike_separation"] = net.apply(classify_way, axis=1)
    lookup = dict(zip(net["id"], net["bike_separation"]))

    def resolve(osmid):
        if isinstance(osmid, (list, tuple, np.ndarray, pd.Series)):
            ids = list(osmid)
        else:
            ids = [osmid]
        best = None
        best_score = -1
        for oid in ids:
            cls = lookup.get(oid, "none")
            score = priority.get(cls, 0)
            if score > best_score:
                best = cls
                best_score = score
        return best

    edges["bike_separation"] = edges["osmid"].apply(resolve)
    return list(edges["bike_separation"])
