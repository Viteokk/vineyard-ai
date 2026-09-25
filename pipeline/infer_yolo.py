"""YOLO-seg canopy inference on tiles, merged with the classical rows / inter-rows -> CVAT xml.

Each 2048 px tile is cut into 640 px crops (stride 512, like train/make_dataset.py); masks are converted to
polygons in tile pixels; duplicates across overlapping crops are removed (IoU >= 0.5, keep the higher score).
Canopies are kept only inside the +-0.45 m band of a classical row (same rule as the baseline: no vines outside
rows), and only on tiles where the baseline found a vineyard. Rows, inter-rows and attributes stay classical.

Usage:  python -m pipeline.infer_yolo --weights runs/vineyard/canopy/weights/best.pt \\
            --base out/baseline.xml --tiles data/examples/images --out out/yolo.xml [--device cpu|mps]
        python -m pipeline.eval --pred out/yolo.xml
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
from shapely.geometry import LineString, Polygon
from shapely.strtree import STRtree
from shapely.validation import make_valid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
from pipeline.cvat_io import read_cvat, write_cvat  # noqa: E402
from pipeline.tiles import open_tile  # noqa: E402

CROP, STRIDE, SIZE = 640, 512, 2048
RES = C.PX


def offsets():
    xs = list(range(0, SIZE - CROP + 1, STRIDE))
    if xs[-1] != SIZE - CROP:
        xs.append(SIZE - CROP)
    return [(x, y) for y in xs for x in xs]


def tile_polys(model, img, conf, device, imgsz):
    polys = []
    crops, origins = [], []
    for x0, y0 in offsets():
        crop = img[y0:y0 + CROP, x0:x0 + CROP]
        if (crop.max(2) < 12).mean() > 0.9:
            continue
        crops.append(np.ascontiguousarray(crop[..., ::-1]))      # ultralytics expects BGR arrays
        origins.append((x0, y0))
    for i in range(0, len(crops), 8):
        res = model.predict(crops[i:i + 8], conf=conf, imgsz=imgsz, device=device, verbose=False, max_det=300,
                            retina_masks=True)
        for r, (x0, y0) in zip(res, origins[i:i + 8]):
            if r.masks is None:
                continue
            for xy, sc in zip(r.masks.xy, r.boxes.conf.tolist()):
                if len(xy) < 3:
                    continue
                p = Polygon(np.asarray(xy) + [x0, y0])
                p = p if p.is_valid else make_valid(p)
                if p.geom_type != "Polygon":
                    p = max(getattr(p, "geoms", [p]), key=lambda g: g.area, default=None)
                if p is None or p.geom_type != "Polygon" or p.area * RES * RES < 0.05:
                    continue
                # crop-border pieces are cut plants: keep them only when no inner crop covers that spot
                polys.append((p, float(sc), x0, y0))
    return polys


def dedup(polys, iou=0.5):
    polys.sort(key=lambda t: -t[1])
    kept, geoms = [], []
    tree = None
    for p, sc, x0, y0 in polys:
        dup = False
        if geoms:
            tree = STRtree(geoms)
            for j in tree.query(p):
                q = geoms[j]
                inter = p.intersection(q).area
                if inter / (p.area + q.area - inter + 1e-9) >= iou or inter / min(p.area, q.area) >= 0.8:
                    dup = True
                    break
        if not dup:
            kept.append((p, sc))
            geoms.append(p)
    return kept


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", default=str(C.ROOT / "runs/vineyard/canopy/weights/best.pt"))
    ap.add_argument("--base", default=str(C.OUT / "baseline_all.xml"), help="classical xml (rows / inter-rows)")
    ap.add_argument("--tiles", default=str(C.TILES))
    ap.add_argument("--out", default=str(C.OUT / "yolo_all.xml"))
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--band", type=float, default=0.45, help="max distance (m) from a row axis")
    ap.add_argument("--grow", type=float, default=0.0, help="grow each polygon by this many metres (the reference "
                                                             "outlines are traced loosely, ~10 cm outside the leaves)")
    ap.add_argument("--device", default="mps")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    from ultralytics import YOLO
    model = YOLO(a.weights)
    base = read_cvat(a.base)
    paths = sorted(Path(a.tiles).glob("siret3_r*_c*.tif"))
    if a.limit:
        paths = paths[:a.limit]
    out, t0, n_can = {}, time.time(), 0
    for k, pth in enumerate(paths):
        objs = [o for o in base.get(pth.name, []) if o["label"] != "vineyard"]
        rows = [LineString(o["points"]) for o in objs if o["label"] == "row"]
        if not rows:                                   # baseline saw no vineyard here: keep it empty
            out[pth.name] = objs
            continue
        img = open_tile(pth).read()
        polys = dedup(tile_polys(model, img, a.conf, a.device, a.imgsz))
        band = a.band / RES
        rtree = STRtree(rows)
        vid = next((o["attrs"].get("vineyard_id", "") for o in objs if o["label"] == "row"), "")
        for p, sc in polys:
            c = p.centroid
            near = [j for j in rtree.query(c.buffer(band)) if rows[j].distance(c) <= band]
            if not near:
                continue
            if a.grow > 0:
                p = p.buffer(a.grow / RES, join_style="round", resolution=4)
            pts = np.asarray(p.simplify(1.0).exterior.coords)[:-1]
            pts = np.clip(pts, 0, SIZE)
            if len(pts) < 3:
                continue
            objs.append({"label": "vineyard", "type": "polygon", "points": pts.tolist(), "attrs": {"vineyard_id": vid}})
            n_can += 1
        out[pth.name] = objs
        if k % 20 == 0:
            print(f"  {k + 1}/{len(paths)} tiles, {n_can} canopies  ({time.time() - t0:.0f}s)", flush=True)
    write_cvat(out, a.out)
    print(f"{len(paths)} tiles, {n_can} YOLO canopies in {time.time() - t0:.0f}s -> {a.out}")


if __name__ == "__main__":
    main()
