"""Walking route START -> every reachable target -> START, only on inter-rows and authorised passages.

Grid graph at 0.5 m over the study area (all geometry in EPSG:32635):
  cost 1    inter-row core (>= 0.5 m inside the inter-row polygon) and organiser passages
  cost 1.6  inter-row rim (keeps the walk in the middle of the inter-row, inside the reference polygon too)
  cost 20   connectors: other ground inside a vineyard block or next to a passage (headlands, gaps between
            vines). Allowed but expensive, so the route leaves inter-rows/passages only for short hops
            (the official limit is 2 % of the length outside inter-rows + passages).
  blocked   canopies, forbidden zones, everything else
Targets are anchored to the nearest free cell within 2 m (a target counts as visited within 2 m); targets
covered by the same anchor are merged. Order: TSP (OR-Tools) on shortest-path distances, depot = START.

Usage:  python -m pipeline.route --inp out/pre_global.xml --targets out/targets.geojson --out route.geojson
        [--types gap,waste] [--time 60]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra
from shapely.geometry import LineString, Point, shape
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
from pipeline.to_geojson import convert  # noqa: E402

RES = 0.5
COST_CORE, COST_RIM, COST_LINK = 1.0, 1.6, 20.0
LINK_REACH = 6.0          # connector cells allowed within this distance of inter-rows / passages (m)
VISIT = 2.0               # target visited if the route passes within this distance (m)
CRS = {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::32635"}}


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

    def raster(self, geom, value=1):
        m = np.zeros((self.h, self.w), np.uint8)
        for g in getattr(geom, "geoms", [geom]):
            if g.is_empty or g.geom_type != "Polygon":
                continue
            ext = np.round(self.to_px(g.exterior.coords) - 0.5).astype(np.int32)
            cv2.fillPoly(m, [ext], value)
            for hole in g.interiors:
                cv2.fillPoly(m, [np.round(self.to_px(hole.coords) - 0.5).astype(np.int32)], 0)
        return m


def build_cost(grid: Grid, inter, passages, canopies, forbidden, study, blocks):
    m_inter = grid.raster(inter)
    core = cv2.erode(m_inter, np.ones((3, 3), np.uint8))
    m_pass = grid.raster(passages)
    near = cv2.dilate(np.maximum(m_inter, m_pass),
                      cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (int(2 * LINK_REACH / RES) + 1,) * 2))
    region = (grid.raster(study) | m_pass) & (near | grid.raster(blocks))
    cost = np.full((grid.h, grid.w), np.inf, np.float32)
    cost[region > 0] = COST_LINK
    cost[m_inter > 0] = COST_RIM
    cost[core > 0] = COST_CORE
    cost[m_pass > 0] = COST_CORE
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
    """Nearest cheap cell within radius of xy (prefer inter-row core / passage), else any passable cell."""
    r0, c0 = grid.to_px([xy])[0][::-1]
    k = int(np.ceil(radius / RES)) + 1
    r0, c0 = int(r0), int(c0)
    rr, cc = np.mgrid[max(0, r0 - k):min(cost.shape[0], r0 + k + 1), max(0, c0 - k):min(cost.shape[1], c0 + k + 1)]
    pts = grid.to_xy(rr.ravel(), cc.ravel())
    d = np.hypot(pts[:, 0] - xy[0], pts[:, 1] - xy[1])
    cst = cost[rr.ravel(), cc.ravel()]
    ok = (d <= radius - RES * 0.75) & np.isfinite(cst)
    if not ok.any():
        return None
    score = d + np.where(cst <= COST_RIM, 0, 5)
    score[~ok] = np.inf
    j = int(np.argmin(score))
    return int(idx[rr.ravel()[j], cc.ravel()[j]])


def solve_tsp(D, time_limit):
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    n = len(D)
    Di = np.minimum(np.round(D * 10), 2 ** 31 // 4).astype(np.int64)
    mgr = pywrapcp.RoutingIndexManager(n, 1, 0)
    rt = pywrapcp.RoutingModel(mgr)
    cb = rt.RegisterTransitCallback(lambda i, j: int(Di[mgr.IndexToNode(i), mgr.IndexToNode(j)]))
    rt.SetArcCostEvaluatorOfAllVehicles(cb)
    prm = pywrapcp.DefaultRoutingSearchParameters()
    prm.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    prm.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    prm.time_limit.seconds = int(time_limit)
    sol = rt.SolveWithParameters(prm)
    order, i = [], rt.Start(0)
    while not rt.IsEnd(i):
        order.append(mgr.IndexToNode(i))
        i = sol.Value(rt.NextVar(i))
    return order


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inp", default=str(C.OUT / "pre_global.xml"))
    ap.add_argument("--tiles", default=str(C.TILES))
    ap.add_argument("--targets", default=str(C.OUT / "targets.geojson"))
    ap.add_argument("--types", default="gap,waste")
    ap.add_argument("--out", default=str(C.ROOT / "route.geojson"))
    ap.add_argument("--targets-out", default=str(C.OUT / "targets_routed.geojson"))
    ap.add_argument("--time", type=int, default=60, help="TSP time limit (s)")
    a = ap.parse_args()
    t0 = time.time()
    layers, _ = convert(Path(a.inp), Path(a.tiles))
    inter = unary_union([shape(f["geometry"]) for f in layers.get("interrows", [])])
    canopies = unary_union([shape(f["geometry"]) for f in layers.get("canopies", [])])
    passages, forbidden, study = load(C.ROUTE_IN / "passages.geojson"), load(C.ROUTE_IN / "forbidden.geojson"), \
        load(C.ROUTE_IN / "study_area.geojson")
    blocks = load(C.OUT / "blocks.geojson") if (C.OUT / "blocks.geojson").exists() else inter.buffer(3)
    start = json.loads((C.ROUTE_IN / "start.geojson").read_text())["features"][0]["geometry"]["coordinates"]
    grid = Grid(study.union(passages).bounds)
    cost = build_cost(grid, inter, passages, canopies, forbidden, study, blocks)
    g, idx = graph(cost)
    print(f"grid {grid.w}x{grid.h}, {g.shape[0]:,} walkable cells, {g.nnz:,} edges  ({time.time() - t0:.0f}s)")

    feats = [f for f in json.loads(Path(a.targets).read_text())["features"]
             if f["properties"]["type"] in a.types.split(",")]
    s_node = anchor(grid, cost, idx, start, radius=5.0)
    anchors, members, unreachable = [], [], []
    for f in feats:
        xy = f["geometry"]["coordinates"]
        nd = anchor(grid, cost, idx, xy)
        if nd is None:
            unreachable.append(f["properties"]["id"])
            continue
        if nd in anchors:
            members[anchors.index(nd)].append(f)
        else:
            anchors.append(nd)
            members.append([f])
    # merge anchors whose targets are all covered by another anchor (both within VISIT of it)
    rc = np.column_stack(np.unravel_index(np.flatnonzero(np.isfinite(cost).ravel()), cost.shape))
    axy = grid.to_xy(rc[anchors, 0], rc[anchors, 1]) if anchors else np.zeros((0, 2))
    keep = list(range(len(anchors)))
    txy = [np.array([m["geometry"]["coordinates"] for m in ms]) for ms in members]
    order_by_size = sorted(range(len(anchors)), key=lambda i: -len(members[i]))
    alive = set(range(len(anchors)))
    for i in order_by_size:
        if i not in alive:
            continue
        near = [j for j in alive if j != i and np.hypot(*(axy[j] - axy[i])) < 2 * VISIT]
        for j in near:
            if np.all(np.hypot(txy[j][:, 0] - axy[i][0], txy[j][:, 1] - axy[i][1]) <= VISIT - 0.3):
                members[i] += members[j]
                txy[i] = np.vstack([txy[i], txy[j]])
                alive.discard(j)
    keep = sorted(alive)
    anchors = [anchors[i] for i in keep]
    members = [members[i] for i in keep]
    nodes = [s_node] + anchors
    print(f"{len(feats)} targets -> {len(anchors)} stops ({len(unreachable)} unreachable)  ({time.time() - t0:.0f}s)")

    D = np.zeros((len(nodes), len(nodes)))
    for k, src in enumerate(nodes):
        D[k] = dijkstra(g, directed=True, indices=src)[nodes]
        if k % 100 == 0:
            print(f"  distances {k}/{len(nodes)}  ({time.time() - t0:.0f}s)", flush=True)
    reach = np.isfinite(D[0]) & np.isfinite(D[:, 0])
    if not reach.all():
        print(f"  {int((~reach).sum())} stops not connected to START -> skipped")
    sub = np.flatnonzero(reach)
    Dsub = np.where(np.isfinite(D), D, 1e7)[np.ix_(sub, sub)]
    order = [int(sub[i]) for i in solve_tsp(Dsub, a.time)] + [0]
    print(f"TSP done  ({time.time() - t0:.0f}s)")

    path_rc = []
    for u, v in zip(order, order[1:]):
        dist, pred = dijkstra(g, directed=True, indices=nodes[u], return_predecessors=True, limit=D[u, v] + 1)
        seq, cur = [], nodes[v]
        while cur != nodes[u] and cur >= 0:
            seq.append(cur)
            cur = pred[cur]
        path_rc += [nodes[u]] + seq[::-1]
    xy = grid.to_xy(rc[path_rc, 0], rc[path_rc, 1])
    coords = [tuple(start)] + [tuple(p) for p in xy] + [tuple(start)]
    line = LineString(coords).simplify(0.2)
    length = round(line.length, 1)
    fc = {"type": "FeatureCollection", "crs": CRS, "features": [{
        "type": "Feature", "properties": {"length_m": length, "targets": len(feats) - len(unreachable),
                                          "types": a.types},
        "geometry": {"type": "LineString", "coordinates": [[round(x, 2), round(y, 2)] for x, y in line.coords]}}]}
    Path(a.out).write_text(json.dumps(fc))
    visit_order = {}
    for rank, k in enumerate(order[1:-1], 1):
        for m in members[k - 1]:
            visit_order[m["properties"]["id"]] = rank
    for f in feats:
        f["properties"]["order"] = visit_order.get(f["properties"]["id"])
    Path(a.targets_out).write_text(json.dumps({"type": "FeatureCollection", "crs": CRS, "features": feats}))
    print(f"route {length / 1000:.2f} km, {len(order) - 2} stops -> {a.out}  ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
