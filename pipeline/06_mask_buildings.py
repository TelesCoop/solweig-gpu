from io import BytesIO
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
import requests
from rasterio.features import rasterize
from rasterio.warp import Resampling, reproject

from solweig_lyon.config import CRS
from solweig_lyon.utils.geo import to_2154
from solweig_lyon.utils.landcover import WATER_WFS

INPUTS = Path("inputs")
OUTPUTS = Path("outputs")

PRODUCTS = ["PET.tif", "PET_index.tif"]


def water_mask(bounds, shape, transform):
    """True where a water body stands (Grand Lyon 'plan d'eau')."""
    x1, y1, x2, y2 = to_2154((bounds.left, bounds.bottom, bounds.right, bounds.top))
    try:
        resp = requests.get(
            WATER_WFS + f"&BBOX={x1},{y1},{x2},{y2},EPSG:2154", timeout=60
        )
        resp.raise_for_status()
        water = gpd.read_file(BytesIO(resp.content))
    except Exception as e:
        print(f"  Warning: water layer failed ({e}), masking buildings only")
        return np.zeros(shape, dtype=bool)

    if water.empty:
        return np.zeros(shape, dtype=bool)

    geoms = [g for g in water.to_crs(CRS).geometry if g is not None]
    print(f"  {len(geoms)} water bodies")
    return rasterize(
        geoms,
        out_shape=shape,
        transform=transform,
        fill=0,
        default_value=1,
        dtype="uint8",
    ).astype(bool)


def excluded_mask():
    """True where a building (Building_DSM - DEM > 0) or a water body stands."""
    with rasterio.open(INPUTS / "Building_DSM.tif") as src:
        bld_dsm = src.read(1)
        transform, crs, bounds = src.transform, src.crs, src.bounds
    with rasterio.open(INPUTS / "DEM.tif") as src:
        dem = src.read(1)
    mask = (bld_dsm - dem) > 0
    mask |= water_mask(bounds, mask.shape, transform)
    return mask, transform, crs


def resample_mask(mask, src_transform, src_crs, dst_profile):
    dst_shape = (dst_profile["height"], dst_profile["width"])
    if (
        mask.shape == dst_shape
        and src_transform == dst_profile["transform"]
        and src_crs == dst_profile["crs"]
    ):
        return mask
    out = np.zeros(dst_shape, dtype=np.uint8)
    reproject(
        source=mask.astype(np.uint8),
        destination=out,
        src_transform=src_transform,
        src_crs=src_crs,
        dst_transform=dst_profile["transform"],
        dst_crs=dst_profile["crs"],
        resampling=Resampling.nearest,
    )
    return out.astype(bool)


def mask_product(path, excl_mask, excl_transform, excl_crs):
    with rasterio.open(path) as src:
        profile = src.profile
        data = src.read()
        band_tags = [src.tags(b) for b in range(1, src.count + 1)]

    mask = resample_mask(excl_mask, excl_transform, excl_crs, profile)

    nodata = profile.get("nodata")
    if nodata is None:
        nodata = float("nan")
        profile["nodata"] = nodata
    data[:, mask] = nodata

    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data)
        for b, tags in enumerate(band_tags, start=1):
            dst.update_tags(b, **tags)
    print(path)


def main():
    excl_mask, excl_transform, excl_crs = excluded_mask()
    for scen_dir in sorted(p for p in OUTPUTS.iterdir() if p.is_dir()):
        for name in PRODUCTS:
            path = scen_dir / name
            if path.exists():
                mask_product(path, excl_mask, excl_transform, excl_crs)


if __name__ == "__main__":
    main()
