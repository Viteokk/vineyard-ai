"""Two-class model (0 vineyard, 1 waste; train/make_multi_dataset.py) on tiles, merged with the classical rows.

Per tile: 640 px crops (stride 512), masks -> polygons in tile pixels, duplicates across crops removed per class.
  canopies  --canopy model:     the model's canopies, kept only within +-0.45 m of a classical row axis
            --canopy classical: the classical canopies unchanged (the model only adds waste)
  waste     boxes with score >= --conf-waste, 0.1-10 m per side; vineyard_id is set later by pipeline.blocks
Rows, inter-rows and attributes always come from the classical detector.

Usage:  python -m pipeline.infer_multi --weights runs/vineyard/multi/weights/best.pt \\
            --base out/baseline_all.xml --out out/multi_all.xml [--canopy classical|model] [--conf-waste 0.5]
        python -m pipeline.infer_multi ... --tiles data/examples/images --base out/baseline.xml --out out/multi.xml
        python -m pipeline.eval --pred out/multi.xml
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
from shapely.geometry import LineString, MultiPoint, Point, Polygon, box
from shapely.ops import unary_union, voronoi_diagram
from shapely.strtree import STRtree
from shapely.validation import make_valid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
from pipeline.cvat_io import read_cvat, write_cvat  # noqa: E402
from pipeline.infer_yolo import dedup, offsets  # noqa: E402
from pipeline.tiles import open_tile  # noqa: E402

RES = C.PX
SIZE = 2048
CROP = 640
WASTE_MIN_M, WASTE_MAX_M = 0.1, 10.0
HYBRID_CLOSE_M = 0.2       # hybrid canopies: gap closed between pieces of one vine
HYBRID_KEEP_M2 = 0.3       # hybrid canopies: extra pieces of a cell kept only above this area


def predict(model, img: np.ndarray, conf: float, device: str, imgsz: int = 640) -> dict[int, list]:
    """-> {class: [(Polygon in tile px, score, x0, y0), ...]} before de-duplication."""
    h, w = img.shape[:2]
    xs = list(range(0, max(w - CROP, 0) + 1, 512)) or [0]
    ys = list(range(0, max(h - CROP, 0) + 1, 512)) or [0]
    if xs[-1] != max(w - CROP, 0):
        xs.append(max(w - CROP, 0))
    if ys[-1] != max(h - CROP, 0):
        ys.append(max(h - CROP, 0))
    crops, origins = [], []
    for y0 in ys:
        for x0 in xs:
            crop = img[y0:y0 + CROP, x0:x0 + CROP]
            if (crop.max(2) < 12).mean() > 0.9:           # nodata
                continue
            crops.append(np.ascontiguousarray(crop[..., ::-1]))  # ultralytics expects BGR arrays
            origins.append((x0, y0))
    out: dict[int, list] = {}
    for i in range(0, len(crops), 8):
        res = model.predict(crops[i:i + 8], conf=conf, imgsz=imgsz, device=device, verbose=False, max_det=300,
                            retina_masks=True)
        for r, (x0, y0) in zip(res, origins[i:i + 8]):
            if r.boxes is None or len(r.boxes) == 0:
                continue
            cls = r.boxes.cls.int().tolist()
            scs = r.boxes.conf.tolist()
            xyxy = r.boxes.xyxy.tolist()
            masks = r.masks.xy if r.masks is not None else [None] * len(cls)
            for k, sc, bb, xy in zip(cls, scs, xyxy, masks):
                if k == 1 or xy is None or len(xy) < 3:      # waste: the detector box is the annotation
                    p = box(bb[0] + x0, bb[1] + y0, bb[2] + x0, bb[3] + y0)
                else:
                    p = Polygon(np.asarray(xy) + [x0, y0])
                    p = p if p.is_valid else make_valid(p)
                    if p.geom_type != "Polygon":
                        p = max(getattr(p, "geoms", [p]), key=lambda g: g.area, default=None)
                    if p is None or p.geom_type != "Polygon":
                        continue
                if p.area * RES * RES < 0.005:
                    continue
                out.setdefault(k, []).append((p, float(sc), x0, y0))
    return out


def canopies_on_rows(polys, rows: list[LineString], band_m: float) -> list[Polygon]:
    band = band_m / RES
    rtree = STRtree(rows)
    keep = []
    for p, _ in polys:
        c = p.centroid
        if any(rows[j].distance(c) <= band for j in rtree.query(c.buffer(band))):
            keep.append(p)
    return keep


def hybrid_canopies(classic: list[Polygon], model_polys: list[Polygon], rows: list[LineString], band_m: float,
                    w: int, h: int) -> list[Polygon]:
    """Classical canopy outline, split into individual vines where the model sees them.

    Per row: the classical canopy area inside the row band is cut by the Voronoi cells of the model's canopy
    centres on that row (the model separates neighbouring vines better, the classical band matches the reference
    outline better). Rows without model canopies and classical area outside every band stay as they were."""
    if not classic or not model_polys or not rows:
        return classic
    U = unary_union([p if p.is_valid else make_valid(p) for p in classic])
    band = band_m / RES
    cents = [p.centroid for p in model_polys]
    out, used = [], []
    frame = box(-10, -10, w + 10, h + 10)
    for r in rows:
        zone = r.buffer(band, cap_style=2)
        area = U.intersection(zone)
        if area.is_empty:
            continue
        pts = [c for c in cents if zone.contains(c)]
        used.append(zone)
        if len(pts) < 2:
            out += [g for g in getattr(area, "geoms", [area]) if g.geom_type == "Polygon"]
            continue
        cells = voronoi_diagram(MultiPoint(pts), envelope=frame)
        for cell in cells.geoms:
            piece = area.intersection(cell)
            parts = [g for g in getattr(piece, "geoms", [piece]) if g.geom_type == "Polygon" and not g.is_empty]
            if len(parts) > 1:           # leaves of one vine split by small gaps: close them into one outline
                k = HYBRID_CLOSE_M / RES
                merged = piece.buffer(k, join_style="round").buffer(-k, join_style="round").intersection(cell)
                parts = [g for g in getattr(merged, "geoms", [merged]) if g.geom_type == "Polygon" and not g.is_empty]
            if not parts:
                continue
            parts.sort(key=lambda g: -g.area)
            out.append(parts[0])
            out += [g for g in parts[1:] if g.area * RES * RES >= HYBRID_KEEP_M2]
    rest = U.difference(unary_union(used)) if used else U
    out += [g for g in getattr(rest, "geoms", [rest]) if g.geom_type == "Polygon"]
    return [g for g in out if g.area * RES * RES >= 0.05]


def waste_boxes(polys, conf_waste: float, w: int, h: int) -> list[tuple[list[float], float]]:
    out = []
    for p, sc in polys:
        if sc < conf_waste:
            continue
        x0, y0, x1, y1 = p.bounds
        side = max(x1 - x0, y1 - y0) * RES
        if not (WASTE_MIN_M <= side <= WASTE_MAX_M):
            continue
        out.append(([max(0.0, x0), max(0.0, y0), min(float(w), x1), min(float(h), y1)], sc))
    return out


def tile_objects(model, img, base_objs: list[dict], canopy: str, conf: float, conf_waste: float, device: str,
                 band_m: float = 0.45, conf_review: float = 0.15, grow_m: float = 0.0) -> tuple[list[dict], list[tuple[list[float], float]]]:
    """Merged objects for one tile (waste >= conf_waste) + every waste box >= conf_review with its score (web map)."""
    h, w = img.shape[:2]
    pred = predict(model, img, min(conf, conf_waste, conf_review), device)
    objs = [o for o in base_objs if not (canopy in ("model", "hybrid") and o["label"] == "vineyard")]
    rows = [LineString(o["points"]) for o in base_objs if o["label"] == "row"]
    vid = next((o["attrs"].get("vineyard_id", "") for o in base_objs if o["label"] == "row"), "")
    if canopy == "hybrid" and rows:
        can = [(p, s) for p, s in dedup(pred.get(0, [])) if s >= conf]
        classic = [Polygon(o["points"]) for o in base_objs if o["label"] == "vineyard" and len(o["points"]) >= 3]
        for p in hybrid_canopies(classic, canopies_on_rows(can, rows, band_m), rows, band_m, w, h):
            pts = np.clip(np.asarray(p.simplify(0.7).exterior.coords)[:-1], 0, SIZE)
            if len(pts) >= 3:
                objs.append({"label": "vineyard", "type": "polygon", "points": pts.tolist(), "attrs": {"vineyard_id": vid}})
    if canopy == "model" and rows:
        can = [(p, s) for p, s in dedup(pred.get(0, [])) if s >= conf]
        for p in canopies_on_rows(can, rows, band_m):
            if grow_m > 0:            # the reference outlines are traced loosely around the leaves
                p = p.buffer(grow_m / RES, join_style="round", resolution=4)
            pts = np.clip(np.asarray(p.simplify(1.0).exterior.coords)[:-1], 0, SIZE)
            if len(pts) >= 3:
                objs.append({"label": "vineyard", "type": "polygon", "points": pts.tolist(), "attrs": {"vineyard_id": vid}})
    boxes = waste_boxes(dedup(pred.get(1, []), iou=0.3), min(conf_waste, conf_review), w, h)
    for (x0, y0, x1, y1), sc in boxes:
        if sc < conf_waste:
            continue
        objs.append({"label": "waste", "type": "box", "xtl": x0, "ytl": y0, "xbr": x1, "ybr": y1,
                     "attrs": {"vineyard_id": ""}})
    return objs, boxes


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", default=str(C.ROOT / "runs/vineyard/multi/weights/best.pt"))
    ap.add_argument("--base", default=str(C.OUT / "baseline_all.xml"), help="classical xml (rows / inter-rows / canopies)")
    ap.add_argument("--tiles", default=str(C.TILES))
    ap.add_argument("--out", default=str(C.OUT / "multi_all.xml"))
    ap.add_argument("--canopy", choices=["classical", "model", "hybrid"], default="classical",
                    help="hybrid: classical outline split into vines at the model's canopy centres")
    ap.add_argument("--conf", type=float, default=0.25, help="canopy score threshold")
    ap.add_argument("--conf-waste", type=float, default=0.5, help="waste score threshold (a false box costs a miss)")
    ap.add_argument("--grow", type=float, default=0.0, help="grow model canopies by this many metres")
    ap.add_argument("--conf-review", type=float, default=0.15, help="lower threshold for the boxes listed in --scores")
    ap.add_argument("--scores", default=str(C.OUT / "waste_model.json"), help="all waste boxes with scores (web / review)")
    ap.add_argument("--device", default="mps")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    import json
    from ultralytics import YOLO
    model = YOLO(a.weights)
    base = read_cvat(a.base)
    paths = sorted(Path(a.tiles).glob("siret3_r*_c*.tif"))[: a.limit or None]
    out, scored, t0 = {}, [], time.time()
    for k, pth in enumerate(paths):
        t = open_tile(pth)
        objs, boxes = tile_objects(model, t.read(), base.get(pth.name, []), a.canopy, a.conf, a.conf_waste, a.device,
                                    conf_review=a.conf_review, grow_m=a.grow)
        out[pth.name] = objs
        for (x0, y0, x1, y1), sc in boxes:
            (ux0, uy1), (ux1, uy0) = t.px_to_utm([[x0, y0], [x1, y1]])
            scored.append({"tile": pth.name, "px": [round(x0), round(y0), round(x1), round(y1)], "score": round(sc, 3),
                           "box": [round(ux0, 2), round(uy0, 2), round(ux1, 2), round(uy1, 2)]})
        if k % 20 == 0:
            print(f"  {k + 1}/{len(paths)} tiles, {len(scored)} waste boxes  ({time.time() - t0:.0f}s)", flush=True)
    write_cvat(out, a.out)
    Path(a.scores).write_text(json.dumps(sorted(scored, key=lambda s: -s["score"]), indent=0))
    n_can = sum(o["label"] == "vineyard" for v in out.values() for o in v)
    n_w = sum(o["label"] == "waste" for v in out.values() for o in v)
    print(f"{len(paths)} tiles in {time.time() - t0:.0f}s: {n_can} canopies ({a.canopy}), {n_w} waste boxes in the xml "
          f"(score >= {a.conf_waste}), {len(scored)} listed (>= {a.conf_review}) -> {a.out}, {a.scores}")


if __name__ == "__main__":
    main()
