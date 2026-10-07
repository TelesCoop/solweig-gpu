"""Run SOLWEIG for the scenario in MET_FILES, then PET and the merge, with a bounded
disk footprint. Resumable: re-run the same command after an interruption.

Fresh run: preprocess + walls/aspect, then SOLWEIG in small batches. Each tile gets
its PET and PET_index right after SOLWEIG, and files that nothing downstream reads
are deleted as soon as it is safe:

  UTCI_*.tif    after each SOLWEIG batch
  TMRT_*.tif    once the tile's PET and PET_index are written and readable
  Shadow_*.tif  once Shadow.tif is merged and checked

PET_*.tif and PET_index_*.tif tiles are kept. --keep-tiles disables the TMRT and
Shadow deletions (UTCI is always deleted) and needs far more disk.

The finished run is moved to outputs/<scenario>_<run_id> (see solweig_lyon.run_config)
and Shadow.tif / PET.tif / PET_index.tif are merged there.

Resume: incomplete tiles (and their SVF cache, whose presence is decided by file
existence in solweig_gpu) are detected and redone. inputs/processed_inputs holds a
.run_id marker; a resume with different inputs or params is refused.

    SOLWEIG_PARALLEL=2 SOLWEIG_GPUS=0,1 nohup uv run python -u pipeline/02_run_solweig.py > run.log 2>&1 &
"""

import argparse
import importlib.util
import multiprocessing as mp
import os
import shutil
import sys
import zipfile
from pathlib import Path

import rasterio
import rasterio.errors
from rasterio.windows import Window
from solweig_gpu import preprocess, run_utci_tiles, run_walls_aspect

from solweig_lyon.config import OVERLAP, TILE_SIZE
from solweig_lyon.run_config import (
    build_config,
    log_step,
    run_dir_name,
    run_id,
    write_run_config,
)

ROOT = Path(__file__).resolve().parents[1]
BASE = "inputs"
OUTPUTS = Path("outputs")

MET_FILES = {
    "2090_end_century": "data/03-END-CENTURY_14jul.txt",
}
DATE_STR = "1985-07-14"  # matches iy=1985, id=195 in all met files

N_WORKERS = int(os.environ.get("SOLWEIG_PARALLEL", "1"))
GPUS = os.environ.get("SOLWEIG_GPUS", "0").split(",")

SAVE_KWARGS = dict(save_tmrt=True, save_svf=True, save_shadow=True)

TILE_PEAK_GIB = 1.6  # UTCI + TMRT + Shadow + SVF cache for one tile, before cleanup
MARGIN_GIB = 5
MERGE_MIN_FREE_GIB = 10
MERGE_ORDER = ["Shadow", "PET", "PET_index"]  # Shadow first: its tiles are deleted


def load_sibling(filename):
    path = Path(__file__).with_name(filename)
    name = "step_" + path.stem
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


pet = load_sibling("03_compute_pet.py")
merge = load_sibling("04_merge_outputs.py")

assert len(MET_FILES) == 1, "enable exactly one scenario in MET_FILES"
((SCENARIO, MET_FILE),) = MET_FILES.items()
PRE = Path(BASE) / "processed_inputs"
WORK = Path(BASE) / "output_folder"
RUN_ID_MARKER = PRE / ".run_id"
ARCHIVE_DIR = Path("outputs_old")


def tile_keys(preprocess_dir):
    bdir = Path(preprocess_dir) / "Building_DSM"
    return sorted(
        p.name[len("Building_DSM_") : -len(".tif")]
        for p in bdir.glob("Building_DSM_*.tif")
    )


def run_chunk(gpu_id, preprocess_dir, tile_keys):
    os.environ["CUDA_VISIBLE_DEVICES"] = gpu_id
    run_utci_tiles(
        base_path=BASE,
        preprocess_dir=preprocess_dir,
        selected_date_str=DATE_STR,
        tile_keys=tile_keys,
        **SAVE_KWARGS,
    )


def solweig(keys):
    """SOLWEIG for `keys`, split over N_WORKERS processes (one GPU each, cycling)."""
    if N_WORKERS <= 1:
        run_utci_tiles(
            base_path=BASE,
            preprocess_dir=str(PRE),
            selected_date_str=DATE_STR,
            tile_keys=keys,
            **SAVE_KWARGS,
        )
        return
    ctx = mp.get_context("spawn")
    procs = []
    for i in range(N_WORKERS):
        chunk = keys[i::N_WORKERS]
        if chunk:
            p = ctx.Process(target=run_chunk, args=(GPUS[i % len(GPUS)], str(PRE), chunk))
            p.start()
            procs.append(p)
    for p in procs:
        p.join()
        if p.exitcode != 0:
            raise RuntimeError(f"tile worker failed (exit {p.exitcode})")


