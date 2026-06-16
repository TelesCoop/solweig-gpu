# Plan: solweig-lyon — thermal comfort pipeline for Lyon

## Goal

Create a new repo `../solweig-lyon/` with scripts to prepare Lyon-specific input data and run SOLWEIG-GPU thermal comfort analysis. Uses local French open data (GrandLyon LiDAR, BD TOPO, GrandLyon WFS) instead of the generic `create_inputs.py` which requires Google Earth Engine.

---

## New repo structure

```
../solweig-lyon/
├── data/                          ← moved from SOLWEIG-GPU/my_data/
│   ├── vegestrate_02_2023_elevation.tif   (vegetation height nDSM, EPSG:3946, 0.2m)
│   ├── 01-CURRENT_14jul.txt               (UMEP met file, 2020 current climate, July 14)
│   ├── 02-MID-CENTURY_14jul.txt           (UMEP met file, 2060 mid-century scenario)
│   └── 03-END-CENTURY_14jul.txt           (UMEP met file, 2090 end-century scenario)
├── lidar_tiles.csv                ← symlink or copy of vegestrate/data/nuage-de-points-lidar-2023-de-la-metropole-de-lyon.csv
├── inputs/                        ← generated SOLWEIG inputs (gitignored, large)
│   ├── DEM.tif
│   ├── Building_DSM.tif
│   ├── Trees.tif
│   └── Landcover.tif
├── outputs/                       ← SOLWEIG results (gitignored)
├── prepare_data.py
├── run.py
└── .gitignore
```

---

## Input data sources

| SOLWEIG input | Source | Status |
|---|---|---|
| `Trees.tif` (veg height above ground, m) | `data/vegestrate_02_2023_elevation.tif` | ✓ exists, needs crop + resample to 1m |
| `DEM.tif` (bare earth elevation, m) | GrandLyon LiDAR ground points (class 2) | needs download + DTM extraction |
| `Building_DSM.tif` (terrain + buildings, m) | BD TOPO `BDTOPO_V3:batiment` + DEM | needs WFS download + rasterize + add to DEM |
| `Landcover.tif` (UMEP classes 1-5) | BD TOPO + Trees.tif + GrandLyon water WFS | derivable without Django |
| Met files | `data/01-CURRENT_14jul.txt` etc. | ✓ already UMEP format (years 2020/2060/2090) |

**UMEP land cover codes:** 1=paved, 2=buildings, 3=water, 4=vegetation, 5=bare soil

**Target:** EPSG:3946, 1m resolution. First run on small neighborhood (~2×2 km), then full metropole with SOLWEIG-GPU tiling.

---

## `prepare_data.py`

Four functions, all idempotent (skip if output exists). Shared args: `bbox=(xmin,ymin,xmax,ymax)` in EPSG:3946, `resolution_m=1`.

### Default test bbox (Confluence district)
```python
DEFAULT_BBOX = (1839000, 5171000, 1841000, 5173000)  # 2×2 km
```

---

### 1. `prepare_trees(bbox, veg_tif, out_path, resolution_m=1)`

- Open `data/vegestrate_02_2023_elevation.tif` with **rasterio windowed read** (avoids loading 147k×215k px into RAM)
- Reproject/resample to 1m at bbox: `rasterio.warp.reproject(..., resampling=Resampling.bilinear)`
- Clip negatives to 0
- Save → `inputs/Trees.tif`

---

### 2. `prepare_dem(bbox, lidar_csv, lidar_dir, out_path, resolution_m=1)`

**GrandLyon LiDAR tiles:**
- Full tile index: `vegestrate/data/nuage-de-points-lidar-2023-de-la-metropole-de-lyon.csv` (2842 tiles, 500m × 500m each)
- CSV columns: `gid;nom;campagne_releve;x_min;y_min;x_max;y_max;url`
- Filter tiles intersecting bbox:
  ```python
  df = pd.read_csv(lidar_csv, sep=";")
  tiles = df[(df.x_min < bbox[2]) & (df.x_max > bbox[0]) &
             (df.y_min < bbox[3]) & (df.y_max > bbox[1])]
  ```
- Download each tile URL → `lidar_dir/{nom}.laz` (skip if exists)

**DTM extraction** (adapted from `vegestrate/src/core/utils.py:points_to_ndsm`, ~20 lines, no package dependency):
- Load `.laz` with laspy
- Filter ground points: `classifications == 2`
- Bin max-Z per pixel → DTM raster
- Mosaic tiles with `rasterio.merge`
- Resample to 1m at bbox
- Save → `inputs/DEM.tif`

---

### 3. `prepare_building_dsm(bbox, dem_path, out_path, resolution_m=1)`

