"""Add the YOLO11 waste detections to a CVAT xml as `waste` boxes, with a fixed automatic rule (no manual choice):
score >= 0.4, at most 2.5 m per side (sheet-metal roofs, the main false positive, are 4-6 m) and centre outside the
organiser forbidden zones (buildings +1 m, compounds). On Sireț3: 11 of 105 detections; about 9 look like real waste
(sacks, plastic, rubble) and are checked in Marcaj like every other pre-annotation.
Usage: python scripts/add_waste.py IN.xml OUT.xml [--thr 0.4] [--max-side 2.5]"""
import argparse
import json
import sys
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
from pipeline.cvat_io import read_cvat, write_cvat  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("inp"); ap.add_argument("out")
ap.add_argument("--thr", type=float, default=0.4)
ap.add_argument("--max-side", type=float, default=2.5)
a = ap.parse_args()
fz = unary_union([shape(f["geometry"]) for f in json.loads((C.ROUTE_IN / "forbidden.geojson").read_text())["features"]])
d = read_cvat(a.inp)
n = 0
for w in json.loads((C.OUT / "waste_model11.json").read_text()):
    x0, y0, x1, y1 = w["box"]
    if w["score"] < a.thr or max(x1 - x0, y1 - y0) > a.max_side or fz.contains(Point((x0 + x1) / 2, (y0 + y1) / 2)):
        continue
    px0, py0, px1, py1 = w["px"]
    d.setdefault(w["tile"], []).append({"label": "waste", "type": "box", "xtl": float(px0), "ytl": float(py0),
                                        "xbr": float(px1), "ybr": float(py1), "attrs": {"vineyard_id": ""}})
    n += 1
write_cvat(d, a.out)
print(f"waste: {n} boxes added (score >= {a.thr}, side <= {a.max_side} m, outside forbidden zones) -> {a.out}")
