"""
data_utils.py
=============
Small helpers used throughout the pipeline: CSV/list parsing, coordinate
fixing, JSON-safe GeoDataFrame conversion. Logic is unchanged from
main_copy.py — only factored out so it can be imported from multiple
modules without copy-pasting.
"""

from __future__ import annotations

import ast
import json

import numpy as np
import pandas as pd


def safe_parse_list(x):
    if pd.isna(x):
        return []
    if isinstance(x, list):
        return x
    if not isinstance(x, str):
        return [x]

    x = x.strip()
    if not x:
        return []
    if x.startswith("[") and x.endswith("]"):
        try:
            val = ast.literal_eval(x)
            return val if isinstance(val, list) else [val]
        except Exception:
            return [x]
    return [x]


def is_list_column(series: pd.Series, sample_size: int = 50, threshold: float = 0.8) -> bool:
    sample = series.dropna().astype(str).head(sample_size)
    parseable = total = 0
    for v in sample:
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            total += 1
            try:
                ast.literal_eval(v)
                parseable += 1
            except Exception:
                pass
    return total > 0 and (parseable / total) >= threshold


def fix_coord(x):
    if pd.isna(x):
        return None
    if isinstance(x, (int, float)):
        return float(x)

    x = str(x).strip()
    parts = x.split(".")
    if len(parts) > 2:
        x = parts[0] + "." + "".join(parts[1:])
    try:
        return float(x)
    except Exception:
        return None


def is_missing(x) -> bool:
    if isinstance(x, (list, tuple, set, np.ndarray)):
        return len(x) == 0
    return pd.isna(x)


def json_serializable(gdf, allow_lists: bool = True):
    """Stringify list/dict columns so a GeoDataFrame can round-trip through
    folium.GeoJson / to_json() without choking on Python objects.

    allow_lists=False additionally stringifies plain Python lists found in
    *scalar* (non-geometry) columns that are not already list-typed pandas
    columns — used for the road_edges frame feeding the PNG layer renderer,
    where list-valued OSM tag columns must become "[]"-style strings before
    being filtered with `!= "[]"`.
    """
    gdf = gdf.copy()

    # -----------------------------
    # 1. remove invalid geometries
    # -----------------------------
    gdf = gdf[gdf.geometry.notnull()]
    gdf = gdf[gdf.is_valid]

    # -----------------------------
    # 2. safe converter
    # -----------------------------
    def is_null(x):
        if x is None:
            return True
        if isinstance(x, float) and pd.isna(x):
            return True
        return False

    def convert(x):
        # nulls
        if is_null(x):
            return "[]"

        # lists / tuples
        if isinstance(x, (list, tuple)):
            vals = list(x)

            if allow_lists:
                return json.dumps(vals)

            # allow_lists == False
            if len(vals) == 0:
                return None

            try:
                return max(vals)
            except Exception:
                # fallback for mixed / non-comparable types
                return max(str(v) for v in vals)

        # numpy arrays
        if isinstance(x, np.ndarray):
            vals = x.tolist()

            if allow_lists:
                return json.dumps(vals)

            if len(vals) == 0:
                return None

            try:
                return max(vals)
            except Exception:
                return max(str(v) for v in vals)

        # dicts
        if isinstance(x, dict):
            return json.dumps(x)

        # everything else stays as string
        return str(x)

    # -----------------------------
    # 3. only convert unsafe columns
    # -----------------------------
    for col in gdf.columns:
        if col == "geometry":
            continue

        if pd.api.types.is_numeric_dtype(gdf[col]):
            continue

        if pd.api.types.is_bool_dtype(gdf[col]):
            continue

        if pd.api.types.is_datetime64_any_dtype(gdf[col]):
            continue

        gdf[col] = gdf[col].apply(convert)

    # -----------------------------
    # 4. final geometry safety check
    # -----------------------------
    gdf = gdf[gdf.geometry.is_valid]

    # -----------------------------
    # 5. fix "[]" strings -> None
    # -----------------------------
    for col in gdf.columns:
        if col == "geometry":
            continue

        if gdf[col].dtype == "object" or gdf[col].dtype == "str":
            gdf[col] = gdf[col].replace("[]", None)
            
    return gdf