def free_gib():
    return shutil.disk_usage(".").free / 2**30


def tif_ok(path, raw=False):
    """True if the tile opens and its last row of the last band reads.

    raw: uncompressed GDAL output, also checks the file is not shorter than its
    pixel data (what a disk-full truncation looks like).
    """
    try:
        with rasterio.open(path) as s:
            if raw and path.stat().st_size < s.width * s.height * s.count * 4:
                return False
            s.read(s.count, window=Window(0, s.height - 1, s.width, 1))
        return True
    except (rasterio.errors.RasterioIOError, OSError):
        return False


def tmrt_path(key):
    return WORK / key / f"TMRT_{key}.tif"


def shadow_ok(key):
    return tif_ok(WORK / key / f"Shadow_{key}.tif", raw=True)


def tmrt_ok(key):
    return tif_ok(tmrt_path(key), raw=True)


def pet_ok(key):
    d = WORK / key
    return tif_ok(d / f"PET_{key}.tif") and tif_ok(d / f"PET_index_{key}.tif")


def solweig_done(key):
    return shadow_ok(key) and (pet_ok(key) or tmrt_ok(key))


def svf_cache_paths(key):
    svf = PRE / "SVF"
    return [
        svf / f"SkyViewFactor_{key}.tif",
        svf / f"svfs_{key}.zip",
        svf / f"shadowmats_{key}.npz",
    ]


def svf_cache_ok(key):
    tif, zip_, npz = svf_cache_paths(key)
    try:
        return (
            tif_ok(tif)
            and zipfile.ZipFile(zip_).testzip() is None
            and zipfile.ZipFile(npz).testzip() is None
        )
    except (zipfile.BadZipFile, OSError):
        return False


def drop_bad_svf_cache(key):
    paths = svf_cache_paths(key)
    if any(p.exists() for p in paths) and not svf_cache_ok(key):
        print(f"  {key}: removing incomplete SVF cache", flush=True)
        for p in paths:
            p.unlink(missing_ok=True)


def discard_incomplete(keys):
    for key in keys:
        if not solweig_done(key):
            shutil.rmtree(WORK / key, ignore_errors=True)
            drop_bad_svf_cache(key)


def remove_utci(keys=None):
    if keys is None:
        paths = WORK.glob("*/UTCI_*.tif")
    else:
        paths = (WORK / k / f"UTCI_{k}.tif" for k in keys)
    for p in paths:
        p.unlink(missing_ok=True)


def finish_tile(key, met, keep):
    d = WORK / key
    if not pet_ok(key):
        try:
            pet.process_tile(tmrt_path(key), met)
        except BaseException:
            for name in (f"PET_{key}.tif", f"PET_index_{key}.tif"):
                (d / name).unlink(missing_ok=True)
            raise
        if not pet_ok(key):
            raise RuntimeError(f"{key}: PET outputs unreadable after writing")
    if not keep:
        tmrt_path(key).unlink(missing_ok=True)


def compute_missing(keys, met, batch, keep):
    todo = [k for k in keys if not solweig_done(k)]
    for key in todo:
        if (WORK / key).exists():
            shutil.rmtree(WORK / key)
        drop_bad_svf_cache(key)

    for key in (k for k in keys if k not in todo):
        if not pet_ok(key):
            print(f"PET for finished tile {key} (free {free_gib():.0f} GiB)", flush=True)
        finish_tile(key, met, keep)

    print(f"\n{len(todo)} tiles to compute, batch of {batch}", flush=True)
    for i in range(0, len(todo), batch):
        chunk = todo[i : i + batch]
        need = len(chunk) * TILE_PEAK_GIB + MARGIN_GIB
        if free_gib() < need:
            sys.exit(f"only {free_gib():.1f} GiB free, batch needs ~{need:.1f}. Stopping.")
        print(f"[{i + len(chunk)}/{len(todo)}] {chunk} (free {free_gib():.0f} GiB)", flush=True)
        try:
            solweig(chunk)
            remove_utci(chunk)
            for key in chunk:
                if not (tmrt_ok(key) and shadow_ok(key)):
                    raise RuntimeError(f"{key}: SOLWEIG outputs incomplete")
            for key in chunk:
                finish_tile(key, met, keep)
        except BaseException:
            remove_utci(chunk)
            discard_incomplete(chunk)
            raise


