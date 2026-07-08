import argparse
from pathlib import Path

from solweig_lyon.config import DEFAULT_BBOX
from solweig_lyon.utils.buildings import prepare_building_dsm
from solweig_lyon.utils.dem import prepare_dem
from solweig_lyon.utils.landcover import prepare_landcover
from solweig_lyon.utils.trees import prepare_trees


def main():
    parser = argparse.ArgumentParser(description="Prepare SOLWEIG-GPU inputs for Lyon")
    parser.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        metavar=("XMIN", "YMIN", "XMAX", "YMAX"),
        default=list(DEFAULT_BBOX),
        help="Bounding box in EPSG:3946 (default: Confluence 5x5km)",
    )
    parser.add_argument(
        "--resolution", type=float, default=1.0, help="Output resolution in meters"
    )
    parser.add_argument("--veg-tif", default="data/vegestrate_02_2023_elevation.tif")
    parser.add_argument("--lidar-csv", default="lidar_tiles.csv")
    parser.add_argument("--lidar-dir", default="data/lidar_tiles")
    parser.add_argument("--inputs-dir", default="inputs")
    args = parser.parse_args()

    bbox = tuple(args.bbox)
    inputs = Path(args.inputs_dir)
    bld_cache = inputs / "cache_buildings.geojson"

    print(f"bbox={bbox}, resolution={args.resolution}m")
    prepare_trees(bbox, args.veg_tif, inputs / "Trees.tif", args.resolution)
    prepare_dem(
        bbox, args.lidar_csv, args.lidar_dir, inputs / "DEM.tif", args.resolution
    )
    prepare_building_dsm(
        bbox,
        inputs / "DEM.tif",
        inputs / "Building_DSM.tif",
        bld_cache,
        args.resolution,
    )
    prepare_landcover(
        bbox, inputs / "Trees.tif", inputs / "Landcover.tif", bld_cache, args.resolution
    )
    print(f"\nAll inputs ready in {inputs}/")


if __name__ == "__main__":
    main()
