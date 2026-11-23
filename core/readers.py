"""Multi-format readers and ingestion engine for geospatial, CAD, and tabular files."""

from dataclasses import dataclass, field
import io
import os
import shutil
import tempfile
from typing import Any, Dict, List, Optional, Tuple, Union
import zipfile

import geopandas as gpd
import pandas as pd
import pyogrio
import shapely
from shapely.geometry import Point

from core.cad_engine import read_cad_to_geodataframe, get_cad_layers, CADConversionError


@dataclass
class FileInspectionResult:
    """Detailed metadata discovered about an uploaded file."""
    file_type: str  # 'shapefile_zip', 'kmz', 'kml', 'csv', 'excel', 'cad', 'geojson', 'gpkg', 'unknown'
    file_name: str
    detected_crs: Optional[str] = None
    feature_count: Optional[int] = None
    geometry_types: List[str] = field(default_factory=list)
    layers: List[str] = field(default_factory=list)
    columns: List[str] = field(default_factory=list)
    suggested_x: Optional[str] = None
    suggested_y: Optional[str] = None
    suggested_wkt: Optional[str] = None
    has_missing_prj: bool = False
    validation_warnings: List[str] = field(default_factory=list)
    preview_df: Optional[pd.DataFrame] = None


class IngestionError(Exception):
    """Raised when file reading or parsing fails."""
    pass


# Heuristics for auto-detecting coordinate and WKT columns
X_COLUMN_CANDIDATES = [
    "longitude", "long", "lon", "lng", "x", "easting", "east", "xcoord", "x_coord", "coord_x"
]
Y_COLUMN_CANDIDATES = [
    "latitude", "lat", "y", "northing", "north", "ycoord", "y_coord", "coord_y"
]
WKT_COLUMN_CANDIDATES = [
    "wkt", "geometry", "geom", "the_geom", "wkt_geom", "shape", "wkt_geometry"
]


def _match_column_candidate(columns: List[str], candidates: List[str]) -> Optional[str]:
    """Find the best matching column name based on list of candidate substrings."""
    lower_map = {col.lower().strip().replace(" ", "_"): col for col in columns}
    # Exact match first
    for cand in candidates:
        if cand in lower_map:
            return lower_map[cand]
    # Substring match second
    for cand in candidates:
        for lower_col, original in lower_map.items():
            if cand in lower_col:
                return original
    return None


