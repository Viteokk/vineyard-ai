"""Inspection targets (row gaps >= 5 m) + waste targets -> targets.geojson (EPSG:32635).

A gap is a stretch of a physical row (global row_id, all its tile segments together) with no canopy, between
two canopies of that row. The target is the gap centre on the row axis. Waste boxes become targets at their
centre. These, and nothing else, are what the walking route has to visit (within 2 m).

Usage:  python -m pipeline.targets --inp out/pre_global.xml [--out out/targets.geojson] [--min-gap 5]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
from pipeline.to_geojson import convert  # noqa: E402

CRS = {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::32635"}}
NEAR_AXIS = 0.45        # a canopy belongs to a row if it lies within this distance of the row axis (m)


def row_gaps(segs, canopies, min_gap: float):
    """segs: LineStrings of one physical row; canopies: its canopy polygons. -> [(point, gap_m)]"""
    longest = max(segs, key=lambda s: s.length)
    a, b = np.array(longest.coords[0]), np.array(longest.coords[-1])
    u = (b - a) / (np.linalg.norm(b - a) + 1e-9)
    proj = lambda xy: float(np.asarray(xy) @ u)  # noqa: E731
    iv = []
    for c in canopies:
        p = [proj(xy) for xy in np.asarray(c.exterior.coords)]
        iv.append((min(p), max(p)))
    if len(iv) < 2:
        return []
    iv.sort()
    merged = [list(iv[0])]
    for s0, s1 in iv[1:]:
        if s0 <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], s1)
        else:
            merged.append([s0, s1])
    out = []
    for (_, e0), (s1, _) in zip(merged, merged[1:]):
        if s1 - e0 >= min_gap:
            mid = (e0 + s1) / 2
            # point on the row axis at that position: the segment whose extent covers it, else the nearest one
            best = None
            for s in segs:
                p0, p1 = sorted((proj(s.coords[0]), proj(s.coords[-1])))
                d = 0 if p0 <= mid <= p1 else min(abs(mid - p0), abs(mid - p1))
                if best is None or d < best[0]:
                    best = (d, s)
            s = best[1]
            sa, sb = np.array(s.coords[0]), np.array(s.coords[-1])
            su = (sb - sa) / (np.linalg.norm(sb - sa) + 1e-9)
            denom = float(su @ u)                        # segments may point either way along the row
            if abs(denom) < 1e-6:
                continue
            t = (mid - proj(sa)) / denom
            out.append((Point(sa + su * t), s1 - e0))
    return out


REACH = 1.6             # a target is inspectable if an inter-row / passage lies within this distance
                        # (the walker must pass within 2 m while staying on inter-rows and passages)


def build(layers, min_gap: float):
    rows = defaultdict(list)
    for f in layers.get("rows", []):
        rows[f["properties"]["row_id"]].append((shape(f["geometry"]), f["properties"]["vineyard_id"]))
    seg_geoms, seg_rid = [], []
    for rid, items in rows.items():
        for g, _ in items:
            seg_geoms.append(g)
            seg_rid.append(rid)
    tree = STRtree(seg_geoms)
    by_row = defaultdict(list)
    for f in layers.get("canopies", []):
        c = shape(f["geometry"])
        if c.geom_type != "Polygon":
            c = max(getattr(c, "geoms", [c]), key=lambda g: g.area)
        cen = c.centroid
        best = None
        for i in tree.query(cen.buffer(NEAR_AXIS)):
            d = seg_geoms[i].distance(cen)
            if d <= NEAR_AXIS and (best is None or d < best[0]):
                best = (d, seg_rid[i])
        if best:
            by_row[best[1]].append(c)
    feats = []
    for rid, items in sorted(rows.items()):
        for p, gap in row_gaps([g for g, _ in items], by_row.get(rid, []), min_gap):
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(p.x, 2), round(p.y, 2)]},
                          "properties": {"type": "gap", "gap_m": round(gap, 1), "vineyard_id": items[0][1],
                                         "row_id": rid}})
    for f in layers.get("waste", []):
        p = shape(f["geometry"]).centroid
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(p.x, 2), round(p.y, 2)]},
                      "properties": {"type": "waste", "vineyard_id": f["properties"].get("vineyard_id", ""),
                                     "row_id": ""}})
    from shapely.ops import unary_union
    walk = unary_union([shape(f["geometry"]) for f in layers.get("interrows", [])])
    pas = C.ROUTE_IN / "passages.geojson"
    if pas.exists():
        walk = unary_union([walk] + [shape(f["geometry"]) for f in json.loads(pas.read_text())["features"]])
    n_t = n_w = 0
    for f in feats:                                       # IDs: T0001 inspection, W001 waste
        f["properties"]["reachable"] = bool(walk.distance(Point(f["geometry"]["coordinates"])) <= REACH)
        if f["properties"]["type"] == "waste":
            n_w += 1
            f["properties"]["id"] = f"W{n_w:03d}"
        else:
            n_t += 1
            f["properties"]["id"] = f"T{n_t:04d}"
        f["properties"]["x"], f["properties"]["y"] = f["geometry"]["coordinates"]
    return feats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inp", default=str(C.OUT / "pre_global.xml"))
    ap.add_argument("--tiles", default=str(C.TILES))
    ap.add_argument("--out", default=str(C.OUT / "targets.geojson"))
    ap.add_argument("--min-gap", type=float, default=5.0)
    a = ap.parse_args()
    layers, _ = convert(Path(a.inp), Path(a.tiles))
    feats = build(layers, a.min_gap)
    Path(a.out).write_text(json.dumps({"type": "FeatureCollection", "crs": CRS, "features": feats}))
    n_w = sum(f["properties"]["type"] == "waste" for f in feats)
    n_u = sum(not f["properties"]["reachable"] for f in feats)
    print(f"{len(feats) - n_w} gap targets + {n_w} waste targets ({n_u} not reachable from inter-rows / passages: "
          f"row ends beyond the last inter-row) -> {a.out}")


if __name__ == "__main__":
    main()