**BD TOPO download** (adapted from `iarbre-back/back/iarbre_data/utils/download.py:download_dbtopo`):
```python
# Key difference from iarbre-back: keep HAUTEUR column (iarbre-back drops it)
params = {
    "SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature",
    "TYPENAMES": "BDTOPO_V3:batiment", "SRSNAME": "EPSG:2154",
    "OUTPUTFORMAT": "text/xml; subtype=gml/3.2",
    "BBOX": f"{bbox_2154_str},EPSG:2154",
}
gdf = gpd.read_file(BytesIO(response.content))  # keeps HAUTEUR
gdf["height"] = pd.to_numeric(gdf["HAUTEUR"], errors="coerce").fillna(5.0)
```
- Reproject to EPSG:3946
- Rasterize building heights at 1m: `rasterio.features.rasterize(shapes, burn_value=height)`
- `Building_DSM = DEM_array + building_height_raster`
- Save → `inputs/Building_DSM.tif`

---

### 4. `prepare_landcover(bbox, trees_path, building_footprints_gdf, out_path, resolution_m=1)`

Priority order (higher overwrites lower):
1. **Default: class 1** (paved) — reasonable for Lyon urban fabric
2. **Class 3** (water): WFS `fpc_fond_plan_communaut.fpcplandeau`  
   URL: `https://data.grandlyon.com/geoserver/metropole-de-lyon/ows?SERVICE=WFS&VERSION=2.0.0&request=GetFeature&typename=metropole-de-lyon:fpc_fond_plan_communaut.fpcplandeau&outputFormat=GML3&SRSNAME=EPSG:2154`  
   (from `iarbre-back/back/iarbre_data/data_config.py` lines 329-337)
3. **Class 4** (vegetation): where `Trees.tif > 0` — reuses already-prepared raster
4. **Class 2** (buildings): burn building footprints — reuses BD TOPO download

Save → `inputs/Landcover.tif`

---

### CLI

```bash
python prepare_data.py                                    # default Confluence bbox
python prepare_data.py --bbox 1839000 5171000 1841000 5173000
python prepare_data.py --bbox 1831000 5152000 1860500 5195000  # full metropole
```

---

## `run.py`

```python
from solweig_gpu import thermal_comfort

# All three files have iy=1985, id=195 in their headers.
# The scenario names (2020/2060/2090) are the climate years they represent, not the iy value.
# selected_date_str must match iy=1985 so SOLWEIG computes correct sun position from met rows.
MET_FILES = {
    "2020_current":     "data/01-CURRENT_14jul.txt",
    "2060_mid_century": "data/02-MID-CENTURY_14jul.txt",
    "2090_end_century": "data/03-END-CENTURY_14jul.txt",
}
DATE_STR = "1985-07-14"

for scenario, met_file in MET_FILES.items():
    thermal_comfort(
        base_path="inputs/",
        selected_date_str=DATE_STR,        # matches iy=1985 in all three met files
        building_dsm_filename="Building_DSM.tif",
        dem_filename="DEM.tif",
        trees_filename="Trees.tif",
        landcover_filename="Landcover.tif",
        tile_size=1000,                    # handles full-metropole tiling natively
        overlap=100,
        use_own_met=True,
        own_met_file=met_file,
        save_tmrt=True,
        save_svf=False,
    )
    # outputs moved to outputs/{scenario}/  e.g. outputs/2020_current/
```

---

## Creation steps

1. `git init ../solweig-lyon`
2. Move `SOLWEIG-GPU/my_data/` → `../solweig-lyon/data/`
3. Symlink (or copy) `vegestrate/data/nuage-de-points-lidar-2023-de-la-metropole-de-lyon.csv` → `../solweig-lyon/lidar_tiles.csv`
4. Write `prepare_data.py` (4 functions + argparse CLI)
5. Write `run.py`
6. Write `.gitignore`:
   ```
   inputs/
   outputs/
   data/lidar_tiles/
   data/*.tif
   __pycache__/
   ```
7. Test: `python prepare_data.py` → inspect rasters → `python run.py`

---

## Notes

- **BD TOPO `HAUTEUR`**: may be null for some buildings → default to 5m
- **LiDAR tiles**: full index at `vegestrate/data/nuage-de-points-lidar-2023-de-la-metropole-de-lyon.csv` (2842 tiles, 500m × 500m, ~2-10 MB each as .laz); a 2×2 km area needs ~16 tiles
- **Met file years**: 2020 (current), 2060 (mid-century), 2090 (end-century) — `selected_date_str` uses these years with July 14; note the `iy` column in the files may differ and should be verified against SOLWEIG-GPU date-matching logic
- **Full metropole**: `tile_size=1000, overlap=100` in SOLWEIG-GPU handles tiling natively, no extra code needed
- **vegestrate dependency**: only ~20 lines copied (DTM from ground points), no import of the package
- **iarbre-back dependency**: only the WFS URL patterns and HAUTEUR field knowledge, no Django import