def inspect_uploaded_file(file_name: str, file_bytes: bytes) -> FileInspectionResult:
    """Analyze an uploaded file or ZIP archive and return metadata and suggestions."""
    name_lower = file_name.lower()

    # 1. ZIP Archives (Shapefiles, KMZ, FileGDB)
    if name_lower.endswith(".zip"):
        try:
            with zipfile.ZipFile(io.BytesIO(file_bytes), "r") as zf:
                namelist = zf.namelist()
                lower_namelist = [n.lower() for n in namelist]

                # Check for Shapefile
                shp_files = [n for n, l in zip(namelist, lower_namelist) if l.endswith(".shp")]
                if shp_files:
                    shp_base = os.path.splitext(shp_files[0])[0].lower()
                    has_shx = any(l == shp_base + ".shx" for l in lower_namelist)
                    has_dbf = any(l == shp_base + ".dbf" for l in lower_namelist)
                    has_prj = any(l == shp_base + ".prj" for l in lower_namelist)

                    warnings = []
                    if not has_shx:
                        warnings.append("Missing .shx index file in ZIP. GDAL may reconstruct it.")
                    if not has_dbf:
                        warnings.append("Missing .dbf attributes file in ZIP. Attributes will not load.")
                    if not has_prj:
                        warnings.append("Missing .prj projection file in ZIP. Please specify Source CRS.")

                    # Read preview
                    with tempfile.TemporaryDirectory() as tmpdir:
                        zf.extractall(tmpdir)
                        full_shp_path = os.path.join(tmpdir, shp_files[0])
                        gdf = gpd.read_file(full_shp_path, rows=5)
                        detected_crs = str(gdf.crs) if gdf.crs else None
                        geom_types = list(gdf.geometry.geom_type.dropna().unique())

                        return FileInspectionResult(
                            file_type="shapefile_zip",
                            file_name=file_name,
                            detected_crs=detected_crs,
                            geometry_types=geom_types,
                            columns=[c for c in gdf.columns if c != "geometry"],
                            has_missing_prj=not has_prj,
                            validation_warnings=warnings,
                            preview_df=gdf.drop(columns=["geometry"], errors="ignore"),
                        )

                # Check for KMZ stored as .zip
                if any(l.endswith("doc.kml") or l.endswith(".kml") for l in lower_namelist):
                    return _inspect_kmz_bytes(file_name, file_bytes)

                # Check for FileGDB
                if any(".gdb/" in l or l.endswith(".gdb") for l in lower_namelist):
                    return FileInspectionResult(
                        file_type="filegdb_zip",
                        file_name=file_name,
                        validation_warnings=["Esri File Geodatabase detected."],
                    )

        except zipfile.BadZipFile:
            raise IngestionError(f"'{file_name}' is not a valid or readable ZIP file.")

    # 2. KMZ direct
    if name_lower.endswith(".kmz"):
        return _inspect_kmz_bytes(file_name, file_bytes)

    # 3. KML direct
    if name_lower.endswith(".kml"):
        with tempfile.NamedTemporaryFile(suffix=".kml", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        try:
            layers = [row[0] for row in pyogrio.list_layers(tmp_path)]
            gdf = gpd.read_file(tmp_path, driver="KML", rows=5)
            geom_types = list(gdf.geometry.geom_type.dropna().unique())
            return FileInspectionResult(
                file_type="kml",
                file_name=file_name,
                detected_crs="EPSG:4326",
                layers=layers,
                geometry_types=geom_types,
                columns=[c for c in gdf.columns if c != "geometry"],
                preview_df=gdf.drop(columns=["geometry"], errors="ignore"),
            )
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    # 4. CSV / TSV / Delimited
    if name_lower.endswith((".csv", ".tsv", ".txt")):
        kwargs = {"nrows": 10}
        if name_lower.endswith(".tsv"):
            kwargs["sep"] = "\t"
        df = pd.read_csv(io.BytesIO(file_bytes), **kwargs)
        cols = list(df.columns)
        sug_x = _match_column_candidate(cols, X_COLUMN_CANDIDATES)
        sug_y = _match_column_candidate(cols, Y_COLUMN_CANDIDATES)
        sug_wkt = _match_column_candidate(cols, WKT_COLUMN_CANDIDATES)

        return FileInspectionResult(
            file_type="csv",
            file_name=file_name,
            detected_crs=None,
            columns=cols,
            suggested_x=sug_x,
            suggested_y=sug_y,
            suggested_wkt=sug_wkt,
            preview_df=df.head(5),
        )

    # 5. Excel (.xlsx / .xls)
    if name_lower.endswith((".xlsx", ".xls")):
        df = pd.read_excel(io.BytesIO(file_bytes), nrows=10)
        cols = list(df.columns)
        sug_x = _match_column_candidate(cols, X_COLUMN_CANDIDATES)
        sug_y = _match_column_candidate(cols, Y_COLUMN_CANDIDATES)
        sug_wkt = _match_column_candidate(cols, WKT_COLUMN_CANDIDATES)

        return FileInspectionResult(
            file_type="excel",
            file_name=file_name,
            detected_crs=None,
            columns=cols,
            suggested_x=sug_x,
            suggested_y=sug_y,
            suggested_wkt=sug_wkt,
            preview_df=df.head(5),
        )

    # 6. CAD (.dxf, .dwg)
    if name_lower.endswith((".dxf", ".dwg")):
        with tempfile.NamedTemporaryFile(suffix=os.path.splitext(name_lower)[1], delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        try:
            is_dwg = name_lower.endswith(".dwg")
            layers = []
            if not is_dwg:
                layers = get_cad_layers(tmp_path)

            return FileInspectionResult(
                file_type="cad",
                file_name=file_name,
                detected_crs=None,  # CAD files do not carry embedded EPSG
                layers=layers,
                has_missing_prj=True,
                validation_warnings=[
                    "CAD drawings do not contain EPSG projection metadata. Please select the Source CRS."
                ]
            )
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    # 7. GeoJSON
    if name_lower.endswith((".geojson", ".json")):
        with tempfile.NamedTemporaryFile(suffix=".geojson", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        try:
            gdf = gpd.read_file(tmp_path, rows=5)
            detected_crs = str(gdf.crs) if gdf.crs else "EPSG:4326"
            geom_types = list(gdf.geometry.geom_type.dropna().unique())
            return FileInspectionResult(
                file_type="geojson",
                file_name=file_name,
                detected_crs=detected_crs,
                geometry_types=geom_types,
                columns=[c for c in gdf.columns if c != "geometry"],
                preview_df=gdf.drop(columns=["geometry"], errors="ignore"),
            )
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    # 8. GeoPackage (.gpkg)
    if name_lower.endswith(".gpkg"):
        with tempfile.NamedTemporaryFile(suffix=".gpkg", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        try:
            layers = [row[0] for row in pyogrio.list_layers(tmp_path)]
            gdf = gpd.read_file(tmp_path, rows=5)
            detected_crs = str(gdf.crs) if gdf.crs else None
            geom_types = list(gdf.geometry.geom_type.dropna().unique())
            return FileInspectionResult(
                file_type="gpkg",
                file_name=file_name,
                detected_crs=detected_crs,
                layers=layers,
                geometry_types=geom_types,
                columns=[c for c in gdf.columns if c != "geometry"],
                preview_df=gdf.drop(columns=["geometry"], errors="ignore"),
            )
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    return FileInspectionResult(
        file_type="unknown",
        file_name=file_name,
        validation_warnings=[f"Unrecognized file extension '{os.path.splitext(file_name)[1]}'."]
    )


def _inspect_kmz_bytes(file_name: str, file_bytes: bytes) -> FileInspectionResult:
    """Helper to inspect KMZ archives."""
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes), "r") as zf:
            kml_candidates = [n for n in zf.namelist() if n.lower().endswith(".kml")]
            if not kml_candidates:
                raise IngestionError("No .kml document found inside KMZ archive.")

            doc_kml = next((n for n in kml_candidates if os.path.basename(n).lower() == "doc.kml"), kml_candidates[0])
            with tempfile.NamedTemporaryFile(suffix=".kml", delete=False) as tmp:
                tmp.write(zf.read(doc_kml))
                tmp_path = tmp.name

            try:
                layers = [row[0] for row in pyogrio.list_layers(tmp_path)]
                gdf = gpd.read_file(tmp_path, driver="KML", rows=5)
                geom_types = list(gdf.geometry.geom_type.dropna().unique())
                return FileInspectionResult(
                    file_type="kmz",
                    file_name=file_name,
                    detected_crs="EPSG:4326",
                    layers=layers,
                    geometry_types=geom_types,
                    columns=[c for c in gdf.columns if c != "geometry"],
                    preview_df=gdf.drop(columns=["geometry"], errors="ignore"),
                )
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
    except Exception as e:
        raise IngestionError(f"Failed to inspect KMZ: {e}")


def load_file_to_geodataframe(
    file_name: str,
    file_bytes: bytes,
    file_type: str,
    crs: Optional[str] = None,
    x_col: Optional[str] = None,
    y_col: Optional[str] = None,
    wkt_col: Optional[str] = None,
    selected_layers: Optional[List[str]] = None,
    unit_scale: float = 1.0,
) -> gpd.GeoDataFrame:
    """Load an uploaded file into a unified GeoPandas GeoDataFrame.
    
    Args:
        file_name: Name of the uploaded file.
        file_bytes: Raw binary content of the file.
        file_type: Categorized file type ('shapefile_zip', 'kmz', 'kml', 'csv', 'excel', 'cad', etc.).
        crs: Coordinate reference system to assign if missing.
        x_col: Longitude / Easting column name (for tabular).
        y_col: Latitude / Northing column name (for tabular).
        wkt_col: WKT geometry column name (for tabular).
        selected_layers: Specific layers to parse (for CAD / KML / GPKG).
        unit_scale: Unit scale factor for CAD.
        
    Returns:
        gpd.GeoDataFrame containing geometries and attributes.
    """
    # 1. Shapefile ZIP
    if file_type == "shapefile_zip":
        with tempfile.TemporaryDirectory() as tmpdir:
            with zipfile.ZipFile(io.BytesIO(file_bytes), "r") as zf:
                zf.extractall(tmpdir)
            shp_files = [
                os.path.join(root, f)
                for root, _, files in os.walk(tmpdir)
                for f in files
                if f.lower().endswith(".shp")
            ]
            if not shp_files:
                raise IngestionError("ZIP file does not contain a valid .shp shapefile.")

            gdf = gpd.read_file(shp_files[0])
            if gdf.crs is None and crs:
                gdf.set_crs(crs, inplace=True)
            return gdf

    # 2. KMZ
    elif file_type == "kmz":
        with tempfile.TemporaryDirectory() as tmpdir:
            with zipfile.ZipFile(io.BytesIO(file_bytes), "r") as zf:
                kml_candidates = [n for n in zf.namelist() if n.lower().endswith(".kml")]
                if not kml_candidates:
                    raise IngestionError("No .kml document found inside KMZ archive.")
                doc_kml = next((n for n in kml_candidates if os.path.basename(n).lower() == "doc.kml"), kml_candidates[0])
                kml_path = os.path.join(tmpdir, "doc.kml")
                with open(kml_path, "wb") as f:
                    f.write(zf.read(doc_kml))

            return _read_kml_file(kml_path, selected_layers)

    # 3. KML
    elif file_type == "kml":
        with tempfile.NamedTemporaryFile(suffix=".kml", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        try:
            return _read_kml_file(tmp_path, selected_layers)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    # 4. CSV / Tabular
    elif file_type in ("csv", "excel"):
        if file_type == "excel":
            df = pd.read_excel(io.BytesIO(file_bytes))
        else:
            kwargs = {}
            if file_name.lower().endswith(".tsv"):
                kwargs["sep"] = "\t"
            df = pd.read_csv(io.BytesIO(file_bytes), **kwargs)

        # Build geometry from WKT
        if wkt_col and wkt_col in df.columns:
            valid_mask = df[wkt_col].notna() & (df[wkt_col].astype(str).str.strip() != "")
            df_valid = df[valid_mask].copy()
            geoms = df_valid[wkt_col].astype(str).apply(shapely.from_wkt)
            gdf = gpd.GeoDataFrame(df_valid, geometry=geoms, crs=crs or "EPSG:4326")
            return gdf

        # Build geometry from X and Y
        if x_col and y_col and x_col in df.columns and y_col in df.columns:
            # Clean non-numeric
            df_valid = df.copy()
            df_valid[x_col] = pd.to_numeric(df_valid[x_col], errors="coerce")
            df_valid[y_col] = pd.to_numeric(df_valid[y_col], errors="coerce")
            df_valid = df_valid.dropna(subset=[x_col, y_col])

            geometry = gpd.points_from_xy(df_valid[x_col], df_valid[y_col], crs=crs or "EPSG:4326")
            gdf = gpd.GeoDataFrame(df_valid, geometry=geometry, crs=crs or "EPSG:4326")
            return gdf

        raise IngestionError("Please specify valid X and Y coordinate columns or a WKT geometry column.")

    # 5. CAD (DXF / DWG)
    elif file_type == "cad":
        ext = os.path.splitext(file_name)[1].lower()
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        try:
            return read_cad_to_geodataframe(
                tmp_path,
                crs=crs,
                selected_layers=selected_layers,
                unit_scale=unit_scale,
            )
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    # 6. GeoJSON
    elif file_type == "geojson":
        with tempfile.NamedTemporaryFile(suffix=".geojson", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        try:
            gdf = gpd.read_file(tmp_path)
            if gdf.crs is None and crs:
                gdf.set_crs(crs, inplace=True)
            return gdf
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    # 7. GeoPackage
    elif file_type == "gpkg":
        with tempfile.NamedTemporaryFile(suffix=".gpkg", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        try:
            layer_to_read = selected_layers[0] if selected_layers else None
            gdf = gpd.read_file(tmp_path, layer=layer_to_read)
            if gdf.crs is None and crs:
                gdf.set_crs(crs, inplace=True)
            return gdf
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    raise IngestionError(f"Unsupported file format: {file_name}")


def _read_kml_file(kml_path: str, selected_layers: Optional[List[str]] = None) -> gpd.GeoDataFrame:
    """Helper to read KML files with multi-layer fallback."""
    layers = [row[0] for row in pyogrio.list_layers(kml_path)]
    gdfs = []

    target_layers = selected_layers if selected_layers else layers

    if not target_layers:
        target_layers = [None]

    for layer in target_layers:
        try:
            part_gdf = gpd.read_file(kml_path, driver="KML", layer=layer)
            if len(part_gdf) > 0:
                if layer:
                    part_gdf["kml_layer"] = layer
                gdfs.append(part_gdf)
        except Exception:
            continue

    if not gdfs:
        raise IngestionError("No readable features found in KML/KMZ document.")

    combined = pd.concat(gdfs, ignore_index=True)
    gdf = gpd.GeoDataFrame(combined, geometry="geometry", crs="EPSG:4326")
    return gdf
