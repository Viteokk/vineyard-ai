"""Row walls + leg smoothing of pipeline/route.py on a small synthetic vineyard (no tiles, < 5 s).
Run:  python -m unittest tests.test_route_walls"""
import unittest
from types import SimpleNamespace

import numpy as np
import shapely
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

from pipeline import route as R

ROWS_Y = (0.0, 2.5, 5.0)            # three rows 40 m long, 2.5 m apart; the middle one has a 4 m planting gap
NO_ROAD = box(100, 100, 101, 101)


def scene(passage=None, seam=None, allow=False):
    """Grid, cost and graph as main() builds them. seam = (id1, id2): the middle row is two tile segments 1.9 m apart
    with a 0.3 m lateral offset at x = 20, inside the planting gap."""
    rows = [("R1", LineString([(0, ROWS_Y[0]), (40, ROWS_Y[0])])), ("R3", LineString([(0, ROWS_Y[2]), (40, ROWS_Y[2])]))]
    if seam:
        rows += [(seam[0], LineString([(0, 2.5), (19.05, 2.5)])), (seam[1], LineString([(20.95, 2.8), (40, 2.8)]))]
    else:
        rows += [("R2", LineString([(0, 2.5), (40, 2.5)]))]
    passages = passage if passage is not None else NO_ROAD
    canopies = unary_union([box(x - 0.25, y - 0.25, x + 0.25, y + 0.25) for y in ROWS_Y
                            for x in np.arange(0.3, 40, 1.2) if not (y == 2.5 and 17 < x < 23)]).difference(passages)
    inter = unary_union([box(0, 0.3, 40, 2.2), box(0, 2.8, 40, 4.7)])
    study, blocks, forbidden = box(-10, -10, 50, 15), box(-1, -1, 41, 6), unary_union([])
    grid = R.Grid(study.bounds)
    walls, axes = (None, None) if allow else R.row_walls(rows, passages)
    cost = R.build_cost(grid, inter, passages, canopies, forbidden, study, blocks, walls)
    g, idx = R.graph(cost, None if axes is None else R.wall_cuts(grid, cost, passages, axes))
    rc = np.column_stack(np.unravel_index(np.flatnonzero(np.isfinite(cost).ravel()), cost.shape))
    return SimpleNamespace(grid=grid, cost=cost, g=g, idx=idx, rc=rc, axes=axes, rows=[l for _, l in rows],
                           allowed=unary_union([inter, passages]).buffer(0.01), blocked=unary_union([canopies, forbidden]))


def route_leg(a, b, **kw):
    """Leg a -> b (x, y) as main() builds it -> (raw cell path xy, pulled xy, length, outside, scene)."""
    s = scene(**kw)
    u, v = R.anchor(s.grid, s.cost, s.idx, a), R.anchor(s.grid, s.cost, s.idx, b)
    R._init_worker(s.g, [u, v], R.REACH_M, s.allowed, s.rc, s.grid, s.cost[s.rc[:, 0], s.rc[:, 1]] >= R.COST_LINK,
                   np.isfinite(s.cost), s.blocked, s.axes)
    path = [u] + R.leg(s.g, u, v, np.inf)
    xy, length, outside = R._leg_job((u, v, np.inf))
    return s.grid.to_xy(s.rc[path, 0], s.rc[path, 1]), xy, length, outside, s


def steps(s):
    """Every edge of the graph as a LineString."""
    a, b = s.g.nonzero()
    return shapely.linestrings(np.stack([s.grid.to_xy(s.rc[a, 0], s.rc[a, 1]), s.grid.to_xy(s.rc[b, 0], s.rc[b, 1])], axis=1))


