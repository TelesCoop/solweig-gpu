from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject

INPUTS = Path("inputs")
OUTPUTS = Path("outputs")

PRODUCTS = ["PET.tif", "PET_index.tif"]


def building_mask():
    """True where a building stands (Building_DSM - DEM > 0)."""
    with rasterio.open(INPUTS / "Building_DSM.tif") as src:
        bld_dsm = src.read(1)
        transform, crs = src.transform, src.crs
    with rasterio.open(INPUTS / "DEM.tif") as src:
        dem = src.read(1)
    return (bld_dsm - dem) > 0, transform, crs


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


def mask_product(path, bld_mask, bld_transform, bld_crs):
    with rasterio.open(path) as src:
        profile = src.profile
        data = src.read()
        band_tags = [src.tags(b) for b in range(1, src.count + 1)]

    mask = resample_mask(bld_mask, bld_transform, bld_crs, profile)

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
    bld_mask, bld_transform, bld_crs = building_mask()
    for scen_dir in sorted(p for p in OUTPUTS.iterdir() if p.is_dir()):
        for name in PRODUCTS:
            path = scen_dir / name
            if path.exists():
                mask_product(path, bld_mask, bld_transform, bld_crs)


if __name__ == "__main__":
    main()
