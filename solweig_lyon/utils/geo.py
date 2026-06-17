from pyproj import Transformer
from rasterio.transform import from_origin


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