class WallTest(unittest.TestCase):
    def test_no_step_jumps_a_wall(self):
        """No edge of the 8-neighbour graph crosses a row axis, at any row angle (diagonal steps included)."""
        for deg in (0, 17, 30, 45, 60, 90):
            u = np.array([np.cos(np.radians(deg)), np.sin(np.radians(deg))])
            n = np.array([-u[1], u[0]])
            rows = [(f"R{k}", LineString([tuple(20 * u + k * 2.5 * n), tuple(-20 * u + k * 2.5 * n)])) for k in range(-2, 3)]
            grid, area = R.Grid((-25, -25, 25, 25)), box(-25, -25, 25, 25)
            walls, axes = R.row_walls(rows, NO_ROAD)
            cost = R.build_cost(grid, area, NO_ROAD, unary_union([]), unary_union([]), area, area, walls)
            g, _ = R.graph(cost, R.wall_cuts(grid, cost, NO_ROAD, axes))
            rc = np.column_stack(np.unravel_index(np.flatnonzero(np.isfinite(cost).ravel()), cost.shape))
            e = steps(SimpleNamespace(g=g, grid=grid, rc=rc))
            self.assertGreater(len(e), 10000)
            self.assertEqual(int(shapely.intersects(e, unary_union([l for _, l in rows])).sum()), 0, f"row at {deg} deg")

    def test_route_goes_around_the_row(self):
        raw, xy, _, _, s = route_leg((10, 1.25), (10, 3.75))
        line = LineString(xy)
        self.assertFalse(LineString(raw).intersects(unary_union(s.rows)))
        self.assertFalse(line.intersects(unary_union(s.rows)))
        self.assertTrue(line.bounds[0] < 0 or line.bounds[2] > 40, "must walk past a row end")

    def test_allow_crossing_uses_the_gap(self):
        _, xy, _, _, s = route_leg((10, 1.25), (10, 3.75), allow=True)
        self.assertTrue(LineString(xy).intersects(s.rows[2]), "old behaviour: through the planting gap")

    def test_tile_seam_does_not_leak(self):
        for ids in (("R2", "R2"), ("R2a", "R2b")):          # same row_id (BRIDGE), ids differ (SEAM)
            _, xy, _, _, s = route_leg((20, 1.25), (20, 3.75), seam=ids)
            line = LineString(xy)
            self.assertFalse(line.intersects(unary_union(s.rows + [LineString([(19.05, 2.5), (20.95, 2.8)])])), ids)
            self.assertTrue(line.bounds[0] < 0 or line.bounds[2] > 40, ids)

    def test_passage_opens_the_wall(self):
        road = box(30, -5, 33, 10)
        _, xy, length, _, s = route_leg((28, 1.25), (28, 3.75), passage=road)
        x = LineString(xy).intersection(unary_union(s.rows))
        self.assertLess(length, 15)
        self.assertFalse(x.is_empty)
        self.assertTrue(road.contains(x), "crossings only inside the passage")

    def test_narrow_oblique_road_stays_open(self):
        """A 1.2 m road crossing the rows at ~68 deg stays walkable along its whole length, and no step of the graph
        crosses a row axis outside it (the steps right next to its edges are cut, not its cells)."""
        road = Polygon([(28, -5), (29.2, -5), (35.2, 10), (34, 10)])
        raw, xy, length, _, s = route_leg((28.6, -4), (34.6, 9), passage=road)
        axes = unary_union(s.rows)
        self.assertLess(length, 1.15 * np.hypot(6, 13))
        self.assertTrue(road.contains(LineString(xy).intersection(axes)))
        self.assertTrue(road.contains(LineString(raw).intersection(axes)))
        self.assertEqual(int(shapely.intersects(steps(s), axes.difference(road)).sum()), 0)

    def test_steps_next_to_a_passage_edge_are_cut(self):
        """A notch in the road edge right on a row axis (in the planting gap): the road cells on both sides stay
        walkable, but the steps between them cross the axis outside the road (in the notch): left out of the graph."""
        road = Polygon([(19, -5), (22, -5), (22, 10), (19, 10), (19, 2.6), (20, 2.5), (19, 2.4)])
        s = scene(passage=road)
        cut = R.wall_cuts(s.grid, s.cost, road, s.axes)
        self.assertGreater(sum(int(m.sum()) for m in cut.values()), 0)
        self.assertEqual(int(shapely.intersects(steps(s), unary_union(s.rows).difference(road)).sum()), 0)
        _, xy, length, _, s = route_leg((20.5, -4), (20.5, 9), passage=road)
        self.assertLess(length, 14)                                                # the road is not cut
        self.assertTrue(road.contains(LineString(xy).intersection(unary_union(s.rows))))


