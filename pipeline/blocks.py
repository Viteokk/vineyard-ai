"""Global vineyard_id / row_id over the whole mosaic (tile-local IDs from the detector -> V01, V01-R001 ...).

Rules (official annotation rules):
  - a block is a connected planting; plantings < 5 m apart are the same block; a road/track ALWAYS separates blocks
  - row_id is unique within its block and identical for the segments of one physical row in neighbouring tiles
  - canopies / inter-rows take the block they lie in; waste takes the block within 10 m, else empty

Method (all in UTM metres):
  blocks : strips of +-1.4 m around every row axis, grown by 2.5 m (so gaps < 5 m join), minus the organiser
           passages (roads) -> connected components, numbered north-west first.
  rows   : two segments of the same block are the same physical row if they are nearly parallel (< 6 deg), their
           facing ends lie on each other's axis (< 0.5 m + 2% of the gap) and they do not overlap along the row.
           Greedy best-match per row end keeps chains linear; chains are numbered across the block.

Usage:  python -m pipeline.blocks --inp out/baseline_all.xml --out out/pre_global.xml [--geojson out/blocks.geojson]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from shapely.geometry import LineString, Point, Polygon, mapping, shape
from shapely.ops import unary_union
from shapely.validation import make_valid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
from pipeline.cvat_io import read_cvat, write_cvat  # noqa: E402
from pipeline.tiles import open_tile  # noqa: E402

STRIP = 1.4          # half-width of the planting strip around a row axis (m) ~ half the row spacing
JOIN = 2.5           # grow strips by this much: plantings < 5 m apart merge
WASTE_DIST = 10.0    # waste gets a vineyard_id if within this distance of a block
ANG_MAX = 6.0        # deg, max angle between two segments of one row (per-tile fits differ by up to ~4 deg)
D0, DK = 0.5, 0.02   # facing-end offset tolerance: D0 + DK * gap (m)
GAP_MAX = 60.0       # max along-row gap bridged between two segments (one missing tile)


def load_passages():
    p = C.ROUTE_IN / "passages.geojson"
    if not p.exists():
        return Polygon()
    return unary_union([make_valid(shape(f["geometry"])) for f in json.loads(p.read_text())["features"]])


def _uf(n):
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        parent[find(i)] = find(j)
    return find, union


def build_blocks(segments: list[LineString], passages) -> list[Polygon]:
    strips = unary_union([s.buffer(STRIP, cap_style="flat") for s in segments])
    area = strips.buffer(JOIN).difference(passages)
    polys = [g for g in getattr(area, "geoms", [area]) if g.geom_type == "Polygon" and g.area > 1.0]
    # keep a block only if it really holds rows (tiny slivers cut off by roads carry none)
    return polys


def link_rows(segs: list[LineString], tiles: list[str]) -> list[int]:
    """Chain id per segment; segments of one physical row across tiles share an id."""
    n = len(segs)
    if n == 0:
        return []
    a = np.array([s.coords[0] for s in segs])
    b = np.array([s.coords[-1] for s in segs])
    u = b - a
    L = np.linalg.norm(u, axis=1) + 1e-9
    u = u / L[:, None]
    u[u[:, 0] < 0] *= -1                                  # orient all segments the same way (mod 180 deg)
    lo = np.minimum((a * u).sum(1), (b * u).sum(1))
    tile_idx = {t: k for k, t in enumerate(sorted(set(tiles)))}
    tid = np.array([tile_idx[t] for t in tiles])
    cand = []
    for i in range(n):
        cosang = np.abs(u @ u[i])
        ok = (cosang >= math.cos(math.radians(ANG_MAX))) & (tid != tid[i])
        ok[i] = False
        js = np.flatnonzero(ok)
        if not len(js):
            continue
        # positions along row i's direction
        pa = np.minimum((a[js] * u[i]).sum(1), (b[js] * u[i]).sum(1))
        pb = np.maximum((a[js] * u[i]).sum(1), (b[js] * u[i]).sum(1))
        ia, ib = sorted(((a[i] * u[i]).sum(), (b[i] * u[i]).sum()))
        gap = np.maximum(pa - ib, ia - pb)                 # > 0: disjoint along the row
        keep = (gap > -2.0) & (gap < GAP_MAX)
        js, gap, pa = js[keep], gap[keep], pa[keep]
        for j, g, pj in zip(js, gap, pa):
            if j < i:
                continue
            # facing ends: the end of i nearest to j and the end of j nearest to i
            ends_i = (a[i], b[i])
            ends_j = (a[j], b[j])
            ei = min(ends_i, key=lambda e: min(np.linalg.norm(e - ends_j[0]), np.linalg.norm(e - ends_j[1])))
            ej = min(ends_j, key=lambda e: np.linalg.norm(e - ei))
            ni = np.array([-u[i][1], u[i][0]])
            nj = np.array([-u[j][1], u[j][0]])
            d = max(abs((ej - a[i]) @ ni), abs((ei - a[j]) @ nj))
            if d <= D0 + DK * max(g, 0.0):
                side_i = 1 if pj > ia else -1               # which end of i the partner is on
                cand.append((d + 0.005 * max(g, 0.0), i, j, side_i, -side_i))
    cand.sort()
    used = set()
    find, union = _uf(n)
    for _, i, j, si, sj in cand:
        if (i, si) in used or (j, sj) in used or find(i) == find(j):
            continue
        used.add((i, si))
        used.add((j, sj))
        union(i, j)
    return [find(i) for i in range(n)]


def assign(data: dict[str, list[dict]], tiles_dir: Path):
    passages = load_passages()
    tiles = {}
    geo = []                                             # (tile, index, label, utm geometry)
    for name, objs in data.items():
        if not objs:
            continue
        p = tiles_dir / name
        if not p.exists():
            p = C.EXAMPLES / "images" / name
        t = tiles.setdefault(name, open_tile(p))
        for k, o in enumerate(objs):
            if o["type"] == "box":
                pts = t.px_to_utm([[o["xtl"], o["ytl"]], [o["xbr"], o["ybr"]]])
                g = Point(pts.mean(0))
            else:
                pts = t.px_to_utm(o["points"])
                g = LineString(pts) if o["type"] == "polyline" else Polygon(pts)
                g = g if g.is_valid else make_valid(g)
            geo.append((name, k, o["label"], g))

    rows = [(n, k, g) for n, k, lab, g in geo if lab == "row" and g.length > 0]
    from shapely.strtree import STRtree
    blocks = build_blocks([g for _, _, g in rows], passages)
    # keep only components that actually hold rows (slivers cut off by a road carry none)
    t0 = STRtree(blocks)
    holds = {int(i) for _, _, g in rows for i in t0.query(g.centroid) if blocks[int(i)].contains(g.centroid)}
    blocks = [b for i, b in enumerate(blocks) if i in holds]
    # number blocks north-west first
    blocks.sort(key=lambda b: (-round(b.centroid.y / 25), b.centroid.x))
    width = max(2, len(str(len(blocks))))
    bid = [f"V{i + 1:0{width}d}" for i in range(len(blocks))]
    tree = STRtree(blocks)

    def block_of(g, maxd=None):
        c = g.centroid if g.geom_type != "Point" else g
        hits = [i for i in tree.query(c) if blocks[i].contains(c)]
        if hits:
            return hits[0]
        i = tree.nearest(c)
        if i is None:
            return None
        if maxd is not None and blocks[i].distance(c) > maxd:
            return None
        return int(i)

    new = {n: [dict(o, attrs=dict(o.get("attrs", {}))) for o in objs] for n, objs in data.items()}
    # rows: block by the largest overlap of their strip, then chains within each block
    per_block = defaultdict(list)
    for n, k, g in rows:
        strip = g.buffer(STRIP, cap_style="flat")
        best, area = None, 0.0
        for i in tree.query(strip):
            a = blocks[i].intersection(strip).area
            if a > area:
                best, area = int(i), a
        if best is None:
            best = block_of(g)
        per_block[best].append((n, k, g))
    n_rows = 0
    for bi, items in per_block.items():
        chains = link_rows([g for _, _, g in items], [n for n, _, _ in items])
        # number chains across the block (perpendicular to its mean direction)
        dirs = np.array([[math.cos(2 * math.atan2(g.coords[-1][1] - g.coords[0][1], g.coords[-1][0] - g.coords[0][0])),
                          math.sin(2 * math.atan2(g.coords[-1][1] - g.coords[0][1], g.coords[-1][0] - g.coords[0][0]))]
                         for _, _, g in items]) * np.array([[g.length] for _, _, g in items])
        ang = 0.5 * math.atan2(dirs[:, 1].sum(), dirs[:, 0].sum())
        nrm = np.array([-math.sin(ang), math.cos(ang)])
        if nrm[1] < 0:                                     # R01 on the northern side
            nrm = -nrm
        off = defaultdict(list)
        for (n, k, g), c in zip(items, chains):
            off[c].append((np.array(g.centroid.coords[0]) @ nrm, g.length))
        order = sorted(off, key=lambda c: -np.average([o for o, _ in off[c]], weights=[w for _, w in off[c]]))
        rw = max(2, len(str(len(order))))
        name_of = {c: f"{bid[bi]}-R{r + 1:0{rw}d}" for r, c in enumerate(order)}
        n_rows += len(order)
        for (n, k, g), c in zip(items, chains):
            new[n][k]["attrs"]["vineyard_id"] = bid[bi]
            new[n][k]["attrs"]["row_id"] = name_of[c]
    # canopies, inter-rows, waste
    for n, k, lab, g in geo:
        if lab == "row":
            continue
        if lab == "waste":
            bi = block_of(g, WASTE_DIST)
        else:
            bi = block_of(g)
        new[n][k]["attrs"]["vineyard_id"] = bid[bi] if bi is not None else ""
    return new, blocks, bid, n_rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inp", default=str(C.OUT / "baseline_all.xml"))
    ap.add_argument("--out", default=str(C.OUT / "pre_global.xml"))
    ap.add_argument("--tiles", default=str(C.TILES))
    ap.add_argument("--geojson", default=str(C.OUT / "blocks.geojson"))
    a = ap.parse_args()
    data = read_cvat(a.inp)
    new, blocks, bid, n_rows = assign(data, Path(a.tiles))
    write_cvat(new, a.out)
    fc = {"type": "FeatureCollection",
          "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::32635"}},
          "features": [{"type": "Feature", "properties": {"vineyard_id": b, "area_m2": round(p.area, 1)},
                        "geometry": mapping(p)} for b, p in zip(bid, blocks)]}
    Path(a.geojson).write_text(json.dumps(fc))
    print(f"{len(blocks)} blocks, {n_rows} rows -> {a.out}  (block outlines: {a.geojson})")


if __name__ == "__main__":
    main()
