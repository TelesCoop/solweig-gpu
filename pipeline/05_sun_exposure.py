from datetime import datetime
from pathlib import Path

import numpy as np
import rasterio

from solweig_lyon.run_config import check_met_file, iter_runs, log_step

OUTPUTS = Path("outputs")



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
            compress="zstd",
            zstd_level=15,
            num_threads="all_cpus",
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
    for scen_dir, record in iter_runs(OUTPUTS):
        shadow_path = scen_dir / "Shadow.tif"
        if not shadow_path.exists():
            continue
        check_met_file(record)
        weights = direct_irradiance_by_hour(record["config"]["met_file"]["path"])
        compute_sun_exposure(shadow_path, weights, scen_dir / "SunExposure.tif")
        log_step(scen_dir, "05_sun_exposure")


if __name__ == "__main__":
    main()
