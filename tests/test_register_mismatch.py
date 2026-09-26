"""US-3 checks: every deliberate DEMO mismatch is found, nothing below the register threshold is flagged.
Run:  python -m unittest tests.test_register_mismatch"""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web" / "data"


class MismatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mm = json.loads((WEB / "register_mismatch.geojson").read_text())
        cls.items = [f["properties"] for f in cls.mm["features"]]
        cls.parcels = [f["properties"] for f in json.loads((WEB / "register.geojson").read_text())["features"]]
        cls.crit = json.loads((ROOT / "registry" / "criteria.json").read_text())["mismatch"]

    def test_unauthorised_halves_found(self):
        a = {i["vineyard_id"] for i in self.items if i["type"] == "A"}
        for p in self.parcels:
            if p["scenario"] == "split_unauthorised":
                self.assertIn(p["vineyard_id"], a)

    def test_declared_abandoned_found(self):
        a = {i["vineyard_id"] for i in self.items if i["type"] == "A"}
        for p in self.parcels:
            if p["scenario"] == "abandoned_declared":
                self.assertIn(p["vineyard_id"], a)

    def test_no_vines_found(self):
        b = {c for i in self.items if i["type"] == "B" for c in i["cad_nr"]}
        for p in self.parcels:
            if p["scenario"] == "no_vines":
                self.assertIn(p["cad_nr"], b)

    def test_unregistered_vineyards_found(self):
        cmp = json.loads((WEB / "compliance.json").read_text())["blocks"]
        a = {i["vineyard_id"] for i in self.items if i["type"] == "A"}
        for b in cmp:
            fail = any(c["id"] == "rvv" and c["status"] == "fail" for c in b["checks"])
            if fail and b["measured"]["rows"] >= self.crit["min_rows"]:
                self.assertIn(b["vineyard_id"], a)

    def test_nothing_below_threshold_flagged(self):
        below = {b["vineyard_id"] for b in self.mm["below_threshold"]}
        for i in self.items:
            self.assertNotIn(i.get("vineyard_id"), below)
            if i["type"] == "A":
                self.assertGreaterEqual(i["block_area_ha"], self.crit["min_area_ha"])

    def test_wording_and_fields(self):
        for i in self.items:
            self.assertTrue(i["label"].startswith("Posibil"), i["id"])
            for k in ("area_ha", "area_m2", "share_pct", "legal", "confidence", "visit"):
                self.assertIn(k, i)


if __name__ == "__main__":
    unittest.main()
