"""CRS utilities, EPSG search, unit conversion, and reprojection functions."""

from functools import lru_cache
import re
from typing import Dict, List, Optional, Tuple

import geopandas as gpd
from pyproj import CRS
import pyproj.database as pdb


# Popular CRS presets for quick access
COMMON_CRS_PRESETS = [
    ("EPSG:4326", "EPSG:4326 - WGS 84 (Global GPS Lat/Lon)"),
    ("EPSG:3857", "EPSG:3857 - WGS 84 / Pseudo-Mercator (Web Maps / OSM / Google)"),
    ("EPSG:2154", "EPSG:2154 - RGF93 / Lambert-93 (France)"),
    ("EPSG:27700", "EPSG:27700 - OSGB36 / British National Grid (UK)"),
    ("EPSG:25832", "EPSG:25832 - ETRS89 / UTM zone 32N (Central Europe)"),
    ("EPSG:32632", "EPSG:32632 - WGS 84 / UTM zone 32N"),
    ("EPSG:32633", "EPSG:32633 - WGS 84 / UTM zone 33N"),
    ("EPSG:26910", "EPSG:26910 - NAD83 / UTM zone 10N (US West Coast)"),
    ("EPSG:26918", "EPSG:26918 - NAD83 / UTM zone 18N (US East Coast)"),
    ("EPSG:2227", "EPSG:2227 - NAD83 / California zone 3 (ftUS)"),
    ("EPSG:2263", "EPSG:2263 - NAD83 / New York Long Island (ftUS)"),
    ("EPSG:7855", "EPSG:7855 - GDA2020 / MGA zone 55 (Australia)"),
    ("EPSG:4269", "EPSG:4269 - NAD83 (North America Geographic)"),
]

# Drawing unit multipliers to convert to meters
CAD_UNIT_SCALES: Dict[str, float] = {
    "Meters (m)": 1.0,
    "US Survey Feet (ftUS)": 0.3048006096012192,
    "International Feet (ft)": 0.3048,
    "Millimeters (mm)": 0.001,
    "Centimeters (cm)": 0.01,
    "Inches (in)": 0.0254,
    "Kilometers (km)": 1000.0,
}


@lru_cache(maxsize=1)
def _load_all_epsg_records() -> List[Tuple[str, str]]:
    """Load and cache all EPSG codes and descriptions from PROJ database."""
    try:
        raw_list = pdb.query_crs_info(auth_name="EPSG")
        records = []
        for r in raw_list:
            records.append((f"EPSG:{r.code}", f"EPSG:{r.code} - {r.name}"))
        return records
    except Exception:
        return COMMON_CRS_PRESETS


def search_epsg(query: str, limit: int = 25) -> List[Tuple[str, str]]:
    """Search for CRS definitions by EPSG code or name keywords.
    
    Args:
        query: Search string (e.g., '4326', 'UTM 32N', 'NAD83', 'California').
        limit: Max number of results to return.
        
    Returns:
        List of tuples: (epsg_code, display_label)
    """
    clean_q = query.strip().lower()
    if not clean_q:
        return COMMON_CRS_PRESETS[:limit]

    records = _load_all_epsg_records()
    matches = []

    # Priority 1: Exact EPSG code match
    exact_code = clean_q.replace("epsg:", "")
    for code, label in records:
        if code.lower() == f"epsg:{exact_code}":
            matches.append((code, label))
            break

    # Priority 2: Matches containing query terms
    q_tokens = clean_q.split()
    for code, label in records:
        if (code, label) in matches:
            continue
        label_lower = label.lower()
        if all(token in label_lower for token in q_tokens):
            matches.append((code, label))
            if len(matches) >= limit:
                break

    return matches


def format_crs_display(crs_obj) -> str:
    """Format a CRS object into a clean human-readable string."""
    if crs_obj is None:
        return "Not Defined (Missing CRS)"
    try:
        c = CRS(crs_obj)
        epsg = c.to_epsg()
        if epsg:
            return f"EPSG:{epsg} ({c.name})"
        return c.name or str(crs_obj)
    except Exception:
        return str(crs_obj)


def reproject_geodataframe(gdf: gpd.GeoDataFrame, target_crs: str) -> gpd.GeoDataFrame:
    """Safely reproject a GeoDataFrame to the specified target CRS."""
    if gdf.crs is None:
        raise ValueError("Cannot reproject GeoDataFrame: source CRS is undefined.")

    try:
        return gdf.to_crs(target_crs)
    except Exception as e:
        raise ValueError(f"Failed to reproject to {target_crs}: {e}")
