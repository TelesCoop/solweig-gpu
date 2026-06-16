import argparse
from io import BytesIO
from pathlib import Path

import geopandas as gpd
import laspy
import numpy as np
import pandas as pd
import rasterio
import requests
from pyproj import Transformer
from rasterio.features import rasterize
from rasterio.merge import merge
from rasterio.transform import from_origin
from rasterio.warp import reproject, Resampling
from scipy.ndimage import distance_transform_edt

CRS = "EPSG:3946"
DEFAULT_BBOX = (1839000, 5171000, 1841000, 5173000)  # Confluence district, 2×2 km

BDTOPO_URL = "https://data.geopf.fr/wfs/ows"
WATER_WFS = (
    "https://data.grandlyon.com/geoserver/metropole-de-lyon/ows"
    "?SERVICE=WFS&VERSION=2.0.0&request=GetFeature"
    "&typename=metropole-de-lyon:fpc_fond_plan_communaut.fpcplandeau"
    "&outputFormat=GML3&SRSNAME=EPSG:2154"
)


def _grid(bbox, res):
    xmin, ymin, xmax, ymax = bbox
    transform = from_origin(xmin, ymax, res, res)
    width = int((xmax - xmin) / res)
    height = int((ymax - ymin) / res)
    return transform, width, height


def _to_2154(bbox):
    t = Transformer.from_crs("EPSG:3946", "EPSG:2154", always_xy=True)
    xmin, ymin, xmax, ymax = bbox
    x1, y1 = t.transform(xmin, ymin)
    x2, y2 = t.transform(xmax, ymax)
    return x1, y1, x2, y2


def prepare_trees(bbox, veg_tif, out_path, res=1):
    out_path = Path(out_path)
    if out_path.exists():
        print(f"✓ Trees exists: {out_path}")
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    transform, width, height = _grid(bbox, res)
    result = np.zeros((height, width), dtype=np.float32)
    with rasterio.open(veg_tif) as src:
        reproject(
            source=rasterio.band(src, 1),
            destination=result,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=transform,
            dst_crs=CRS,
            resampling=Resampling.bilinear,
        )
    result = np.clip(result, 0, None)
    with rasterio.open(out_path, "w", driver="GTiff", height=height, width=width,
                       count=1, dtype="float32", crs=CRS, transform=transform,
                       nodata=0, compress="lzw") as dst:
        dst.write(result, 1)
    print(f"✓ Trees: {out_path}")