def promote_to_outputs(final, config):
    archive = ARCHIVE_DIR / final.name
    if final.exists():
        if archive.exists():
            sys.exit(f"{final} and {archive} both exist, move one away first")
        print(f"archiving previous {final} -> {archive}", flush=True)
        shutil.move(str(final), str(archive))
    shutil.move(str(WORK), str(final))
    write_run_config(final, config)
    print(f"{WORK} -> {final}", flush=True)


def merge_all(final, keep):
    for prefix in MERGE_ORDER:
        tiles = sorted(final.glob(f"*/{prefix}_[0-9]*.tif"))
        if not tiles:
            print(f"{prefix}: no tiles left (already merged?)", flush=True)
            continue
        if free_gib() < MERGE_MIN_FREE_GIB:
            sys.exit(f"only {free_gib():.1f} GiB free, cannot merge {prefix}")
        out = final / f"{prefix}.tif"
        print(f"merging {prefix} from {len(tiles)} tiles (free {free_gib():.0f} GiB)", flush=True)
        try:
            merge.merge_product(final, prefix, merge.PRODUCTS[prefix])
            with rasterio.open(tiles[0]) as t, rasterio.open(out) as m:
                assert m.count == t.count, f"{out}: {m.count} bands, tiles have {t.count}"
            assert tif_ok(out), f"{out} unreadable after merge"
        except BaseException:
            out.unlink(missing_ok=True)
            raise
        if prefix == "Shadow" and not keep:
            for p in tiles:
                p.unlink()
            print(f"deleted {len(tiles)} Shadow tiles, free {free_gib():.0f} GiB", flush=True)


def prepare(config):
    """Preprocess + walls/aspect, or check we are resuming the same run."""
    rid = run_id(config)
    if RUN_ID_MARKER.exists():
        found = RUN_ID_MARKER.read_text().strip()
        if found != rid:
            sys.exit(
                f"{PRE} was prepared for run {found}, but inputs/params now give {rid}. "
                f"Delete {PRE} (and {WORK}) to start over."
            )
        print(f"resuming run {rid}", flush=True)
        return
    if PRE.exists():
        sys.exit(f"{PRE} exists without {RUN_ID_MARKER.name} (incomplete preprocess?). Delete it.")
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
        own_met_file=MET_FILE,
    )
    assert Path(preprocess_dir).resolve() == PRE.resolve(), f"preprocess wrote {preprocess_dir}, expected {PRE}"
    run_walls_aspect(str(PRE))
    RUN_ID_MARKER.write_text(rid + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--batch", type=int, default=4, help="tiles per SOLWEIG call")
    parser.add_argument("--keep-tiles", action="store_true", help="keep TMRT and Shadow tiles")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    args = parser.parse_args()

    os.chdir(ROOT)
    sys.stdout.reconfigure(line_buffering=True)

    config = build_config(
        SCENARIO,
        MET_FILE,
        params=dict(
            date_str=DATE_STR,
            tile_size=TILE_SIZE,
            overlap=OVERLAP,
            save_kwargs=SAVE_KWARGS,
        ),
        inputs_dir=BASE,
    )
    final = OUTPUTS / run_dir_name(config)
    print(f"{SCENARIO} -> {final}", flush=True)

    if args.dry_run and not RUN_ID_MARKER.exists():
        print("fresh run: would preprocess, then compute all tiles")
        return
    if not args.dry_run:
        prepare(config)

    if WORK.exists() or not final.exists():
        keys = tile_keys(PRE)
        n_done = sum(solweig_done(k) for k in keys)
        n_pet = sum(pet_ok(k) for k in keys)
        print(
            f"{len(keys)} tiles, {n_done} SOLWEIG done, {n_pet} with PET, "
            f"{len(keys) - n_done} to compute, {free_gib():.0f} GiB free"
        )
        if args.dry_run:
            return
        met = pet.read_met(MET_FILE)
        pet.check_humidity_bucket(SCENARIO, met)
        remove_utci()
        compute_missing(keys, met, args.batch, args.keep_tiles)
        assert all(pet_ok(k) and shadow_ok(k) for k in keys), "tiles still incomplete"
        promote_to_outputs(final, config)
    else:
        print(f"{WORK} already promoted to {final}, only merging", flush=True)

    if args.dry_run:
        return
    merge_all(final, args.keep_tiles)
    log_step(final, "02_run_solweig")
    print(f"\ndone, free {free_gib():.0f} GiB", flush=True)
    for prefix in MERGE_ORDER:
        p = final / f"{prefix}.tif"
        print(f"  {p} {p.stat().st_size / 2**30:.1f} GiB")


if __name__ == "__main__":
    main()
