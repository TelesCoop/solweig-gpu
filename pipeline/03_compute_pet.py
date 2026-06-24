import time
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import rasterio

from solweig_lyon.pet import (
    pet_polynomial,
    wind_speed_from_svf,
    PET_BINS,
    _HUSS_VALUES,
    specific_humidity,
)

OUTPUTS = Path("outputs")

TIMINGS = defaultdict(float)


@contextmanager
def timed(step):
    t0 = time.perf_counter()
    yield
    TIMINGS[step] += time.perf_counter() - t0


MET_FILES = {
    "2020_current": "data/01-CURRENT_14jul.txt",
    # "2060_mid_century": "data/02-MID-CENTURY_14jul.txt",
    # "2090_end_century": "data/03-END-CENTURY_14jul.txt",
}


def read_met(met_file):
    met = {}
    with open(met_file) as f:
        header = f.readline().split()
        ci = {name: i for i, name in enumerate(header)}
        for line in f:
            v = line.split()
            if not v:
                continue
            ts = datetime(int(float(v[ci["iy"]])), 1, 1) + timedelta(
                days=int(float(v[ci["id"]])) - 1, hours=int(float(v[ci["it"]]))
            )
            met[ts] = (
                float(v[ci["Tair"]]),
                float(v[ci["U"]]),
                float(v[ci["RH"]]),
            )
    return met


def pet_to_index(pet):
    index = (np.digitize(pet, PET_BINS) + 1).astype(np.uint8)
    index[~np.isfinite(pet)] = 0
    return index


def process_tile(tmrt_path, met):
    svf_path = tmrt_path.with_name(tmrt_path.name.replace("TMRT", "SVF"))
    with timed("read_svf"), rasterio.open(svf_path) as svf_src:
        svf = svf_src.read(1).astype(np.float64)

    with rasterio.open(tmrt_path) as src:
        profile = src.profile
        band_tags = [src.tags(b) for b in range(1, src.count + 1)]
        pet_bands = []
        for b in range(1, src.count + 1):
            ts = datetime.fromisoformat(band_tags[b - 1]["Time"])
            if ts not in met:
                raise KeyError(f"{tmrt_path} band {b}: no met row for {ts}")
            ta, u, rh = met[ts]
            with timed("wind_speed_from_svf"):
                va = wind_speed_from_svf(u, svf)
            with timed("read_tmrt"):
                tmrt = src.read(b).astype(np.float64)
            with timed("pet_polynomial"):
                pet = pet_polynomial(tmrt - ta, ta, va, rh)
            pet_bands.append(pet.astype(np.float16))

    with timed("stack"):
        pet_stack = np.stack(pet_bands)
        index_stack = np.stack([pet_to_index(p) for p in pet_bands])

    with timed("write_pet"):
        write_stack(
            tmrt_path,
            "PET",
            pet_stack,
            {**profile, "dtype": "float16", "nodata": None, "compress": "deflate"},
            band_tags,
        )
    with timed("write_pet_index"):
        write_stack(
            tmrt_path,
            "PET_index",
            index_stack,
            {**profile, "dtype": "uint8", "nodata": 0, "compress": "deflate"},
            band_tags,
        )


def write_stack(tmrt_path, prefix, stack, profile, band_tags):
    out_path = tmrt_path.with_name(tmrt_path.name.replace("TMRT", prefix))
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(stack)
        for b, tags in enumerate(band_tags, start=1):
            dst.update_tags(b, **tags)


def main():
    for scenario, met_file in MET_FILES.items():
        scen_dir = OUTPUTS / scenario
        if not scen_dir.exists():
            continue
        met = read_met(met_file)
        buckets = {
            int(np.argmin(np.abs(_HUSS_VALUES - specific_humidity(ta, rh))))
            for ta, _, rh in met.values()
        }
        assert (
            len(buckets) == 1
        ), f"{scenario}: humidity bucket changes across timesteps: {buckets}"
        ta0, _, rh0 = next(iter(met.values()))
        huss = specific_humidity(ta0, rh0)
        nearest = _HUSS_VALUES[next(iter(buckets))]
        assert (
            abs(huss - nearest) < 0.001
        ), f"{scenario}: huss {huss:.4f} too far from nearest RHSD {nearest:.4f}"
        for tmrt_path in sorted(scen_dir.glob("*/TMRT_*.tif")):
            print(tmrt_path)
            process_tile(tmrt_path, met)

    total = sum(TIMINGS.values())
    print("\n--- timings (s) ---")
    for step, secs in sorted(TIMINGS.items(), key=lambda kv: -kv[1]):
        print(f"{step:20s} {secs:8.2f}  {secs / total:5.1%}")
    print(f"{'total':20s} {total:8.2f}")


if __name__ == "__main__":
    main()
