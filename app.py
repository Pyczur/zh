from __future__ import annotations

import base64
import io
import json
import math
import re
import socket
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
from flask import Flask, jsonify, render_template, request
from PIL import Image
from pyproj import CRS, Transformer
from shapely.geometry import Point
from shapely.prepared import prep

try:
    from scipy.spatial import cKDTree
except ImportError:  # SciPy is used when installed; NumPy IDW below is the fallback.
    cKDTree = None


BASE_DIR = Path(__file__).resolve().parent

# If the automatic detector chooses the wrong columns for a different CSV,
# set any of these names manually, for example: MANUAL_X_COLUMN = "longitude".
MANUAL_X_COLUMN = None
MANUAL_Y_COLUMN = None
MANUAL_COORD_CRS = None  # Example: "EPSG:4326" for lon/lat or "EPSG:23700".
MANUAL_ID_COLUMN = None
MANUAL_TIME_COLUMN = None
MANUAL_YEAR_COLUMN = None
MANUAL_MONTH_COLUMN = None


app = Flask(__name__)
cKDTree = None


@dataclass
class TimeInfo:
    has_time: bool
    label_column: str | None
    source_columns: list[str]
    values: list[str]
    message: str


def normalize_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def clean_number(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    value = float(value)
    if math.isnan(value) or math.isinf(value):
        return None
    return value


def clean_label(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def find_first_csv() -> Path | None:
    csv_files = sorted(BASE_DIR.glob("*.csv"))
    if not csv_files:
        return None
    return max(csv_files, key=lambda path: path.stat().st_size)


def find_first_shapefile() -> Path | None:
    shapefiles = sorted(BASE_DIR.rglob("*.shp"))
    if not shapefiles:
        return None
    return shapefiles[0]


def read_csv_safely(path: Path) -> tuple[pd.DataFrame, str]:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp1250", "latin1"):
        try:
            return pd.read_csv(path, encoding=encoding), encoding
        except UnicodeDecodeError as exc:
            last_error = exc
    raise RuntimeError(f"Could not read CSV with common encodings: {last_error}")


def numeric_series(df: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(df[column], errors="coerce")


def lon_score(column: str) -> int:
    name = normalize_name(column)
    if name in {"longitude", "long", "lon", "lng"}:
        return 120
    if "longitude" in name or "lon" in name or "lng" in name:
        return 100
    if name in {"x", "xcoord", "xcoordinate", "coordx", "utmx"}:
        return 70
    if name in {"e", "east", "easting", "eastcoord"}:
        return 55
    return 0


def lat_score(column: str) -> int:
    name = normalize_name(column)
    if name in {"latitude", "lat"}:
        return 120
    if "latitude" in name or "lat" in name:
        return 100
    if name in {"y", "ycoord", "ycoordinate", "coordy", "utmy"}:
        return 70
    if name in {"n", "north", "northing", "northcoord"}:
        return 55
    return 0


def values_look_like_lon_lat(x_values: pd.Series, y_values: pd.Series) -> bool:
    x = pd.to_numeric(x_values, errors="coerce").dropna()
    y = pd.to_numeric(y_values, errors="coerce").dropna()
    if x.empty or y.empty:
        return False
    return (
        x.between(-180, 180).mean() > 0.95
        and y.between(-90, 90).mean() > 0.95
    )


def detect_coordinate_columns(df: pd.DataFrame) -> tuple[str, str, str]:
    if MANUAL_X_COLUMN and MANUAL_Y_COLUMN:
        if MANUAL_X_COLUMN not in df.columns or MANUAL_Y_COLUMN not in df.columns:
            raise RuntimeError("Manual coordinate columns are not present in the CSV.")
        crs = MANUAL_COORD_CRS or "EPSG:4326"
        return MANUAL_X_COLUMN, MANUAL_Y_COLUMN, crs

    numeric_columns = [
        column
        for column in df.columns
        if numeric_series(df, column).notna().sum() > 0
    ]
    best: tuple[int, str, str] | None = None
    for x_column in numeric_columns:
        for y_column in numeric_columns:
            if x_column == y_column:
                continue
            score = lon_score(x_column) + lat_score(y_column)
            if score <= 0:
                continue
            if values_look_like_lon_lat(df[x_column], df[y_column]):
                score += 60
            if best is None or score > best[0]:
                best = (score, x_column, y_column)

    if best is None:
        raise RuntimeError(
            "Could not detect coordinate columns. Set MANUAL_X_COLUMN and "
            "MANUAL_Y_COLUMN near the top of app.py."
        )

    _, x_column, y_column = best
    crs = MANUAL_COORD_CRS
    if crs is None:
        if values_look_like_lon_lat(df[x_column], df[y_column]):
            crs = "EPSG:4326"
        else:
            crs = "EPSG:4326"
    return x_column, y_column, crs


def detect_id_column(df: pd.DataFrame, excluded: set[str]) -> str | None:
    if MANUAL_ID_COLUMN:
        return MANUAL_ID_COLUMN if MANUAL_ID_COLUMN in df.columns else None

    best: tuple[int, str] | None = None
    for column in df.columns:
        if column in excluded:
            continue
        name = normalize_name(column)
        score = 0
        if name in {"well", "wellid", "wellname", "station", "stationid", "kut", "kutid"}:
            score = 120
        elif "well" in name or "kut" in name or "station" in name:
            score = 100
        elif name in {"id", "name", "identifier"} or name.endswith("id"):
            score = 60
        if score:
            unique_count = df[column].nunique(dropna=True)
            if unique_count == len(df) and name in {"id", "identifier"}:
                score -= 30
            if best is None or score > best[0]:
                best = (score, column)
    return best[1] if best else None


def find_year_month_columns(df: pd.DataFrame) -> tuple[str | None, str | None]:
    year_column = MANUAL_YEAR_COLUMN
    month_column = MANUAL_MONTH_COLUMN
    if year_column and year_column not in df.columns:
        year_column = None
    if month_column and month_column not in df.columns:
        month_column = None

    for column in df.columns:
        name = normalize_name(column)
        if year_column is None and name in {"year", "yr", "ev"}:
            year_column = column
        if month_column is None and name in {"month", "mon", "honap"}:
            month_column = column
    return year_column, month_column


def candidate_time_columns(df: pd.DataFrame, excluded: set[str]) -> list[str]:
    candidates = []
    for column in df.columns:
        if column in excluded:
            continue
        name = normalize_name(column)
        is_candidate = (
            name in {"date", "datetime", "time", "timestamp", "datum", "ido", "sampledate"}
            or "date" in name
            or "time" in name
            or "datum" in name
        )
        if is_candidate:
            unique_count = df[column].nunique(dropna=True)
            if 1 < unique_count < len(df):
                candidates.append(column)
    return candidates


def format_time_values(series: pd.Series) -> tuple[pd.Series, list[str]]:
    parsed = pd.to_datetime(series, errors="coerce")
    if parsed.notna().mean() > 0.7 and not pd.api.types.is_numeric_dtype(series):
        if (parsed.dt.time == pd.Timestamp("00:00:00").time()).all():
            labels = parsed.dt.strftime("%Y-%m-%d")
        else:
            labels = parsed.dt.strftime("%Y-%m-%d %H:%M:%S")
        ordered = sorted(labels.dropna().unique().tolist())
        return labels, ordered

    labels = series.map(clean_label)
    sortable = pd.DataFrame({"label": labels, "raw": series})
    sortable = sortable[sortable["label"] != ""].drop_duplicates("label")
    sortable["_number"] = pd.to_numeric(sortable["raw"], errors="coerce")
    if sortable["_number"].notna().all():
        sortable = sortable.sort_values("_number")
    else:
        sortable = sortable.sort_values("label")
    return labels, sortable["label"].tolist()


def detect_time_info(df: pd.DataFrame, excluded: set[str]) -> TimeInfo:
    if MANUAL_TIME_COLUMN and MANUAL_TIME_COLUMN in df.columns:
        labels, values = format_time_values(df[MANUAL_TIME_COLUMN])
        df["__time_label"] = labels
        return TimeInfo(True, "__time_label", [MANUAL_TIME_COLUMN], values, "")

    candidates = candidate_time_columns(df, excluded)
    if candidates:
        time_column = candidates[0]
        labels, values = format_time_values(df[time_column])
        df["__time_label"] = labels
        return TimeInfo(True, "__time_label", [time_column], values, "")

    year_column, month_column = find_year_month_columns(df)
    if year_column and month_column:
        years = numeric_series(df, year_column)
        months = numeric_series(df, month_column)

        def make_label(row: tuple[float, float]) -> str:
            year, month = row
            if pd.isna(year) or pd.isna(month):
                return ""
            if year >= 1000 and 1 <= month <= 12:
                return f"{int(year):04d}-{int(month):02d}"
            return f"Year {year:g} / Month {month:g}"

        labels = pd.Series(
            [make_label(item) for item in zip(years, months)],
            index=df.index,
            dtype="object",
        )
        order_frame = pd.DataFrame(
            {"label": labels, "year": years, "month": months}
        ).dropna(subset=["year", "month"])
        order_frame = order_frame[order_frame["label"] != ""].drop_duplicates("label")
        order_frame = order_frame.sort_values(["year", "month"])
        df["__time_label"] = labels
        return TimeInfo(
            True,
            "__time_label",
            [year_column, month_column],
            order_frame["label"].tolist(),
            "",
        )

    return TimeInfo(
        False,
        None,
        [],
        [],
        "No repeated date/time column was found. Parameter visualization still works.",
    )


def detect_parameter_columns(
    df: pd.DataFrame,
    coordinate_columns: set[str],
    id_column: str | None,
    time_info: TimeInfo,
) -> list[str]:
    excluded = set(coordinate_columns)
    if id_column:
        excluded.add(id_column)
    excluded.update(time_info.source_columns)
    excluded.add("__time_label")

    blocked_names = {
        "id",
        "identifier",
        "date",
        "datetime",
        "time",
        "timestamp",
        "datum",
        "ido",
        "year",
        "yr",
        "ev",
        "month",
        "mon",
        "honap",
    }

    parameters = []
    for column in df.columns:
        if column in excluded:
            continue
        if normalize_name(column) in blocked_names:
            continue
        series = numeric_series(df, column)
        if series.notna().sum() >= 3:
            df[column] = series
            parameters.append(column)
    return parameters


def bounds_to_leaflet(bounds: np.ndarray | list[float], source_crs: CRS | str) -> list[list[float]]:
    minx, miny, maxx, maxy = [float(value) for value in bounds]
    transformer = Transformer.from_crs(source_crs, "EPSG:4326", always_xy=True)
    corners = [
        transformer.transform(minx, miny),
        transformer.transform(minx, maxy),
        transformer.transform(maxx, miny),
        transformer.transform(maxx, maxy),
    ]
    lons = [point[0] for point in corners]
    lats = [point[1] for point in corners]
    return [[min(lats), min(lons)], [max(lats), max(lons)]]


def make_mask(boundary_geometry: Any, grid_x: np.ndarray, grid_y: np.ndarray) -> np.ndarray:
    if boundary_geometry is None:
        return np.ones(grid_x.shape, dtype=bool)
    try:
        from shapely import contains_xy

        return contains_xy(boundary_geometry, grid_x, grid_y)
    except Exception:
        prepared = prep(boundary_geometry)
        flat_mask = [
            prepared.contains(Point(float(x), float(y)))
            for x, y in zip(grid_x.ravel(), grid_y.ravel())
        ]
        return np.array(flat_mask, dtype=bool).reshape(grid_x.shape)


def viridis_like_rgba(normalized: np.ndarray) -> np.ndarray:
    stops = np.array([0.0, 0.34, 0.68, 1.0], dtype=float)
    colors = np.array(
        [
            [68, 1, 84],
            [49, 104, 142],
            [53, 183, 121],
            [253, 231, 37],
        ],
        dtype=float,
    )
    rgba = np.zeros((*normalized.shape, 4), dtype=np.uint8)
    for channel in range(3):
        rgba[..., channel] = np.interp(normalized, stops, colors[:, channel]).astype(
            np.uint8
        )
    rgba[..., 3] = 255
    return rgba


def idw_with_numpy(
    points_xy: np.ndarray,
    values: np.ndarray,
    query_points: np.ndarray,
    power: float = 2.0,
    chunk_size: int = 5000,
) -> np.ndarray:
    interpolated = np.empty(len(query_points), dtype=float)
    for start in range(0, len(query_points), chunk_size):
        end = min(start + chunk_size, len(query_points))
        chunk = query_points[start:end]
        distances = np.sqrt(
            np.sum((chunk[:, np.newaxis, :] - points_xy[np.newaxis, :, :]) ** 2, axis=2)
        )
        zero_rows = np.any(distances == 0, axis=1)
        chunk_values = np.empty(len(chunk), dtype=float)

        if zero_rows.any():
            zero_position = np.argmax(distances[zero_rows] == 0, axis=1)
            chunk_values[zero_rows] = values[zero_position]

        nonzero_rows = ~zero_rows
        if nonzero_rows.any():
            safe_distances = np.maximum(distances[nonzero_rows], 1e-12)
            weights = 1.0 / np.power(safe_distances, power)
            chunk_values[nonzero_rows] = (
                np.sum(weights * values[np.newaxis, :], axis=1) / np.sum(weights, axis=1)
            )

        interpolated[start:end] = chunk_values
    return interpolated


def idw_interpolate(
    points_xy: np.ndarray,
    values: np.ndarray,
    query_points: np.ndarray,
    power: float = 2.0,
) -> np.ndarray:
    if cKDTree is None:
        return idw_with_numpy(points_xy, values, query_points, power=power)

    k = min(12, len(values))
    tree = cKDTree(points_xy)
    distances, indexes = tree.query(query_points, k=k)
    if k == 1:
        distances = distances[:, np.newaxis]
        indexes = indexes[:, np.newaxis]

    interpolated = np.empty(len(query_points), dtype=float)
    zero_rows = np.any(distances == 0, axis=1)
    if zero_rows.any():
        zero_position = np.argmax(distances[zero_rows] == 0, axis=1)
        interpolated[zero_rows] = values[indexes[zero_rows, zero_position]]

    nonzero_rows = ~zero_rows
    if nonzero_rows.any():
        safe_distances = np.maximum(distances[nonzero_rows], 1e-12)
        weights = 1.0 / np.power(safe_distances, power)
        neighbor_values = values[indexes[nonzero_rows]]
        interpolated[nonzero_rows] = (
            np.sum(weights * neighbor_values, axis=1) / np.sum(weights, axis=1)
        )
    return interpolated


def create_interpolation_image(
    points_xy: np.ndarray,
    values: np.ndarray,
    bounds: tuple[float, float, float, float],
    output_crs: CRS | str,
    boundary_geometry: Any = None,
    grid_size: int = 180,
) -> dict[str, Any] | None:
    valid = np.isfinite(points_xy).all(axis=1) & np.isfinite(values)
    points_xy = points_xy[valid]
    values = values[valid]
    if len(values) == 0:
        return None

    minx, miny, maxx, maxy = bounds
    if minx == maxx or miny == maxy:
        return None

    width = grid_size
    height = grid_size
    xs = np.linspace(minx, maxx, width)
    ys = np.linspace(maxy, miny, height)
    grid_x, grid_y = np.meshgrid(xs, ys)
    query_points = np.column_stack([grid_x.ravel(), grid_y.ravel()])

    interpolated = idw_interpolate(points_xy, values, query_points)
    grid = interpolated.reshape((height, width))
    mask = make_mask(boundary_geometry, grid_x, grid_y)
    grid[~mask] = np.nan

    finite_values = grid[np.isfinite(grid)]
    if finite_values.size == 0:
        return None

    vmin = float(np.nanmin(finite_values))
    vmax = float(np.nanmax(finite_values))
    if math.isclose(vmin, vmax):
        normalized = np.full(grid.shape, 0.5, dtype=float)
    else:
        normalized = (grid - vmin) / (vmax - vmin)
    normalized = np.clip(np.nan_to_num(normalized, nan=0.0), 0.0, 1.0)

    rgba = viridis_like_rgba(normalized)
    rgba[..., 3] = np.where(np.isfinite(grid), 215, 0).astype(np.uint8)

    image = Image.fromarray(rgba, mode="RGBA")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")

    return {
        "image": f"data:image/png;base64,{encoded}",
        "bounds": bounds_to_leaflet([minx, miny, maxx, maxy], output_crs),
        "min": vmin,
        "max": vmax,
    }


def load_project_data() -> dict[str, Any]:
    messages: list[str] = []
    csv_path = find_first_csv()
    if csv_path is None:
        raise RuntimeError("No CSV file was found in the project folder.")

    df, encoding = read_csv_safely(csv_path)
    messages.append(f"Loaded CSV: {csv_path.name} ({len(df)} rows, {encoding}).")

    shapefile_path = find_first_shapefile()
    boundary_gdf = None
    boundary_geojson = None
    boundary_geometry = None
    boundary_bounds = None
    boundary_crs = None

    if shapefile_path:
        boundary_gdf = gpd.read_file(shapefile_path)
        if boundary_gdf.crs is None:
            boundary_gdf = boundary_gdf.set_crs("EPSG:4326")
            messages.append("Boundary shapefile had no CRS; assuming EPSG:4326.")
        boundary_crs = CRS.from_user_input(boundary_gdf.crs)
        boundary_geometry = boundary_gdf.geometry.union_all()
        boundary_wgs84 = boundary_gdf.to_crs("EPSG:4326")
        boundary_geojson = json.loads(boundary_wgs84.to_json())
        boundary_bounds = bounds_to_leaflet(boundary_gdf.total_bounds, boundary_crs)
        messages.append(f"Loaded boundary: {shapefile_path.name}.")
    else:
        messages.append("No shapefile was found. The dashboard will run without a boundary.")

    x_column, y_column, coordinate_crs_text = detect_coordinate_columns(df)
    coordinate_crs = CRS.from_user_input(coordinate_crs_text)
    if MANUAL_COORD_CRS is None and boundary_crs and not values_look_like_lon_lat(df[x_column], df[y_column]):
        coordinate_crs = boundary_crs

    excluded_for_id_and_time = {x_column, y_column}
    id_column = detect_id_column(df, excluded_for_id_and_time)
    time_info = detect_time_info(df, excluded_for_id_and_time | ({id_column} if id_column else set()))
    parameters = detect_parameter_columns(df, {x_column, y_column}, id_column, time_info)

    if not parameters:
        messages.append("No numeric parameter columns were found.")
    if time_info.message:
        messages.append(time_info.message)

    interpolation_crs = boundary_crs or coordinate_crs
    if interpolation_crs.to_epsg() == 4326:
        interpolation_crs = CRS.from_epsg(3857)

    if boundary_gdf is not None:
        boundary_for_interpolation = boundary_gdf.to_crs(interpolation_crs)
        boundary_geometry_for_interpolation = boundary_for_interpolation.geometry.union_all()
        interpolation_bounds = tuple(float(value) for value in boundary_for_interpolation.total_bounds)
    else:
        boundary_geometry_for_interpolation = None
        points = gpd.GeoDataFrame(
            df.copy(),
            geometry=gpd.points_from_xy(numeric_series(df, x_column), numeric_series(df, y_column)),
            crs=coordinate_crs,
        ).to_crs(interpolation_crs)
        interpolation_bounds = tuple(float(value) for value in points.total_bounds)

    return {
        "status": "ok",
        "messages": messages,
        "csv_path": csv_path,
        "shapefile_path": shapefile_path,
        "df": df,
        "x_column": x_column,
        "y_column": y_column,
        "coordinate_crs": coordinate_crs,
        "id_column": id_column,
        "time_info": time_info,
        "parameters": parameters,
        "boundary_geojson": boundary_geojson,
        "boundary_bounds": boundary_bounds,
        "boundary_geometry": boundary_geometry,
        "boundary_geometry_for_interpolation": boundary_geometry_for_interpolation,
        "interpolation_crs": interpolation_crs,
        "interpolation_bounds": interpolation_bounds,
    }


def make_error_state(error: Exception) -> dict[str, Any]:
    return {
        "status": "error",
        "messages": [str(error)],
        "parameters": [],
        "time_info": TimeInfo(False, None, [], [], ""),
    }


PROJECT = make_error_state(RuntimeError("Project not loaded yet."))
try:
    PROJECT = load_project_data()
except Exception as exc:  # Keep the web page available so it can show the error.
    PROJECT = make_error_state(exc)


@app.route("/")
def index() -> str:
    return render_template("index.html")


@app.route("/api/metadata")
def metadata() -> Any:
    time_info: TimeInfo = PROJECT.get("time_info", TimeInfo(False, None, [], [], ""))
    return jsonify(
        {
            "status": PROJECT.get("status", "error"),
            "messages": PROJECT.get("messages", []),
            "csv_file": PROJECT.get("csv_path").name if PROJECT.get("csv_path") else None,
            "shapefile": PROJECT.get("shapefile_path").name if PROJECT.get("shapefile_path") else None,
            "columns": {
                "x": PROJECT.get("x_column"),
                "y": PROJECT.get("y_column"),
                "well_id": PROJECT.get("id_column"),
                "time": time_info.source_columns,
            },
            "parameters": PROJECT.get("parameters", []),
            "default_parameter": PROJECT.get("parameters", [None])[0],
            "has_time": time_info.has_time,
            "times": [{"value": value, "label": value} for value in time_info.values],
            "time_message": time_info.message,
            "boundary": PROJECT.get("boundary_geojson"),
            "bounds": PROJECT.get("boundary_bounds"),
        }
    )


def filtered_wells(parameter: str, selected_time: str | None) -> tuple[gpd.GeoDataFrame, str | None]:
    df = PROJECT["df"]
    x_column = PROJECT["x_column"]
    y_column = PROJECT["y_column"]
    id_column = PROJECT.get("id_column")
    time_info: TimeInfo = PROJECT["time_info"]

    if time_info.has_time:
        selected_time = selected_time or (time_info.values[0] if time_info.values else None)
        if selected_time:
            df = df[df[time_info.label_column] == selected_time]

    work = pd.DataFrame(
        {
            "_x": numeric_series(df, x_column),
            "_y": numeric_series(df, y_column),
            "_value": numeric_series(df, parameter),
        }
    )
    if id_column:
        work["_well_id"] = df[id_column].map(clean_label)
    else:
        work["_well_id"] = ""
    work = work.dropna(subset=["_x", "_y", "_value"])

    if work.empty:
        return gpd.GeoDataFrame(geometry=[], crs=PROJECT["coordinate_crs"]), selected_time

    group_columns = ["_x", "_y"]
    if id_column:
        group_columns.insert(0, "_well_id")
    grouped = work.groupby(group_columns, dropna=False, as_index=False)["_value"].mean()

    if not id_column:
        grouped["_well_id"] = [f"Well {index + 1}" for index in range(len(grouped))]

    return (
        gpd.GeoDataFrame(
            grouped,
            geometry=gpd.points_from_xy(grouped["_x"], grouped["_y"]),
            crs=PROJECT["coordinate_crs"],
        ),
        selected_time,
    )


@app.route("/api/data")
def data() -> Any:
    if PROJECT.get("status") != "ok":
        return jsonify({"status": "error", "messages": PROJECT.get("messages", [])}), 500

    parameter = request.args.get("parameter") or PROJECT["parameters"][0]
    if parameter not in PROJECT["parameters"]:
        return jsonify({"status": "error", "messages": [f"Unknown parameter: {parameter}"]}), 400

    selected_time = request.args.get("time")
    wells, selected_time = filtered_wells(parameter, selected_time)
    messages: list[str] = []

    wells_wgs84 = wells.to_crs("EPSG:4326") if not wells.empty else wells
    features = []
    for _, row in wells_wgs84.iterrows():
        value = clean_number(row["_value"])
        lon = clean_number(row.geometry.x)
        lat = clean_number(row.geometry.y)
        if value is None or lon is None or lat is None:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "well_id": row["_well_id"],
                    "x": clean_number(row["_x"]),
                    "y": clean_number(row["_y"]),
                    "longitude": lon,
                    "latitude": lat,
                    "parameter": parameter,
                    "value": value,
                    "time": selected_time,
                },
            }
        )

    interpolation = None
    if len(wells) > 0:
        wells_interpolation = wells.to_crs(PROJECT["interpolation_crs"])
        points_xy = np.array(
            [[geometry.x, geometry.y] for geometry in wells_interpolation.geometry],
            dtype=float,
        )
        values = wells_interpolation["_value"].to_numpy(dtype=float)
        interpolation = create_interpolation_image(
            points_xy,
            values,
            PROJECT["interpolation_bounds"],
            PROJECT["interpolation_crs"],
            PROJECT["boundary_geometry_for_interpolation"],
        )
        if len(wells) < 3:
            messages.append(
                "Interpolation is based on fewer than three wells, so treat it as a rough guide."
            )
    else:
        messages.append("No well values were available for this parameter/time selection.")

    if interpolation is None and len(wells) > 0:
        messages.append("Interpolation could not be created for the current selection.")

    value_series = wells["_value"] if not wells.empty else pd.Series(dtype=float)
    return jsonify(
        {
            "status": "ok",
            "messages": messages,
            "parameter": parameter,
            "time": selected_time,
            "well_count": len(features),
            "value_min": clean_number(value_series.min()) if not value_series.empty else None,
            "value_max": clean_number(value_series.max()) if not value_series.empty else None,
            "wells": {"type": "FeatureCollection", "features": features},
            "interpolation": interpolation,
        }
    )


def get_network_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip_address = sock.getsockname()[0]
            if ip_address and not ip_address.startswith("127."):
                return ip_address
    except OSError:
        pass

    try:
        for address in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip_address = address[4][0]
            if ip_address and not ip_address.startswith("127."):
                return ip_address
    except OSError:
        pass
    return None


def find_available_port(start: int = 5000, attempts: int = 20) -> int:
    for port in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("0.0.0.0", port))
            except OSError:
                continue
            return port
    return start


if __name__ == "__main__":
    port = find_available_port(5000)
    local_url = f"http://127.0.0.1:{port}"
    network_ip = get_network_ip()
    print(f"Open dashboard on this computer at: {local_url}", flush=True)
    if network_ip:
        print(
            f"Open dashboard from another device on this network at: "
            f"http://{network_ip}:{port}",
            flush=True,
        )
    app.run(host="0.0.0.0", port=port, debug=True, use_reloader=False)
