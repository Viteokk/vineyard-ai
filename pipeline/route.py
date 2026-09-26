"""Walking routes START -> targets -> START, only on inter-rows and authorised passages (EPSG:32635).

Two routes (organiser requirement):
  --mode inspector  BLUE  row gaps / missing vines + waste  -> route.geojson        (the scored one)
  --mode farmer     RED   waste only                        -> route_waste.geojson

Grid graph at 0.5 m over the study area:
  cost 1    cells >= 0.35 m inside inter-rows / passages (exact shapely test on the cell centre)
  cost 20   connectors: the rest of the inter-row / passage area, other ground inside a vineyard block or within
            6 m of it (headlands past the row ends). Walkable but "outside" for the official 2 % rule.
  blocked   canopies (+0.35 m), forbidden zones, everything else, and the row walls:
            every row axis +-0.65 m (vine band 0.3 m + 0.35 m) with round caps, over its whole length, planting gaps
            included (the trellis wires make a row impassable even where vines are missing); the segments of one row
            are joined across tile seams; open on the authorised passages (roads crossing the rows), where only the
            steps that would cross an axis just outside the passage edge are left out of the graph.
So the route walks ALONG the inter-rows and changes inter-row only past the row ends or on a passage.
--allow-row-crossing drops the walls (old behaviour: stepping over a row through a planting gap).
Targets are anchored to the nearest cheap cell within 2 m (visited = within 2 m), anchors covering the same
targets are merged, the order is a TSP (OR-Tools) on shortest-path distances with START as depot.
Each leg is rebuilt cell by cell, then string-pulled (stops stay fixed vertices): a straight shortcut is kept only if
dense samples stay on walkable cells, it touches no canopy / forbidden zone, crosses no row axis outside a passage
and adds no metres outside.
Then the outside share is measured per leg; while it exceeds --max-outside, the stops whose legs need the
most outside walking are dropped and the tour is re-solved (the route criterion scores 0 above 2 %).
The distance matrix is cached (out/route_cache_<mode>.npz, _cross with --allow-row-crossing) so re-solving is fast.

Usage:  python -m pipeline.route --mode inspector --inp out/pre_global.xml [--targets out/targets.geojson]
        python -m pipeline.route --mode farmer
"""
from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
import shapely
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree
from shapely.geometry import LineString, Point, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
from pipeline.tiles import all_tiles  # noqa: E402
from pipeline.to_geojson import convert  # noqa: E402

RES = 0.5
COST_CORE, COST_RIM, COST_LINK = 1.0, 2.5, 20.0
VERSION = "grid-v8"       # bump when the cost grid changes: invalidates the cached distance matrix
INSET = 0.35              # walk only on cells >= this far inside inter-rows / passages, so the straight segments
                          # between cell centres (<= 0.71 m) never cut a polygon corner (validate.py is the referee)
