from io import BytesIO
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import requests
from rasterio.features import rasterize

from .geo import CRS, grid, to_2154

BDTOPO_URL = "https://data.geopf.fr/wfs/ows"


def _download_buildings(bbox, cache_path):
    cache_path = Path(cache_path)
    if cache_path.exists():
        return gpd.read_file(cache_path)
    print("  Downloading BD TOPO buildings (BDTOPO_V3:batiment)...")
    x1, y1, x2, y2 = to_2154(bbox)
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

    gdf = pd.concat(all_gdfs, ignore_index=True)
    gdf = gpd.GeoDataFrame(gdf, geometry="geometry").set_crs("EPSG:2154", allow_override=True)
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
    transform, width, height = grid(bbox, res)

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
