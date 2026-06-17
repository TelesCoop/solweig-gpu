from pathlib import Path

import laspy
import numpy as np
import pandas as pd
import rasterio
import requests
from rasterio.merge import merge
from rasterio.transform import from_origin
from scipy.ndimage import distance_transform_edt

from solweig_lyon.config import CRS
from solweig_lyon.utils.geo import grid


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
        (df.x_min < xmax) & (df.x_max > xmin) & (df.y_min < ymax) & (df.y_max > ymin)
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

            dtm = np.full((th, tw), -np.inf, dtype=np.float32)
            ci = np.clip(((x - tx) / res).astype(int), 0, tw - 1)
            ri = np.clip(((ty_max - y) / res).astype(int), 0, th - 1)
            np.maximum.at(dtm, (ri, ci), z)
            dtm[dtm == -np.inf] = np.nan

            with rasterio.open(
                tif_path,
                "w",
                driver="GTiff",
                height=th,
                width=tw,
                count=1,
                dtype="float32",
                crs=CRS,
                transform=tile_transform,
                nodata=np.nan,
                compress="lzw",
            ) as dst:
                dst.write(dtm, 1)

        tile_tifs.append(tif_path)

    if not tile_tifs:
        raise RuntimeError(
            "No DTM tiles produced — check LiDAR download and ground points"
        )

    transform, width, height = grid(bbox, res)
    srcs = [rasterio.open(p) for p in tile_tifs]
    try:
        mosaic, _ = merge(srcs, bounds=(xmin, ymin, xmax, ymax), res=res, nodata=np.nan)
    finally:
        for s in srcs:
            s.close()

    dtm = mosaic[0]
    valid = np.isfinite(dtm)
    if valid.any() and not valid.all():
        idx = distance_transform_edt(
            ~valid, return_distances=False, return_indices=True
        )
        dtm = dtm[tuple(i.astype(int) for i in idx)]

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
        nodata=np.nan,
        compress="lzw",
    ) as dst:
        dst.write(dtm, 1)
    print(f"✓ DEM: {out_path}")
