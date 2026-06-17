from pyproj import Transformer
from rasterio.transform import from_origin

CRS = "EPSG:3946"
DEFAULT_BBOX = (1839000, 5171000, 1841000, 5173000)


def grid(bbox, res):
    xmin, ymin, xmax, ymax = bbox
    transform = from_origin(xmin, ymax, res, res)
    width = int((xmax - xmin) / res)
    height = int((ymax - ymin) / res)
    return transform, width, height


def to_2154(bbox):
    t = Transformer.from_crs("EPSG:3946", "EPSG:2154", always_xy=True)
    xmin, ymin, xmax, ymax = bbox
    x1, y1 = t.transform(xmin, ymin)
    x2, y2 = t.transform(xmax, ymax)
    return x1, y1, x2, y2
