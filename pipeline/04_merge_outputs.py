from pathlib import Path

import rasterio
from rasterio.transform import from_origin
from rasterio.windows import Window

from solweig_lyon.config import OVERLAP

OUTPUTS = Path("outputs")

PRODUCTS = {
    "PET": "float16",
    "PET_index": "uint8",
    "Shadow": "float16",
}


def merge_product(scen_dir, prefix, dtype):
    tiles = sorted(scen_dir.glob(f"*/{prefix}_[0-9]*.tif"))
    if not tiles:
        return

    bounds = {}
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")
    for p in tiles:
        with rasterio.open(p) as s:
            b = s.bounds
        bounds[p] = b
        min_x, min_y = min(min_x, b.left), min(min_y, b.bottom)
        max_x, max_y = max(max_x, b.right), max(max_y, b.top)

    with rasterio.open(tiles[0]) as s:
        profile = s.profile
        nodata = s.nodata
        res = s.res[0]
        band_tags = [s.tags(b) for b in range(1, s.count + 1)]

    width = round((max_x - min_x) / res)
    height = round((max_y - min_y) / res)
    profile.update(
        dtype=dtype,
        nodata=nodata,
        width=width,
        height=height,
        transform=from_origin(min_x, max_y, res, res),
        compress="deflate",
        tiled=True,
        blockxsize=512,
        blockysize=512,
        BIGTIFF="IF_SAFER",
    )

    trim = OVERLAP // 2
    out_path = scen_dir / f"{prefix}.tif"
    with rasterio.open(out_path, "w", **profile) as dst:
        for p, b in bounds.items():
            left = 0 if b.left == min_x else trim
            right = 0 if b.right == max_x else trim
            top = 0 if b.top == max_y else trim
            bottom = 0 if b.bottom == min_y else trim
            with rasterio.open(p) as s:
                read_win = Window(
                    left, top, s.width - left - right, s.height - top - bottom
                )
                data = s.read(window=read_win).astype(dtype)
            col_off = round((b.left - min_x) / res) + left
            row_off = round((max_y - b.top) / res) + top
            dst.write(
                data, window=Window(col_off, row_off, data.shape[2], data.shape[1])
            )
        for b, tags in enumerate(band_tags, start=1):
            dst.update_tags(b, **tags)
    print(out_path)


def main():
    for scen_dir in sorted(p for p in OUTPUTS.iterdir() if p.is_dir()):
        for prefix, dtype in PRODUCTS.items():
            merge_product(scen_dir, prefix, dtype)


if __name__ == "__main__":
    main()
