import shutil
from pathlib import Path

from solweig_gpu import thermal_comfort

BASE = "inputs"
OUTPUTS = Path("outputs")

MET_FILES = {
    "2020_current":     "data/01-CURRENT_14jul.txt",}
    #"2060_mid_century": "data/02-MID-CENTURY_14jul.txt",
    #"2090_end_century": "data/03-END-CENTURY_14jul.txt",
#}
DATE_STR = "1985-07-14"  # matches iy=1985, id=195 in all met files

for scenario, met_file in MET_FILES.items():
    print(f"\n=== {scenario} ===")
    thermal_comfort(
        base_path=BASE,
        selected_date_str=DATE_STR,
        building_dsm_filename="Building_DSM.tif",
        dem_filename="DEM.tif",
        trees_filename="Trees.tif",
        landcover_filename="Landcover.tif",
        tile_size=1000,
        overlap=100,
        use_own_met=True,
        own_met_file=met_file,
        save_tmrt=True,
        save_svf=True,
    )
    out_src = Path(BASE) / "output_folder"
    out_dst = OUTPUTS / scenario
    if out_src.exists():
        if out_dst.exists():
            shutil.rmtree(out_dst)
        shutil.move(str(out_src), str(out_dst))
        print(f"  → {out_dst}")
