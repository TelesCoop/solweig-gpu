from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import reproject, Resampling

from solweig_lyon.config import CRS
from solweig_lyon.utils.geo import grid


def prepare_trees(bbox, veg_tif, out_path, res=1):
    out_path = Path(out_path)
    if out_path.exists():
        print(f"✓ Trees exists: {out_path}")
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    transform, width, height = grid(bbox, res)
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
    with rasterio.open(
        out_path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype="float32",
        crs=CRS,
        transform=transform,
        nodata=0,
        compress="lzw",
    ) as dst:
        dst.write(result, 1)
    print(f"✓ Trees: {out_path}")
