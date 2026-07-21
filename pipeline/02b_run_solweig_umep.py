import json
import os
import time
from pathlib import Path

import solweig

BASE = Path("inputs")
OUTPUTS = Path("outputs") / "umep"

DSM = BASE / "Building_DSM.tif"
DEM = BASE / "DEM.tif"
TREES = BASE / "Trees.tif"
LANDCOVER = BASE / "Landcover.tif"

MET_FILES = {
#    "2020_current": "data/01-CURRENT_14jul.txt",
# "2060_mid_century": "data/02-MID-CENTURY_14jul.txt",
 "2090_end_century": "data/03-END-CENTURY_14jul.txt",
}
DATE_STR = "1985-07-14"  # iy=1985, id=195 dans les fichiers météo

UTC_OFFSET = float(os.environ.get("SOLWEIG_UTC_OFFSET", "0"))

USE_GPU = os.environ.get("SOLWEIG_NO_GPU", "") not in ("1", "true", "True")


def setup_backend():
    if USE_GPU:
        solweig.enable_gpu()
        if not solweig.is_gpu_available():
            print(
                "  ! GPU demandé (défaut) mais indisponible : "
                "exécution sur CPU — la comparaison de perf sera faussée."
            )
    else:
        solweig.disable_gpu()
    backend = solweig.get_compute_backend()
    print(f"solweig {solweig.__version__} | backend={backend}")
    return backend


def save_svf(surface, dsm_path, out_path):
    svf = getattr(surface, "svf", None)
    if svf is None or getattr(svf, "svf", None) is None:
        print("  ! pas de tableau SVF sur la surface préparée ; SVF ignoré")
        return
    _, transform, crs, _ = solweig.io.load_raster(str(dsm_path))
    solweig.io.save_raster(str(out_path), svf.svf, transform, crs)
    print(f"  → {out_path}")


def main():
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    print(f"solweig {solweig.__version__} | backend={solweig.get_compute_backend()}")

    timings = {}
    for scenario, met_file in MET_FILES.items():
        print(f"\n=== {scenario} ===")
        out_dir = OUTPUTS / scenario
        cache_dir = out_dir / "cache"  # walls + SVF mis en cache par prepare()
        out_dir.mkdir(parents=True, exist_ok=True)

        weather = solweig.Weather.from_umep_met(
            met_file,
            start=f"{DATE_STR}T00:00:00",
            end=f"{DATE_STR}T23:00:00",
        )
        location = solweig.Location.from_dsm_crs(str(DSM), utc_offset=int(UTC_OFFSET))
        print(
            f"  {len(weather)} pas horaires | "
            f"{location.latitude:.4f}N {location.longitude:.4f}E UTC{UTC_OFFSET:+g}"
        )

        t0 = time.perf_counter()
        surface = solweig.SurfaceData.prepare(
            dsm=str(DSM),
            dem=str(DEM),
            cdsm=str(TREES),
            land_cover=str(LANDCOVER),
            working_dir=str(cache_dir),
        )
        t_prepare = time.perf_counter() - t0

        t0 = time.perf_counter()
        summary = solweig.calculate_timeseries(
            surface=surface,
            weather_series=weather,
            location=location,
            output_dir=str(out_dir),
            outputs=["tmrt", "shadow"],
        )
        t_calc = time.perf_counter() - t0

        save_svf(surface, DSM, out_dir / "SVF.tif")

        tmrt_mean = getattr(summary, "tmrt_mean", None)
        timings[scenario] = {
            "backend": solweig.get_compute_backend(),
            "timesteps": len(weather),
            "prepare_svf_s": round(t_prepare, 1),
            "calculate_s": round(t_calc, 1),
            "total_s": round(t_prepare + t_calc, 1),
            "tmrt_mean_c": (
                round(float(tmrt_mean.mean()), 2) if tmrt_mean is not None else None
            ),
        }
        print(
            f"  prepare(SVF)={t_prepare:.1f}s  "
            f"calculate={t_calc:.1f}s  total={t_prepare + t_calc:.1f}s"
        )
        print(f"  → {out_dir}")

    report = OUTPUTS / "timing_umep.json"
    report.write_text(json.dumps(timings, indent=2))
    print(f"\nChronométrage écrit dans {report}")


if __name__ == "__main__":
    main()
