"""CAD Ingestion Engine for DXF and DWG formats.

Extracts CAD entities (lines, polylines, circles, arcs, points, text)
and converts them into standard Shapely geometries and GeoDataFrames.
Supports DWG files via LibreDWG's dwg2dxf converter when available.
"""

import os
import shutil
import subprocess
import tempfile
from typing import List, Optional, Set, Tuple

import ezdxf
import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Point, Polygon

from core.crs_utils import CAD_UNIT_SCALES


class CADConversionError(Exception):
    """Raised when CAD conversion fails."""
    pass


def is_dwg2dxf_available() -> bool:
    """Check if dwg2dxf (LibreDWG) command line utility is available."""
    return shutil.which("dwg2dxf") is not None


def convert_dwg_to_dxf(dwg_path: str, output_dxf_path: Optional[str] = None) -> str:
    """Convert a DWG file to DXF using LibreDWG's dwg2dxf utility.
    
    Args:
        dwg_path: Path to the input .dwg file.
        output_dxf_path: Optional path for the output .dxf. If omitted,
                         a temporary file is created.
                         
    Returns:
        Path to the converted .dxf file.
        
    Raises:
        CADConversionError: If dwg2dxf is not installed or conversion fails.
    """
    if not is_dwg2dxf_available():
        raise CADConversionError(
            "AutoCAD .DWG is a closed proprietary binary format and requires external translation.\n"
            "👉 Please save or export your drawing as .DXF from AutoCAD, Civil 3D, or QGIS before uploading.\n"
            ".DXF is 100% supported with full layer, curve, and entity extraction."
        )

    if not output_dxf_path:
        base_name = os.path.splitext(os.path.basename(dwg_path))[0]
        temp_dir = tempfile.mkdtemp()
        output_dxf_path = os.path.join(temp_dir, f"{base_name}.dxf")

    try:
        # dwg2dxf [options] <dwg_file> -o <dxf_file>
        cmd = ["dwg2dxf", "-y", "-o", output_dxf_path, dwg_path]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)
        if result.returncode != 0 and not os.path.exists(output_dxf_path):
            raise CADConversionError(
                f"dwg2dxf failed to convert '{os.path.basename(dwg_path)}'.\n"
                f"Error details: {result.stderr or result.stdout}"
            )
        return output_dxf_path
    except subprocess.TimeoutExpired:
        raise CADConversionError("dwg2dxf timed out while processing large DWG drawing.")
    except Exception as e:
        if isinstance(e, CADConversionError):
            raise
        raise CADConversionError(f"Unexpected error during DWG conversion: {e}")


def get_cad_layers(dxf_path: str) -> List[str]:
    """Retrieve list of layer names present in the DXF file (both table definitions and entity references)."""
    try:
        doc = ezdxf.readfile(dxf_path)
        layers = set(layer.dxf.name for layer in doc.layers)
        for entity in doc.modelspace():
            if hasattr(entity.dxf, "layer") and entity.dxf.layer:
                layers.add(entity.dxf.layer)
        return sorted(layers)
    except Exception as e:
        raise CADConversionError(f"Failed to read DXF layers: {e}")


