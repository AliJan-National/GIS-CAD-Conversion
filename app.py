"""Universal GIS & CAD File Conversion Web Application.

Supports Shapefile (.zip), KMZ/KML, AutoCAD (DWG/DXF), CSV/Excel, GeoJSON, and GeoPackage.
Built for deployment on Streamlit Community Cloud (share.streamlit.io).
"""

import os
import re
import traceback

import geopandas as gpd
import pandas as pd
import streamlit as st

from core.cad_engine import is_dwg2dxf_available
from core.crs_utils import (
    CAD_UNIT_SCALES,
    COMMON_CRS_PRESETS,
    format_crs_display,
    search_epsg,
)
from core.preview import check_coordinate_sanity, generate_pydeck_map
from core.readers import (
    FileInspectionResult,
    IngestionError,
    inspect_uploaded_file,
    load_file_to_geodataframe,
)
from core.writers import ExportError, export_dataset


# Page configuration
st.set_page_config(
    page_title="Universal GIS & CAD Converter",
    page_icon="🗺️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS styling
st.markdown(
    """
    <style>
    .main-header {
        font-size: 2.2rem;
        font-weight: 700;
        color: #1e3a8a;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        font-size: 1.05rem;
        color: #4b5563;
        margin-bottom: 1.5rem;
    }
    .badge-pill {
        display: inline-block;
        padding: 0.25rem 0.6rem;
        border-radius: 9999px;
        font-size: 0.75rem;
        font-weight: 600;
        margin-right: 0.4rem;
        background-color: #e0f2fe;
        color: #0369a1;
    }
    .badge-green {
        background-color: #dcfce7;
        color: #15803d;
    }
    .badge-yellow {
        background-color: #fef9c3;
        color: #a16207;
    }
    .metric-card {
        background-color: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 1rem;
        text-align: center;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def render_sidebar():
    """Render informative sidebar with format guides and cloud deployment notes."""
    with st.sidebar:
        st.markdown("### 🗺️ GIS & CAD Converter")
        st.markdown(
            "Convert geospatial, CAD, and tabular data seamlessly in your browser. "
            "Built with open-source GIS tools for zero cloud lock-in."
        )

        st.divider()

        st.markdown("#### 📦 Format Checklist")
        st.markdown(
            """
            - **Shapefile**: Upload `.zip` containing `.shp`, `.shx`, `.dbf`, `.prj`.
            - **KMZ / KML**: Single `.kmz` or `.kml`.
            - **CSV / Excel**: Files with Longitude/Latitude or WKT.
            - **CAD**: `.dxf` or `.dwg` (AutoCAD).
            - **Web / OGC**: `.geojson`, `.gpkg` (GeoPackage).
            """
        )

        st.divider()

        st.markdown("#### ⚙️ System Status")
        dwg_ready = is_dwg2dxf_available()
        if dwg_ready:
            st.markdown("🟢 **DWG Engine**: LibreDWG (`dwg2dxf`) Active")
        else:
            st.markdown("🟢 **CAD Engine**: Native DXF Engine Active")
            st.caption(
                "AutoCAD `.DXF` files are 100% supported natively. For `.DWG` files, "
                "please save as `.DXF` in AutoCAD or QGIS before uploading."
            )

        st.divider()

        st.markdown("#### 🔒 Privacy & Data Retention")
        st.caption(
            "Conversions execute in memory and temporary sandboxed storage. "
            "No files are permanently stored or shared."
        )

        if st.button("🔄 Reset App & Clear Cache", use_container_width=True):
            st.session_state.clear()
            st.rerun()


def main():
    render_sidebar()

    st.markdown('<div class="main-header">Universal GIS & CAD File Converter</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="sub-header">Convert between <b>Shapefile (.zip)</b>, <b>KMZ/KML</b>, '
        '<b>AutoCAD (DWG/DXF)</b>, <b>CSV (Coordinates/WKT)</b>, <b>GeoJSON</b>, and <b>GeoPackage</b>. '
        'Inspect geometries on an interactive map before downloading.</div>',
        unsafe_allow_html=True,
    )

    # Step 1: Upload Zone
    st.markdown("### 1. Upload File or Archive")
    uploaded_file = st.file_uploader(
        "Upload a single geospatial file or a Shapefile .ZIP archive",
        type=["zip", "kmz", "kml", "csv", "tsv", "txt", "xlsx", "xls", "dxf", "dwg", "geojson", "json", "gpkg"],
        help="Upload Shapefile as .zip (containing .shp, .shx, .dbf, .prj), KMZ, KML, CSV, DXF/DWG, or GeoPackage.",
    )

    if not uploaded_file:
        st.info("👆 Select or drag-and-drop a file above to begin.")
        # Quick demo data prompt
        st.markdown("---")
        st.markdown("#### 💡 Need sample data to test?")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.caption("**CSV with Lat/Lon**: Spreadsheets with coordinate columns.")
        with col2:
            st.caption("**KMZ / KML**: Google Earth waypoints, GPS tracks, polygons.")
        with col3:
            st.caption("**DXF / DWG / Shapefile**: CAD layers and GIS boundary files.")
        return

    file_name = uploaded_file.name
    file_bytes = uploaded_file.getvalue()

    # Step 2: File Inspection
    try:
        inspection: FileInspectionResult = inspect_uploaded_file(file_name, file_bytes)
    except Exception as e:
        st.error(f"Failed to inspect uploaded file: {e}")
        return

    st.markdown("---")
    st.markdown("### 2. File Inspection & Configuration")

    # Display inspection badges
    col_t1, col_t2, col_t3, col_t4 = st.columns(4)
    with col_t1:
        st.metric("Detected Format", inspection.file_type.upper().replace("_", " "))
    with col_t2:
        file_size_kb = len(file_bytes) / 1024.0
        st.metric("File Size", f"{file_size_kb:,.1f} KB" if file_size_kb < 1024 else f"{file_size_kb/1024:,.2f} MB")
    with col_t3:
        st.metric("Detected CRS", inspection.detected_crs or "Missing / Undefined")
    with col_t4:
        st.metric("Geometries", ", ".join(inspection.geometry_types) if inspection.geometry_types else "Auto-detect")

    # Display warnings if any
    for warn in inspection.validation_warnings:
        st.warning(f"⚠️ {warn}")

    # Configuration options based on format
    source_crs = inspection.detected_crs
    x_col = None
    y_col = None
    wkt_col = None
    selected_layers = None
    unit_scale = 1.0

    with st.expander("🛠️ Parsing & Coordinate System Settings", expanded=True):
        # 1. Tabular (CSV / Excel) Configuration
        if inspection.file_type in ("csv", "excel"):
            st.markdown("##### 📍 Tabular Coordinate Mapping")
            geom_mode = st.radio(
                "Geometry Definition Method",
                ["Coordinate Columns (X / Y)", "WKT Geometry String"],
                horizontal=True,
            )

            if geom_mode == "Coordinate Columns (X / Y)":
                col_x, col_y, col_swap = st.columns([4, 4, 2])
                with col_x:
                    default_x_idx = 0
                    if inspection.suggested_x and inspection.suggested_x in inspection.columns:
                        default_x_idx = inspection.columns.index(inspection.suggested_x)
                    x_col = st.selectbox("Longitude / Easting / X Column", inspection.columns, index=default_x_idx)
                with col_y:
                    default_y_idx = min(1, len(inspection.columns) - 1)
                    if inspection.suggested_y and inspection.suggested_y in inspection.columns:
                        default_y_idx = inspection.columns.index(inspection.suggested_y)
                    y_col = st.selectbox("Latitude / Northing / Y Column", inspection.columns, index=default_y_idx)
                with col_swap:
                    st.write("")
                    st.write("")
                    if st.button("⇄ Swap X / Y", help="Click if latitude and longitude are inverted"):
                        temp_x = x_col
                        x_col = y_col
                        y_col = temp_x
                        st.success("Swapped!")
            else:
                default_wkt_idx = 0
                if inspection.suggested_wkt and inspection.suggested_wkt in inspection.columns:
                    default_wkt_idx = inspection.columns.index(inspection.suggested_wkt)
                wkt_col = st.selectbox("WKT Geometry Column", inspection.columns, index=default_wkt_idx)

        # 2. CAD Configuration
        if inspection.file_type == "cad":
            st.markdown("##### 📐 CAD Drawing Settings")
            col_u1, col_u2 = st.columns(2)
            with col_u1:
                selected_unit = st.selectbox(
                    "CAD Drawing Units",
                    list(CAD_UNIT_SCALES.keys()),
                    index=0,
                    help="AutoCAD drawings use unitless coordinates. Select the drawing unit used by the surveyor/drafter.",
                )
                unit_scale = CAD_UNIT_SCALES[selected_unit]
            with col_u2:
                if inspection.layers:
                    selected_layers = st.multiselect(
                        "Layers to Ingest (ModelSpace)",
                        options=inspection.layers,
                        default=inspection.layers,
                        help="Select which CAD layers to import into vector geometries.",
                    )

        # 3. CRS Assignment (for CAD, unprojected Shapefiles, or CSV)
        if not source_crs or inspection.has_missing_prj or inspection.file_type in ("cad", "csv", "excel"):
            st.markdown("##### 🌐 Coordinate Reference System (Source CRS)")
            crs_search = st.text_input(
                "Search CRS by EPSG code or keywords (e.g. '4326', 'UTM 32N', 'State Plane California')",
                value="4326" if not source_crs else "",
                help="Type to search from 7,000+ official EPSG projections.",
            )

            crs_results = search_epsg(crs_search, limit=15)
            if crs_results:
                crs_options = [code for code, label in crs_results]
                crs_labels = {code: label for code, label in crs_results}
                chosen_crs = st.selectbox(
                    "Select Matching CRS",
                    crs_options,
                    format_func=lambda c: crs_labels.get(c, c),
                    index=0,
                )
                source_crs = chosen_crs
            else:
                st.warning("No matching CRS found. Defaulting to EPSG:4326 (WGS 84).")
                source_crs = "EPSG:4326"

    # Step 3: Load Data to GeoDataFrame
    try:
        with st.spinner("Loading and parsing geometries..."):
            gdf = load_file_to_geodataframe(
                file_name=file_name,
                file_bytes=file_bytes,
                file_type=inspection.file_type,
                crs=source_crs,
                x_col=x_col,
                y_col=y_col,
                wkt_col=wkt_col,
                selected_layers=selected_layers,
                unit_scale=unit_scale,
            )
    except IngestionError as e:
        st.error(f"❌ Ingestion Error: {e}")
        return
    except Exception as e:
        st.error(f"❌ Unexpected Error while reading file: {e}")
        with st.expander("Technical Traceback"):
            st.code(traceback.format_exc())
        return

    # Check for empty data
    if len(gdf) == 0:
        st.warning("No features loaded from the dataset.")
        return

    # Step 4: Interactive Verification Map & Attribute Inspection
    st.markdown("---")
    st.markdown("### 3. Interactive Verification Map & Attributes")

    tab_map, tab_table = st.tabs(["🗺️ Interactive Map Preview", "📋 Attribute Table & Statistics"])

    with tab_map:
        col_m1, col_m2 = st.columns([8, 2])
        with col_m2:
            map_style = st.selectbox("Basemap Style", ["road", "light", "dark"], index=0)

        # Generate PyDeck map
        deck, map_warning = generate_pydeck_map(gdf, max_features=1200, map_style=map_style)

        if map_warning:
            st.info(f"💡 {map_warning}")

        if deck:
            st.pydeck_chart(deck, use_container_width=True)
        else:
            st.warning("Could not generate map preview for this geometry structure.")

    with tab_table:
        col_s1, col_s2, col_s3 = st.columns(3)
        with col_s1:
            st.metric("Total Feature Count", f"{len(gdf):,}")
        with col_s2:
            st.metric("Assigned CRS", format_crs_display(gdf.crs))
        with col_s3:
            st.metric("Attribute Columns", f"{len(gdf.columns) - 1}")

        # Display preview table
        display_df = gdf.drop(columns=["geometry"], errors="ignore").head(25)
        st.dataframe(display_df, use_container_width=True)

    # Step 5: Export Configuration & Download
    st.markdown("---")
    st.markdown("### 4. Target Format & Export")

    col_e1, col_e2 = st.columns([5, 5])

    with col_e1:
        target_format = st.selectbox(
            "Select Target Output Format",
            [
                ("shapefile", "📦 ESRI Shapefile (.zip)"),
                ("kmz", "🌍 Google Earth KMZ (.kmz)"),
                ("kml", "📄 Google Earth KML (.kml)"),
                ("geojson", "🌐 GeoJSON (.geojson)"),
                ("csv", "📊 CSV Spreadsheet with Coordinates & WKT (.csv)"),
                ("dxf", "📐 AutoCAD Drawing Exchange Format (.dxf)"),
                ("geopackage", "🗄️ OGC GeoPackage (.gpkg)"),
            ],
            format_func=lambda x: x[1],
            index=0,
        )[0]

    with col_e2:
        reproject_choice = st.radio(
            "Target Projection",
            ["Keep Current Projection", "Reproject to WGS 84 (EPSG:4326)", "Reproject to Web Mercator (EPSG:3857)"],
            horizontal=True,
        )

        target_crs_code = None
        if reproject_choice == "Reproject to WGS 84 (EPSG:4326)":
            target_crs_code = "EPSG:4326"
        elif reproject_choice == "Reproject to Web Mercator (EPSG:3857)":
            target_crs_code = "EPSG:3857"

    # Base output filename
    base_file_name = os.path.splitext(file_name)[0]
    output_base = st.text_input("Output Filename (without extension)", value=f"{base_file_name}_converted")

    # Step 6: Convert & Download
    if st.button("🚀 Convert & Prepare Download", type="primary", use_container_width=True):
        try:
            with st.spinner("Converting dataset..."):
                file_bytes_out, out_filename, mime_type = export_dataset(
                    gdf=gdf,
                    target_format=target_format,
                    base_name=output_base,
                    target_crs=target_crs_code,
                )

            st.success(f"✅ Conversion complete! File size: {len(file_bytes_out) / 1024:,.1f} KB")

            st.download_button(
                label=f"⬇️ Download {out_filename}",
                data=file_bytes_out,
                file_name=out_filename,
                mime=mime_type,
                type="primary",
                use_container_width=True,
            )

        except ExportError as e:
            st.error(f"Export failed: {e}")
        except Exception as e:
            st.error(f"Unexpected conversion error: {e}")
            with st.expander("Technical Traceback"):
                st.code(traceback.format_exc())


if __name__ == "__main__":
    main()
