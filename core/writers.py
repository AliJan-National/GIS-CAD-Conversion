"""Multi-format export and serialization engine for GIS and CAD datasets."""

import io
import json
import os
import re
import tempfile
from typing import Optional, Tuple
import zipfile

import ezdxf
import geopandas as gpd
import pandas as pd
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)


class ExportError(Exception):
    """Raised when file export fails."""
    pass


def _sanitize_shapefile_columns(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Ensure Shapefile attribute columns meet the 10-character limit and are unique."""
    df = gdf.copy()
    used_names = set()
    new_columns = {}

    for col in df.columns:
        if col == "geometry":
            continue

        clean_col = re.sub(r"[^\w]", "_", str(col))
        truncated = clean_col[:10]

        unique_name = truncated
        counter = 1
        while unique_name in used_names:
            suffix = f"_{counter}"
            unique_name = f"{truncated[:10 - len(suffix)]}{suffix}"
            counter += 1

        used_names.add(unique_name)
        new_columns[col] = unique_name

    df = df.rename(columns=new_columns)

    # Convert complex object types to string
    for col in df.columns:
        if col != "geometry" and df[col].dtype == "object":
            df[col] = df[col].apply(lambda v: json.dumps(v) if isinstance(v, (list, dict)) else (str(v) if pd.notna(v) else ""))

    return df


def export_to_shapefile_zip(gdf: gpd.GeoDataFrame, base_name: str = "converted_shapefile") -> bytes:
    """Bundle GeoDataFrame into an ESRI Shapefile ZIP archive.
    
    If mixed geometry types exist (e.g. Points and Lines), partitions
    them into clean separate shapefiles inside the single ZIP to respect
    ESRI Shapefile's single-geometry-type specification.
    """
    try:
        sanitized_gdf = _sanitize_shapefile_columns(gdf)

        # Drop null or empty geometries
        sanitized_gdf = sanitized_gdf[sanitized_gdf.geometry.notna() & ~sanitized_gdf.geometry.is_empty].copy()
        if len(sanitized_gdf) == 0:
            raise ExportError("Cannot export empty GeoDataFrame to Shapefile.")

        # Classify geometry families
        type_families = {
            "points": ["Point", "MultiPoint"],
            "lines": ["LineString", "MultiLineString"],
            "polygons": ["Polygon", "MultiPolygon"],
        }

        # Check which families are present
        present_families = {}
        for family_name, geom_types in type_families.items():
            sub = sanitized_gdf[sanitized_gdf.geometry.geom_type.isin(geom_types)]
            if len(sub) > 0:
                present_families[family_name] = sub

        # Fallback for any other geometry collections
        handled_indices = [idx for sub in present_families.values() for idx in sub.index]
        remaining = sanitized_gdf.loc[~sanitized_gdf.index.isin(handled_indices)]
        if len(remaining) > 0:
            present_families["other"] = remaining

        with tempfile.TemporaryDirectory() as tmpdir:
            # If only 1 family, use original base_name
            if len(present_families) == 1:
                family_gdf = list(present_families.values())[0]
                shp_path = os.path.join(tmpdir, f"{base_name}.shp")
                family_gdf.to_file(shp_path, driver="ESRI Shapefile", encoding="utf-8")
                cpg_path = os.path.join(tmpdir, f"{base_name}.cpg")
                with open(cpg_path, "w", encoding="utf-8") as f:
                    f.write("UTF-8\n")
            else:
                # Multiple families: create clean separate shapefiles in the zip
                for family_name, family_gdf in present_families.items():
                    sub_base = f"{base_name}_{family_name}"
                    shp_path = os.path.join(tmpdir, f"{sub_base}.shp")
                    family_gdf.to_file(shp_path, driver="ESRI Shapefile", encoding="utf-8")
                    cpg_path = os.path.join(tmpdir, f"{sub_base}.cpg")
                    with open(cpg_path, "w", encoding="utf-8") as f:
                        f.write("UTF-8\n")

            # Bundle all files into ZIP buffer
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                for file in os.listdir(tmpdir):
                    zf.write(os.path.join(tmpdir, file), arcname=file)

            return zip_buffer.getvalue()
    except Exception as e:
        if isinstance(e, ExportError):
            raise
        raise ExportError(f"Shapefile export failed: {e}")


def export_to_kmz(gdf: gpd.GeoDataFrame, base_name: str = "converted_kmz") -> bytes:
    """Export GeoDataFrame to compressed KMZ archive containing doc.kml."""
    try:
        # KML standard requires WGS 84 (EPSG:4326)
        export_gdf = gdf.copy()
        if export_gdf.crs is not None and str(export_gdf.crs) != "EPSG:4326":
            export_gdf = export_gdf.to_crs("EPSG:4326")
        elif export_gdf.crs is None:
            export_gdf.set_crs("EPSG:4326", inplace=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            kml_path = os.path.join(tmpdir, "doc.kml")
            export_gdf.to_file(kml_path, driver="KML")

            kmz_buffer = io.BytesIO()
            with zipfile.ZipFile(kmz_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(kml_path, arcname="doc.kml")

            return kmz_buffer.getvalue()
    except Exception as e:
        raise ExportError(f"KMZ export failed: {e}")


def export_to_kml(gdf: gpd.GeoDataFrame) -> bytes:
    """Export GeoDataFrame directly to uncompressed KML XML bytes."""
    try:
        export_gdf = gdf.copy()
        if export_gdf.crs is not None and str(export_gdf.crs) != "EPSG:4326":
            export_gdf = export_gdf.to_crs("EPSG:4326")
        elif export_gdf.crs is None:
            export_gdf.set_crs("EPSG:4326", inplace=True)

        with tempfile.NamedTemporaryFile(suffix=".kml", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            export_gdf.to_file(tmp_path, driver="KML")
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
    except Exception as e:
        raise ExportError(f"KML export failed: {e}")


def export_to_geojson(gdf: gpd.GeoDataFrame) -> bytes:
    """Export GeoDataFrame to GeoJSON bytes (reprojected to WGS84 per RFC 7946)."""
    try:
        export_gdf = gdf.copy()
        if export_gdf.crs is not None and str(export_gdf.crs) != "EPSG:4326":
            export_gdf = export_gdf.to_crs("EPSG:4326")
        elif export_gdf.crs is None:
            export_gdf.set_crs("EPSG:4326", inplace=True)

        return export_gdf.to_json(indent=2).encode("utf-8")
    except Exception as e:
        raise ExportError(f"GeoJSON export failed: {e}")


def export_to_csv(gdf: gpd.GeoDataFrame) -> bytes:
    """Export GeoDataFrame to CSV with Longitude, Latitude, and WKT columns."""
    try:
        df = pd.DataFrame(gdf.drop(columns=["geometry"], errors="ignore"))

        # Add WKT geometry column
        df["WKT"] = gdf.geometry.apply(lambda g: g.wkt if g is not None and not g.is_empty else "")

        # If points, also add explicit Lon/Lat columns
        if all(gdf.geometry.geom_type.isin(["Point", None])):
            df["Longitude"] = gdf.geometry.apply(lambda g: g.x if g is not None and not g.is_empty else None)
            df["Latitude"] = gdf.geometry.apply(lambda g: g.y if g is not None and not g.is_empty else None)

        csv_buffer = io.StringIO()
        df.to_csv(csv_buffer, index=False)
        return csv_buffer.getvalue().encode("utf-8")
    except Exception as e:
        raise ExportError(f"CSV export failed: {e}")


def export_to_dxf(gdf: gpd.GeoDataFrame) -> bytes:
    """Export GeoDataFrame geometries to AutoCAD DXF format using ezdxf."""
    try:
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()

        has_layer_col = "layer" in gdf.columns

        for _, row in gdf.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue

            layer_name = str(row["layer"]).strip() if has_layer_col and pd.notna(row["layer"]) else "0"
            layer_name = re.sub(r'[<>\\/:?"*|=;]', "_", layer_name) or "0"

            if layer_name not in doc.layers:
                doc.layers.add(layer_name)

            _add_geom_to_dxf(msp, geom, layer_name)

        stream = io.StringIO()
        doc.write(stream)
        return stream.getvalue().encode("utf-8")
    except Exception as e:
        raise ExportError(f"DXF export failed: {e}")


def _add_geom_to_dxf(msp, geom, layer: str):
    """Add a Shapely geometry to DXF modelspace."""
    if isinstance(geom, Point):
        msp.add_point((geom.x, geom.y), dxfattribs={"layer": layer})
    elif isinstance(geom, MultiPoint):
        for pt in geom.geoms:
            msp.add_point((pt.x, pt.y), dxfattribs={"layer": layer})
    elif isinstance(geom, LineString):
        msp.add_lwpolyline(list(geom.coords), dxfattribs={"layer": layer})
    elif isinstance(geom, MultiLineString):
        for ls in geom.geoms:
            msp.add_lwpolyline(list(ls.coords), dxfattribs={"layer": layer})
    elif isinstance(geom, Polygon):
        msp.add_lwpolyline(list(geom.exterior.coords), close=True, dxfattribs={"layer": layer})
        for interior in geom.interiors:
            msp.add_lwpolyline(list(interior.coords), close=True, dxfattribs={"layer": layer})
    elif isinstance(geom, MultiPolygon):
        for poly in geom.geoms:
            msp.add_lwpolyline(list(poly.exterior.coords), close=True, dxfattribs={"layer": layer})
            for interior in poly.interiors:
                msp.add_lwpolyline(list(interior.coords), close=True, dxfattribs={"layer": layer})
    elif isinstance(geom, GeometryCollection):
        for g in geom.geoms:
            _add_geom_to_dxf(msp, g, layer)


def export_to_geopackage(gdf: gpd.GeoDataFrame, layer_name: str = "features") -> bytes:
    """Export GeoDataFrame to single-file OGC GeoPackage (.gpkg)."""
    try:
        clean_layer = re.sub(r"[^\w]", "_", layer_name) or "features"
        with tempfile.NamedTemporaryFile(suffix=".gpkg", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            gdf.to_file(tmp_path, driver="GPKG", layer=clean_layer)
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
    except Exception as e:
        raise ExportError(f"GeoPackage export failed: {e}")


def export_dataset(
    gdf: gpd.GeoDataFrame,
    target_format: str,
    base_name: str = "converted_dataset",
    target_crs: Optional[str] = None,
) -> Tuple[bytes, str, str]:
    """Universal dispatcher to convert and export a GeoDataFrame to the desired format.
    
    Args:
        gdf: Input GeoDataFrame.
        target_format: Output format ('shapefile', 'kmz', 'kml', 'geojson', 'csv', 'dxf', 'gpkg').
        base_name: Base filename without extension.
        target_crs: Optional target CRS to reproject before exporting.
        
    Returns:
        Tuple of (file_bytes, final_file_name, mime_type)
    """
    clean_base = re.sub(r"[^\w\-]", "_", base_name).strip("_") or "converted_data"
    work_gdf = gdf.copy()

    # Reproject if requested
    if target_crs and work_gdf.crs is not None:
        try:
            work_gdf = work_gdf.to_crs(target_crs)
        except Exception as e:
            raise ExportError(f"Reprojection from {work_gdf.crs} to {target_crs} failed: {e}")
    elif target_crs and work_gdf.crs is None:
        work_gdf.set_crs(target_crs, inplace=True)

    fmt = target_format.lower()

    if fmt in ("shapefile", "shp"):
        data = export_to_shapefile_zip(work_gdf, base_name=clean_base)
        return data, f"{clean_base}.zip", "application/zip"

    elif fmt == "kmz":
        data = export_to_kmz(work_gdf, base_name=clean_base)
        return data, f"{clean_base}.kmz", "application/vnd.google-earth.kmz"

    elif fmt == "kml":
        data = export_to_kml(work_gdf)
        return data, f"{clean_base}.kml", "application/vnd.google-earth.kml+xml"

    elif fmt in ("geojson", "json"):
        data = export_to_geojson(work_gdf)
        return data, f"{clean_base}.geojson", "application/geo+json"

    elif fmt == "csv":
        data = export_to_csv(work_gdf)
        return data, f"{clean_base}.csv", "text/csv"

    elif fmt == "dxf":
        data = export_to_dxf(work_gdf)
        return data, f"{clean_base}.dxf", "application/dxf"

    elif fmt in ("geopackage", "gpkg"):
        data = export_to_geopackage(work_gdf, layer_name=clean_base)
        return data, f"{clean_base}.gpkg", "application/x-sqlite3"

    raise ExportError(f"Unsupported export format: {target_format}")
