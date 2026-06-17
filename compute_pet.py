from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import rasterio

from utils.pet import pet_polynomial, PET_BINS

OUTPUTS = Path("outputs")

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
    with rasterio.open(tmrt_path) as src:
        profile = src.profile
        band_tags = [src.tags(b) for b in range(1, src.count + 1)]
        pet_bands = []
        for b in range(1, src.count + 1):
            ts = datetime.fromisoformat(band_tags[b - 1]["Time"])
            if ts not in met:
                raise KeyError(f"{tmrt_path} band {b}: no met row for {ts}")
            ta, u, rh = met[ts]
            tmrt = src.read(b).astype(np.float64)
            pet = pet_polynomial(tmrt - ta, ta, u, rh)
            pet_bands.append(pet.astype(np.float32))

    pet_stack = np.stack(pet_bands)
    index_stack = np.stack([pet_to_index(p) for p in pet_bands])

    write_stack(
        tmrt_path,
        "PET",
        pet_stack,
        {**profile, "dtype": "float32", "nodata": None},
        band_tags,
    )
    write_stack(
        tmrt_path,
        "PET_index",
        index_stack,
        {**profile, "dtype": "uint8", "nodata": 0},
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
        for tmrt_path in sorted(scen_dir.glob("*/TMRT_*.tif")):
            print(tmrt_path)
            process_tile(tmrt_path, met)


if __name__ == "__main__":
    main()