def _extract_entity_geometry(entity, unit_scale: float = 1.0):
    """Extract Shapely geometry from an ezdxf entity, applying unit scaling."""
    dxftype = entity.dxftype()

    def scale_pt(pt):
        return (pt[0] * unit_scale, pt[1] * unit_scale)

    if dxftype == "POINT":
        loc = entity.dxf.location
        return Point(scale_pt((loc.x, loc.y)))

    elif dxftype == "LINE":
        start = scale_pt((entity.dxf.start.x, entity.dxf.start.y))
        end = scale_pt((entity.dxf.end.x, entity.dxf.end.y))
        if start != end:
            return LineString([start, end])
        return Point(start)

    elif dxftype == "LWPOLYLINE":
        pts = [scale_pt(p) for p in entity.get_points(format="xy")]
        if len(pts) < 2:
            return None
        if entity.closed and len(pts) >= 3:
            try:
                poly = Polygon(pts)
                if poly.is_valid and poly.area > 0:
                    return poly
            except Exception:
                pass
        return LineString(pts)

    elif dxftype == "POLYLINE":
        pts = [scale_pt((v.dxf.location.x, v.dxf.location.y)) for v in entity.vertices]
        if len(pts) < 2:
            return None
        if entity.is_closed and len(pts) >= 3:
            try:
                poly = Polygon(pts)
                if poly.is_valid and poly.area > 0:
                    return poly
            except Exception:
                pass
        return LineString(pts)

    elif dxftype == "CIRCLE":
        try:
            pts = [scale_pt(p) for p in entity.flattening(0.05)]
            if len(pts) >= 3:
                return Polygon(pts)
        except Exception:
            c = entity.dxf.center
            r = entity.dxf.radius * unit_scale
            return Point(c.x * unit_scale, c.y * unit_scale).buffer(r)

    elif dxftype == "ARC":
        try:
            pts = [scale_pt(p) for p in entity.flattening(0.05)]
            if len(pts) >= 2:
                return LineString(pts)
        except Exception:
            pass

    elif dxftype == "SPLINE":
        try:
            pts = [scale_pt(p) for p in entity.flattening(0.05)]
            if len(pts) >= 2:
                return LineString(pts)
        except Exception:
            pass

    elif dxftype == "ELLIPSE":
        try:
            pts = [scale_pt(p) for p in entity.flattening(0.05)]
            if len(pts) >= 3 and entity.dxf.start_param == 0 and entity.dxf.end_param >= 6.28:
                return Polygon(pts)
            elif len(pts) >= 2:
                return LineString(pts)
        except Exception:
            pass

    elif dxftype in ("TEXT", "MTEXT"):
        try:
            insert = entity.dxf.insert
            return Point(scale_pt((insert.x, insert.y)))
        except Exception:
            pass

    elif dxftype == "INSERT":
        # Block reference insert point
        try:
            insert = entity.dxf.insert
            return Point(scale_pt((insert.x, insert.y)))
        except Exception:
            pass

    elif dxftype in ("3DFACE", "SOLID", "TRACE"):
        try:
            corners = [
                scale_pt((entity.dxf.vtx0.x, entity.dxf.vtx0.y)),
                scale_pt((entity.dxf.vtx1.x, entity.dxf.vtx1.y)),
                scale_pt((entity.dxf.vtx2.x, entity.dxf.vtx2.y)),
                scale_pt((entity.dxf.vtx3.x, entity.dxf.vtx3.y)),
            ]
            unique_corners = []
            for c in corners:
                if c not in unique_corners:
                    unique_corners.append(c)
            if len(unique_corners) >= 3:
                return Polygon(unique_corners)
            elif len(unique_corners) == 2:
                return LineString(unique_corners)
        except Exception:
            pass

    return None


def read_cad_to_geodataframe(
    file_path: str,
    crs: Optional[str] = None,
    selected_layers: Optional[List[str]] = None,
    unit_scale: float = 1.0,
) -> gpd.GeoDataFrame:
    """Read a DXF or DWG file and return a GeoDataFrame.
    
    Args:
        file_path: Path to the .dxf or .dwg file.
        crs: Coordinate Reference System (e.g., 'EPSG:4326', 'EPSG:3857', 'EPSG:2227').
        selected_layers: Optional list of layers to extract. If None, extracts all.
        unit_scale: Multiplication factor for coordinates (e.g. 0.3048 to convert feet to meters).
        
    Returns:
        gpd.GeoDataFrame containing extracted geometries and CAD attributes.
    """
    ext = os.path.splitext(file_path)[1].lower()
    cleanup_temp_dxf = False
    actual_dxf_path = file_path

    if ext == ".dwg":
        actual_dxf_path = convert_dwg_to_dxf(file_path)
        cleanup_temp_dxf = True

    try:
        doc = ezdxf.readfile(actual_dxf_path)
        msp = doc.modelspace()

        records = []
        selected_set: Optional[Set[str]] = set(selected_layers) if selected_layers else None

        for entity in msp:
            layer = entity.dxf.layer if hasattr(entity.dxf, "layer") else "0"
            if selected_set and layer not in selected_set:
                continue

            geom = _extract_entity_geometry(entity, unit_scale=unit_scale)
            if geom is None or geom.is_empty:
                continue

            record = {
                "layer": layer,
                "entity_type": entity.dxftype(),
                "color": getattr(entity.dxf, "color", None),
                "linetype": getattr(entity.dxf, "linetype", None),
                "handle": getattr(entity.dxf, "handle", None),
                "text": None,
                "block_name": None,
                "geometry": geom,
            }

            if entity.dxftype() == "TEXT":
                record["text"] = getattr(entity.dxf, "text", "")
            elif entity.dxftype() == "MTEXT":
                record["text"] = entity.text if hasattr(entity, "text") else ""
            elif entity.dxftype() == "INSERT":
                record["block_name"] = getattr(entity.dxf, "name", "")

            records.append(record)

        if not records:
            raise CADConversionError("No valid geometries found in the CAD drawing modelspace.")

        df = pd.DataFrame(records)
        gdf = gpd.GeoDataFrame(df, geometry="geometry")

        if crs:
            gdf.set_crs(crs, inplace=True)

        return gdf

    except Exception as e:
        if isinstance(e, CADConversionError):
            raise
        raise CADConversionError(f"Failed to process CAD file: {e}")
    finally:
        if cleanup_temp_dxf and os.path.exists(actual_dxf_path):
            try:
                os.remove(actual_dxf_path)
            except Exception:
                pass