WALL = 0.3 + INSET        # row wall half-width: > RES * 2 ** 0.5 / 2, so no step (<= 0.71 m) can jump a row axis
SEAM = 2.0                # join collinear row ends facing each other this close (a row cut at a tile edge)
SEAM_EDGE = 20.0          # ... and this far when one end sits on a tile edge (ids often change across the edge)
EDGE = 0.8                # a row end this close to its tile border was cut by the tile, not by the planting
EXTEND = 5.0              # a row cut by the tile edge with no continuation: the vines go on, extend the wall
BRIDGE = 15.0             # join the segments of one row_id across tile seams up to this gap (missing vines: wires go on)
LINK_REACH = 6.0          # connector cells allowed within this distance of inter-rows / passages (m)
STEPS = ((0, 1), (1, 0), (1, 1), (1, -1))       # graph edges: every cell to these 4 neighbours (and back)
VISIT = 2.0               # target visited if the route passes within this distance (m)
CRS = {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::32635"}}
MODES = {"inspector": ("gap,waste", "route.geojson"), "farmer": ("waste", "route_waste.geojson")}


def load(path):
    return unary_union([shape(f["geometry"]) for f in json.loads(Path(path).read_text())["features"]])


class Grid:
    def __init__(self, bounds):
        self.x0, y0, x1, self.y1 = bounds
        self.w = int(np.ceil((x1 - self.x0) / RES)) + 1
        self.h = int(np.ceil((self.y1 - y0) / RES)) + 1

    def to_px(self, xy):
        xy = np.asarray(xy, float).reshape(-1, 2)
        return np.column_stack([(xy[:, 0] - self.x0) / RES, (self.y1 - xy[:, 1]) / RES])

    def to_xy(self, r, c):
        return np.column_stack([self.x0 + (np.asarray(c) + 0.5) * RES, self.y1 - (np.asarray(r) + 0.5) * RES])

    def exact(self, geom):
        """Cells whose CENTRE lies inside geom (shapely, exact) - same test as pipeline/validate.py.
        cv2.fillPoly over-fills by one cell on two sides, which is fine for blocked areas but not for the
        allowed area, where every cell must really be inside the inter-row / passage polygons."""
        rr, cc = np.mgrid[0:self.h, 0:self.w]
        xy = self.to_xy(rr.ravel(), cc.ravel())
        shapely.prepare(geom)
        return shapely.contains_xy(geom, xy[:, 0], xy[:, 1]).reshape(self.h, self.w).astype(np.uint8)

    def raster(self, geom, value=1):
        m = np.zeros((self.h, self.w), np.uint8)
        for g in getattr(geom, "geoms", [geom]):
            if g.is_empty or g.geom_type != "Polygon":
                continue
            # fixed-point vertices (shift=8): a cell is filled iff its centre is inside the polygon
            cv2.fillPoly(m, [np.round((self.to_px(g.exterior.coords) - 0.5) * 256).astype(np.int32)], value, shift=8)
            for hole in g.interiors:
                cv2.fillPoly(m, [np.round((self.to_px(hole.coords) - 0.5) * 256).astype(np.int32)], 0, shift=8)
        return m


def row_walls(rows, passages, tile_bounds=None):
    """Row walls: rows = [(row_id, tile, LineString)], one per tile segment -> (blocked polygon: axes +-WALL with
    round caps minus the authorised passages, so a road crossing the rows stays open; the axes outside the passages).
    Tile seams must not leak (the annotation stops at the tile edge, the trellis does not):
      - the segments of one row_id are joined end to start along the row (gap <= BRIDGE, lateral offset <= 1 m);
      - any two collinear ends facing each other are joined: <= SEAM m apart, or <= SEAM_EDGE m when one of them
        sits on a tile edge (row_id and even vineyard_id often differ across the edge);
      - an end on a tile edge that is still open gets EXTEND m more wall along the row (the row goes on)."""
    tile_bounds = tile_bounds or {}
    rows = [r if len(r) == 3 else (r[0], '', r[1]) for r in rows]      # (row_id, LineString) still accepted
    segs = [(rid, tile, g) for rid, tile, g in rows if g.length > 0]
    lines, joined = [g for *_, g in segs], set()
    key = lambda q: (round(float(q[0]), 2), round(float(q[1]), 2))
    by = defaultdict(list)
    for rid, _, g in segs:
        if rid:
            by[rid].append(np.asarray(g.coords))
    for parts in by.values():                            # one row_id: consecutive segments along the row
        if len(parts) < 2:
            continue
        c = max(parts, key=lambda xy: np.hypot(*(xy[-1] - xy[0])))
        if np.hypot(*(c[-1] - c[0])) == 0:
            continue
        u = (c[-1] - c[0]) / np.hypot(*(c[-1] - c[0]))
        se = sorted([(xy[(xy @ u).argmin()], xy[(xy @ u).argmax()]) for xy in parts], key=lambda e: e[0] @ u)
        for (_, b), (a, _) in zip(se, se[1:]):               # end of one segment -> start of the next
            d = a - b
            if d @ u <= BRIDGE and abs(d @ [-u[1], u[0]]) <= 1.0:
                lines.append(LineString([b, a]))
                joined.update((key(a), key(b)))
    ends, dirs, owner, edge = [], [], [], []             # any row: collinear ends facing each other
    for k, (_, tile, g) in enumerate(segs):
        xy, tb = np.asarray(g.coords), tile_bounds.get(tile)
        for p, q in ((xy[0], xy[1]), (xy[-1], xy[-2])):
            if np.hypot(*(p - q)) > 0:
                ends.append(p)
                dirs.append((p - q) / np.hypot(*(p - q)))    # outward, along the row
                owner.append(k)
                edge.append(bool(tb) and min(p[0] - tb[0], tb[2] - p[0], p[1] - tb[1], tb[3] - p[1]) < EDGE)
    for i, j in (cKDTree(ends).query_pairs(SEAM_EDGE) if ends else ()):
        d = ends[j] - ends[i]
        dist, side = np.hypot(*d), abs(d @ [-dirs[i][1], dirs[i][0]])   # lateral offset (neighbour rows are >= 2 m apart)
        if (owner[i] != owner[j] and dirs[i] @ dirs[j] < -0.98 and dist > 0.01 and d @ dirs[i] > 0 and side <= 1.2
                and (dist <= SEAM or edge[i] or edge[j])):
            lines.append(LineString([ends[i], ends[j]]))
            joined.update((key(ends[i]), key(ends[j])))
    for p, u, e in zip(ends, dirs, edge):                # cut by the tile edge and still open: the row goes on
        if e and key(p) not in joined:
            lines.append(LineString([p, p + u * EXTEND]))
    axes = unary_union(lines)
    return axes.buffer(WALL).difference(passages), axes.difference(passages)


def wall_cuts(grid, cost, passages, axes):
    """Steps that cross a row axis outside the passages -> {(dr, dc): cells whose step to (r + dr, c + dc) is cut}.
    The wall is open on a passage, so a step from a passage cell next to the passage edge could still cross the axis
    just outside it: only those steps are dropped, every passage cell stays walkable (no road is cut). A step with
    both ends outside the wall never crosses an axis (2 x WALL > 0.71 m), so only passage cells near an axis count."""
    h, w = cost.shape
    ok = np.isfinite(cost)
    cut = {d: np.zeros((h, w), bool) for d in STEPS}
    rr, cc = np.nonzero(ok & (grid.raster(passages) > 0))
    if axes.is_empty or not len(rr):
        return cut
    shapely.prepare(axes)
    m = shapely.dwithin(axes, shapely.points(grid.to_xy(rr, cc)), WALL)
    rr, cc = rr[m], cc[m]
    for dr, dc in STEPS + tuple((-dr, -dc) for dr, dc in STEPS):
        r2, c2 = rr + dr, cc + dc
        m = (r2 >= 0) & (r2 < h) & (c2 >= 0) & (c2 < w)
        m[m] = ok[r2[m], c2[m]]
        a, b = (rr[m], cc[m]), (r2[m], c2[m])
        if not len(a[0]):
            continue
        x = shapely.intersects(axes, shapely.linestrings(np.stack([grid.to_xy(*a), grid.to_xy(*b)], axis=1)))
        if (dr, dc) in STEPS:
            cut[(dr, dc)][a[0][x], a[1][x]] = True
        else:
            cut[(-dr, -dc)][b[0][x], b[1][x]] = True
    return cut


def build_cost(grid, inter, passages, canopies, forbidden, study, blocks, walls=None):
    allowed = unary_union([inter, passages])
    m_allowed = grid.exact(allowed)                  # cell centre inside inter-rows / passages (exact)
    core = grid.exact(allowed.buffer(-INSET))        # ... and at least INSET inside: the only cheap cells
    k = int(2 * LINK_REACH / RES) + 1
    near = cv2.dilate(m_allowed, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    region = (grid.raster(study) | m_allowed) & (near | grid.raster(blocks))
    cost = np.full((grid.h, grid.w), np.inf, np.float32)
    cost[region > 0] = COST_LINK                     # outside for the 2 % rule (headlands, row bands, edges)
    cost[core > 0] = COST_CORE
    cost[grid.raster(canopies) > 0] = np.inf
    cost[grid.exact(canopies.buffer(INSET + 0.01)) > 0] = np.inf   # keep >= INSET from every canopy: a 0.71 m
    cost[grid.raster(forbidden) > 0] = np.inf                       # diagonal step can then never clip one
    if walls is not None:                                            # row walls (row_walls), off with --allow-row-crossing
        cost[grid.exact(walls) > 0] = np.inf
    return cost


def graph(cost, cut=None):
    """8-neighbour grid graph over walkable cells; edge weight = step length x mean cell cost.
    cut: steps to leave out (wall_cuts)."""
    h, w = cost.shape
    ok = np.isfinite(cost)
    idx = -np.ones((h, w), np.int64)
    idx[ok] = np.arange(ok.sum())
    rows, cols, wts = [], [], []
    for dr, dc in STEPS:
        L = np.hypot(dr, dc)
        sa = (slice(0, h - dr), slice(max(0, -dc), w - max(0, dc)))
        sb = (slice(dr, h), slice(max(0, dc), w - max(0, -dc)))
        both = ok[sa] & ok[sb]
        if cut is not None:
            both &= ~cut[(dr, dc)][sa]
        ia, ib = idx[sa][both], idx[sb][both]
        wt = (cost[sa][both] + cost[sb][both]) * 0.5 * L * RES
        rows += [ia, ib]
        cols += [ib, ia]
        wts += [wt, wt]
    n = int(ok.sum())
    g = coo_matrix((np.concatenate(wts), (np.concatenate(rows), np.concatenate(cols))), shape=(n, n)).tocsr()
    return g, idx


def anchor(grid, cost, idx, xy, radius=VISIT):
    """Nearest cheap cell within radius of xy (prefer inter-row / passage), else any passable cell."""
    r0, c0 = grid.to_px([xy])[0][::-1]
    k = int(np.ceil(radius / RES)) + 1
    r0, c0 = int(r0), int(c0)
    rr, cc = np.mgrid[max(0, r0 - k):min(cost.shape[0], r0 + k + 1), max(0, c0 - k):min(cost.shape[1], c0 + k + 1)]
    rr, cc = rr.ravel(), cc.ravel()
    pts = grid.to_xy(rr, cc)
    d = np.hypot(pts[:, 0] - xy[0], pts[:, 1] - xy[1])
    cst = cost[rr, cc]
    ok = (d <= radius - RES * 0.75) & np.isfinite(cst)
    if not ok.any():
        return None
    score = d + np.where(cst <= COST_RIM, 0, 5)
    score[~ok] = np.inf
    j = int(np.argmin(score))
    return int(idx[rr[j], cc[j]])


def solve_tsp(D, time_limit):
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    n = len(D)
    if n <= 2:
        return list(range(n))
    Di = np.minimum(np.round(D * 10), 2 ** 31 // 4).astype(np.int64)
    mgr = pywrapcp.RoutingIndexManager(n, 1, 0)
    rt = pywrapcp.RoutingModel(mgr)
    cb = rt.RegisterTransitCallback(lambda i, j: int(Di[mgr.IndexToNode(i), mgr.IndexToNode(j)]))
    rt.SetArcCostEvaluatorOfAllVehicles(cb)
    prm = pywrapcp.DefaultRoutingSearchParameters()
    prm.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    prm.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    prm.time_limit.seconds = max(1, int(time_limit))
    sol = rt.SolveWithParameters(prm)
    order, i = [], rt.Start(0)
    while not rt.IsEnd(i):
        order.append(mgr.IndexToNode(i))
        i = sol.Value(rt.NextVar(i))
    return order


REACH_M = 1500.0          # Dijkstra limit per stop (metres of cost); pairs beyond it get a large distance
_G = {}


def visible(finite, a, b):
    """Straight line between cell centres a, b (row, col): every sample (step <= RES/4) lies on a walkable cell.
    Walkable centres are >= INSET + 0.01 from a canopy and, away from the passages, >= WALL from a row axis, so the
    line can clip a canopy corner by a few cm at most and cross an axis only on or right next to a passage (the
    caller checks both exactly)."""
    n = int(np.ceil(np.hypot(*(b - a)) * 4)) + 1
    p = np.rint(a + (b - a) * np.linspace(0.0, 1.0, n)[:, None]).astype(np.int64)
    return bool(finite[p[:, 0], p[:, 1]].all())


def pull(n, ok):
    """String pulling over path vertices 0..n-1 (first and last fixed): from each kept vertex jump to the farthest
    vertex ok(i, j) accepts (galloping, then bisection; ok(i, i + 1) is the raw step) -> kept indices."""
    keep, i = [0], 0
    while i < n - 1:
        lo, step = i + 1, 1
        while lo + step < n and ok(i, lo + step):
            lo += step
            step *= 2
        hi = min(lo + step, n)
        while hi - lo > 1:
            m = (lo + hi) // 2
            if ok(i, m):
                lo = m
            else:
                hi = m
        keep.append(lo)
        i = lo
    return keep


def _init_worker(g, nodes, reach, allowed=None, rc=None, grid=None, is_out=None, finite=None, blocked=None, axes=None):
    _G.update(g=g, nodes=np.asarray(nodes), reach=reach, rc=rc, grid=grid, is_out=is_out, finite=finite,
              blocked=blocked, allowed=allowed, axes=axes)
    if allowed is not None:
        parts = list(getattr(allowed, "geoms", [allowed]))
        _G.update(parts=parts, tree=STRtree(parts))
    for geom in (allowed, blocked, axes):
        if geom is not None:
            shapely.prepare(geom)


def _outside(line):
    """Metres of line outside inter-rows / passages (shapely difference, like pipeline/validate.py)."""
    if _G["allowed"].contains(line):
        return 0.0
    near = [_G["parts"][i] for i in _G["tree"].query(line)]
    return float(line.difference(unary_union(near)).length if near else line.length)


def _leg_job(args):
    """Cell path of one leg, string-pulled (stops = its fixed ends), + its length and the metres outside
    inter-rows/passages measured exactly like pipeline/validate.py. A shortcut i -> j is kept only if it is
    visible() on the grid, touches no canopy / forbidden zone and no row axis outside a passage (exact) and is not
    more outside than the cells it replaces; if the pulled leg still ends up more outside than the cell path, the
    cell path is kept."""
    src, dst, limit = args
    path = [src] + leg(_G["g"], src, dst, limit)
    rc = _G["rc"]
    xy = _G["grid"].to_xy(rc[path, 0], rc[path, 1])
    if len(path) < 2:
        return xy, 0.0, 0.0
    io = _G["is_out"][path]
    step_out = np.zeros(len(path) - 1)                    # core -> core steps are inside by construction
    for k in np.flatnonzero(io[1:] | io[:-1]):
        step_out[k] = _outside(LineString(xy[k:k + 2]))
    cum = np.concatenate([[0.0], np.cumsum(step_out)])
    pc = rc[path].astype(float)

    def ok(i, j):
        if not visible(_G["finite"], pc[i], pc[j]):
            return False
        seg = LineString(xy[[i, j]])
        if _G["blocked"].intersects(seg) or (_G["axes"] is not None and _G["axes"].intersects(seg)):
            return False
        return _outside(seg) <= cum[j] - cum[i] + 1e-6

    raw = LineString(xy)
    raw_out = _outside(raw) if io.any() else 0.0
    sm = xy[pull(len(path), ok)]
    line = LineString(sm)
    out = _outside(line)
    if out > raw_out + 1e-6:
        return xy, raw.length, raw_out
    return sm, line.length, out


def _row(src):
    d = dijkstra(_G["g"], directed=True, indices=src, limit=_G["reach"] * COST_CORE)[_G["nodes"]]
    return src, d


def distance_matrix(g, nodes, workers, t0, reach=REACH_M):
    """Shortest-path cost from every stop to every other stop, one Dijkstra per stop, in parallel.
    Each search stops at REACH_M; unreached pairs are +inf and later replaced by a large constant."""
    D = np.zeros((len(nodes), len(nodes)))
    pos = {n: i for i, n in enumerate(nodes)}
    D[0] = dijkstra(g, directed=True, indices=nodes[0])[nodes]      # depot row: no limit, every stop must connect
    ctx = mp.get_context("fork") if hasattr(os, "fork") else mp.get_context()
    done = 0
    with ctx.Pool(workers, initializer=_init_worker, initargs=(g, nodes, reach)) as pool:
        for src, d in pool.imap_unordered(_row, nodes[1:], chunksize=8):
            D[pos[src]] = d
            done += 1
            if done % 200 == 0:
                print(f"  distances {done}/{len(nodes)}  ({time.time() - t0:.0f}s, {workers} workers)", flush=True)
    D[:, 0] = D[0]                                                   # the grid graph is symmetric
    return D


def leg(g, src, dst, limit):
    """Cell path src -> dst (list of cell ids, src excluded)."""
    _, pred = dijkstra(g, directed=True, indices=src, return_predecessors=True, limit=limit)
    seq, cur = [], dst
    while cur != src and cur >= 0:
        seq.append(cur)
        cur = pred[cur]
    return seq[::-1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=MODES, default="inspector")
    ap.add_argument("--inp", default=str(C.OUT / "pre_global.xml"))
    ap.add_argument("--tiles", default=str(C.TILES))
    ap.add_argument("--targets", default=str(C.OUT / "targets.geojson"))
    ap.add_argument("--out", default="", help="default: route.geojson / route_waste.geojson in the repo root")
    ap.add_argument("--targets-out", default="", help="default: out/targets_<mode>.geojson (with visit order)")
    ap.add_argument("--time", type=int, default=60, help="TSP time limit (s) for the first solve")
    ap.add_argument("--max-outside", type=float, default=0.017, help="max share of length outside inter-rows + passages "
                                                                       "(official limit 2 %%; measured like validate.py)")
    ap.add_argument("--recompute", action="store_true", help="ignore the cached distance matrix")
    ap.add_argument("--allow-row-crossing", action="store_true",
                    help="no row walls: the route may step over a vine row through a planting gap (old behaviour; "
                         "by default the trellis makes every row a wall, open only on the authorised passages)")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1), help="parallel Dijkstra workers")
    ap.add_argument("--start", default="", help="custom start 'x,y' (EPSG:32635) instead of data/route/start.geojson; "
                                                 "snapped to the nearest walkable cell within --start-radius")
    ap.add_argument("--start-radius", type=float, default=0.0, help="snap radius for --start (m); default 5 m official, 80 m custom")
    ap.add_argument("--reach", type=float, default=REACH_M, help="Dijkstra search limit per stop (m); farther pairs are"
                                                                  " never consecutive in a good tour")
    a = ap.parse_args()
    types, default_out = MODES[a.mode]
    out_path = Path(a.out) if a.out else C.ROOT / default_out
    tg_out = Path(a.targets_out) if a.targets_out else C.OUT / f"targets_{a.mode}.geojson"
    t0 = time.time()

    layers, _ = convert(Path(a.inp), Path(a.tiles))
    inter = unary_union([shape(f["geometry"]) for f in layers.get("interrows", [])])
    canopies = unary_union([shape(f["geometry"]) for f in layers.get("canopies", [])])
    passages = load(C.ROUTE_IN / "passages.geojson")
    forbidden = load(C.ROUTE_IN / "forbidden.geojson")
    study = load(C.ROUTE_IN / "study_area.geojson")
    blocks = load(C.OUT / "blocks.geojson") if (C.OUT / "blocks.geojson").exists() else inter.buffer(3)
    custom = bool(a.start)
    start = ([float(v) for v in a.start.split(",")] if custom
             else json.loads((C.ROUTE_IN / "start.geojson").read_text())["features"][0]["geometry"]["coordinates"])
    grid = Grid(study.union(passages).bounds)
    tile_bounds = {t.name: t.bounds for t in all_tiles(Path(a.tiles))}
    walls, axes = (None, None) if a.allow_row_crossing else row_walls(
        [(f["properties"].get("row_id", ""), f["properties"].get("tile", ""), shape(f["geometry"])) for f in layers.get("rows", [])],
        passages, tile_bounds)
    cost = build_cost(grid, inter, passages, canopies, forbidden, study, blocks, walls)
    g, idx = graph(cost, None if axes is None else wall_cuts(grid, cost, passages, axes))
    rc = np.column_stack(np.unravel_index(np.flatnonzero(np.isfinite(cost).ravel()), cost.shape))
    is_out = (cost[rc[:, 0], rc[:, 1]] >= COST_LINK)          # per walkable cell: outside inter-rows + passages
    print(f"grid {grid.w}x{grid.h}, {g.shape[0]:,} walkable cells  ({time.time() - t0:.0f}s)")

    feats = [f for f in json.loads(Path(a.targets).read_text())["features"]
             if f["properties"]["type"] in types.split(",") and f["properties"].get("reachable", True)]
    s_node = anchor(grid, cost, idx, start, radius=a.start_radius or (80.0 if custom else 5.0))
    if s_node is None:
        sys.exit(f"START {start[0]:.1f}, {start[1]:.1f} is not within reach of an inter-row or passage")
    if custom:                                   # begin on the walkable cell itself, never across a canopy
        start = [float(v) for v in grid.to_xy(rc[[s_node], 0], rc[[s_node], 1])[0]]
        print(f"custom START snapped to {start[0]:.1f}, {start[1]:.1f}")
    anchors, members, unreachable = [], [], []
    for f in feats:
        nd = anchor(grid, cost, idx, f["geometry"]["coordinates"])
        if nd is None:
            unreachable.append(f["properties"]["id"])
        elif nd in anchors:
            members[anchors.index(nd)].append(f)
        else:
            anchors.append(nd)
            members.append([f])
    # merge anchors whose targets are all within reach of another anchor
    axy = grid.to_xy(rc[anchors, 0], rc[anchors, 1]) if anchors else np.zeros((0, 2))
    txy = [np.array([m["geometry"]["coordinates"] for m in ms]) for ms in members]
    alive = set(range(len(anchors)))
    for i in sorted(range(len(anchors)), key=lambda i: -len(members[i])):
        if i not in alive:
            continue
        for j in [j for j in alive if j != i and np.hypot(*(axy[j] - axy[i])) < 2 * VISIT]:
            if np.all(np.hypot(txy[j][:, 0] - axy[i][0], txy[j][:, 1] - axy[i][1]) <= VISIT - 0.3):
                members[i] += members[j]
                txy[i] = np.vstack([txy[i], txy[j]])
                alive.discard(j)
    keep = sorted(alive)
    anchors, members = [anchors[i] for i in keep], [members[i] for i in keep]
    nodes = [s_node] + anchors
    value = [1.0] + [max(10.0 if m["properties"]["type"] == "waste" else (3.0 if m["properties"].get("gap_m", 0) >= 5 else 1.0)
                     for m in ms) for ms in members]
    print(f"{a.mode}: {len(feats)} targets -> {len(anchors)} stops ({len(unreachable)} unreachable)  ({time.time() - t0:.0f}s)")

    # distance matrix (cached: same inputs -> same nodes)
    key = hashlib.md5((Path(a.inp).read_bytes() + VERSION.encode() + str(a.allow_row_crossing).encode())).hexdigest()
    cache = C.OUT / f"route_cache_{a.mode}{'_cross' if a.allow_row_crossing else ''}.npz"
    D = None
    if cache.exists() and not a.recompute:
        z = np.load(cache, allow_pickle=False)
        pos = {int(n): i for i, n in enumerate(z["nodes"])}
        missing = [i for i, n in enumerate(nodes) if n not in pos]
        if str(z["key"]) == key and not missing:                       # any subset of the cached stops
            sel = [pos[n] for n in nodes]
            D = z["D"][np.ix_(sel, sel)]
            print("distance matrix from cache")
        elif str(z["key"]) == key and len(missing) <= 20:              # e.g. a custom START: a few new rows only
            D = np.full((len(nodes), len(nodes)), np.inf)
            have = [i for i, n in enumerate(nodes) if n in pos]
            sel = [pos[nodes[i]] for i in have]
            D[np.ix_(have, have)] = z["D"][np.ix_(sel, sel)]
            for i in missing:
                row = dijkstra(g, directed=True, indices=nodes[i], limit=np.inf if i == 0 else a.reach * COST_CORE)[nodes]
                D[i] = row
                D[:, i] = row                                          # the grid graph is symmetric
            print(f"distance matrix from cache + {len(missing)} new row(s)")
    if D is None:
        D = distance_matrix(g, nodes, a.workers, t0, a.reach)
        if not custom:                                                 # keep the cache on the official START
            np.savez_compressed(cache, D=D, nodes=np.array(nodes), key=np.array(key))
    reach = np.isfinite(D[0]) & np.isfinite(D[:, 0])
    if not reach.all():
        print(f"  {int((~reach).sum())} stops not connected to START -> skipped")
    Dw = np.where(np.isfinite(D), D, 1e7)

    # solve, measure the outside share per leg, drop the worst stops while over budget.
    # Leg paths are cached per (u, v): between iterations most consecutive pairs stay the same.
    active = [i for i in range(1, len(nodes)) if reach[i]]
    tlimit = a.time
    leg_cache = {}
    ctx = mp.get_context("fork") if hasattr(os, "fork") else mp.get_context()
    allowed_geom = unary_union([inter, passages]).buffer(0.01)
    pool = ctx.Pool(a.workers, initializer=_init_worker, initargs=(g, nodes, REACH_M, allowed_geom, rc, grid, is_out,
                                                                   np.isfinite(cost), unary_union([canopies, forbidden]), axes))
    for it in range(40):
        sub = [0] + active
        order = [sub[i] for i in solve_tsp(Dw[np.ix_(sub, sub)], tlimit)] + [0]
        pairs = [(u, v) for u, v in zip(order, order[1:]) if (u, v) not in leg_cache]
        for (u, v), res in zip(pairs, pool.imap(_leg_job, [(nodes[u], nodes[v], Dw[u, v] + 1) for u, v in pairs], chunksize=16)):
            leg_cache[(u, v)] = res
        legs, pts = [], []
        for u, v in zip(order, order[1:]):
            xy, length, outside = leg_cache[(u, v)]
            legs.append((u, v, length, outside))
            pts.append(xy)
        total = sum(L for _, _, L, _ in legs)
        outside = sum(o for _, _, _, o in legs)
        share = outside / total if total else 0.0
        print(f"  iteration {it}: {len(active)} stops, {total / 1000:.2f} km, outside {outside:.0f} m = {share * 100:.2f} %"
              f"  ({time.time() - t0:.0f}s)")
        if share <= a.max_outside or not active:
            break
        # outside metres attributable to each stop = its incoming + outgoing legs; drop the worst few %
        attr = {}
        for u, v, _, o in legs:
            attr[v] = attr.get(v, 0.0) + o
            attr[u] = attr.get(u, 0.0) + o
        attr.pop(0, None)
        excess = outside - a.max_outside * total
        frac = min(0.12, max(0.03, excess / max(outside, 1e-9) * 0.6))   # proportional to how far over budget
        # drop first the stops that cost the most outside metres per unit of value: long gaps (>= 5 m, the
        # likely reference targets) and waste are worth more than short gaps
        worst = sorted(active, key=lambda i: -attr.get(i, 0.0) / value[i])[:max(3, int(len(active) * frac))]
        active = [i for i in active if i not in set(worst)]
        tlimit = min(a.time, 10)
    pool.close()

    xy = np.vstack([pts[0][:1]] + [p[1:] for p in pts])      # legs share their end stop
    line = LineString([tuple(start)] + [tuple(p) for p in xy] + [tuple(start)]).simplify(0)   # collinear points only
    length = round(line.length, 1)
    visited = [f for f in feats if line.distance(Point(f["geometry"]["coordinates"])) <= VISIT]
    fc = {"type": "FeatureCollection", "crs": CRS, "features": [{
        "type": "Feature",
        "properties": {"length_m": length, "mode": a.mode, "start": [round(start[0], 2), round(start[1], 2)], "custom_start": custom, "target_types": types, "targets_total": len(feats),
                       "targets_visited": len(visited), "outside_share": round(share, 4),
                       "row_walls": not a.allow_row_crossing, "minutes_at_4kmh": round(length / 4000 * 60, 1)},
        "geometry": {"type": "LineString", "coordinates": [[round(x, 3), round(y, 3)] for x, y in line.coords]}}]}
    out_path.write_text(json.dumps(fc))
    rank = {}
    for r, k in enumerate(order[1:-1], 1):
        for m in members[k - 1]:
            rank[m["properties"]["id"]] = r
    for f in feats:
        f["properties"]["order"] = rank.get(f["properties"]["id"])
        f["properties"]["visited"] = line.distance(Point(f["geometry"]["coordinates"])) <= VISIT
    tg_out.write_text(json.dumps({"type": "FeatureCollection", "crs": CRS, "features": feats}))
    print(f"{a.mode} route {length / 1000:.2f} km, {len(order) - 2} stops, {len(visited)}/{len(feats)} targets within 2 m, "
          f"outside {share * 100:.2f} %, {len(line.coords)} vertices -> {out_path}  ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
