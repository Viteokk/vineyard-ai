"""Check route.geojson against the official route rules before handing it in.

Hard rules (the route criterion scores 0 if broken):
  - one LineString in EPSG:32635 with a length_m property
  - starts and ends within 5 m of the supplied START
  - at most 2 % of its length outside passable inter-row areas + authorised passages (we aim for < 0.5 %)
Also reported: length through canopies / forbidden zones, which targets are not visited (> 2 m away), and how
many times the route crosses a vine row axis outside the authorised passages (the trellis wires make a row
impassable, even at a planting gap; pipeline/route.py treats rows as walls). Info only, not a hard rule.

Usage:  python -m pipeline.validate --route route.geojson --inp out/pre_global.xml [--targets out/targets.geojson]
Exit code 1 if a hard rule fails.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from shapely.geometry import LineString, Point, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
from pipeline.to_geojson import convert  # noqa: E402


def load(path):
    return unary_union([shape(f["geometry"]) for f in json.loads(Path(path).read_text())["features"]])


def row_crossings(line, rows, passages):
    """Times the route crosses (or touches) a row axis outside the passages. A crossing exactly on a route vertex
    is counted once; walking the same way back over the same point counts again (it is a second crossing)."""
    axes = unary_union(rows).difference(passages) if rows else None
    parts = [g for g in getattr(axes, "geoms", [axes]) if g is not None and not g.is_empty]
    if not parts:
        return 0
    c = list(line.coords)
    segs = [LineString(c[i:i + 2]) for i in range(len(c) - 1)]
    si, pi = STRtree(parts).query(segs, predicate="intersects")
    hits = {}
    for s, p in zip(si, pi):
        x = segs[s].intersection(parts[p])
        for g in getattr(x, "geoms", [x]):
            q = g.coords[0] if g.geom_type == "Point" else g.representative_point().coords[0]
            hits.setdefault(int(s), set()).add((round(q[0], 3), round(q[1], 3)))
    n = 0
    for s in sorted(hits):
        pts = hits[s]
        if s - 1 in hits:                                   # already counted at the end of the previous segment
            v = (round(c[s][0], 3), round(c[s][1], 3))
            pts = pts - ({v} & hits[s - 1])
        n += len(pts)
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--route", default=str(C.ROOT / "route.geojson"))
    ap.add_argument("--inp", default=str(C.OUT / "pre_global.xml"))
    ap.add_argument("--tiles", default=str(C.TILES))
    ap.add_argument("--targets", default=str(C.OUT / "targets.geojson"))
    ap.add_argument("--json", default="", help="also write the check results here (for the web app)")
    a = ap.parse_args()
    fc = json.loads(Path(a.route).read_text())
    ok, msgs = True, []
    feats = fc.get("features", [])
    lines = [f for f in feats if f["geometry"]["type"] == "LineString"]
    if len(feats) != 1 or len(lines) != 1:
        ok = False
        msgs.append(f"FAIL  expected exactly one LineString feature, found {len(feats)} features")
    crs = json.dumps(fc.get("crs", {}))
    if "32635" not in crs:
        msgs.append("WARN  no EPSG:32635 crs member (coordinates must still be UTM 35N)")
    line = shape(lines[0]["geometry"])
    L = line.length
    lm = lines[0]["properties"].get("length_m")
    if lm is None or abs(lm - L) > 1.0:
        ok = False
        msgs.append(f"FAIL  length_m={lm} but geometry length is {L:.1f} m")
    else:
        msgs.append(f"OK    length {L:.1f} m (length_m={lm})")
    start = Point(json.loads((C.ROUTE_IN / "start.geojson").read_text())["features"][0]["geometry"]["coordinates"])
    d0, d1 = Point(line.coords[0]).distance(start), Point(line.coords[-1]).distance(start)
    good = d0 <= 5 and d1 <= 5
    ok &= good
    msgs.append(f"{'OK  ' if good else 'FAIL'}  start {d0:.2f} m / end {d1:.2f} m from START (max 5 m)")

    layers, _ = convert(Path(a.inp), Path(a.tiles))
    inter = unary_union([shape(f["geometry"]) for f in layers.get("interrows", [])])
    canopies = unary_union([shape(f["geometry"]) for f in layers.get("canopies", [])])
    passages, forbidden = load(C.ROUTE_IN / "passages.geojson"), load(C.ROUTE_IN / "forbidden.geojson")
    allowed = unary_union([inter, passages]).buffer(0.01)
    out_len = line.difference(allowed).length
    share = out_len / L if L else 1.0
    good = share <= 0.02
    ok &= good
    msgs.append(f"{'OK  ' if good else 'FAIL'}  outside inter-rows + passages: {out_len:.1f} m = {share * 100:.2f} % "
                f"(limit 2 %, target < 0.5 %)")
    msgs.append(f"INFO  through canopies: {line.intersection(canopies).length:.1f} m, "
                f"through forbidden: {line.intersection(forbidden).length:.1f} m")
    n_cross = row_crossings(line, [shape(f["geometry"]) for f in layers.get("rows", [])], passages)
    msgs.append(f"{'INFO' if n_cross == 0 else 'WARN'}  row crossings (outside passages): {n_cross}")
    tg, miss = [], []
    if Path(a.targets).exists():
        tg = json.loads(Path(a.targets).read_text())["features"]
        miss = [f["properties"]["id"] for f in tg if line.distance(shape(f["geometry"])) > 2.0]
        msgs.append(f"INFO  targets visited (<= 2 m): {len(tg) - len(miss)} / {len(tg)}"
                    + (f"; not visited e.g. {miss[:8]}" if miss else ""))
    if a.json:
        Path(a.json).write_text(json.dumps({
            "valid": bool(ok), "length_m": round(L, 1), "start_m": round(d0, 2), "end_m": round(d1, 2),
            "outside_m": round(out_len, 1), "outside_share": round(share, 5),
            "canopy_m": round(line.intersection(canopies).length, 1),
            "forbidden_m": round(line.intersection(forbidden).length, 1), "row_crossings": n_cross,
            "targets": len(tg), "visited": len(tg) - len(miss), "not_visited": miss}, indent=1))
    print("\n".join(msgs))
    print("ROUTE VALID" if ok else "ROUTE INVALID")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
