import multiprocessing as mp
import os
import shutil
from pathlib import Path

from solweig_gpu import preprocess, run_utci_tiles, run_walls_aspect

from solweig_lyon.config import OVERLAP, TILE_SIZE

BASE = "inputs"
OUTPUTS = Path("outputs")

MET_FILES = {
    "2020_current": "data/01-CURRENT_14jul.txt",
}
# "2060_mid_century": "data/02-MID-CENTURY_14jul.txt",
# "2090_end_century": "data/03-END-CENTURY_14jul.txt",
# }
DATE_STR = "1985-07-14"  # matches iy=1985, id=195 in all met files

N_WORKERS = int(os.environ.get("SOLWEIG_PARALLEL", "1"))
GPUS = os.environ.get("SOLWEIG_GPUS", "0").split(",")

SAVE_KWARGS = dict(save_tmrt=True, save_svf=True, save_shadow=True)


def run_chunk(gpu_id, preprocess_dir, tile_keys):
    os.environ["CUDA_VISIBLE_DEVICES"] = gpu_id
    run_utci_tiles(
        base_path=BASE,
        preprocess_dir=preprocess_dir,
        selected_date_str=DATE_STR,
        tile_keys=tile_keys,
        **SAVE_KWARGS,
    )


def tile_keys(preprocess_dir):
    bdir = Path(preprocess_dir) / "Building_DSM"
    return sorted(
        p.name[len("Building_DSM_") : -len(".tif")]
        for p in bdir.glob("Building_DSM_*.tif")
    )


def main():
    ctx = mp.get_context("spawn")
    for scenario, met_file in MET_FILES.items():
        print(f"\n=== {scenario} ===")
        preprocess_dir = preprocess(
            base_path=BASE,
            selected_date_str=DATE_STR,
            building_dsm_filename="Building_DSM.tif",
            dem_filename="DEM.tif",
            trees_filename="Trees.tif",
            landcover_filename="Landcover.tif",
            tile_size=TILE_SIZE,
            overlap=OVERLAP,
            use_own_met=True,
            own_met_file=met_file,
        )
        run_walls_aspect(preprocess_dir)

        keys = tile_keys(preprocess_dir)
        if N_WORKERS <= 1:
            run_utci_tiles(
                base_path=BASE,
                preprocess_dir=preprocess_dir,
                selected_date_str=DATE_STR,
                tile_keys=keys,
                **SAVE_KWARGS,
            )
        else:
            chunks = [keys[i::N_WORKERS] for i in range(N_WORKERS)]
            procs = []
            for i, chunk in enumerate(chunks):
                if not chunk:
                    continue
                p = ctx.Process(
                    target=run_chunk,
                    args=(GPUS[i % len(GPUS)], preprocess_dir, chunk),
                )
                p.start()
                procs.append(p)
            for p in procs:
                p.join()
                if p.exitcode != 0:
                    raise RuntimeError(f"tile worker failed (exit {p.exitcode})")

        out_src = Path(BASE) / "output_folder"
        out_dst = OUTPUTS / scenario
        if out_src.exists():
            if out_dst.exists():
                shutil.rmtree(out_dst)
            shutil.move(str(out_src), str(out_dst))
            print(f"  → {out_dst}")


if __name__ == "__main__":
    main()
