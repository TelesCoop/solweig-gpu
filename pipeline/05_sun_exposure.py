from datetime import datetime
from pathlib import Path

import numpy as np
import rasterio

OUTPUTS = Path("outputs")

MET_FILES = {
    "2020_current": "data/01-CURRENT_14jul.txt",
}
DEFAULT_MET_FILE = "data/01-CURRENT_14jul.txt"


def direct_irradiance_by_hour(met_file):
    with open(met_file) as f:
        header = f.readline().split()
        it_idx = header.index("it")
        kdir_idx = header.index("kdir")
        weights = {}
        for line in f:
            cols = line.split()
            if not cols:
                continue
            hour = int(cols[it_idx])
            weights[hour] = max(float(cols[kdir_idx]), 0.0)
    return weights


def band_hours(src):
    hours = []
    for b in range(1, src.count + 1):
        time = src.tags(b).get("Time")
        hours.append(datetime.fromisoformat(time).hour if time else None)
    return hours


def compute_sun_exposure(shadow_path, weights, out_path):
    with rasterio.open(shadow_path) as src:
        hours = band_hours(src)
        band_weights = np.array(
            [weights.get(h, 0.0) if h is not None else 0.0 for h in hours],
            dtype="float64",
        )
        total_weight = band_weights.sum()
        if total_weight == 0:
            raise ValueError(f"no positive direct irradiance for {shadow_path}")

        profile = src.profile
        profile.update(
            count=1,
            dtype="float32",
            nodata=-1.0,
            compress="deflate",
            tiled=True,
            blockxsize=512,
            blockysize=512,
            BIGTIFF="IF_SAFER",
        )

        with rasterio.open(out_path, "w", **profile) as dst:
            for _, window in src.block_windows(1):
                stack = src.read(window=window).astype("float64")
                score = np.tensordot(band_weights, stack, axes=(0, 0)) / total_weight
                dst.write(score.astype("float32"), 1, window=window)
    print(out_path)


def main():
    for scen_dir in sorted(p for p in OUTPUTS.iterdir() if p.is_dir()):
        shadow_path = scen_dir / "Shadow.tif"
        if not shadow_path.exists():
            continue
        met_file = MET_FILES.get(scen_dir.name, DEFAULT_MET_FILE)
        weights = direct_irradiance_by_hour(met_file)
        compute_sun_exposure(shadow_path, weights, scen_dir / "SunExposure.tif")


if __name__ == "__main__":
    main()
