"""Comprehensive unit and integration test suite for the GIS & CAD converter."""

import io
import os
import tempfile
import zipfile

import ezdxf
import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point, Polygon

from core.cad_engine import read_cad_to_geodataframe
from core.crs_utils import reproject_geodataframe, search_epsg
from core.preview import check_coordinate_sanity, generate_pydeck_map
from core.readers import inspect_uploaded_file, load_file_to_geodataframe
from core.writers import export_dataset


def test_csv_ingestion_and_export():
    """Test reading CSV with coordinate columns, auto-detecting, and converting."""
    csv_content = b"SiteID,longitude,latitude,Elevation\nAlpha,-122.4194,37.7749,15\nBeta,-118.2437,34.0522,85"
    inspection = inspect_uploaded_file("sites.csv", csv_content)

    assert inspection.file_type == "csv"
    assert inspection.suggested_x == "longitude"
    assert inspection.suggested_y == "latitude"

    gdf = load_file_to_geodataframe(
        file_name="sites.csv",
        file_bytes=csv_content,
        file_type="csv",
        crs="EPSG:4326",
        x_col=inspection.suggested_x,
        y_col=inspection.suggested_y,
    )

    assert len(gdf) == 2
    assert gdf.crs == "EPSG:4326"
    assert gdf.iloc[0].geometry.geom_type == "Point"

    # Export to GeoJSON
    out_bytes, filename, mime = export_dataset(gdf, "geojson", base_name="sites")
    assert filename == "sites.geojson"
    assert b"FeatureCollection" in out_bytes


def test_csv_wkt_ingestion():
    """Test reading CSV containing WKT polygon geometry strings."""
    csv_content = b'Name,WKT\nParcelA,"POLYGON ((0 0, 10 0, 10 10, 0 10, 0 0))"'
    inspection = inspect_uploaded_file("parcels.csv", csv_content)
    assert inspection.suggested_wkt == "WKT"

    gdf = load_file_to_geodataframe(
        file_name="parcels.csv",
        file_bytes=csv_content,
        file_type="csv",
        wkt_col="WKT",
        crs="EPSG:4326",
    )

    assert len(gdf) == 1
    assert gdf.iloc[0].geometry.geom_type == "Polygon"


def test_kmz_ingestion_and_export():
    """Test reading KMZ archive containing doc.kml and exporting to KMZ."""
    kml = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<kml xmlns="http://www.opengis.net/kml/2.2">'
        b"<Document>"
        b"<Placemark><name>Point1</name><Point><coordinates>15.0,45.0,0</coordinates></Point></Placemark>"
        b"</Document>"
        b"</kml>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("doc.kml", kml)
    kmz_bytes = buf.getvalue()

    inspection = inspect_uploaded_file("tracks.kmz", kmz_bytes)
    assert inspection.file_type == "kmz"
    assert inspection.detected_crs == "EPSG:4326"

    gdf = load_file_to_geodataframe("tracks.kmz", kmz_bytes, "kmz")
    assert len(gdf) >= 1
    assert gdf.crs == "EPSG:4326"

    # Export to KMZ
    out_bytes, filename, mime = export_dataset(gdf, "kmz", base_name="tracks_out")
    assert filename == "tracks_out.kmz"
    with zipfile.ZipFile(io.BytesIO(out_bytes)) as out_zf:
        assert "doc.kml" in out_zf.namelist()


def test_shapefile_zip_ingestion_and_mixed_export():
    """Test reading a Shapefile ZIP and exporting mixed geometries into partitioned Shapefile ZIP."""
    gdf = gpd.GeoDataFrame(
        {
            "name": ["PointA", "LineB"],
            "layer": ["Pins", "Roads"],
            "geometry": [Point(10, 20), LineString([(0, 0), (1, 1), (2, 2)])],
        },
        crs="EPSG:4326",
    )

    # Export to Shapefile ZIP (mixed geometry types partitioned)
    zip_bytes, filename, mime = export_dataset(gdf, "shapefile", base_name="mixed_data")
    assert filename == "mixed_data.zip"

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        namelist = zf.namelist()
        # Should have partitioned points and lines shapefiles
        assert any("mixed_data_points.shp" in n for n in namelist)
        assert any("mixed_data_lines.shp" in n for n in namelist)
        assert any("mixed_data_points.prj" in n for n in namelist)

    # Ingest the generated ZIP back
    inspection = inspect_uploaded_file(filename, zip_bytes)
    assert inspection.file_type == "shapefile_zip"


def test_cad_dxf_extraction():
    """Test extracting entities from a synthetic AutoCAD DXF file."""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_line((100, 200), (300, 400), dxfattribs={"layer": "PIPELINE"})
    msp.add_lwpolyline([(0, 0), (50, 0), (50, 50), (0, 50)], close=True, dxfattribs={"layer": "BOUNDARY"})
    msp.add_circle((150, 150), radius=25, dxfattribs={"layer": "MANHOLES"})
    msp.add_text("Valve #12", dxfattribs={"insert": (120, 120), "layer": "TEXT_LABELS"})

    with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tmp:
        doc.saveas(tmp.name)
        tmp_path = tmp.name

    try:
        with open(tmp_path, "rb") as f:
            dxf_bytes = f.read()

        inspection = inspect_uploaded_file("project.dxf", dxf_bytes)
        assert inspection.file_type == "cad"
        assert "PIPELINE" in inspection.layers
        assert "BOUNDARY" in inspection.layers

        gdf = load_file_to_geodataframe(
            file_name="project.dxf",
            file_bytes=dxf_bytes,
            file_type="cad",
            crs="EPSG:3857",
            selected_layers=["PIPELINE", "BOUNDARY"],
        )

        assert len(gdf) == 2
        assert gdf.crs == "EPSG:3857"
        assert set(gdf["layer"].unique()) == {"PIPELINE", "BOUNDARY"}

        # Export to DXF
        dxf_out, out_name, _ = export_dataset(gdf, "dxf", base_name="project_out")
        assert out_name == "project_out.dxf"
        assert len(dxf_out) > 0
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def test_coordinate_sanity_detection():
    """Test detection of inverted coordinates or out-of-bound coordinates."""
    # Latitude > 90° inverted
    inverted_gdf = gpd.GeoDataFrame(
        {"name": ["BadCoord"], "geometry": [Point(45.0, 120.0)]}, crs="EPSG:4326"
    )
    warn = check_coordinate_sanity(inverted_gdf)
    assert warn is not None
    assert "inverted" in warn.lower()

    # Valid coordinates
    valid_gdf = gpd.GeoDataFrame(
        {"name": ["GoodCoord"], "geometry": [Point(-122.4, 37.7)]}, crs="EPSG:4326"
    )
    assert check_coordinate_sanity(valid_gdf) is None


def test_epsg_search_and_reprojection():
    """Test searching official EPSG projections and reprojection."""
    results = search_epsg("3857")
    assert any(code == "EPSG:3857" for code, _ in results)

    gdf = gpd.GeoDataFrame(
        {"name": ["NYC"], "geometry": [Point(-74.0060, 40.7128)]}, crs="EPSG:4326"
    )
    reprojected = reproject_geodataframe(gdf, "EPSG:3857")
    assert reprojected.crs == "EPSG:3857"
    # In Web Mercator, coordinates should be in hundreds of thousands/millions
    assert abs(reprojected.geometry[0].x) > 1000000