def prepare_dem(bbox, lidar_csv, lidar_dir, out_path, res=1):
    out_path = Path(out_path)
    if out_path.exists():
        print(f"✓ DEM exists: {out_path}")
        return
    lidar_dir = Path(lidar_dir)
    lidar_dir.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    xmin, ymin, xmax, ymax = bbox
    df = pd.read_csv(lidar_csv, sep=";")
    tiles = df[
        (df.x_min < xmax) & (df.x_max > xmin) &
        (df.y_min < ymax) & (df.y_max > ymin)
    ]
    print(f"  {len(tiles)} LiDAR tiles intersect bbox")

    tile_tifs = []
    for _, row in tiles.iterrows():
        laz_path = lidar_dir / f"{row['nom']}.laz"
        tif_path = lidar_dir / f"{row['nom']}_dtm.tif"

        if not tif_path.exists():
            if not laz_path.exists():
                print(f"  Downloading {row['nom']}.laz ...")
                resp = requests.get(row["url"], stream=True, timeout=300)
                resp.raise_for_status()
                with open(laz_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=65536):
                        f.write(chunk)

            las = laspy.read(str(laz_path))
            mask = np.array(las.classification) == 2
            x = np.array(las.x[mask], dtype=np.float64)
            y = np.array(las.y[mask], dtype=np.float64)
            z = np.array(las.z[mask], dtype=np.float32)

            if len(x) == 0:
                print(f"  Warning: no ground points in {row['nom']}, skipping")
                continue

            tx, ty_max = float(row.x_min), float(row.y_max)
            tw = int((row.x_max - row.x_min) / res)
            th = int((row.y_max - row.y_min) / res)
            tile_transform = from_origin(tx, ty_max, res, res)

            dtm = np.full((th, tw), np.nan, dtype=np.float32)
            ci = np.clip(((x - tx) / res).astype(int), 0, tw - 1)
            ri = np.clip(((ty_max - y) / res).astype(int), 0, th - 1)
            np.maximum.at(dtm, (ri, ci), z)

            with rasterio.open(tif_path, "w", driver="GTiff", height=th, width=tw,
                               count=1, dtype="float32", crs=CRS, transform=tile_transform,
                               nodata=np.nan, compress="lzw") as dst:
                dst.write(dtm, 1)

        tile_tifs.append(tif_path)

    if not tile_tifs:
        raise RuntimeError("No DTM tiles produced — check LiDAR download and ground points")

    transform, width, height = _grid(bbox, res)
    srcs = [rasterio.open(p) for p in tile_tifs]
    try:
        mosaic, _ = merge(srcs, bounds=(xmin, ymin, xmax, ymax), res=res, nodata=np.nan)
    finally:
        for s in srcs:
            s.close()

    dtm = mosaic[0]
    valid = np.isfinite(dtm)
    if valid.any() and not valid.all():
        idx = distance_transform_edt(~valid, return_indices=True)
        dtm = dtm[tuple(idx)]

    with rasterio.open(out_path, "w", driver="GTiff", height=height, width=width,
                       count=1, dtype="float32", crs=CRS, transform=transform,
                       nodata=np.nan, compress="lzw") as dst:
        dst.write(dtm, 1)
    print(f"✓ DEM: {out_path}")


def _download_buildings(bbox, cache_path):
    cache_path = Path(cache_path)
    if cache_path.exists():
        return gpd.read_file(cache_path)
    print("  Downloading BD TOPO buildings (BDTOPO_V3:batiment)...")
    x1, y1, x2, y2 = _to_2154(bbox)
    # paginate: BD TOPO WFS caps at 5000 features per request
    all_gdfs = []
    start = 0
    while True:
        params = {
            "SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature",
            "TYPENAMES": "BDTOPO_V3:batiment", "SRSNAME": "EPSG:2154",
            "OUTPUTFORMAT": "text/xml; subtype=gml/3.2",
            "BBOX": f"{x1},{y1},{x2},{y2},EPSG:2154",
            "COUNT": 5000, "STARTINDEX": start,
        }
        resp = requests.get(BDTOPO_URL, params=params, timeout=120)
        resp.raise_for_status()
        gdf = gpd.read_file(BytesIO(resp.content))
        if gdf.empty:
            break
        all_gdfs.append(gdf)
        if len(gdf) < 5000:
            break
        start += 5000

    if not all_gdfs:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:2154")

    gdf = gpd.GeoDataFrame(pd.concat(all_gdfs, ignore_index=True), crs="EPSG:2154")
    hauteur_col = next((c for c in gdf.columns if c.upper() == "HAUTEUR"), None)
    gdf["height"] = pd.to_numeric(gdf[hauteur_col] if hauteur_col else pd.Series(dtype=float),
                                  errors="coerce").fillna(5.0)
    gdf = gdf[["geometry", "height"]].to_crs(CRS)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(cache_path, driver="GeoJSON")
    print(f"  {len(gdf)} buildings cached → {cache_path}")
    return gdf


