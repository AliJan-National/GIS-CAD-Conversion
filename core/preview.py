"""Interactive map preview generation and spatial sanity validation using PyDeck."""

import json
import math
from typing import Any, Dict, Optional, Tuple

import geopandas as gpd
import pydeck as pdk


def check_coordinate_sanity(gdf: gpd.GeoDataFrame) -> Optional[str]:
    """Inspect coordinates to detect common errors like inverted Lat/Lon or unprojected CAD."""
    if gdf.crs is None:
        return "Source CRS is undefined. Coordinates cannot be reliably mapped."

    try:
        # Check if projected in WGS84
        if str(gdf.crs).upper() in ("EPSG:4326", "WGS 84", "OGC:CRS84"):
            bounds = gdf.total_bounds  # [minx, miny, maxx, maxy]
            minx, miny, maxx, maxy = bounds

            # Latitudes must be between -90 and 90, Longitudes between -180 and 180
            if abs(miny) > 90 or abs(maxy) > 90:
                if abs(minx) <= 90 and abs(maxx) <= 90:
                    return (
                        "Latitude values exceed ±90° while Longitude is within ±90°. "
                        "Coordinates appear to be inverted (swapped X and Y)."
                    )
                return (
                    "Coordinates exceed standard global latitude/longitude bounds (±90° / ±180°). "
                    "The dataset is likely in a local or projected grid (e.g. UTM or State Plane), not WGS 84."
                )

        return None
    except Exception:
        return None


def calculate_map_bounds(gdf: gpd.GeoDataFrame) -> Tuple[float, float, int]:
    """Calculate the center latitude, longitude, and sensible zoom level from bounding box."""
    minx, miny, maxx, maxy = gdf.total_bounds
    center_lon = (minx + maxx) / 2.0
    center_lat = (miny + maxy) / 2.0

    # Clamp center lat/lon to valid world coordinates
    center_lat = max(-85.0, min(85.0, center_lat))
    center_lon = max(-180.0, min(180.0, center_lon))

    delta = max(abs(maxx - minx), abs(maxy - miny))
    if delta == 0:
        zoom = 14
    else:
        # Approximate zoom level based on degree span
        zoom = int(round(11 - math.log2(delta + 0.005)))
        zoom = max(1, min(18, zoom))

    return center_lat, center_lon, zoom


def generate_pydeck_map(
    gdf: gpd.GeoDataFrame,
    max_features: int = 1500,
    map_style: str = "road",
) -> Tuple[Optional[pdk.Deck], Optional[str]]:
    """Build an interactive PyDeck map visualizing the GeoDataFrame.
    
    Args:
        gdf: GeoDataFrame to visualize.
        max_features: Sampling threshold for fast rendering.
        map_style: 'road', 'dark', 'light', or 'satellite'.
        
    Returns:
        Tuple of (pydeck.Deck object, warning_message if any)
    """
    if len(gdf) == 0:
        return None, "Dataset is empty."

    work_gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    if len(work_gdf) == 0:
        return None, "No valid, non-empty geometries found to display."

    # Reproject to WGS84 for mapping if CRS is set
    warning_msg = None
    if work_gdf.crs is not None and str(work_gdf.crs) != "EPSG:4326":
        try:
            work_gdf = work_gdf.to_crs("EPSG:4326")
        except Exception as e:
            return None, f"Could not reproject to WGS84 for map preview: {e}"
    elif work_gdf.crs is None:
        # Assume WGS84 for preview but warn
        work_gdf.set_crs("EPSG:4326", inplace=True)
        warning_msg = "No CRS metadata found; preview assumes WGS 84 coordinates."

    # Validate coordinates
    sanity_warn = check_coordinate_sanity(work_gdf)
    if sanity_warn:
        warning_msg = sanity_warn

    # Downsample if dataset is large to ensure zero browser lag
    is_sampled = len(work_gdf) > max_features
    if is_sampled:
        display_gdf = work_gdf.sample(max_features, random_state=42)
        warning_msg = (
            f"Displaying a sample of {max_features:,} features (out of {len(work_gdf):,} total) "
            "for smooth map interaction. The converted export will contain 100% of features."
        )
    else:
        display_gdf = work_gdf

    # Simplify complex geometries slightly for smooth WebGL rendering
    try:
        center_lat, center_lon, zoom = calculate_map_bounds(display_gdf)
    except Exception:
        center_lat, center_lon, zoom = 0.0, 0.0, 2

    # Prepare GeoJSON
    try:
        geojson_data = json.loads(display_gdf.to_json())
    except Exception as e:
        return None, f"Failed to serialize geometries for preview: {e}"

    # Map styles
    style_urls = {
        "road": "https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json",
        "dark": "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
        "light": "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
        "satellite": None,  # Falls back to standard view
    }
    selected_style = style_urls.get(map_style, style_urls["road"])

    layer = pdk.Layer(
        "GeoJsonLayer",
        data=geojson_data,
        opacity=0.8,
        stroked=True,
        filled=True,
        extruded=False,
        wireframe=True,
        get_fill_color=[37, 99, 235, 140],       # Tailwind Blue-600
        get_line_color=[30, 64, 175, 240],      # Tailwind Blue-800
        get_line_width=2,
        line_width_min_pixels=1.5,
        get_point_radius=5,
        point_radius_min_pixels=4,
        point_radius_max_pixels=15,
        pickable=True,
        auto_highlight=True,
    )

    view_state = pdk.ViewState(
        latitude=center_lat,
        longitude=center_lon,
        zoom=zoom,
        pitch=0,
        bearing=0,
    )

    deck = pdk.Deck(
        layers=[layer],
        initial_view_state=view_state,
        map_style=selected_style,
        tooltip={
            "html": "<b>Type:</b> {geometry.type}<br/><b>Layer:</b> {layer}<br/><b>Text:</b> {text}",
            "style": {"backgroundColor": "rgba(24, 24, 27, 0.9)", "color": "white", "fontSize": "12px"},
        },
    )

    return deck, warning_msg
