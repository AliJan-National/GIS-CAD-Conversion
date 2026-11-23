# 🗺️ Universal GIS & CAD File Converter

An open-source, web-based geospatial and CAD data conversion portal built with Python and Streamlit. Designed for seamless deployment on **[Streamlit Community Cloud (share.streamlit.io)](https://share.streamlit.io)**.

---

## 🌟 Key Features

- **Multi-Format Ingestion**:
  - 📦 **Shapefile**: Drag-and-drop `.zip` archives (validates `.shp`, `.shx`, `.dbf`, `.prj`).
  - 🌍 **KMZ & KML**: Google Earth compressed and uncompressed files with auto-extraction.
  - 📊 **CSV & Excel**: Interactive column mapper for X/Y coordinates or WKT geometry strings.
  - 📐 **AutoCAD CAD (DWG & DXF)**: Extracts layers, lines, polylines, circles, arcs, text, and blocks.
  - 🌐 **Web GIS**: GeoJSON and OGC GeoPackage (`.gpkg`).
- **Interactive Verification Map**:
  - WebGL-accelerated 2D/3D map preview powered by **PyDeck**.
  - **Spatial Sanity Checks**: Detects inverted coordinates (swapped Lat/Lon) or missing projection coordinates before you convert.
  - 1-click **Swap X / Y** button for instant coordinate correction.
- **Universal Export Engine**:
  - Export to **Shapefile (.zip)** (automatically partitions mixed geometry types into separate shapefile layers inside the zip).
  - Export to **KMZ (.kmz)**, **KML (.kml)**, **GeoJSON**, **CSV (with Lon/Lat/WKT)**, **AutoCAD DXF**, and **GeoPackage**.
- **CRS Lookup & Reprojection Engine**:
  - Instant search across 7,000+ official EPSG projections.
  - Built-in on-the-fly reprojection (e.g. from local State Plane or UTM to WGS 84 or Web Mercator).
- **Zero Server Data Retention**:
  - All processing runs in sandboxed memory and temporary buffers, ensuring user data privacy.

---

## 🚀 Quick Start (Local Setup)

### 1. Clone the repository
```bash
git clone https://github.com/your-username/arcgis-conversion-tools.git
cd arcgis-conversion-tools
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Run the application
```bash
streamlit run app.py
```
Open your browser at `http://localhost:8501`.

---

## ☁️ Deploying to `share.streamlit.io` (Free Public Hosting)

This repository includes pre-configured deployment files:
- **`packages.txt`**: Installs Linux system packages (`gdal-bin`, `libgdal-dev`, and `libredwg-tools` for DWG conversion).
- **`requirements.txt`**: Installs Python geospatial packages.

### Steps to Deploy:
1. Push this repository to **GitHub**.
2. Go to **[share.streamlit.io](https://share.streamlit.io)** and click **"New app"**.
3. Select your repository, branch (`main`), and set the main file path to `app.py`.
4. Click **"Deploy"**! Streamlit Cloud will automatically install the system packages and launch the app online.

---

## 📐 AutoCAD DWG Handling

Autodesk DWG is a closed proprietary binary format. On **Streamlit Community Cloud (`share.streamlit.io`)**, the Linux container automatically installs `libredwg-tools` via `packages.txt`, providing the open-source `dwg2dxf` command line utility.

> **Tip for CAD Users**: For maximum conversion speed and complex 3D / Civil 3D models, exporting as **`.DXF`** directly from AutoCAD or Civil 3D before uploading is recommended.

---

## 📂 Project Structure

```text
├── app.py                     # Streamlit web application & user interface
├── packages.txt               # Debian apt packages for share.streamlit.io
├── requirements.txt           # Python dependencies
├── README.md                  # Project documentation & deployment guide
├── core/
│   ├── __init__.py
│   ├── cad_engine.py          # DXF parser and DWG fallback conversion
│   ├── crs_utils.py           # EPSG search database, unit scales, reprojection
│   ├── preview.py             # PyDeck interactive map and sanity checks
│   ├── readers.py             # Multi-format ingestion and file inspection
│   └── writers.py             # Multi-format export and ZIP packaging
└── tests/
    └── test_converters.py     # End-to-end unit and integration tests
```

---

## 📄 License
MIT License. Free to use, modify, and distribute.