def prepare_building_dsm(bbox, dem_path, out_path, bld_cache, res=1):
    out_path = Path(out_path)
    if out_path.exists():
        print(f"✓ Building_DSM exists: {out_path}")
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)

    gdf = _download_buildings(bbox, bld_cache)
    transform, width, height = _grid(bbox, res)

    bld_h = np.zeros((height, width), dtype=np.float32)
    if not gdf.empty:
        shapes = ((geom, float(h)) for geom, h in zip(gdf.geometry, gdf["height"]) if geom is not None)
        bld_h = rasterize(shapes, out_shape=(height, width), transform=transform,
                          fill=0, dtype="float32")

    with rasterio.open(dem_path) as src:
        dem = src.read(1)

    with rasterio.open(out_path, "w", driver="GTiff", height=height, width=width,
                       count=1, dtype="float32", crs=CRS, transform=transform,
                       nodata=np.nan, compress="lzw") as dst:
        dst.write(dem + bld_h, 1)
    print(f"✓ Building_DSM: {out_path}")


def prepare_landcover(bbox, trees_path, out_path, bld_cache, res=1):
    out_path = Path(out_path)
    if out_path.exists():
        print(f"✓ Landcover exists: {out_path}")
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)

    transform, width, height = _grid(bbox, res)
    lc = np.ones((height, width), dtype=np.uint8)  # default: class 1 (paved)

    # class 3: water
    try:
        x1, y1, x2, y2 = _to_2154(bbox)
        resp = requests.get(WATER_WFS + f"&BBOX={x1},{y1},{x2},{y2},EPSG:2154", timeout=60)
        if resp.ok:
            water = gpd.read_file(BytesIO(resp.content))
            if not water.empty:
                water = water.to_crs(CRS)
                water_r = rasterize(
                    [(g, 3) for g in water.geometry if g is not None],
                    out_shape=(height, width), transform=transform, fill=0, dtype="uint8",
                )
                lc[water_r == 3] = 3
    except Exception as e:
        print(f"  Warning: water layer failed ({e}), skipping")

    # class 4: vegetation where Trees > 0
    with rasterio.open(trees_path) as src:
        lc[src.read(1) > 0] = 4

    # class 2: buildings (highest priority)
    gdf = _download_buildings(bbox, bld_cache)
    if not gdf.empty:
        bld_r = rasterize(
            [(g, 2) for g in gdf.geometry if g is not None],
            out_shape=(height, width), transform=transform, fill=0, dtype="uint8",
        )
        lc[bld_r == 2] = 2

    with rasterio.open(out_path, "w", driver="GTiff", height=height, width=width,
                       count=1, dtype="uint8", crs=CRS, transform=transform,
                       nodata=0, compress="lzw") as dst:
        dst.write(lc, 1)
    print(f"✓ Landcover: {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Prepare SOLWEIG-GPU inputs for Lyon")
    parser.add_argument("--bbox", nargs=4, type=float, metavar=("XMIN", "YMIN", "XMAX", "YMAX"),
                        default=list(DEFAULT_BBOX),
                        help="Bounding box in EPSG:3946 (default: Confluence 2×2 km)")
    parser.add_argument("--resolution", type=float, default=1.0, help="Output resolution in meters")
    parser.add_argument("--veg-tif", default="data/vegestrate_02_2023_elevation.tif")
    parser.add_argument("--lidar-csv", default="lidar_tiles.csv")
    parser.add_argument("--lidar-dir", default="data/lidar_tiles")
    parser.add_argument("--inputs-dir", default="inputs")
    args = parser.parse_args()

    bbox = tuple(args.bbox)
    inputs = Path(args.inputs_dir)
    bld_cache = inputs / "cache_buildings.geojson"

    print(f"bbox={bbox}, resolution={args.resolution}m")
    prepare_trees(bbox, args.veg_tif, inputs / "Trees.tif", args.resolution)
    prepare_dem(bbox, args.lidar_csv, args.lidar_dir, inputs / "DEM.tif", args.resolution)
    prepare_building_dsm(bbox, inputs / "DEM.tif", inputs / "Building_DSM.tif", bld_cache, args.resolution)
    prepare_landcover(bbox, inputs / "Trees.tif", inputs / "Landcover.tif", bld_cache, args.resolution)
    print(f"\nAll inputs ready in {inputs}/")


if __name__ == "__main__":
    main()
