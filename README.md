# solweig-lyon

Thermal comfort analysis for the Lyon metropolis using [SOLWEIG-GPU](https://github.com/nvnsudharsan/SOLWEIG-GPU).

Prepares local French open data inputs (GrandLyon LiDAR, BD TOPO, GrandLyon WFS) and runs three climate scenarios (2020 / 2060 / 2090) for July 14.

## Setup

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) if you don't have it, then:

```bash
uv sync
```

This installs all dependencies including `solweig-gpu` directly from the latest commit on the [main branch](https://github.com/nvnsudharsan/SOLWEIG-GPU).

## Data

| File | Description |
|---|---|
| `data/vegestrate_02_2023_elevation.tif` | Vegetation height nDSM, EPSG:3946, 0.2 m |
| `data/01-CURRENT_14jul.txt` | UMEP met file — 2020 current climate, July 14 |
| `data/02-MID-CENTURY_14jul.txt` | UMEP met file — 2060 mid-century scenario |
| `data/03-END-CENTURY_14jul.txt` | UMEP met file — 2090 end-century scenario |
| `lidar_tiles.csv` | Index of 2842 GrandLyon LiDAR tiles (500 m × 500 m) with download URLs |

## Step 1 — Prepare inputs

```bash
uv run python prepare_data.py
```

Downloads data and writes four rasters to `inputs/`:

| Output | Source |
|---|---|
| `Trees.tif` | Cropped & resampled from `vegestrate_02_2023_elevation.tif` |
| `DEM.tif` | GrandLyon LiDAR 2023 (.laz tiles), ground class 2 → DTM |
| `Building_DSM.tif` | BD TOPO `HAUTEUR` rasterised + DEM |
| `Landcover.tif` | UMEP classes: paved default, water (GrandLyon WFS), vegetation (Trees > 0), buildings |

Default bbox is the Confluence district (2 × 2 km, ~16 LiDAR tiles):

```bash
# Custom bbox in EPSG:3946
uv run python prepare_data.py --bbox 1839000 5171000 1841000 5173000

# Full Lyon metropolis (uses SOLWEIG-GPU tiling internally)
uv run python prepare_data.py --bbox 1831000 5152000 1860500 5195000

# Coarser resolution (faster)
uv run python prepare_data.py --resolution 2
```

LiDAR tiles are cached in `data/lidar_tiles/` and skipped on re-runs. Building footprints are cached in `inputs/cache_buildings.geojson`.

## Step 2 — Run SOLWEIG

```bash
uv run python run.py
```

Runs all three climate scenarios sequentially. Results land in:

```
outputs/
├── 2020_current/
├── 2060_mid_century/
└── 2090_end_century/
```

Each folder contains per-tile GeoTIFFs (UTCI, Tmrt, …) from SOLWEIG-GPU.

## UMEP land cover codes

| Code | Class |
|---|---|
| 1 | Paved |
| 2 | Buildings |
| 3 | Water |
| 4 | Vegetation |
| 5 | Bare soil |