class SmoothTest(unittest.TestCase):
    def test_pulled_leg(self):
        raw, xy, length, outside, s = route_leg((5, 1.25), (35, 3.75))
        rawl, line = LineString(raw), LineString(xy)
        self.assertLess(len(xy), len(raw) / 4, "staircase removed")
        self.assertLessEqual(length, rawl.length + 1e-9)
        np.testing.assert_allclose(xy[[0, -1]], raw[[0, -1]])                      # stops stay fixed vertices
        self.assertLessEqual(outside, rawl.difference(s.allowed).length + 1e-6)   # no more outside metres
        self.assertAlmostEqual(outside, line.difference(s.allowed).length, places=6)
        self.assertFalse(line.intersects(s.blocked))                              # no canopy clipped
        self.assertFalse(line.intersects(unary_union(s.rows)))                    # no row crossed
        pc = np.column_stack([(s.grid.y1 - xy[:, 1]) / R.RES - 0.5, (xy[:, 0] - s.grid.x0) / R.RES - 0.5])
        for a, b in zip(pc, pc[1:]):
            self.assertTrue(R.visible(np.isfinite(s.cost), a, b))

    def test_pull_keeps_ends(self):
        self.assertEqual(R.pull(2, lambda i, j: True), [0, 1])
        self.assertEqual(R.pull(9, lambda i, j: True), [0, 8])
        self.assertEqual(R.pull(9, lambda i, j: j - i == 1), list(range(9)))
        self.assertEqual(R.pull(9, lambda i, j: j <= 4 or i >= 4), [0, 4, 8])


if __name__ == "__main__":
    unittest.main()


class TileEdgeSeams(unittest.TestCase):
    """The annotation stops at the tile edge, the trellis does not (seen on the real data: ids change across the
    edge and the next segment starts up to 15 m further on, or is missing)."""

    def test_different_ids_across_a_tile_edge_are_joined(self):
        tb = {"t1": (-10.0, -10.0, 20.0, 10.0), "t2": (20.0, -10.0, 50.0, 10.0)}
        rows = [("V14-R55", "t1", LineString([(0, 0), (19.8, 0)])), ("V15-R52", "t2", LineString([(31.0, 0.3), (48, 0.3)]))]
        _, axes = R.row_walls(rows, NO_ROAD, tb)
        self.assertTrue(axes.intersects(LineString([(25, -2), (25, 2)])), "11 m seam across the tile edge is walled")

    def test_open_end_on_a_tile_edge_is_extended(self):
        tb = {"t1": (-10.0, -10.0, 20.0, 10.0)}
        _, axes = R.row_walls([("R1", "t1", LineString([(0, 0), (19.7, 0)]))], NO_ROAD, tb)
        self.assertTrue(axes.intersects(LineString([(23, -2), (23, 2)])), "the row goes on past the tile edge")
        self.assertFalse(axes.intersects(LineString([(26, -2), (26, 2)])), "but only EXTEND m")

    def test_real_row_end_inside_the_tile_is_not_extended(self):
        tb = {"t1": (-10.0, -10.0, 40.0, 10.0)}
        _, axes = R.row_walls([("R1", "t1", LineString([(0, 0), (19.7, 0)]))], NO_ROAD, tb)
        self.assertFalse(axes.intersects(LineString([(21, -2), (21, 2)])), "a headland inside the tile stays open")

