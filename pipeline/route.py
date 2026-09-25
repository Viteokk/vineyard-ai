"""Walking routes START -> targets -> START, only on inter-rows and authorised passages (EPSG:32635).

Two routes (organiser requirement):
  --mode inspector  BLUE  row gaps / missing vines + waste  -> route.geojson        (the scored one)
  --mode farmer     RED   waste only                        -> route_waste.geojson

Grid graph at 0.5 m over the study area:
  cost 1    cells >= 0.35 m inside inter-rows / passages (exact shapely test on the cell centre)
  cost 20   connectors: the rest of the inter-row / passage area, other ground inside a vineyard block or within
            6 m of it (headlands, row bands at gaps). Walkable but "outside" for the official 2 % rule.
  blocked   canopies, forbidden zones, everything else
Targets are anchored to the nearest cheap cell within 2 m (visited = within 2 m), anchors covering the same
targets are merged, the order is a TSP (OR-Tools) on shortest-path distances with START as depot.
Then the outside share is measured per leg; while it exceeds --max-outside, the stops whose legs need the
most outside walking are dropped and the tour is re-solved (the route criterion scores 0 above 2 %).
The distance matrix is cached (out/route_cache_<mode>.npz) so re-solving is fast.

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
from pathlib import Path

import cv2
import numpy as np
import shapely
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra
from shapely.geometry import LineString, Point, shape
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
from pipeline.to_geojson import convert  # noqa: E402

RES = 0.5
COST_CORE, COST_RIM, COST_LINK = 1.0, 2.5, 20.0
VERSION = "grid-v4"       # bump when the cost grid changes: invalidates the cached distance matrix
INSET = 0.35              # walk only on cells >= this far inside inter-rows / passages, so the straight segments
                          # between cell centres (<= 0.71 m) never cut a polygon corner (validate.py is the referee)
LINK_REACH = 6.0          # connector cells allowed within this distance of inter-rows / passages (m)
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


def build_cost(grid, inter, passages, canopies, forbidden, study, blocks):
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
    cost[grid.raster(forbidden) > 0] = np.inf
    return cost


def graph(cost):
    """8-neighbour grid graph over walkable cells; edge weight = step length x mean cell cost."""
    h, w = cost.shape
    ok = np.isfinite(cost)
    idx = -np.ones((h, w), np.int64)
    idx[ok] = np.arange(ok.sum())
    rows, cols, wts = [], [], []
    for dr, dc, L in ((0, 1, 1.0), (1, 0, 1.0), (1, 1, 2 ** 0.5), (1, -1, 2 ** 0.5)):
        sa = (slice(0, h - dr), slice(max(0, -dc), w - max(0, dc)))
        sb = (slice(dr, h), slice(max(0, dc), w - max(0, -dc)))
        both = ok[sa] & ok[sb]
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


def _init_worker(g, nodes, reach):
    _G.update(g=g, nodes=np.asarray(nodes), reach=reach)


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
    ap.add_argument("--max-outside", type=float, default=0.015, help="max share of length outside inter-rows + passages")
    ap.add_argument("--recompute", action="store_true", help="ignore the cached distance matrix")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1), help="parallel Dijkstra workers")
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
    start = json.loads((C.ROUTE_IN / "start.geojson").read_text())["features"][0]["geometry"]["coordinates"]
    grid = Grid(study.union(passages).bounds)
    cost = build_cost(grid, inter, passages, canopies, forbidden, study, blocks)
    g, idx = graph(cost)
    rc = np.column_stack(np.unravel_index(np.flatnonzero(np.isfinite(cost).ravel()), cost.shape))
    is_out = (cost[rc[:, 0], rc[:, 1]] >= COST_LINK)          # per walkable cell: outside inter-rows + passages
    print(f"grid {grid.w}x{grid.h}, {g.shape[0]:,} walkable cells  ({time.time() - t0:.0f}s)")

    feats = [f for f in json.loads(Path(a.targets).read_text())["features"]
             if f["properties"]["type"] in types.split(",") and f["properties"].get("reachable", True)]
    s_node = anchor(grid, cost, idx, start, radius=5.0)
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
    print(f"{a.mode}: {len(feats)} targets -> {len(anchors)} stops ({len(unreachable)} unreachable)  ({time.time() - t0:.0f}s)")

    # distance matrix (cached: same inputs -> same nodes)
    key = hashlib.md5((Path(a.inp).read_bytes() + VERSION.encode())).hexdigest()   # same grid -> same distances
    cache = C.OUT / f"route_cache_{a.mode}.npz"
    D = None
    if cache.exists() and not a.recompute:
        z = np.load(cache, allow_pickle=False)
        pos = {int(n): i for i, n in enumerate(z["nodes"])}
        if str(z["key"]) == key and all(n in pos for n in nodes):       # any subset of the cached stops
            sel = [pos[n] for n in nodes]
            D = z["D"][np.ix_(sel, sel)]
            print("distance matrix from cache")
    if D is None:
        D = distance_matrix(g, nodes, a.workers, t0, a.reach)
        np.savez_compressed(cache, D=D, nodes=np.array(nodes), key=np.array(key))
    reach = np.isfinite(D[0]) & np.isfinite(D[:, 0])
    if not reach.all():
        print(f"  {int((~reach).sum())} stops not connected to START -> skipped")
    Dw = np.where(np.isfinite(D), D, 1e7)

    # solve, measure the outside share per leg, drop the worst stops while over budget
    active = [i for i in range(1, len(nodes)) if reach[i]]
    tlimit = a.time
    for it in range(15):
        sub = [0] + active
        order = [sub[i] for i in solve_tsp(Dw[np.ix_(sub, sub)], tlimit)] + [0]
        legs, cells = [], []
        for u, v in zip(order, order[1:]):
            seq = leg(g, nodes[u], nodes[v], Dw[u, v] + 1)
            path = [nodes[u]] + seq
            step = np.hypot(*(rc[path[1:]] - rc[path[:-1]]).T) * RES if len(path) > 1 else np.zeros(0)
            outside = float(step[(is_out[path[1:]] | is_out[path[:-1]])].sum()) if len(path) > 1 else 0.0
            legs.append((u, v, float(step.sum()), outside))
            cells.append(seq)
        total = sum(L for _, _, L, _ in legs)
        outside = sum(o for _, _, _, o in legs)
        share = outside / total if total else 0.0
        print(f"  iteration {it}: {len(active)} stops, {total / 1000:.2f} km, outside {outside:.0f} m = {share * 100:.2f} %"
              f"  ({time.time() - t0:.0f}s)")
        if share <= a.max_outside or not active:
            break
        # outside metres attributable to each stop = its incoming + outgoing legs; drop the worst 10 %
        attr = {}
        for u, v, _, o in legs:
            attr[v] = attr.get(v, 0.0) + o
            attr[u] = attr.get(u, 0.0) + o
        attr.pop(0, None)
        worst = sorted(active, key=lambda i: -attr.get(i, 0.0))[:max(3, len(active) // 10)]
        active = [i for i in active if i not in set(worst)]
        tlimit = min(a.time, 20)

    path_cells = [nodes[order[0]]] + [c for seq in cells for c in seq]
    xy = grid.to_xy(rc[path_cells, 0], rc[path_cells, 1])
    line = LineString([tuple(start)] + [tuple(p) for p in xy] + [tuple(start)]).simplify(0.1)
    length = round(line.length, 1)
    visited = [f for f in feats if line.distance(Point(f["geometry"]["coordinates"])) <= VISIT]
    fc = {"type": "FeatureCollection", "crs": CRS, "features": [{
        "type": "Feature",
        "properties": {"length_m": length, "mode": a.mode, "target_types": types, "targets_total": len(feats),
                       "targets_visited": len(visited), "outside_share": round(share, 4),
                       "minutes_at_4kmh": round(length / 4000 * 60, 1)},
        "geometry": {"type": "LineString", "coordinates": [[round(x, 2), round(y, 2)] for x, y in line.coords]}}]}
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
          f"outside {share * 100:.2f} % -> {out_path}  ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
