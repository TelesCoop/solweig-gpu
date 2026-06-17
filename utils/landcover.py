from io import BytesIO
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
import requests
from pyproj import Transformer
from rasterio.features import rasterize
from rasterio.transform import from_bounds
from rasterio.warp import Resampling, reproject

from .buildings import _download_buildings
from .geo import CRS, grid, to_2154

WATER_WFS = (
    "https://data.grandlyon.com/geoserver/metropole-de-lyon/ows"
    "?SERVICE=WFS&VERSION=2.0.0&request=GetFeature"
    "&typename=metropole-de-lyon:fpc_fond_plan_communaut.fpcplandeau"
    "&outputFormat=GML3&SRSNAME=EPSG:2154"
)

COSIA_WMS = "https://data.geopf.fr/wms-r/wms"
COSIA_LAYER = "IGNF_COSIA_2021-2023"

# COSIA legend RGB → SOLWEIG class (1=paved,2=building,3=conifer,4=deciduous,5=grass,6=bare,7=water)
_COSIA_PALETTE = np.array(
    [
        [206, 112, 121],  # Bâtiment → 2
        [152, 119, 82],  # Route/minéral → 1
        [166, 170, 183],  # Zone imperméable → 1
        [98, 208, 255],  # Piscine → 7
        [187, 176, 150],  # Zone perméable → 1
        [51, 117, 161],  # Surface eau → 7
        [233, 239, 254],  # Neige → 1
        [18, 100, 33],  # Conifère → 3
        [76, 145, 41],  # Feuillu → 4
        [181, 195, 53],  # Broussaille → 5
        [176, 130, 144],  # Vigne → 5
        [140, 215, 106],  # Pelouse → 5
        [222, 207, 85],  # Culture → 5
        [208, 163, 73],  # Terre labourée → 6
        [185, 226, 212],  # Serre → 2
        [223, 139, 82],  # Sol nu → 6
        [34, 34, 34],  # Autre → 1
    ],
    dtype=np.float32,
)

_COSIA_CLASSES = np.array(
    [2, 1, 1, 7, 1, 7, 1, 5, 5, 5, 5, 5, 5, 6, 2, 6, 1], dtype=np.uint8
)


def _fetch_cosia(bbox, transform, width, height):
    t = Transformer.from_crs(CRS, "EPSG:3857", always_xy=True)
    xmin, ymin, xmax, ymax = bbox
    x1, y1 = t.transform(xmin, ymin)
    x2, y2 = t.transform(xmax, ymax)

    url = (
        f"{COSIA_WMS}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap"
        f"&LAYERS={COSIA_LAYER}&BBOX={x1},{y1},{x2},{y2}"
        f"&CRS=EPSG:3857&WIDTH={width}&HEIGHT={height}&FORMAT=image/geotiff&STYLES="
    )
    resp = requests.get(url, timeout=90)
    if not resp.ok:
        return None

    with rasterio.open(BytesIO(resp.content)) as src:
        rgb = src.read()[:3].reshape(3, -1).T.astype(np.float32)

    min_dist = np.full(len(rgb), np.inf, dtype=np.float32)
    nearest = np.zeros(len(rgb), dtype=np.uint8)
    for i, color in enumerate(_COSIA_PALETTE):
        dist = ((rgb - color) ** 2).sum(axis=1)
        mask = dist < min_dist
        min_dist[mask] = dist[mask]
        nearest[mask] = i

    lc_3857 = _COSIA_CLASSES[nearest].reshape(height, width)
    lc_3857[lc_3857 == 0] = 1

    src_transform = from_bounds(x1, y1, x2, y2, width, height)
    lc_out = np.ones((height, width), dtype=np.uint8)
    reproject(
        lc_3857,
        lc_out,
        src_transform=src_transform,
        src_crs="EPSG:3857",
        dst_transform=transform,
        dst_crs=CRS,
        resampling=Resampling.nearest,
    )
    lc_out[lc_out == 0] = 1
    return lc_out


def prepare_landcover(bbox, trees_path, out_path, bld_cache, res=1):
    out_path = Path(out_path)
    if out_path.exists():
        print(f"✓ Landcover exists: {out_path}")
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)

    transform, width, height = grid(bbox, res)

    lc = _fetch_cosia(bbox, transform, width, height)
    if lc is None:
        print("  Warning: COSIA failed, using paved default")
        lc = np.ones((height, width), dtype=np.uint8)
    else:
        print("  COSIA base loaded")

    try:
        x1, y1, x2, y2 = to_2154(bbox)
        resp = requests.get(
            WATER_WFS + f"&BBOX={x1},{y1},{x2},{y2},EPSG:2154", timeout=60
        )
        if resp.ok:
            water = gpd.read_file(BytesIO(resp.content))
            if not water.empty:
                water = water.to_crs(CRS)
                water_r = rasterize(
                    [(g, 7) for g in water.geometry if g is not None],
                    out_shape=(height, width),
                    transform=transform,
                    fill=0,
                    dtype="uint8",
                )
                lc[water_r == 7] = 7
    except Exception as e:
        print(f"  Warning: water layer failed ({e}), skipping")

    with rasterio.open(trees_path) as src:
        lc[src.read(1) > 0] = 5

    gdf = _download_buildings(bbox, bld_cache)
    if not gdf.empty:
        bld_r = rasterize(
            [(g, 2) for g in gdf.geometry if g is not None],
            out_shape=(height, width),
            transform=transform,
            fill=0,
            dtype="uint8",
        )
        lc[bld_r == 2] = 2

    with rasterio.open(
        out_path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype="uint8",
        crs=CRS,
        transform=transform,
        nodata=0,
        compress="lzw",
    ) as dst:
        dst.write(lc, 1)
    print(f"✓ Landcover: {out_path}")
