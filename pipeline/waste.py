"""Waste candidate scanner (classical). Ranks bright / vivid / dark compact blobs near the vineyard; does NOT
write boxes unless --write is given.

Calibrated on all 311 tiles (out/preview/waste_candidates.jpg, waste_strict.jpg): even the strictest survivors
were cars, roof parts, tanks, stones and tree blossom, never clearly waste. A false box costs as much as a
miss ("when in doubt, leave it out"), so the pre-annotations carry no automatic waste; the ranked candidate
list is a checklist for the human pass in Marcaj, where real waste is drawn by hand.

Usage:  python -m pipeline.waste --inp out/pre_global.xml                 # scan -> out/waste_candidates.json
        python -m pipeline.waste --inp out/pre_global.xml --write --score-min 3   # only if you really want boxes
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from multiprocessing import Pool
from pathlib import Path

import cv2
import numpy as np
from shapely.geometry import LineString, Point, box, shape
from shapely.ops import unary_union
from shapely.prepared import prep

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
from pipeline.cvat_io import read_cvat, write_cvat  # noqa: E402
from pipeline.tiles import open_tile  # noqa: E402

RES = C.PX


@dataclass
class W:
    v_white: int = 205
    s_white: int = 45
    s_vivid: int = 150
    v_vivid: int = 90
    v_dark: int = 45
    s_dark: int = 60
    min_m2: float = 0.05
    max_m2: float = 4.0
    solidity: float = 0.6
    aspect: float = 3.5
    contrast: int = 55         # blob mean V minus ring mean V (white); ring minus blob for dark
    axis_dist: float = 0.5     # metres from any row axis
    score_min: float = 1.0     # final acceptance score (see score())
    block_dist: float = 15.0   # candidates only within this distance of a vineyard block (m)
    building_dist: float = 15.0  # ... and further than this from forbidden zones / buildings


_G = {}


def _init(inp, params):
    ann = read_cvat(inp)
    _G["rows"] = {n: [LineString(o["points"]) for o in objs if o["label"] == "row"] for n, objs in ann.items()}
    _G["forbidden"] = unary_union([shape(f["geometry"]) for f in json.loads((C.ROUTE_IN / "forbidden.geojson").read_text())["features"]])
    _G["passages"] = unary_union([shape(f["geometry"]) for f in json.loads((C.ROUTE_IN / "passages.geojson").read_text())["features"]])
    _G["blocks"] = unary_union([shape(f["geometry"]) for f in json.loads((C.OUT / "blocks.geojson").read_text())["features"]]) \
        if (C.OUT / "blocks.geojson").exists() else None
    _G["p"] = W(**params)


def scan_tile(path: Path):
    p: W = _G["p"]
    t = open_tile(path)
    img = t.read()
    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
    Hh, S, V = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    valid = img.max(2) > 12
    green = (Hh >= 30) & (Hh <= 95)
    white = (V >= p.v_white) & (S <= p.s_white) & valid
    vivid = (S >= p.s_vivid) & (V >= p.v_vivid) & ~green & valid
    dark = (V <= p.v_dark) & (S <= p.s_dark) & valid
    tb = box(*t.bounds)
    forb = prep(_G["forbidden"].intersection(tb.buffer(p.building_dist)).buffer(p.building_dist)) \
        if _G["forbidden"].intersects(tb.buffer(p.building_dist)) else None
    if _G["blocks"] is not None:
        near_blk = _G["blocks"].intersection(tb.buffer(p.block_dist)).buffer(p.block_dist)
        if near_blk.is_empty:
            return []                                  # no vineyard anywhere near this tile
        near_blk = prep(near_blk)
    else:
        near_blk = None
    pas = prep(_G["passages"].intersection(tb)) if _G["passages"].intersects(tb) else None
    rows = _G["rows"].get(t.name, [])
    out = []
    for kind, m in (("white", white), ("vivid", vivid), ("dark", dark)):
        m = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
        n, lab, st, cen = cv2.connectedComponentsWithStats(m, connectivity=8)
        for i in range(1, n):
            x, y, w, h, area = st[i]
            a = area * RES * RES
            if a < p.min_m2 or a > p.max_m2:
                continue
            comp = (lab[y:y + h, x:x + w] == i).astype(np.uint8)
            cs, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            c = max(cs, key=cv2.contourArea)
            hull = cv2.convexHull(c)
            solidity = area / max(cv2.contourArea(hull), 1)
            (cx, cy), (rw, rh), _ = cv2.minAreaRect(c)
            aspect = max(rw, rh) / max(min(rw, rh), 1)
            if solidity < p.solidity or aspect > p.aspect:
                continue
            # local contrast: ring 6-14 px around the blob
            pad = 16
            y0, y1, x0, x1 = max(0, y - pad), min(2048, y + h + pad), max(0, x - pad), min(2048, x + w + pad)
            sub = np.zeros((y1 - y0, x1 - x0), np.uint8)
            sub[y - y0:y - y0 + h, x - x0:x - x0 + w] = comp
            ring = cv2.dilate(sub, np.ones((29, 29), np.uint8)) & ~cv2.dilate(sub, np.ones((13, 13), np.uint8))
            ring = ring & valid[y0:y1, x0:x1]
            if ring.sum() < 20:
                continue
            vb = float(V[y0:y1, x0:x1][sub > 0].mean())
            vr = float(V[y0:y1, x0:x1][ring > 0].mean())
            sr = float(S[y0:y1, x0:x1][ring > 0].mean())
            gr = float(green[y0:y1, x0:x1][ring > 0].mean())
            contrast = vb - vr if kind != "dark" else vr - vb
            if contrast < p.contrast:
                continue
            px, py = x + w / 2, y + h / 2
            d_axis = min((r.distance(Point(px, py)) for r in rows), default=1e9) * RES
            if d_axis < p.axis_dist:
                continue
            ux, uy = t.px_to_utm([[px, py]])[0]
            if forb is not None and forb.contains(Point(ux, uy)):
                continue
            if near_blk is not None and not near_blk.contains(Point(ux, uy)):
                continue                               # only the vineyard area and its surroundings
            on_pass = pas is not None and pas.contains(Point(ux, uy))
            if on_pass and kind == "white":
                continue
            # score: strong contrast, mid size, compact, sitting on vegetation/soil rather than in a uniform bright area
            score = min(contrast / p.contrast, 2.0) * min(solidity / p.solidity, 1.5) * (1.0 if 0.1 <= a <= 2.0 else 0.7)
            if kind == "dark":
                score *= 0.8 if aspect < 1.6 else 0.4          # tyres are round; long dark blobs are shadows
            out.append({"tile": t.name, "kind": kind, "x": int(x), "y": int(y), "w": int(w), "h": int(h),
                        "area_m2": round(a, 3), "solidity": round(solidity, 2), "aspect": round(aspect, 2),
                        "contrast": round(contrast, 1), "ring_s": round(sr, 1), "ring_green": round(gr, 2),
                        "d_axis": round(d_axis, 2), "on_passage": bool(on_pass), "score": round(score, 2)})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inp", default=str(C.OUT / "pre_global.xml"))
    ap.add_argument("--tiles", default=str(C.TILES))
    ap.add_argument("--out", default="", help="CVAT xml with waste boxes added (default: --inp, in place)")
    ap.add_argument("--scan", default=str(C.OUT / "waste_candidates.json"))
    ap.add_argument("--write", action="store_true", help="write boxes for candidates >= --score-min (default: scan only)")
    ap.add_argument("--score-min", type=float, default=W.score_min)
    a = ap.parse_args()
    params = asdict(W(score_min=a.score_min))
    paths = sorted(Path(a.tiles).glob("siret3_r*_c*.tif"))
    with Pool(6, initializer=_init, initargs=(a.inp, params)) as pool:
        cands = [c for cs in pool.map(scan_tile, paths) for c in cs]
    cands.sort(key=lambda c: -c["score"])
    Path(a.scan).write_text(json.dumps(cands))
    keep = [c for c in cands if c["score"] >= a.score_min]
    print(f"{len(cands)} candidates, {len(keep)} above score {a.score_min} on {len({c['tile'] for c in keep})} tiles")
    if not a.write:
        return
    ann = read_cvat(a.inp)
    for n in ann:
        ann[n] = [o for o in ann[n] if o["label"] != "waste"]
    for c in keep:
        ann.setdefault(c["tile"], []).append({"label": "waste", "type": "box", "xtl": float(c["x"]), "ytl": float(c["y"]),
                                              "xbr": float(c["x"] + c["w"]), "ybr": float(c["y"] + c["h"]),
                                              "attrs": {"vineyard_id": ""}})
    write_cvat(ann, a.out or a.inp)
    print(f"{len(keep)} waste boxes written -> {a.out or a.inp} (vineyard_id is set by pipeline/blocks.py)")


if __name__ == "__main__":
    main()
