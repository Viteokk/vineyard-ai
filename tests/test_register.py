"""US-2 checks for the DEMO vineyard register.  Run:  python -m unittest tests.test_register"""
import json
import statistics
import sys
import unittest
from collections import defaultdict
from pathlib import Path

from shapely.geometry import mapping, shape
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import config as C  # noqa: E402
from pipeline.compliance import row_spacing  # noqa: E402
from pipeline.register import PARCELS, WEB, load_fc, measure  # noqa: E402


class RegisterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parcels = load_fc(PARCELS)
        cls.blocks = {f["properties"]["vineyard_id"]: shape(f["geometry"]).buffer(0) for f in load_fc(C.OUT / "blocks.geojson")}
        cls.register = load_fc(WEB / "register.geojson")

    def test_every_parcel_has_id_geometry_status_demo(self):
        for f in self.register:
            p = f["properties"]
            self.assertTrue(p["cad_nr"].startswith("DEMO-"))
            self.assertGreater(shape(f["geometry"]).area, 0)
            self.assertIn(p["status"], ("planted", "grubbed_up", "abandoned"))
            self.assertIs(p["demo"], True)
            if p["status"] == "planted" and p["measured"]["status_detected"] == "planted":
                self.assertIn("area_diff_ha", p["measured"])

    def test_split_halves_sum_to_block(self):
        rows_fc = load_fc(WEB / "pred" / "rows.geojson")
        sp = row_spacing(WEB / "pred" / "rows.geojson")
        dflt = statistics.median(sp.values())
        halves = defaultdict(list)
        for f in self.parcels:
            if f["properties"]["scenario"].startswith("split"):
                halves[f["properties"]["vineyard_id"]].append(f)
        self.assertEqual(len(halves), 6)
        for vid, fs in halves.items():
            self.assertEqual(len(fs), 2, vid)
            geo = sum(shape(f["geometry"]).area for f in fs)
            self.assertAlmostEqual(geo / self.blocks[vid].area, 1.0, delta=0.01, msg=f"{vid}: geometric area")
            whole = {"type": "Feature", "geometry": mapping(self.blocks[vid]), "properties": {**fs[0]["properties"], "status": "planted"}}
            m = measure([whole] + fs, rows_fc, [], [], sp, dflt, 1.2)
            parts = m[1]["properties"]["measured"]["measured_area_ha"] + m[2]["properties"]["measured"]["measured_area_ha"]
            self.assertAlmostEqual(parts / m[0]["properties"]["measured"]["measured_area_ha"], 1.0, delta=0.01, msg=f"{vid}: measured area")

    def test_deliberate_scenarios_present(self):
        sc = defaultdict(list)
        for f in self.register:
            sc[f["properties"]["scenario"]].append(f["properties"])
        self.assertEqual(len(sc["no_vines"]), 2)
        self.assertTrue(all(p["measured"]["status_detected"] == "no_vines" for p in sc["no_vines"]))
        self.assertEqual(sum(p["authorisation_nr"] is None for p in sc["split_unauthorised"]), 2)
        self.assertEqual(len(sc["abandoned_declared"]), 1)

    def test_registry_linked_by_cad_nr(self):
        recs = json.loads((ROOT / "registry" / "rvv_demo.json").read_text())["records"]
        cads = {f["properties"]["cad_nr"] for f in self.parcels}
        for r in recs:
            self.assertTrue(r.get("cad_nr"), r["vineyard_id"])
            self.assertTrue(set(r["cad_nr"]) <= cads, r["vineyard_id"])


if __name__ == "__main__":
    unittest.main()
